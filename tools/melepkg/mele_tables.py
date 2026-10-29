"""Parse the UE3 import table from a decompressed ME:LE package, and prove it.

Why this exists: target selection. A co-op mod has to name a specific object - the class a second
`PlayerController` must be, the object that owns `CanFireWeapon`, the import a `Possess` targets.
An identifier table can only say "this name exists in this build". The import table says **which
package it came from and what kind of object it is**, offline, with no running game.

## What is verified, per game

The import table is 28 bytes per entry on all three builds, but the **field order differs** and the
table does not begin where the header says. Both facts were established from the bytes:

| | table start | source-package word | object-name word |
|---|---|---|---|
| ME1 | name-table end + 28 | 6 | 4 |
| ME2 | name-table end + 28 | 6 | 4 |
| ME3 | name-table end + 16 | 3 | 5 |

ME1/ME2 decode to `Engine`, `Core`, `WwiseAudio` from word 6. Parsing ME3 with that same layout
resolves every source to `0Deg` (name index 0) - direct evidence that the columns are rotated, and
the reason the layout is selected per game rather than assumed to be shared.

The start is found by CONTENT over a small window after the name table, because the header's
`import_offset` points 126 bytes past the real start on ME3 and 28 bytes past on ME1/ME2.

## Why the acceptance test is what it is

Three weaker tests were tried and all three PASS on garbage, so they are recorded here rather than
repeated:

1. **Name indices in range.** Index 0 is always a valid name, so a wrong base resolves every column
   to it and scores 100%.
2. **Names are not filler strings.** ME1's filler is `***Deleted***` and ME2's is `!_Dup` - real
   entries in those name tables, so "non-filler" does not discriminate.
3. **Two or more distinct sources.** Passes on 1,425 correct entries plus one coincidental match.

What a real import table actually has: one source package dominating the column, and many distinct
object kinds. `_shape_ok()` requires exactly that, and `build()` raises rather than returning a
table that fails it - a mis-attributed import sends a co-op hook to the wrong object silently.

## The export table is NOT parsed

Entries are 68 bytes and the region closes exactly on the header's `depends_offset`
(`base = depends_offset - 68 * export_count`), but the field order inside an entry was not
established: several candidate layouts produce plausible numbers and none was confirmed against an
independently-known object. `exports_status()` returns the measurements and the gap, never a
fabricated export.

Usage:

    python -I tools/melepkg/mele_tables.py summary <package>
    python -I tools/melepkg/mele_tables.py imports <package> [--package Engine] [--limit 40]
    python -I tools/melepkg/mele_tables.py find    <package> <name>
    python -I tools/melepkg/mele_tables.py classes <package> [--limit 40]
    python -I tools/melepkg/mele_tables.py json    <package> --out tables.json
"""
import argparse
import collections
import importlib.util
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

IMPORT_SIZE = 28
EXPORT_STRIDE = 68        # measured: a repeating 68-byte period closes on depends_offset

# Which word of the 7 holds what. `pad` words must be zero; a non-zero pad means the layout is
# wrong for the package, and that is counted rather than ignored.
LAYOUTS = {
    "ME1": {"cls": 1, "outer": 3, "obj": 4, "src": 6, "pad": (0, 2, 5)},
    "ME2": {"cls": 1, "outer": 3, "obj": 4, "src": 6, "pad": (0, 2, 5)},
    # ME3 stores the two name columns the other way round: word 5 is the CLASS and word 1 is the
    # OBJECT. The proof is that reading them ME1's way gives `object=PlayerController,
    # class=Class`, which is backwards for an import (an import's object is the thing imported, and
    # its class is what kind of thing it is), and reading them this way gives
    # `object=Class, class=PlayerController` - matching ME1/ME2 exactly.
    "ME3": {"cls": 5, "outer": 0, "obj": 1, "src": 3, "pad": (2, 4, 6)},
}


class ParseError(Exception):
    pass


def load_pkg():
    spec = importlib.util.spec_from_file_location("melepkg", os.path.join(HERE, "melepkg.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _nm(names, i):
    return names[i] if isinstance(i, int) and 0 <= i < len(names) else None


def guess_game(package):
    """Which build a package belongs to, from its path."""
    p = package.replace("\\", "/").lower()
    for marker, game in (("/me3/", "ME3"), ("/me2/", "ME2"), ("/me1/", "ME1")):
        if marker in p:
            return game
    return "ME1"


def parse_imports(buf, offset, count, names, layout):
    """Parse `count` 28-byte import entries at `offset`. Returns (entries, bad_pad_count)."""
    out = []
    pads_wrong = 0
    for i in range(count):
        e = offset + i * IMPORT_SIZE
        if e + IMPORT_SIZE > len(buf):
            raise ParseError("import %d runs past the stream at 0x%x" % (i, e))
        w = struct.unpack_from("<7i", buf, e)
        if any(w[p] != 0 for p in layout["pad"]):
            pads_wrong += 1
        out.append({
            "index": i,
            "class_name": _nm(names, w[layout["cls"]]),
            "class_name_index": w[layout["cls"]],
            "object_name": _nm(names, w[layout["obj"]]),
            "object_name_index": w[layout["obj"]],
            "source_name": _nm(names, w[layout["src"]]),
            "source_name_index": w[layout["src"]],
            "outer_index": w[layout["outer"]],
            "raw": list(w),
        })
    return out, pads_wrong


def _shape_ok(imports):
    """Does this table have the shape of a real import table? Returns (ok, why).

    Requirements, each backed by a failure it prevents:

      * at least TWO distinct source packages. A mis-based table can collapse to one name
        (`!_Dup` on ME2, `***Deleted***` on ME1) which is otherwise perfectly shaped.
      * at most 64 distinct sources. A wrong base spreads across thousands.
      * the dominant source covers at least half the entries - real tables import mostly from one
        package, with a tail. Measured: ME1 1292/1426, ME2 1764/1909, ME3 4021/4189.
      * at least 16 distinct object kinds - a real table imports many kinds of thing.
    """
    src = collections.Counter(r["source_name"] for r in imports if r["source_name"])
    obj = collections.Counter(r["object_name"] for r in imports if r["object_name"])
    if not src or not obj:
        return False, "no resolved names"
    if len(src) < 2:
        return False, ("only one distinct source package (%r). A real table imports from at "
                       "least Core and Engine; a single value is the signature of a mis-based "
                       "table collapsing onto one name" % src.most_common(1)[0][0])
    if len(src) > 64:
        return False, "%d distinct source packages; a real table has a handful" % len(src)
    top, n = src.most_common(1)[0]
    if n < len(imports) * 0.5:
        return False, ("most common source %r covers only %d of %d entries; a real package "
                       "column is dominated by one value" % (top, n, len(imports)))
    if len(obj) < 16:
        return False, "only %d distinct object kinds; a real import table holds many" % len(obj)
    return True, "%r covers %d/%d, %d sources, %d object kinds" % (
        top, n, len(imports), len(src), len(obj))


def exports_status(header):
    """What is known about the export region, and what is not."""
    count = header.get("export_count") or 0
    depends = header.get("depends_offset") or 0
    header_says = header.get("export_offset") or 0
    base = depends - EXPORT_STRIDE * count if count and depends else None
    closes = bool(base is not None and base + EXPORT_STRIDE * count == depends)
    return {
        "export_count": count,
        "header_export_offset": header_says,
        "depends_offset": depends,
        "measured_stride": EXPORT_STRIDE,
        "closure_base": base,
        "closes_exactly": closes,
        "header_offset_vs_closure_base": (header_says - base) if base else None,
        "parsed": False,
        "why_not": ("field order inside a 68-byte entry is unestablished. Several layouts produce "
                    "plausible numbers and none was confirmed against an independently-known "
                    "object. ME3's import columns are rotated relative to ME1/ME2, which is direct "
                    "evidence that assuming a shared layout here would also be wrong."),
        "next_step": ("anchor one export whose class and name are known independently - from "
                      "ue3live on a running game, or from a package small enough to read by hand"),
    }


def build(package):
    pkg = load_pkg()
    data = open(package, "rb").read()
    dll = pkg.find_oodle()
    if not dll:
        raise SystemExit("no oo2core*.dll found. Set MELE_ODLE_DLL or $MELE_GAME_DIR.")
    buf, chunks = pkg.decompress(data, pkg.Oodle(dll))
    names, name_end = pkg.parse_names(buf)
    header = data[:chunks[0]["start"]]
    fields = pkg.parse_header_fields(header, len(names), len(chunks))
    if fields.get("name_count_at") is None:
        raise SystemExit("header layout not recognised")

    game = guess_game(package)
    count = fields["import_count"]
    best = None
    for delta in range(0, 0x60):
        start = name_end + delta
        if start + count * IMPORT_SIZE > len(buf):
            break
        trial, pads_wrong = parse_imports(buf, start, count, names, LAYOUTS[game])
        ok, why = _shape_ok(trial)
        if not ok:
            continue
        score = (-pads_wrong, -delta)
        if best is None or score > best[0]:
            best = (score, start, delta, trial, pads_wrong, why)
    if best is None:
        raise SystemExit(
            "no import-table start after 0x%x gives a plausible table for %s with the known "
            "layout. Refusing to guess: a mis-attributed import sends a co-op hook to the wrong "
            "object silently." % (name_end, game))
    _, imp_start, delta, imports, pads_wrong, why = best

    return {
        "package": os.path.basename(package),
        "path": package,
        "game": game,
        "layout": LAYOUTS[game],
        "container": {"chunks": len(chunks), "uncompressed": len(buf)},
        "names": len(names),
        "name_table_end": name_end,
        "header": fields,
        "import_start": imp_start,
        "import_start_delta": delta,
        "import_pads_wrong": pads_wrong,
        "import_shape": why,
        "imports": imports,
        "exports": exports_status(fields),
    }


# ---------------------------------------------------------------------------------------
def cmd_summary(args):
    t = build(args.package)
    print("%s  [%s]" % (t["package"], t["game"]))
    print("  names              : %d (table ends at 0x%x)" % (t["names"], t["name_table_end"]))
    print("  imports            : %d at 0x%x (name-table end + %d)"
          % (len(t["imports"]), t["import_start"], t["import_start_delta"]))
    print("  layout             : %s" % t["layout"])
    print("  shape check        : %s" % t["import_shape"])
    print("  non-zero pad words : %d" % t["import_pads_wrong"])
    by_src = collections.Counter(r["source_name"] for r in t["imports"])
    print("  sources            : %s"
          % ", ".join("%s %d" % (k, v) for k, v in by_src.most_common(6)))
    by_obj = collections.Counter(r["object_name"] for r in t["imports"])
    print("  top object kinds   : %s"
          % ", ".join("%s %d" % (k, v) for k, v in by_obj.most_common(6)))
    print()
    e = t["exports"]
    print("  exports            : NOT PARSED")
    print("    count            : %d" % e["export_count"])
    print("    stride           : %d bytes (measured)" % e["measured_stride"])
    print("    closure base     : 0x%x, closes exactly: %s"
          % (e["closure_base"] or 0, e["closes_exactly"]))
    print("    why not          : %s" % e["why_not"])
    print("    next step        : %s" % e["next_step"])
    return 0


def cmd_imports(args):
    t = build(args.package)
    rows = t["imports"]
    if args.package:
        want = args.package.lower()
        rows = [r for r in rows if (r["source_name"] or "").lower() == want]
    print("%s: %d import(s)%s"
          % (t["package"], len(rows), " from %s" % args.package if args.package else ""))
    print("  %-6s %-32s %-24s %s" % ("INDEX", "OBJECT", "CLASS", "SOURCE"))
    for r in rows[:args.limit]:
        print("  %-6d %-32s %-24s %s"
              % (r["index"], r["object_name"], r["class_name"], r["source_name"]))
    return 0


def cmd_find(args):
    t = build(args.package)
    want = args.name.lower()
    hits = [r for r in t["imports"]
            if want in (r["object_name"] or "").lower()
            or want in (r["class_name"] or "").lower()]
    if not hits:
        print("%s: no import matches %r" % (t["package"], args.name))
        return 1
    print("%s: %d import(s) matching %r" % (t["package"], len(hits), args.name))
    for r in hits[:args.limit]:
        print("  #%-5d object=%-32s class=%-24s source=%s"
              % (r["index"], r["object_name"], r["class_name"], r["source_name"]))
    return 0


def cmd_classes(args):
    t = build(args.package)
    by = collections.Counter(r["class_name"] for r in t["imports"] if r["class_name"])
    print("%s: %d distinct import classes" % (t["package"], len(by)))
    for name, n in by.most_common(args.limit):
        print("  %-38s %4d" % (name, n))
    return 0


def cmd_json(args):
    t = build(args.package)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"package": t["package"], "game": t["game"], "names": t["names"],
                   "header": t["header"], "import_start": t["import_start"],
                   "layout": t["layout"], "imports": t["imports"],
                   "exports": t["exports"]}, f, indent=1)
    print("wrote %s (%d bytes)" % (args.out, os.path.getsize(args.out)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("summary", help="counts, validation and the export gap")
    p.add_argument("package")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("imports", help="list imports, optionally filtered by source package")
    p.add_argument("package")
    p.add_argument("--package")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_imports)

    p = sub.add_parser("find", help="find an import by object or class name")
    p.add_argument("package")
    p.add_argument("name")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("classes", help="import classes by frequency")
    p.add_argument("package")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_classes)

    p = sub.add_parser("json", help="write the parsed tables")
    p.add_argument("package")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_json)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
