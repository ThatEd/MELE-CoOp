"""
Report a game's engine player chain, using only measured offsets.

Read-only. Answers the three questions a P2 spawn needs, for whichever game is running:
where the local-player array is, whether player 1's controller resolves, and whether a
pawn is attached. It reports what it finds instead of assuming the offset from another
game, because LE1 is an older UE3 revision than LE2/LE3.

Every candidate is cross-checked by name/pool decoding, so a wrong offset shows up as
"not a LocalPlayer" rather than as a confident wrong answer.

The sys.path insert below makes the sibling modules importable when this file is run
with `python -I`, which omits the script directory from sys.path. Without it these tools
only work when invoked without -I, which is not how the README documents them.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argparse
import ctypes
import json
import struct
import sys

from ctypes import wintypes

import os
import sys
# Running with -I omits the script directory from sys.path, and the README documents
# these tools as runnable with -I. Make the sibling modules importable regardless.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ue3_reader
from ue3_proc import Reader, module_base


def plausible_array(reader, addr, max_count=8):
    raw = reader.read(addr, 16)
    if not raw:
        return None
    data, count, mx = struct.unpack("<Qii", raw)
    if not data or data % 8 or not (0 <= count <= max_count) or mx < count or mx > max_count:
        return None
    return {"data": data, "count": count, "max": mx}


def live_engines(game):
    """Every live UEngine-derived object.

    Why this matters: the engine global does NOT necessarily dereference to the object
    that owns GamePlayers. LE1's engine global is SFXEngine, but GamePlayers,
    GameViewport and LocalPlayerClass are declared on GameEngine - a different live
    object. Scanning only SFXEngine finds no local players and looks exactly like
    "the player is not in game yet", which was wrong twice over.
    """
    out = []
    for i in range(1, game.count):
        o = game.r.ptr(game.data + i * 8)
        if not o:
            continue
        name = game.object_name(o)
        if not name or name.startswith("Default__") or not name.endswith("Engine"):
            continue
        cls = game.obj_class(o)
        if cls and cls.endswith("Engine"):
            out.append({"ptr": o, "name": name, "class": cls})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--offsets", required=True)
    ap.add_argument("--search-low", type=lambda s: int(s, 0), default=0x100)
    ap.add_argument("--search-high", type=lambda s: int(s, 0), default=0xA00)
    ap.add_argument("--hint-offset", type=lambda s: int(s, 0), default=0x498,
                    help="known GamePlayers offset to probe on every engine")
    args = ap.parse_args()

    base = module_base(args.pid, args.exe)
    reader = Reader(args.pid)
    try:
        game = ue3_reader.Game(reader, base, args.offsets)
        out = {"read_only": True, "objects": game.count, "exe": args.exe}
        if not game.engine_global:
            out["error"] = "offsets file has no engine_global_rva"
            print(json.dumps(out, indent=1))
            return 1
        engine = reader.ptr(game.engine_global)
        out["engine"] = {"slot": hex(game.engine_global), "ptr": hex(engine),
                         "name": game.object_name(engine),
                         "class": game.obj_class(engine)}
        if not engine:
            out["error"] = "engine global dereference failed"
            print(json.dumps(out, indent=1))
            return 1

        # Scan EVERY live engine for a small TArray whose members are LocalPlayers, and
        # probe a few known candidate offsets directly instead of only walking by 8 from
        # the low bound. The engine that owns GamePlayers is not always the one the
        # engine global points at (LE1: SFXEngine vs GameEngine).
        candidates = []
        engines = live_engines(game)
        out["live_engines_considered"] = [
            {"ptr": hex(e["ptr"]), "name": e["name"], "class": e["class"]}
            for e in engines]
        probe_offsets = sorted({args.search_low, 0x498, args.hint_offset}
                               | set(range(args.search_low, args.search_high, 8)))
        for e in engines:
            for off in probe_offsets:
                arr = plausible_array(reader, e["ptr"] + off)
                if not arr or arr["count"] < 1:
                    continue
                first = reader.ptr(arr["data"])
                if not first:
                    continue
                name = game.object_name(first)
                cls = game.obj_class(first)
                if name == "LocalPlayer" or (cls and "LocalPlayer" in cls):
                    candidates.append({"engine": e["name"], "engine_class": e["class"],
                                       "engine_ptr": hex(e["ptr"]),
                                       "offset": off, "data": hex(arr["data"]),
                                       "count": arr["count"], "max": arr["max"],
                                       "first_name": name, "first_class": cls})
        out["game_players_candidates"] = candidates

        if candidates:
            best = candidates[0]
            out["game_players_offset"] = hex(best["offset"])
            out["game_players_owner"] = best["engine"]
            arr = plausible_array(reader, int(best["engine_ptr"], 16) + best["offset"])
            slots = []
            for i in range(arr["count"]):
                lp = reader.ptr(arr["data"] + i * 8)
                entry = {"slot": i, "ptr": hex(lp) if lp else None,
                         "name": game.object_name(lp),
                         "class": game.obj_class(lp),
                         "object_props": []}
                # Object-valued properties of the local player, so the controller link
                # is read rather than assumed.
                if lp:
                    from ue3_props import class_properties
                    klass = reader.ptr(lp + 0x50)
                    for n, k, o, sz, d in class_properties(reader, game, klass,
                                                           include_functions=False):
                        if sz != 8 or not (0x28 < o < 0x1000):
                            continue
                        t = reader.ptr(lp + o)
                        if not t:
                            continue
                        tn, tc = game.object_name(t), game.obj_class(t)
                        if tn or tc:
                            entry["object_props"].append(
                                {"prop": n, "offset": o, "target": hex(t),
                                 "name": tn, "class": tc, "declared_on": d})
                slots.append(entry)
            out["local_players"] = slots

        print(json.dumps(out, indent=1))
        return 0
    finally:
        reader.k32.CloseHandle(reader.handle)


if __name__ == "__main__":
    sys.exit(main())
