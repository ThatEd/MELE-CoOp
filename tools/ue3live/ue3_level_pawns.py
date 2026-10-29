"""
Inventory the actors of the level the player is in, and name the pawns.

Why: taking over a squadmate for player 2 needs the actual object to possess, and
guessing from a wiki is how the wrong character gets possessed. This walks the player's
own pawn back to its owning ULevel, then lists every pawn and controller there, with the
fields that identify a character (tag, PawnType, squad membership, physics).

Read-only: PROCESS_VM_READ only.

The sys.path insert below makes the sibling modules importable when this file is run
with `python -I`, which omits the script directory from sys.path. Without it these tools
only work when invoked without -I, which is not how the README documents them.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argparse
import collections
import json
import struct
import sys

import os
import sys
# Running with -I omits the script directory from sys.path, and the README documents
# these tools as runnable with -I. Make the sibling modules importable regardless.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ue3_reader
from ue3_proc import Reader, module_base
from ue3_props import find_class
from ue3_playerchain import live_engines

LEVEL_ACTORS = 0x60
PHYS = {0: "None", 1: "Walking", 2: "Falling", 3: "Swimming", 4: "Flying",
        5: "Rotating", 6: "Projectile", 7: "Interpolating", 8: "Spider",
        9: "Ladder", 10: "RigidBody", 11: "SoftBody", 12: "Custom"}


def h(s):
    return int(s, 16)


def read_cfg(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def name_string(game, obj, off):
    """UE3 FString at offset: {wchar_t *data, int count, int max}."""
    raw = game.r.read(obj + off, 16)
    if not raw:
        return None
    data, count, mx = struct.unpack("<Qii", raw)
    if not data or not (0 < count <= 256):
        return None
    text = game.r.read(data, min(count, 128) * 2)
    if not text:
        return None
    try:
        return text.decode("utf-16-le", "ignore").split("\0")[0]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--offsets", required=True)
    args = ap.parse_args()

    base = module_base(args.pid, args.exe)
    reader = Reader(args.pid)
    cfg = read_cfg(args.offsets)
    try:
        game = ue3_reader.Game(reader, base, args.offsets)
        out = {"read_only": True, "objects": game.count}

        # Player chain -> the level that owns the player pawn.
        owner = None
        for e in live_engines(game):
            raw = reader.read(e["ptr"] + h(cfg["engine_game_players"]), 16)
            if not raw:
                continue
            d, c, m = struct.unpack("<Qii", raw)
            if d and 1 <= c <= 8 and m >= c:
                first = reader.ptr(d)
                if first and game.obj_class(first) == "LocalPlayer":
                    owner = (e, d, c)
                    break
        if not owner:
            print(json.dumps(dict(out, error="no GamePlayers found"), indent=1))
            return 1
        engine, data, count = owner
        lp = reader.ptr(data)
        pc = reader.ptr(lp + h(cfg["local_player_actor"]))
        player_pawn = reader.ptr(pc + h(cfg["controller_pawn"]))
        out["player"] = {"engine": engine["name"], "local_player": hex(lp),
                         "controller": hex(pc), "pawn": hex(player_pawn)}

        level_class = find_class(reader, game, "Level")
        holder, actors = None, None
        for i in range(1, game.count):
            o = reader.ptr(game.data + i * 8)
            if not o or game.obj_class(o) != "Level":
                continue
            raw = reader.read(o + LEVEL_ACTORS, 16)
            if not raw:
                continue
            d, c, m = struct.unpack("<Qii", raw)
            if not d or not (0 < c <= 200000):
                continue
            for k in range(c):
                if reader.ptr(d + k * 8) == player_pawn:
                    holder, actors = o, (d, c)
                    break
            if holder:
                break
        if not holder:
            out["error"] = "player pawn not found in any level's Actors array"
            print(json.dumps(out, indent=1))
            return 1

        theworld = reader.ptr(holder + 0x40)
        pkg = reader.ptr(theworld + 0x40) if theworld else 0
        out["level"] = {"ptr": hex(holder), "actor_count": actors[1],
                        "package": game.object_name(pkg)}

        d, c = actors
        pawns, histogram = [], collections.Counter()
        # Fields we want per pawn, looked up by name on its class so no offset is assumed.
        for k in range(c):
            a = reader.ptr(d + k * 8)
            if not a:
                continue
            cls = game.obj_class(a) or "(undecodable)"
            histogram[cls] += 1
            if cls not in ("BioPawn",) and "Pawn" not in cls:
                continue
            entry = {"ptr": hex(a), "class": cls, "name": game.object_name(a)}
            for field, key, kind in (
                    ("Tag", "tag", "name"), ("PawnType", "pawn_type", "obj"),
                    ("Squad", "squad", "obj"), ("Controller", "controller", "obj"),
                    ("Physics", "physics", "byte"), ("Health", "health", "int"),
                    ("m_sDisplayName", "display", "fstring"),
                    ("DisplayName", "display2", "fstring"),
                    ("m_sName", "char_name", "fstring")):
                if field in (cfg.get("pawns", {}) or {}):
                    off = h(cfg["pawns"][field])
                else:
                    off = None
                if off is None:
                    # Resolve by property name on this pawn's class.
                    from ue3_props import class_properties
                    tbl = {}
                    cur = reader.ptr(a + 0x50)
                    for _ in range(40):
                        if not cur:
                            break
                        for n, kk, o, sz, dd in class_properties(
                                reader, game, cur, include_functions=False):
                            tbl.setdefault(n, (o, sz, kk))
                        cur = reader.ptr(cur + 0x60)
                    if field not in tbl:
                        continue
                    off, sz, kk2 = tbl[field]
                    if kind == "obj":
                        t = reader.ptr(a + off)
                        entry[key] = {"offset": off, "ptr": hex(t),
                                      "name": game.object_name(t),
                                      "class": game.obj_class(t)}
                    elif kind == "byte":
                        raw = reader.read(a + off, 1)
                        entry[key] = {"offset": off, "value": raw[0] if raw else None}
                    elif kind == "int":
                        raw = reader.read(a + off, 4)
                        entry[key] = {"offset": off,
                                      "value": struct.unpack("<i", raw)[0] if raw else None}
                    else:
                        entry[key] = {"offset": off,
                                      "text": name_string(game, a, off)}
            if "physics" in entry and isinstance(entry["physics"], dict):
                v = entry["physics"].get("value")
                entry["physics"]["state"] = PHYS.get(v, "?")
            pawns.append(entry)

        out["level"]["class_histogram"] = histogram.most_common(30)
        out["level"]["pawns"] = pawns
        print(json.dumps(out, indent=1))
        return 0
    finally:
        reader.k32.CloseHandle(reader.handle)


if __name__ == "__main__":
    sys.exit(main())
