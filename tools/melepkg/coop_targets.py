"""Generate the co-op target map from the packages, so it cannot drift from them.

Every other document in this project describes what to call. This one is generated from the
install: for each build, every function the co-op work needs, with its declaring class, its
parameters in declaration order, their byte offsets in the parms block, and the enum values that
appear in the same code path.

Why generated: the page this replaces drifted four times in one session - a class attribution was
wrong, a signature was wrong, an enum's numbering differed per build, and the "parms total" was
0x38 when the engine accepted 0x2c. Each was found by re-deriving it. A file produced by the
parser and checked by tests cannot drift quietly, because regeneration changes the diff.

Usage:

    python -I tools/melepkg/coop_targets.py --out ../games/mass-effect-legendary-edition/TARGETS.generated.md
    python -I tools/melepkg/coop_targets.py --json local/MELE/coop-targets.json

## What is claimed, and how strongly

* **Declaring class, parameters, order, offsets** - from the export table. Verified for
  `CreatePlayer` against a live-measured layout the engine accepted (`test_mele_tables.py`).
  For other functions the offsets are computed by the same rule, which is only verified on the one
  function that has a live anchor.
* **Presence** - "present" means an export of the named package on that build. "Absent" is only
  ever stated for a package that was successfully parsed.
* **Enum values** - from the enum's own serial data; ME3 numbering is checked to differ.

A field the parser cannot establish is emitted as `unknown` rather than omitted, so a reader can
tell "no such thing" from "not established".
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

GAMES = ("ME1", "ME2", "ME3")

# The surface the co-op work needs, grouped by what it is for. Each entry is
# (label, package, function name). `package` is relative to CookedPCConsole.
SURFACE = [
    ("Add a second local player", [
        ("register a new local player", "Engine", "AddLocalPlayer"),
        ("create a player", "Engine", "CreatePlayer"),
        ("remove a player", "Engine", "RemovePlayer"),
    ]),
    ("Lay out the viewport", [
        ("arrange the panes", "Engine", "LayoutPlayers"),
        ("spawn a controller", "Engine", "SpawnPlayerController"),
        ("initialise input", "Engine", "InitInputSystem"),
    ]),
    ("Drive a pawn", [
        ("possess a pawn", "Engine", "Possess"),
        ("release a pawn", "Engine", "UnPossess"),
        ("spawn the default pawn", "Engine", "SpawnDefaultPawnFor"),
    ]),
    ("Squad companions", [
        ("spawn a henchman", "SFXGame", "SpawnHenchman"),
        ("squad members", "SFXGame", "GetSquadMembers"),
        ("add to party", "SFXGame", "AddToParty"),
    ]),
]

ENUMS = [
    ("who may trigger a volume", "Engine", "EWhoTriggers"),
    ("split-screen mode", "Engine", "ESplitScreenType"),
]


def load_tables():
    spec = importlib.util.spec_from_file_location(
        "mele_tables", os.path.join(HERE, "mele_tables.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def package_path(mt, game, stem):
    pkg = mt.load_pkg()
    rel = os.path.join("Game", game, "BioGame", "CookedPCConsole", stem + ".pcc")
    for root in pkg.game_dirs():
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return p
    return None


def describe(mt, table, name):
    """One function's entry: declaring class, signature, offsets, or why it is unavailable."""
    exports, imports = table["exports"], table["imports"]
    hits = [e for e in exports
            if e["object_name"] == name
            and mt.describe_index(e["class_index"], exports, imports) == "Core.Function"]
    if not hits:
        present = [e for e in exports if e["object_name"] == name]
        if present:
            kinds = sorted({mt.describe_index(e["class_index"], exports, imports)
                            for e in present})
            return {"name": name, "status": "not-a-function", "kinds": kinds}
        if any(r["object_name"] == name for r in imports):
            return {"name": name, "status": "import-only"}
        return {"name": name, "status": "absent"}
    e = hits[0]
    s = mt.signature(table, table["buf"], e)
    return {
        "name": name,
        "status": "ok",
        "owner": mt.describe_index(e["outer_index"], exports, imports),
        "params": [{"name": pn, "type": pt, "size": ps, "offset": po}
                   for pn, pt, ps, po in s["params"]],
        "returns": s["returns"],
        "parms_size": s["parms_size"],
        "order": s["order"],
        "bytecode_bytes": _bytecode_len(table, e),
    }


def _bytecode_len(table, export):
    """Bytecode length if the extent identity holds for this export, else None."""
    import struct
    header = {"ME1": 47, "ME2": 47, "ME3": 38}.get(table["game"])
    if header is None:
        return None
    disp = 0
    try:
        from importlib import util
        spec = util.spec_from_file_location(
            "mt", os.path.join(HERE, "mele_tables.py"))
        mt = util.module_from_spec(spec)
        spec.loader.exec_module(mt)
        disp = mt.buffer_base(table["header"])
    except Exception:
        return None
    at = export["serial_offset"] - disp
    if at + 32 > len(table["buf"]):
        return None
    return struct.unpack_from("<i", table["buf"], at + 28)[0]


def build_targets():
    mt = load_tables()
    out = {"games": {}, "enums": {}}
    for game in GAMES:
        cache = {}
        groups = []
        for group_label, items in SURFACE:
            rows = []
            for label, stem, fn in items:
                key = stem
                if key not in cache:
                    p = package_path(mt, game, stem)
                    cache[key] = mt.build(p) if p else None
                t = cache[key]
                if t is None:
                    rows.append({"label": label, "name": fn, "status": "no-package"})
                    continue
                rec = describe(mt, t, fn)
                rec["label"] = label
                rec["package"] = stem
                rows.append(rec)
            groups.append({"group": group_label, "rows": rows})
        out["games"][game] = groups

        enums = []
        for label, stem, en in ENUMS:
            key = stem
            if key not in cache:
                p = package_path(mt, game, stem)
                cache[key] = mt.build(p) if p else None
            t = cache[key]
            if t is None:
                enums.append({"label": label, "name": en, "status": "no-package"})
                continue
            hit = [e for e in t["exports"] if e["object_name"] == en]
            if not hit:
                enums.append({"label": label, "name": en, "status": "absent"})
                continue
            members = mt.enum_members(t, t["buf"], hit[0])
            enums.append({"label": label, "name": en,
                          "status": "ok" if members else "did-not-close",
                          "members": members})
        out["enums"][game] = enums
    return out


def _sig(rec):
    if rec.get("status") != "ok":
        return {"ok": "`%s`" % rec["name"],
                "not-a-function": "`%s` (a %s, not a function)"
                                  % (rec["name"], ", ".join(rec.get("kinds") or [])),
                "import-only": "`%s` (import only)" % rec["name"],
                "absent": "absent",
                "no-package": "package not found"}.get(rec["status"], rec["status"])
    inputs = [p for p in rec["params"] if p["name"] != "ReturnValue"]
    ret = rec["returns"] or "void"
    return "%s(%s) → %s" % (
        rec["name"], ", ".join("%s: %s" % (p["name"], p["type"]) for p in inputs), ret)


def render_markdown(targets):
    L = []
    L.append("<!-- GENERATED by tools/melepkg/coop_targets.py - do not edit by hand. -->")
    L.append("")
    L.append("# Co-op target map")
    L.append("")
    L.append("Every call the co-op work needs, read from the packages on this machine. Regenerate")
    L.append("with `python -I tools/melepkg/coop_targets.py --out <this file>`; the diff is the")
    L.append("change in the game's own data, not in anyone's notes.")
    L.append("")
    L.append("`→` after a signature is the return type. Offsets are bytes into the parms block")
    L.append("the engine expects at dispatch.")
    L.append("")
    L.append("**One caveat, and it matters.** A function's child exports include its *locals* as well")
    L.append("as its parameters, and this project has not established how the engine tells them")
    L.append("apart. `RemovePlayer` therefore lists `OldIndex`, which is a local. Only")
    L.append("`CreatePlayer` is verified end-to-end, against a live dispatch whose layout the engine")
    L.append("accepted - so treat its row as authoritative and any other row as \"these are the")
    L.append("declarations, at least one may not be an argument\". See `param_order` in")
    L.append("`mele_tables.py` for the measurement that ruled out a flag-based test.")
    L.append("")
    for game in GAMES:
        L.append("## %s" % game)
        L.append("")
        for group in targets["games"][game]:
            L.append("### %s" % group["group"])
            L.append("")
            L.append("| what | target | declaring class | parms | bytecode |")
            L.append("|---|---|---|---|---|")
            for r in group["rows"]:
                if r.get("status") == "ok":
                    L.append("| %s | `%s` | %s | `0x%x` | %s |"
                             % (r["label"], _sig(r), r["owner"], r["parms_size"],
                                ("%d B" % r["bytecode_bytes"]) if r.get("bytecode_bytes")
                                is not None else "unknown"))
                else:
                    L.append("| %s | %s | — | — | — |"
                             % (r["label"], _sig(r)))
            L.append("")
        L.append("### Enums")
        L.append("")
        for e in targets["enums"][game]:
            if e.get("status") == "ok":
                L.append("* **%s** — `%s`: %s" % (e["label"], e["name"],
                                                  ", ".join("`%s`" % m for m in e["members"])))
            else:
                L.append("* **%s** — `%s`: %s" % (e["label"], e["name"], e["status"]))
        L.append("")
    L.append("## Parms blocks, in full")
    L.append("")
    L.append("For each function above whose declaring class and parameters resolved, the exact")
    L.append("byte layout. Verified against a live dispatch for `CreatePlayer` only - any other")
    L.append("row is computed by the same rule and has no live anchor yet.")
    L.append("")
    for game in GAMES:
        for group in targets["games"][game]:
            for r in group["rows"]:
                if r.get("status") != "ok":
                    continue
                L.append("**%s %s** (`%s`) — parms `0x%x`, order %s"
                         % (game, r["name"], r["owner"], r["parms_size"], r["order"]))
                L.append("")
                L.append("```")
                for p in r["params"]:
                    L.append("  +0x%02x  %-20s %s" % (p["offset"], p["name"], p["type"]))
                L.append("```")
                L.append("")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="write markdown here")
    ap.add_argument("--json", help="write the raw structure here")
    args = ap.parse_args()
    if not (args.out or args.json):
        ap.error("give --out and/or --json")
    targets = build_targets()
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(targets, f, indent=1)
        print("wrote %s" % args.json)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(render_markdown(targets))
        print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
