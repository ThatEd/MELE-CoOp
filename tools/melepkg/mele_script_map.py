"""Offline script map and gate inventory for ME:LE packages.

Uses the verified container reader (`melepkg.py`) to decompress a package and then answers the
two questions a co-op port keeps asking:

* **which control gates exist in this build** - the functions and flags that decide whether the
  player may fire, move, use powers, or act at all;
* **which map-event classes this build has** - the `SeqAct_*` / `BioSeqAct_*` / `BioSeqEvt_*`
  family that fires on level entry, trigger and story progress.

It runs without the game, which is the point: the same questions used to need a live process.

Honest limits, because they matter:

* These are **identifier** inventories, not per-class attribution. The decompressed stream has no
  parsed export table yet, so "`CanFireWeapon` exists in this build" is provable while "it belongs
  to `SFXGameModeDefault`" is not. Do not read an inventory as a class listing.
* Both ASCII and UTF-16LE are scanned, because ME3 stores identifiers wide. Checking one encoding
  reports a clean zero, which reads as "does not exist" - a wrong answer about a gate.
* A name existing does not mean it is reachable at the moment you care about. It is a lead that
  says "this lever is present in this build", to be confirmed live.

Usage:

    python -I tools/melepkg/mele_script_map.py --package <SFXGame.pcc> --out map.json
    python -I tools/melepkg/mele_script_map.py --package <pcc> --gates
    python -I tools/melepkg/mele_script_map.py --package <pcc> --events --limit 40
    python -I tools/melepkg/mele_script_map.py --diff a.json b.json
"""
import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load_pkg():
    spec = importlib.util.spec_from_file_location("melepkg", os.path.join(HERE, "melepkg.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


IDENT_ASCII = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{2,63}")
IDENT_WIDE = re.compile(rb"(?:[A-Za-z_]\x00)(?:[A-Za-z0-9_]\x00){2,62}")

# Control gates: things that decide whether the player may act. Grouped so the report reads as
# "which lever", not a flat word list. Names come from the documented firing chain plus the
# conversation/pause/input machinery; each group is checked independently.
GATE_GROUPS = {
    "firing": ("CanFire", "bCanFire", "AllowFire", "StartFire", "StopFire",
               "HandleWeaponFiring", "FireWeapon", "WeaponFire", "bFire", "IsFiring",
               "bWeaponFireEnabled", "PreventFire"),
    "local_control": ("IsLocallyControlled", "IsLocalPlayerController", "LocalPlayerController",
                      "GetALocalPlayerController", "IsLocalController", "bIsLocal"),
    "movement": ("CanMove", "bCanMove", "AllowMovement", "bMovementEnabled", "SetMovement",
                 "bBlockMovement", "bIgnoreMoveInput", "SetIgnoreMoveInput", "bRunPhysics"),
    "powers": ("CanUsePower", "UseAbility", "UsePower", "doHotKey", "ActivatePower",
               "SquadOrderUsePower", "CanStartPower", "StartPowerCustomAction"),
    "input": ("PlayerInput", "DisableInput", "bInputEnabled", "EnableInput", "bBlockInput",
              "PushInput", "PopInput", "bCinematicMode", "SetCinematicMode"),
    "pause_scene": ("bPlayersOnly", "PauseGame", "SetPause", "bPausedByFocusLoss", "HasFocus",
                    "IsPaused", "bShowHUD", "SetTimer"),
    "conversation": ("ConversationActive", "ActiveConversation", "ActivatingConversation",
                     "CanStartConversation", "StartConversation", "EndConversation",
                     "ConversationMode", "TalkTo", "InteractionType"),
    "combat_state": ("InCombat", "EnterCombat", "ExitCombat", "SetCombatMode", "CombatMode",
                     "bCombatEnabled", "DamageEnabled", "bCanBeDamaged"),
    "squad": ("SquadMember", "AddMember", "RemoveMember", "GetSquadSize", "SquadOrder",
              "Henchman", "PartyMember", "FollowSquad"),
}

EVENT_PREFIXES = ("SeqAct_", "SeqEvt_", "SeqCond_", "SeqVar_", "BioSeqAct_", "BioSeqEvt_",
                  "BioSeqCond_", "BioSeqVar_", "SFXSeqAct_", "SFXSeqEvt_", "BioSeq_",
                  "SequenceAction", "SequenceEvent", "SequenceCondition", "SequenceVariable")


def identifiers(buf):
    """Every identifier-shaped string in either encoding."""
    out = {m.group().decode("ascii") for m in IDENT_ASCII.finditer(buf)}
    out |= {m.group().decode("utf-16-le") for m in IDENT_WIDE.finditer(buf)}
    return out


def build_map(package, pkg=None):
    pkg = pkg or load_pkg()
    data = open(package, "rb").read()
    dll = pkg.find_oodle()
    if not dll:
        raise SystemExit("no oo2core*.dll found; is the game installed?")
    buf, chunks = pkg.decompress(data, pkg.Oodle(dll))
    ids = identifiers(buf)

    lower = {i.lower(): i for i in ids}
    gates = {}
    for group, names in GATE_GROUPS.items():
        found = []
        for want in names:
            hit = lower.get(want.lower())
            if hit:
                found.append(hit)
        gates[group] = sorted(set(found))

    events = sorted(i for i in ids if i.startswith(EVENT_PREFIXES))

    return {
        "package": os.path.basename(package),
        "schema": 1,
        "container": {
            "chunks": len(chunks),
            "blocks": sum(c["blocks"] for c in chunks),
            "compressed": sum(c["total_csize"] for c in chunks),
            "uncompressed": len(buf),
        },
        "identifier_count": len(ids),
        "gates": gates,
        "gate_summary": {g: len(v) for g, v in gates.items()},
        "events": events,
        "event_count": len(events),
        "identifiers": sorted(ids),
    }


def cmd_gates(args):
    m = build_map(args.package)
    print("%s: %d chunks, %d blocks, %d bytes, %d identifiers"
          % (m["package"], m["container"]["chunks"], m["container"]["blocks"],
             m["container"]["uncompressed"], m["identifier_count"]))
    print()
    print("%-16s %s" % ("GATE GROUP", "names present in this build"))
    for group in GATE_GROUPS:
        found = m["gates"][group]
        print("%-16s (%d) %s" % (group, len(found), ", ".join(found) if found else "-"))
    missing = [g for g, v in m["gates"].items() if not v]
    if missing:
        print("\nno names found for: %s" % ", ".join(missing))
        print("(a group with nothing found means none of its KNOWN names are in this build;")
        print(" it does not prove the capability is absent - list the group above and look)")
    return 0


def cmd_events(args):
    m = build_map(args.package)
    print("%s: %d map-event class names" % (m["package"], m["event_count"]))
    by_prefix = {}
    for e in m["events"]:
        for p in EVENT_PREFIXES:
            if e.startswith(p):
                by_prefix.setdefault(p, []).append(e)
                break
    for p in sorted(by_prefix):
        print("  %-14s %4d   e.g. %s" % (p, len(by_prefix[p]),
                                         ", ".join(by_prefix[p][:4])))
    print()
    for e in m["events"][:args.limit]:
        print("   %s" % e)
    if m["event_count"] > args.limit:
        print("   ... %d more (use --limit)" % (m["event_count"] - args.limit))
    return 0


def cmd_diff(args):
    a = json.load(open(args.diff[0], encoding="utf-8"))
    b = json.load(open(args.diff[1], encoding="utf-8"))
    # Both games ship a file called SFXGame.pcc, so label by the map's own path instead -
    # otherwise the diff says "only in SFXGame.pcc" twice and is unreadable.
    a["label"] = os.path.basename(args.diff[0]).replace(".json", "")
    b["label"] = os.path.basename(args.diff[1]).replace(".json", "")
    A = set(a["identifiers"])
    B = set(b["identifiers"])
    print("%s  vs  %s" % (a["label"], b["label"]))
    print("identifiers: %d vs %d, %d common"
          % (len(A), len(B), len(A & B)))
    for group in GATE_GROUPS:
        ga, gb = set(a["gates"][group]), set(b["gates"][group])
        onlya = sorted(ga - gb)
        onlyb = sorted(gb - ga)
        if onlya or onlyb:
            print("\n  gate group %s:" % group)
            if onlya:
                print("    only in %s: %s" % (a["label"], ", ".join(onlya)))
            if onlyb:
                print("    only in %s: %s" % (b["label"], ", ".join(onlyb)))
    ea, eb = set(a["events"]), set(b["events"])
    print("\nevents: %d vs %d, %d common" % (len(ea), len(eb), len(ea & eb)))
    if ea - eb:
        print("  only in %s (%d): %s" % (a["label"], len(ea - eb),
                                         ", ".join(sorted(ea - eb)[:20])))
    if eb - ea:
        print("  only in %s (%d): %s" % (b["label"], len(eb - ea),
                                         ", ".join(sorted(eb - ea)[:20])))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--package")
    ap.add_argument("--out")
    ap.add_argument("--gates", action="store_true")
    ap.add_argument("--events", action="store_true")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"))
    args = ap.parse_args()

    if args.diff:
        return cmd_diff(args)
    if not args.package:
        ap.error("--package is required (or use --diff A B)")
    if args.gates:
        return cmd_gates(args)
    if args.events:
        return cmd_events(args)

    m = build_map(args.package)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=1)
        print("wrote %s (%d bytes)" % (args.out, os.path.getsize(args.out)))
    print("%s: %d chunks, %d blocks, %d identifiers, %d events"
          % (m["package"], m["container"]["chunks"], m["container"]["blocks"],
             m["identifier_count"], m["event_count"]))
    for group in GATE_GROUPS:
        print("  %-16s %d" % (group, len(m["gates"][group])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
