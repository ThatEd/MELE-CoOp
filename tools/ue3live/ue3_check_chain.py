import os
import sys
# Running with -I omits the script directory from sys.path, and the README documents
# these tools as runnable with -I. Make the sibling modules importable regardless.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argparse
import sys, json, struct
import ue3_reader
from ue3_proc import Reader, module_base

PHYS = {0:"None",1:"Walking",2:"Falling",3:"Swimming",4:"Flying",5:"Rotating",
        6:"Projectile",7:"Interpolating",8:"Spider",9:"Ladder",10:"RigidBody",
        11:"SoftBody",12:"Custom"}

def check(pid, exe, offpath):
    base = module_base(pid, exe)
    r = Reader(pid)
    cfg = json.load(open(offpath, encoding="utf-8"))
    H = lambda k: int(cfg[k], 16)
    try:
        g = ue3_reader.Game(r, base, offpath)
        engines = [o for o in (r.ptr(g.data+i*8) for i in range(g.count))
                   if o and (g.obj_class(o) or "").endswith("Engine")
                   and not (g.object_name(o) or "").startswith("Default__")]
        owner = None
        for e in engines:
            raw = r.read(e + H("engine_game_players"), 16)
            if not raw: continue
            d, c, m = struct.unpack("<Qii", raw)
            if d and 1 <= c <= 8 and m >= c:
                first = r.ptr(d)
                if first and g.obj_class(first) == "LocalPlayer":
                    owner = (e, d, c, m); break
        if not owner:
            return False, "no engine exposes a GamePlayers array of LocalPlayers"
        eng, data, count, mx = owner
        results = []
        results.append(("GamePlayers owner", g.obj_class(eng), True))
        results.append(("GamePlayers count/max", "%d/%d" % (count, mx), True))
        lp = r.ptr(data)
        pc = r.ptr(lp + H("local_player_actor"))
        results.append(("LocalPlayer.Actor", g.obj_class(pc) or "undecodable",
                        bool(pc)))
        pawn = r.ptr(pc + H("controller_pawn")) if pc else 0
        results.append(("Controller.Pawn live", hex(pawn) if pawn else "-", bool(pawn)))
        if not pawn:
            return True, "player present but no pawn (menu/cutscene)"
        results.append(("Pawn.Controller reciprocal",
                        str(r.ptr(pawn + H("pawn_controller")) == pc),
                        r.ptr(pawn + H("pawn_controller")) == pc))
        results.append(("Controller.Player reciprocal",
                        str(r.ptr(pc + H("controller_player")) == lp),
                        r.ptr(pc + H("controller_player")) == lp))
        phys = r.read(pawn + H("pawn_physics"), 1)[0]
        loc = struct.unpack("<fff", r.read(pawn + H("pawn_location"), 12))
        scripted = phys == 7
        results.append(("Pawn.Physics", "%d (%s)" % (phys, PHYS.get(phys, "?")), True))
        # A pawn driven by a matinee legitimately has no authoritative Location, so
        # only assert it when the pawn is actually moving under its own physics.
        results.append(("Pawn.Location", "%.1f %.1f %.1f" % loc,
                        scripted or not (loc == (0.0, 0.0, 0.0))))
        print("== %s (pid %d)" % (exe, pid))
        for name, val, ok in results:
            print("   [%s] %-28s %s" % ("PASS" if ok else "FAIL", name, val))
        if scripted:
            print("\n   NOTE: Physics 7 = PHYS_Interpolating -> the pawn is under script\n"
                  "   control (cutscene/matinee), so movement input is ignored by design.\n"
                  "   This is the same condition that blocked player 2 in ME3 (Anderson at\n"
                  "   physics 7). It is NOT an offsets failure.")
        hard = [n for n, _, ok in results if not ok]
        return not hard, ("%d failing check(s): %s" % (len(hard), hard)) if hard else "structure verified"
    finally:
        r.k32.CloseHandle(r.handle)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--offsets", required=True)
    args = ap.parse_args()
    ok, msg = check(args.pid, args.exe, args.offsets)
    print("\nRESULT:", "OK" if ok else "PROBLEM", "-", msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
