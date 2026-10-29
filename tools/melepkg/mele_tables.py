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
#
# Established by measuring, for each column, what fraction of entries resolve to an engine TYPE
# (`Function`, `Class`, `BoolProperty`, ...). A type column is 88-94% type names on a real table
# and 0% on a wrong one; nothing else discriminates as sharply:
#
#     word1 type share:   ME1 88%   ME2 91%   ME3  0%
#     word5 type share:   ME1  0%   ME2  0%   ME3 94%
#
# The object column is the one that reads as an alphabetically ordered object list:
# `Actor`, `ActorFactory`, `AIController`, `AnimNode` (ME1/ME2) and `Default__Actor`,
# `Default__ActorComponent`, ... (ME3). The source column is the one dominated by `Core`/`Engine`.
#
# So ME3 is not "ME1 with two columns swapped" - three of the four columns move. Recording the
# intermediate wrong guesses is what stopped them being re-derived: `(cls 1, obj 4)` read ME1's
# columns on ME3 and produced `object=PlayerController class=Class`, which looks fine and is
# backwards.
LAYOUTS = {
    "ME1": {"type": 1, "obj": 4, "src": 6, "outer": 3, "pad": (0, 2, 5)},
    "ME2": {"type": 1, "obj": 4, "src": 6, "outer": 3, "pad": (0, 2, 5)},
    "ME3": {"type": 5, "obj": 1, "src": 3, "outer": 0, "pad": (2, 4, 6)},
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
    """Parse `count` 28-byte import entries at `offset`. Returns (entries, bad_pad_count).

    Column names are `type` (what kind of object: `Function`, `Class`, `BoolProperty`, ...),
    `obj` (the object's own name), `src` (the source package) and `outer`. Earlier drafts called
    the type column "class", which conflated "what kind of thing is this" with "which object owns
    it" - two different questions that the import table answers in different columns.
    """
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
            "type_name": _nm(names, w[layout["type"]]),
            "type_name_index": w[layout["type"]],
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
        "name_field_offset": EXPORT_NAME_FIELD,
        "parsed": True,
        "partial": True,
        "why_partial": EXPORT_LIMITS,
        "next_step": ("find the variable-length rule: entries whose 4-byte marker at +22 is "
                      "`04 00 0f 04` rather than `04 00 07 00` carry 4 extra bytes, which shifts "
                      "every later entry. The reference count is `export_count`, so a complete "
                      "parse must consume all of it exactly."),
    }


# ---------------------------------------------------------------------------------------
# Export table: the NAME field is proven, the rest is not
# ---------------------------------------------------------------------------------------
# The object-name field sits at a fixed offset from each entry start, and the offset differs per
# build. Established by requiring, for the whole table, that the 4-byte int there resolves to a
# NON-ZERO in-range name (index 0 is a filler like `0Deg`/`A`, so "in range" alone passes on
# garbage - the trap already recorded twice in this project):
#
#     ME1   +15   87% of sampled entries, best of 65 candidate offsets
#     ME2   +35  100% of sampled entries
#     ME3   +26   95-99% (GFxUI 606/613, SFXGame 52204/54768)
#
# The resulting names are real identifiers: on GFxUI they read `AttachMovie`, `bDisabled`,
# `GFxAction_SetVariable`, `bWriteable`.
# The offset at which a NON-ZERO name index can be read is NOT the export name field. Recorded
# here because the measurement is real and someone will repeat it: requiring "a non-zero, in-range
# name index" for most entries finds these offsets (ME1 +15 at 87%, ME2 +35 at 99.5%, ME3 +26 at
# 95.3%). It looks like a win and it is not - the names it produces are dominated by ONE value
# (`__LoadingMovieDelegate__Delegate` appearing 18,914 times in ME3's 54,768 exports, `0Deg`,
# `A`). A real export table has thousands of distinct object names and every one of them contains
# the class names, yet this yields only 7 names containing `Controller`.
#
# That is the fourth false pass in this project's history of this exact shape: a test that passes
# on a constant. The lesson is not "try harder", it is that "most entries resolve" is not evidence
# of correctness unless the resolved values are DISTINCT and PLAUSIBLE, and the only way to know
# that is to look at them - which is what produced this note.
EXPORT_NAME_FIELD_UNPROVEN = {"ME1": 15, "ME2": 35, "ME3": 26}

EXPORT_LIMITS = (
    "The export table is NOT parsed. Entries are nominally 68 bytes - "
    "(depends_offset - export_offset) / export_count is exactly 68.000 on GFxUI and 68.005 on "
    "SFXGame, and the boundary signature `04 00 07 00` appears 583 times for 613 GFxUI exports, "
    "so some entries differ in length. A fixed-stride read of the best-looking name offset "
    "yields mostly ONE repeated value (`__LoadingMovieDelegate__Delegate` 18,914 times in ME3), "
    "which is filler, not object names. Class attribution therefore remains unavailable offline."
)

EXPORT_NEXT_STEP = (
    "Parse entries sequentially with the variable-length rule rather than at a fixed stride, and "
    "validate by requiring (a) exactly export_count entries, (b) the cursor to land on "
    "depends_offset, and (c) the name column to contain the class names known to exist - every "
    "one of ME3's 54,768 exports has a class, and that class list must contain `SFXGameInfoSP`, "
    "`SFXPlayerController` and the `Default__*` archetypes. A parse that cannot produce those is "
    "wrong however plausible its percentages look."
)


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
        "why_not": EXPORT_LIMITS,
        "next_step": EXPORT_NEXT_STEP,
    }
    entries, unresolved = [], []
    n = len(names)
    for k in range(count):
        e = start + k * EXPORT_STRIDE
        if e + EXPORT_STRIDE > len(buf):
            unresolved.append(k)
            continue
        v = struct.unpack_from("<i", buf, e + field_off)[0]
        if 0 < v < n:
            entries.append({"index": k, "name": names[v], "name_index": v,
                            "offset": e})
        else:
            entries.append({"index": k, "name": None, "name_index": v, "offset": e})
            unresolved.append(k)
    cov = (count - len(unresolved)) / count if count else 0.0
    return {"entries": entries, "coverage": cov, "unresolved": unresolved,
            "limits": EXPORT_LIMITS}


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
    print()
    print("  exports            : NOT PARSED")
    print("    count            : %d" % e["export_count"])
    print("    stride           : %d bytes nominal (%.3f measured over the region)"
          % (e["measured_stride"],
             (e["depends_offset"] - e["header_export_offset"]) / e["export_count"]
             if e["export_count"] else 0))
    print("    why not          : %s" % e["why_not"])
    print("    next step        : %s" % e["next_step"])
    return 0


def cmd_imports(args):
    t = build(args.package)
    rows = t["imports"]
    want = getattr(args, "from_package", None)
    if want:
        rows = [r for r in rows if (r["source_name"] or "").lower() == want.lower()]
    print("%s: %d import(s)%s"
          % (t["package"], len(rows), " from %s" % want if want else ""))
    by_src = collections.Counter(r["source_name"] for r in t["imports"])
    print("  sources: %s" % ", ".join("%s %d" % (k, v) for k, v in by_src.most_common(8)))
    print("  %-6s %-32s %-24s %s" % ("INDEX", "OBJECT", "TYPE", "SOURCE"))
    for r in rows[:args.limit]:
        print("  %-6d %-32s %-24s %s"
              % (r["index"], r["object_name"], r["type_name"], r["source_name"]))
    return 0


def cmd_find(args):
    t = build(args.package)
    want = args.name.lower()
    hits = [r for r in t["imports"]
            if want in (r["object_name"] or "").lower()
            or want in (r["type_name"] or "").lower()]
    if not hits:
        print("%s: no import matches %r" % (t["package"], args.name))
        return 1
    print("%s: %d import(s) matching %r" % (t["package"], len(hits), args.name))
    for r in hits[:args.limit]:
        print("  #%-5d object=%-32s type=%-24s source=%s"
              % (r["index"], r["object_name"], r["type_name"], r["source_name"]))
    return 0


def cmd_classes(args):
    t = build(args.package)
    by = collections.Counter(r["type_name"] for r in t["imports"] if r["type_name"])
    print("%s: %d distinct import classes" % (t["package"], len(by)))
    for name, n in by.most_common(args.limit):
        print("  %-38s %4d" % (name, n))
    return 0


def cmd_objects(args):
    """Report the export-attribution attempt and why it was retracted.

    This command exists so the negative result is discoverable rather than living only in a
    commit message. It deliberately prints no object list: the fixed-stride read produces one
    repeated filler name, and printing it would invite someone to use it.
    """
    t = build(args.package)
    e = t["exports"]
    print("%s: the export table is NOT parsed" % t["package"])
    print()
    print("  exports           : %d" % e["export_count"])
    print("  nominal stride    : %d bytes" % e["measured_stride"])
    print("  why not           : %s" % e["why_not"])
    print("  next step         : %s" % e["next_step"])
    print()
    print("  The measured-but-wrong name offsets were %s."
          % ", ".join("%s +%d" % (g, o) for g, o in
                      sorted(EXPORT_NAME_FIELD_UNPROVEN.items())))
    print("  They resolve 87-99%% of entries to a name index - and those names are ~18,900 copies")
    print("  of `__LoadingMovieDelegate__Delegate`. A test that accepts a constant is not a test.")
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
    # NOTE: not `--package` - that destroys the positional `package` (argparse writes both into
    # the same `args.package`), which presented as "FileNotFoundError: 'Engine'".
    p.add_argument("--from", dest="from_package",
                   help="only imports whose source package is this")
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

    p = sub.add_parser("objects",
                       help="objects this package DEFINES (export names), with coverage")
    p.add_argument("package")
    p.add_argument("name", nargs="?")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_objects)

    p = sub.add_parser("json", help="write the parsed tables")
    p.add_argument("package")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_json)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
