"""Read-only conversation/scene detector for a running Mass Effect Legendary Edition game.

Why this exists: every conversation-handling mechanism depends on being able to tell
"a scene owns the player" from "ordinary gameplay", and the two are easy to confuse. A
mechanism tested against a misdetected state proves nothing. This tool only READS, so it can
be run as often as wanted and it cannot break anything.

It reports four independent signals and refuses to reduce them to one boolean:

1. `WorldInfo.bPlayersOnly`      - world-wide; the thing that actually freezes pawns
2. `PlayerController` cinematic bit(s)
3. active / activating conversation counts
4. the player pawn's `Physics`    - 7 means the script is driving it

Why four: either signal alone lies. A world can be `PlayersOnly` for a reason that is not a
conversation; a controller can report a conversation while the world carries on; and a pawn
can be script-driven with no conversation at all (a matinee). The tool states what it read and
flags disagreement instead of picking a winner.

Crucially, an unreadable field is reported as `unread`, never as zero. The project's own
documented finding is that a missing value must not be treated as "no conversation".

Offsets come from the per-game JSON offsets file, and every property is resolved BY NAME on
the live object first. A ported offset is a hypothesis, so nothing here is hardcoded per game.

Usage:

    python -I tools/ue3live/ue3_conversation.py --pid <pid> --exe MassEffect1.exe \
        --offsets local/MELE/offsets/offsets_LE1.json
    ... --watch 30 --interval 0.5     # sample for 30 seconds instead of once

Read-only: OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION).
"""
import argparse
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue3_reader
from ue3_proc import Reader, module_base
from ue3_props import class_properties
from ue3_playerchain import live_engines

PHYS = {0: "None", 1: "Walking", 2: "Falling", 3: "Swimming", 4: "Flying",
        5: "Rotating", 6: "Projectile", 7: "Interpolating", 8: "Spider",
        9: "Ladder", 10: "RigidBody", 11: "SoftBody", 12: "Custom"}

# Names to try for each signal. This game family uses Bio/SFX prefixes inconsistently across
# builds, so each signal lists every spelling seen or plausible instead of assuming one.
SCENE_FLAGS = ("bPlayersOnly",)
CINEMATIC_BITS = ("bCinematicMode", "bStartCinematicMode", "m_bCinematicMode",
                  "bInCinematicMode", "bCameraCut")
CONV_ACTIVE = ("m_nActiveConversations", "ActiveConversationCount", "m_iActiveConversations",
               "NumActiveConversations")
CONV_ACTIVATING = ("m_nActivatingConversations", "ActivatingConversationCount",
                   "m_iActivatingConversations", "NumActivatingConversations")
CONV_OBJECT = ("m_oActiveConversation", "ActiveConversation", "m_pConversation",
               "CurrentConversation")
PAUSE_FLAGS = ("m_bPausedByFocusLoss", "bPausedByFocusLoss")


def prop_table(game, obj):
    """All reflected fields of obj's class, including inherited, keyed by name."""
    table = {}
    cls = game.r.ptr(obj + 0x50)
    for name, kind, off, size, declaring in class_properties(game.r, game, cls,
                                                             include_functions=False):
        if name:
            table.setdefault(name, (off, size, kind, declaring))
    return table


def read_field(game, obj, table, names):
    """First matching field's value, with its provenance. Unreadable is not zero."""
    for want in names:
        if want not in table:
            continue
        off, size, kind, declaring = table[want]
        if size == 1:
            raw = game.r.read(obj + off, 1)
            if raw is None:
                return {"name": want, "state": "unread", "offset": off,
                        "declared_on": declaring}
            return {"name": want, "state": "ok", "offset": off, "kind": kind,
                    "value": raw[0], "declared_on": declaring}
        if size == 4:
            raw = game.r.read(obj + off, 4)
            if raw is None:
                return {"name": want, "state": "unread", "offset": off,
                        "declared_on": declaring}
            return {"name": want, "state": "ok", "offset": off, "kind": kind,
                    "value": struct.unpack("<i", raw)[0], "declared_on": declaring}
        if size == 8:
            v = game.r.ptr(obj + off)
            return {"name": want, "state": "ok", "offset": off, "kind": kind,
                    "value": hex(v), "name_of": game.object_name(v) if v else None,
                    "class_of": game.obj_class(v) if v else None,
                    "declared_on": declaring}
        return {"name": want, "state": "ok", "offset": off, "kind": kind,
                "size": size, "declared_on": declaring}
    return {"state": "absent", "tried": list(names)}


def player_chain(game, cfg):
    """(engine, local_players, controller, pawn) for the first local player, or unknowns."""
    for engine in live_engines(game):
        raw = game.r.read(engine["ptr"] + int(cfg["engine_game_players"], 16), 16)
        if not raw:
            continue
        data, count, mx = struct.unpack("<Qii", raw)
        if not data or not (1 <= count <= 8) or mx < count:
            continue
        lp = game.r.ptr(data)
        if not lp or game.obj_class(lp) != "LocalPlayer":
            continue
        pc = game.r.ptr(lp + int(cfg["local_player_actor"], 16))
        pawn = game.r.ptr(pc + int(cfg["controller_pawn"], 16)) if pc else 0
        return engine, count, lp, pc, pawn
    return None, 0, 0, 0, 0


def world_of(game, controller, cfg):
    key = "controller_worldinfo"
    if key not in cfg or not controller:
        return 0
    return game.r.ptr(controller + int(cfg[key], 16))


def sample(pid, exe, offsets):
    base = module_base(pid, exe)
    if not base:
        return {"error": "%s not found in pid %d" % (exe, pid)}
    reader = Reader(pid)
    try:
        game = ue3_reader.Game(reader, base, offsets)
        with open(offsets, encoding="utf-8") as f:
            cfg = json.load(f)
        out = {"pid": pid, "objects": game.count, "read_only": True}

        engine, players, lp, pc, pawn = player_chain(game, cfg)
        out["players"] = players
        out["chain"] = {"engine": hex(engine["ptr"]) if engine else None,
                        "local_player": hex(lp) if lp else None,
                        "controller": hex(pc) if pc else None,
                        "pawn": hex(pawn) if pawn else None}
        if not engine:
            out["error"] = "no engine exposes GamePlayers of LocalPlayer"
            return out

        world = world_of(game, pc, cfg)
        out["worldinfo"] = hex(world) if world else None

        # 1. world-wide scene flag
        if world:
            wt = prop_table(game, world)
            out["bPlayersOnly"] = read_field(game, world, wt, SCENE_FLAGS)
        else:
            out["bPlayersOnly"] = {"state": "absent", "reason": "no worldinfo"}

        # 2 + 3. per-controller signals
        if pc:
            ct = prop_table(game, pc)
            out["cinematic"] = read_field(game, pc, ct, CINEMATIC_BITS)
            out["conversations_active"] = read_field(game, pc, ct, CONV_ACTIVE)
            out["conversations_activating"] = read_field(game, pc, ct, CONV_ACTIVATING)
            out["conversation_object"] = read_field(game, pc, ct, CONV_OBJECT)
            out["focus_pause"] = read_field(game, pc, ct, PAUSE_FLAGS)
        else:
            for k in ("cinematic", "conversations_active", "conversations_activating",
                      "conversation_object", "focus_pause"):
                out[k] = {"state": "absent", "reason": "no controller"}

        # 4. pawn physics
        if pawn:
            pt = prop_table(game, pawn)
            out["pawn_physics"] = read_field(game, pawn, pt, ("Physics",))
            if out["pawn_physics"].get("state") == "ok" and "value" in out["pawn_physics"]:
                out["pawn_physics"]["meaning"] = PHYS.get(out["pawn_physics"]["value"], "?")
        else:
            out["pawn_physics"] = {"state": "absent", "reason": "no pawn"}

        out["verdict"] = verdict(out)
        return out
    finally:
        reader.k32.CloseHandle(reader.handle)


def verdict(s):
    """A conservative classification that says so when the signals disagree.

    Deliberately not a single boolean: 'scene' requires positive evidence, and disagreement is
    reported as `conflicting` rather than resolved by preference.
    """
    def val(key):
        d = s.get(key) or {}
        return d.get("value") if d.get("state") == "ok" else None

    def unread(*keys):
        return [k for k in keys if (s.get(k) or {}).get("state") == "unread"]

    players_only = val("bPlayersOnly")
    cinematic = val("cinematic")
    active = val("conversations_active")
    activating = val("conversations_activating")
    physics = val("pawn_physics")
    conv_obj = s.get("conversation_object") or {}
    has_conv_obj = bool(conv_obj.get("value") and conv_obj.get("value") != "0x0")

    evidence = {
        "players_only": bool(players_only),
        "cinematic": bool(cinematic),
        "conversation_counts": bool(active) or bool(activating),
        "conversation_object": has_conv_obj,
        "script_driven_pawn": physics == 7,
    }
    positives = sum(1 for v in evidence.values() if v)
    missing = unread("bPlayersOnly", "cinematic", "conversations_active",
                     "conversations_activating", "pawn_physics")

    if missing:
        return {"state": "incomplete", "evidence": evidence,
                "unread": missing,
                "why": "unread fields must not be treated as 'no scene'"}
    if positives == 0:
        return {"state": "playing", "evidence": evidence}
    if positives >= 2:
        return {"state": "scene", "evidence": evidence,
                "why": "two or more independent signals agree a scene owns the player"}
    return {"state": "conflicting", "evidence": evidence,
            "why": "exactly one signal is positive; do not act on this"}


def summarise(s):
    if "error" in s:
        print("error: %s" % s["error"])
        return
    v = s.get("verdict") or {}
    print("players=%s  controller=%s  pawn=%s"
          % (s["players"], s["chain"]["controller"], s["chain"]["pawn"]))
    for key in ("bPlayersOnly", "cinematic", "conversations_active",
                "conversations_activating", "conversation_object", "focus_pause",
                "pawn_physics"):
        d = s.get(key) or {}
        st = d.get("state")
        if st == "ok":
            if "meaning" in d:
                print("  %-26s = %s (%s)  [%s]" % (key, d.get("value"), d["meaning"],
                                                   d.get("declared_on")))
            elif "name_of" in d:
                print("  %-26s = %s %s  [%s]" % (key, d.get("value"),
                                                 d.get("class_of") or "", d.get("declared_on")))
            else:
                print("  %-26s = %s  [%s]" % (key, d.get("value"), d.get("declared_on")))
        else:
            print("  %-26s %s%s" % (key, st,
                                    (" (" + ", ".join(d["tried"]) + ")") if "tried" in d else ""))
    print("  verdict: %s   evidence=%s" % (v.get("state"), v.get("evidence")))
    if v.get("why"):
        print("           %s" % v["why"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--offsets", required=True)
    ap.add_argument("--watch", type=float, default=0, help="seconds to sample for")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.watch <= 0:
        s = sample(args.pid, args.exe, args.offsets)
        print(json.dumps(s, indent=1) if args.json else "", end="")
        if not args.json:
            summarise(s)
        return 0

    end = time.time() + args.watch
    last = None
    changes = 0
    while time.time() < end:
        s = sample(args.pid, args.exe, args.offsets)
        v = (s.get("verdict") or {}).get("state")
        if last is not None and v != last:
            changes += 1
            print("[%6.2fs] -> %s" % (args.watch - (end - time.time()), v))
        last = v
        time.sleep(args.interval)
    print("\n%d verdict change(s) in %.1fs" % (changes, args.watch))
    s = sample(args.pid, args.exe, args.offsets)
    print(json.dumps(s, indent=1) if args.json else "", end="")
    if not args.json:
        summarise(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
