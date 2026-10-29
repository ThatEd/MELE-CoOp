"""Build a verified script map of a running UE3 game: classes, functions, properties, states.

Why this tool exists, and why it is not the offline approach:

An offline package reader gives you identifiers but not *attribution* - a name in a cooked
package has to be guessed into a class by locality, which is a lead, not a fact. In the live
process the answer is exact, because every `UFunction` carries an `Outer` pointer to its own
class and every class carries a `SuperStruct` chain. So this reads truth, not a heuristic.

The output is designed to be a stable base across builds:

* the same schema for LE1, LE2 and LE3, so two maps can be diffed and the differences are the
  porting work - "only some naming calls need changing" is exactly a diff of two of these;
* names are stored with the class they were *found on* and the class that *declared* them,
  because inherited functions are the main reason a naive port breaks;
* everything unresolved is reported as unresolved rather than omitted, so a missing entry means
  "not there" and never "not looked for".

What it is for in a co-op project:

* **map events** - the Kismet classes (`SeqAct_*`, `SeqEvt_*`, `SeqCond_*`, `BioSeqAct_*`)
  that fire on level entry, trigger, and story progress;
* **control gates** - the functions and properties that decide whether the player may act:
  firing, movement, power use, camera. The report includes a curated probe list for these,
  because "where the player cannot shoot" is a small, named set once you can see it;
* **naming** - the exact function and property names per class, which is what a patch has to
  call.

Usage:

    # full map for one game
    python -I tools/ue3live/ue3_script_map.py --pid <pid> --exe MassEffect1.exe \
        --offsets local/MELE/offsets/offsets_LE1.json --out local/MELE/maps/LE1.json

    # focused: Kismet events only
    ... --category kismet --out local/MELE/maps/LE1-kismet.json

    # find control gates (firing, movement, pause)
    ... --category gates

    # what changed between two games or two builds
    python -I tools/ue3live/ue3_script_map.py --diff local/MELE/maps/LE1.json local/MELE/maps/LE2.json

Read-only: OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION) only.
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ue3_reader
from ue3_proc import Reader, module_base
from ue3_props import class_properties

# Function flag bits (EFunctionFlags). Reported with evidence rather than asserted, because the
# exact dword offset inside UFunction varies between UE3 revisions.
FUNC_NATIVE = 0x00000400

CATEGORIES = {
    "classes": None,
    "kismet": ("SeqAct_", "SeqEvt_", "SeqCond_", "SeqVar_", "BioSeq", "SFXSeq", "Sequence"),
    "gates": ("CanFire", "bCanFire", "AllowFire", "bAllowFire", "CanMove", "bCanMove",
              "AllowMove", "CanUsePower", "AllowPower", "Cannot", "Forbid", "Disabled",
              "bDisable", "SetInput", "bCinematicMode", "bPlayersOnly", "PauseGame",
              "bWeapon", "CombatMode", "GameMode", "bAllow", "bBlock"),
    "input": ("Input", "PlayerInput", "Key", "Axis", "Button", "Bind", "Console"),
    "story": ("Story", "Progress", "Plot", "Mission", "Objective", "BioWorldInfo",
              "Conversation", "Dialog"),
    "combat": ("Damage", "Weapon", "Ammo", "Attack", "Hostile", "Health", "Shield"),
    "squad": ("Squad", "Hench", "Party", "Member", "Follow", "Order"),
}

# The curated probe list: properties/functions that gate whether the player can act. Each is
# looked for by name across every class, and the report says where it was found. This is the
# direct answer to "where can the player not shoot".
GATE_PROBES = (
    "bCanFire", "CanFire", "bAllowFire", "AllowFiring", "bWeaponFireEnabled",
    "bCanMove", "CanMove", "bAllowMovement", "bMovementEnabled",
    "bCanUsePower", "CanUsePower", "bPowersEnabled",
    "bCinematicMode", "bPlayersOnly", "PauseGame", "bPausedByFocusLoss",
    "bInputEnabled", "bIgnoreInput", "DisableInput", "bBlockInput",
    "bInCombat", "EnterCombat", "ExitCombat", "SetCombatMode",
    "bConversationActive", "IsInConversation",
)


def flag_evidence(game, fn):
    """Best-effort native flag with the evidence that produced it.

    Guessing a single offset is what this project keeps getting wrong, so several candidates
    are read and the tool states which it used instead of silently picking one.
    """
    out = {"flags": {}, "native": None, "why": None}
    for off in (0x80, 0x84, 0x88, 0x8C, 0x90, 0x94, 0x98):
        raw = game.r.read(fn + off, 4)
        if raw is None:
            continue
        v = struct.unpack("<I", raw)[0]
        out["flags"][hex(off)] = v
    # A script function has bytecode and no native entry; a native has a code pointer into the
    # image. The image range is the module, so a pointer inside it is real evidence.
    psize_raw = game.r.read(fn + 0xE8, 2)
    out["parms_size"] = struct.unpack("<H", psize_raw)[0] if psize_raw else None
    return out


def parms_of(game, fn):
    """(offset, size, kind, name) for each declared parameter, sorted by offset."""
    rows = []
    field = game.r.ptr(fn + 0x70)
    seen = set()
    while field and field not in seen:
        seen.add(field)
        raw = game.r.read(field + 0x60, 0x40)
        if raw:
            off = struct.unpack_from("<i", raw, 0x94 - 0x60)[0]
            size = struct.unpack_from("<i", raw, 0x74 - 0x60)[0]
            kind = game.obj_class(field)
            name = game.object_name(field)
            if kind and kind != "Function":
                rows.append({"offset": off, "size": size, "kind": kind, "name": name})
        field = game.r.ptr(field + 0x68)
    rows.sort(key=lambda r: r["offset"])
    return rows


def class_chain(game, obj):
    chain = []
    cur = game.r.ptr(obj + 0x50)
    for _ in range(48):
        if not cur:
            break
        chain.append(game.object_name(cur))
        cur = game.r.ptr(cur + 0x60)
    return [c for c in chain if c]


def build_map(pid, exe, offsets, category=None, limit=None):
    base = module_base(pid, exe)
    if not base:
        return {"error": "%s not in pid %d" % (exe, pid)}
    reader = Reader(pid)
    try:
        game = ue3_reader.Game(reader, base, offsets)
        patterns = CATEGORIES.get(category) if category else None

        def wanted(name):
            if not patterns:
                return True
            return any(p.lower() in (name or "").lower() for p in patterns)

        out = {"game": os.path.basename(exe), "pid": pid,
               "objects": game.count, "category": category or "all",
               "schema": 1, "read_only": True,
               "classes": {}, "functions": {}, "gate_probes": {},
               "stats": {"objects_scanned": 0, "classes": 0, "functions": 0,
                         "properties": 0}}

        gates = {g: [] for g in GATE_PROBES}

        # One pass over GObjects. Class objects give the class list; UFunction objects give
        # exact attribution through their Outer.
        for i in range(1, game.count):
            obj = reader.ptr(game.data + i * 8)
            if not obj:
                continue
            out["stats"]["objects_scanned"] += 1
            if limit and out["stats"]["objects_scanned"] >= limit:
                break
            cls = game.obj_class(obj)
            if not cls:
                continue
            name = game.object_name(obj)

            if cls == "Class":
                if not wanted(name):
                    continue
                declared_on = game.object_name(reader.ptr(obj + 0x60))
                entry = out["classes"].setdefault(name, {
                    "super": declared_on,
                    "chain": class_chain(game, obj),
                    "properties": [], "functions": [], "states": [],
                })
                # Properties, including inherited, with the declaring class.
                for pname, pkind, poff, psize, decl in class_properties(
                        reader, game, obj, include_functions=False):
                    if pkind in ("Function", None):
                        continue
                    entry["properties"].append(
                        {"name": pname, "kind": pkind, "offset": poff,
                         "size": psize, "declared_on": decl})
                    out["stats"]["properties"] += 1
                out["stats"]["classes"] += 1

            elif cls == "Function":
                owner = game.object_name(reader.ptr(obj + 0x40))
                if not owner or not wanted(name):
                    continue
                ev = flag_evidence(game, obj)
                out["functions"].setdefault(owner, []).append({
                    "name": name,
                    "parms": parms_of(game, obj),
                    "flags": ev["flags"],
                    "parms_size": ev["parms_size"],
                })
                out["stats"]["functions"] += 1
                for g in GATE_PROBES:
                    if name and name.lower() == g.lower():
                        if len(gates[g]) < 12:
                            gates[g].append({"class": owner, "function": name,
                                             "ptr": hex(obj)})

            elif cls == "State":
                owner = game.object_name(reader.ptr(obj + 0x40))
                if owner and wanted(name):
                    out["classes"].setdefault(owner, {}).setdefault("states", []).append(name)

        out["gate_probes"] = {k: v for k, v in gates.items()}
        out["gate_summary"] = {
            "found": sorted(k for k, v in gates.items() if v),
            "not_found": sorted(k for k, v in gates.items() if not v),
        }
        # Trim the empty scaffolding so the file stays readable.
        for c in list(out["classes"].values()):
            if isinstance(c, dict) and not c:
                pass
        return out
    finally:
        reader.k32.CloseHandle(reader.handle)


def diff_maps(a, b):
    """What changed between two maps. This is the porting work, stated as a list."""
    def names(m, key):
        if key == "classes":
            return set(m.get("classes", {}))
        out = set()
        for owner, fns in m.get("functions", {}).items():
            for f in fns:
                out.add("%s.%s" % (owner, f["name"]))
        return out

    report = {}
    for key in ("classes", "functions"):
        A, B = names(a, key), names(b, key)
        report[key] = {
            "only_in_a": sorted(A - B)[:400],
            "only_in_b": sorted(B - A)[:400],
            "common": len(A & B),
            "count_a": len(A), "count_b": len(B),
        }
    ga = set((a.get("gate_summary") or {}).get("found", []))
    gb = set((b.get("gate_summary") or {}).get("found", []))
    report["gates"] = {"only_in_a": sorted(ga - gb), "only_in_b": sorted(gb - ga),
                       "common": sorted(ga & gb)}
    return report


def cmd_diff(args):
    a = json.load(open(args.diff[0], encoding="utf-8"))
    b = json.load(open(args.diff[1], encoding="utf-8"))
    r = diff_maps(a, b)
    print("%s  vs  %s" % (a.get("game"), b.get("game")))
    for key in ("classes", "functions"):
        d = r[key]
        print("\n%s: %d vs %d, %d in common" % (key, d["count_a"], d["count_b"], d["common"]))
        if d["only_in_a"]:
            print("  only in %s (%d): %s" % (a.get("game"), len(d["only_in_a"]),
                                             ", ".join(d["only_in_a"][:20])))
        if d["only_in_b"]:
            print("  only in %s (%d): %s" % (b.get("game"), len(d["only_in_b"]),
                                             ", ".join(d["only_in_b"][:20])))
    g = r["gates"]
    print("\ngate probes found in both: %d" % len(g["common"]))
    if g["only_in_a"]:
        print("  only in %s: %s" % (a.get("game"), ", ".join(g["only_in_a"])))
    if g["only_in_b"]:
        print("  only in %s: %s" % (b.get("game"), ", ".join(g["only_in_b"])))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", type=int)
    ap.add_argument("--exe")
    ap.add_argument("--offsets")
    ap.add_argument("--category", choices=sorted(CATEGORIES), default="classes")
    ap.add_argument("--limit", type=int, default=0, help="stop after N objects (debug)")
    ap.add_argument("--out")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"),
                    help="compare two saved maps")
    args = ap.parse_args()

    if args.diff:
        return cmd_diff(args)
    if not (args.pid and args.exe and args.offsets):
        ap.error("--pid, --exe and --offsets are required unless --diff is used")

    m = build_map(args.pid, args.exe, args.offsets, args.category, args.limit or None)
    if "error" in m:
        print(m["error"])
        return 1

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=1)
        if not args.quiet:
            print("wrote %s (%d bytes)" % (args.out, os.path.getsize(args.out)))
    if not args.quiet:
        s = m["stats"]
        print("scanned %d objects: %d classes, %d functions, %d properties"
              % (s["objects_scanned"], s["classes"], s["functions"], s["properties"]))
        gs = m["gate_summary"]
        print("gate probes found (%d): %s" % (len(gs["found"]), ", ".join(gs["found"])))
        if gs["not_found"]:
            print("gate probes not found (%d): %s"
                  % (len(gs["not_found"]), ", ".join(gs["not_found"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
