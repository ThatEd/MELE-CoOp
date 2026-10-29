"""Parse the UE3 import AND export tables from a decompressed ME:LE package, and prove them.

Why this exists: target selection. A co-op mod has to name a specific object - the class a second
`PlayerController` must be, the object that owns `CanFireWeapon`, the import a `Possess` targets.
An identifier table can only say "this name exists in this build". The tables say **which package it
came from, what kind of object it is, and what it inherits from**, offline, with no running game.

## The one thing that was wrong for a whole session: coordinate space

Every header offset (`name_offset`, `import_offset`, `export_offset`, `depends_offset`) is an offset
into the **serialized image**, which includes the package summary. The decompressed buffer is only
the part *after* the summary, so:

    buffer[i] == image[name_offset + i]
    buffer_start == image[name_offset]

Every header offset must therefore be reduced by `name_offset` before it is used to index the
decompressed buffer. `name_offset` is 142 for all three ME3 packages measured and 133 for ME1/ME2 -
it is a header field, not a constant to hardcode.

Reading the tables at the un-displaced offset lands 142 bytes (ME3) / 133 bytes (ME1, ME2) *past*
where each entry really starts. Every entry then straddles two real entries, and the consequences
were recorded for weeks as genuine cross-build format differences:

  * "the import layout is rotated on ME3 - three columns move, not two". There is one layout on all
    three builds. It is the standard UE3 `FObjectImport`
    `(ClassPackage FName, ClassName FName, OuterIndex, ObjectName FName)` = words 0/2/4/5, and the
    three FName number halves at words 1/3/6 are zero in **100% of entries in every package**.
  * "the export stride is 68.000 on GFxUI but 68.005 on SFXGame, so entries are variable length in
    an unparsed way". The base stride is 68 on every build; entries are followed by a UE3 TArray
    whose int32 count sits in the 68-byte record at +44 and whose payload is `4 * count` bytes.
    Zero-payload is the normal case, which is why most packages looked uniform.
  * "the export name field is at +15 / +35 / +26 and is filler-dominated". Those offsets were
    measured against the wrong base. The real object-name word is +12 (an FName: index then number).

The displacement is not a guess: it makes four independent boundaries close **exactly**, on every
package that has tables to close. `verify_tables()` runs them as assertions rather than describing
them. Verified over **all 12,704 packages** in the three installs - not a sample; the first version
of this note claimed "587 of 587 sampled", which was a random sample and had missed 414 failures
(see rule 36).

## Table shapes, once the base is right

Imports, 28 bytes, identical layout on ME1/ME2/ME3:

| word | 0,1 | 2,3 | 4 | 5,6 |
|---|---|---|---|---|
| field | ClassPackage FName | ClassName FName | OuterIndex | ObjectName FName |

Exports, 68 bytes plus a TArray tail:

| off | field |
|---|---|
| +0 | ClassIndex |
| +4 | SuperIndex |
| +8 | OuterIndex |
| +12,16 | ObjectName FName |
| +20 | ArchetypeIndex |
| +24 | ObjectFlags (u64) |
| +32 | SerialSize |
| +36 | SerialOffset |
| +40 | ExportFlags |
| +44 | NetObjectCount (int32) -> `4 * count` tail bytes |

Package indices are resolved the UE3 way: `0` is null, positive `v` is `export[v - 1]`, negative `v`
is `import[-v - 1]`. Reading a class as "the name in that slot" - which is what every earlier attempt
did - is wrong; a class slot points at an object, and whether that object is itself a class is a
property of the target, not of the column.

## Why the acceptance test is what it is

Three weaker tests were tried and all three PASS on garbage, so they are recorded here rather than
repeated:

1. **Name indices in range.** Index 0 is always a valid name, so a wrong base resolves every column
   to it and scores 100%.
2. **Names are not filler strings.** ME1's filler is `***Deleted***` and ME2's is `!_Dup` - real
   entries in those name tables, so "non-filler" does not discriminate.
3. **Two or more distinct sources.** Passes on 1,425 correct entries plus one coincidental match.

With the base now DERIVED rather than searched, `_shape_ok()` no longer has to earn belief; it only
has to reject the impossible. Three of its rules were removed because they rejected correct
packages, each recorded with the measurement that killed it in the function's docstring.

## The export table IS parsed

Entries are 68 bytes plus a `4 * NetObjectCount` TArray tail, walked sequentially. The field order
is the standard UE3 `FObjectExport` order and it is pinned by `EXPORT_FIELDS` at the offsets where
all four closure checks pass - including `serial_contiguity`, which is not satisfiable by a
coincidence (serial data is packed with no gaps, so 54,768 records must land end-to-end on
65,619,603 bytes exactly).

`RETRACTED` records the five findings this base error produced, so none of them are re-derived.

Usage:

    python -I tools/melepkg/mele_tables.py summary <package>
    python -I tools/melepkg/mele_tables.py imports <package> [--from Engine] [--limit 40]
    python -I tools/melepkg/mele_tables.py find    <package> <name>
    python -I tools/melepkg/mele_tables.py classes <package> [--limit 40]
    python -I tools/melepkg/mele_tables.py chain   <package> <class>
    python -I tools/melepkg/mele_tables.py objects <package> [name] [--limit 40]
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
EXPORT_BASE_SIZE = 68     # fixed part of an FObjectExport; a 4*NetObjectCount tail follows
EXPORT_TAIL_FIELD = 44    # int32 NetObjectCount inside the 68-byte record
TAIL_SIZE = 4             # bytes per net object

# One import layout on all three builds - the standard UE3 FObjectImport. Words 1, 3 and 6 are the
# number halves of the three FNames and are 0 in 100% of entries in every package measured. The
# earlier per-game layouts (`type`/`obj`/`src`/`outer` moving on ME3) were an artefact of reading
# every entry 142 bytes past its real start; see the module docstring.
IMPORT_LAYOUT = {"pkg": 0, "pkg_num": 1, "type": 2, "type_num": 3,
                 "outer": 4, "obj": 5, "obj_num": 6}

# FObjectExport field offsets. Unlike the import table these are NOT field-rotation per game; they
# are the standard UE3 order and they are the offsets at which the four closure checks pass.
EXPORT_FIELDS = {"cls": 0, "super": 4, "outer": 8, "name": 12, "name_num": 16,
                 "archetype": 20, "flags": 24, "serial_size": 32, "serial_offset": 36,
                 "export_flags": 40, "net_count": 44}


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


def buffer_base(header):
    """The displacement between image space and buffer space.

    Every header offset is into the serialized image; the decompressed buffer starts at
    `image[name_offset]`, because the name table is the first thing in the image after the summary.

    Returns `header["name_offset"]`. It is a header field - 142 on ME3, 133 on ME1/ME2 for the
    packages measured - and it must NOT be replaced by a constant, because it is the length of the
    summary's variable part.
    """
    d = header.get("name_offset")
    if d is None:
        raise ParseError("header has no name_offset; cannot place the tables")
    return int(d)


def image(header, key):
    """A header offset converted to a buffer offset."""
    v = header.get(key)
    if v is None:
        raise ParseError("header has no %s" % key)
    return int(v) - buffer_base(header)


def parse_imports(buf, offset, count, names, layout=None):
    """Parse `count` 28-byte import entries at `offset`. Returns (entries, bad_number_count).

    Column names are `type_name` (what kind of object: `Function`, `Class`, `BoolProperty`, ...),
    `object_name` (the object's own name), `source_name` (the ClassPackage) and `outer_index`.
    "type" answers "what kind of thing is this"; it is not "which object owns it" - that is
    `outer_index`, and the two were conflated in earlier drafts of this file.
    """
    layout = layout or IMPORT_LAYOUT
    out = []
    numbers_wrong = 0
    for i in range(count):
        e = offset + i * IMPORT_SIZE
        if e + IMPORT_SIZE > len(buf):
            raise ParseError("import %d runs past the stream at 0x%x" % (i, e))
        w = struct.unpack_from("<7i", buf, e)
        if any(w[layout[k]] != 0 for k in ("pkg_num", "type_num", "obj_num")):
            numbers_wrong += 1
        out.append({
            "index": i,
            "type_name": _nm(names, w[layout["type"]]),
            "type_name_index": w[layout["type"]],
            "object_name": _nm(names, w[layout["obj"]]),
            "object_name_index": w[layout["obj"]],
            "source_name": _nm(names, w[layout["pkg"]]),
            "source_name_index": w[layout["pkg"]],
            "outer_index": w[layout["outer"]],
            "raw": list(w),
        })
    return out, numbers_wrong


def parse_exports(buf, offset, count, names, limit=None):
    """Walk `count` FObjectExport records at `offset`. Returns (entries, end_offset).

    The base record is 68 bytes; a UE3 TArray of net objects follows, sized by the int32 at +44.
    Walking it this way - rather than at a fixed stride - is what makes the table close exactly on
    `depends_offset` on every package, including the ones where a handful of entries carry a
    4-byte net-object payload.
    """
    out = []
    cur = offset
    n = count if limit is None else min(count, limit)
    for k in range(n):
        if cur + EXPORT_BASE_SIZE > len(buf):
            raise ParseError("export %d runs past the stream at 0x%x" % (k, cur))
        f = EXPORT_FIELDS
        w = struct.unpack_from("<17i", buf, cur)
        net_count = w[f["net_count"] // 4]
        if net_count < 0 or net_count > 10000:
            raise ParseError("export %d has implausible NetObjectCount %d at 0x%x"
                             % (k, net_count, cur))
        out.append({
            "index": k,
            "class_index": w[f["cls"] // 4],
            "super_index": w[f["super"] // 4],
            "outer_index": w[f["outer"] // 4],
            "object_name": _nm(names, w[f["name"] // 4]),
            "object_name_index": w[f["name"] // 4],
            "archetype_index": w[f["archetype"] // 4],
            "serial_size": w[f["serial_size"] // 4],
            "serial_offset": w[f["serial_offset"] // 4],
            "export_flags": w[f["export_flags"] // 4],
            "net_count": net_count,
            "entry_offset": cur,
            "raw": list(w),
        })
        cur += EXPORT_BASE_SIZE + TAIL_SIZE * net_count
    return out, cur


def resolve_index(v, exports, imports):
    """Package index -> ("null" | "export" | "import", payload), the UE3 way.

    `0` is null, `v > 0` is `export[v - 1]`, `v < 0` is `import[-v - 1]`. This is the step every
    earlier attempt skipped by reading a name straight out of the slot.
    """
    if v == 0:
        return ("null", None)
    if v > 0:
        k = v - 1
        return ("export", exports[k] if 0 <= k < len(exports) else None)
    k = -v - 1
    return ("import", imports[k] if 0 <= k < len(imports) else None)


def describe_index(v, exports, imports):
    """Human-readable name for a package index, e.g. `Core.Function` or `export:SFXGame`."""
    kind, obj = resolve_index(v, exports, imports)
    if kind == "null":
        return "None"
    if obj is None:
        return "%s[%d]?" % (kind, (v - 1) if v > 0 else (-v - 1))
    if kind == "import":
        return "%s.%s" % (obj.get("source_name"), obj.get("object_name"))
    return "export:%s" % obj.get("object_name")


TYPE_NAMES = frozenset((
    "Class", "Function", "BoolProperty", "ByteProperty", "IntProperty", "FloatProperty",
    "ObjectProperty", "StructProperty", "ArrayProperty", "StrProperty", "NameProperty",
    "MapProperty", "ClassProperty", "InterfaceProperty", "DelegateProperty", "Enum",
    "ScriptStruct", "Const", "Package", "State", "Interface", "Component", "Field",
    "TextBuffer",
))


def _shape_ok(imports):
    """Is this a real import table? Returns (ok, why) plus the measured rates.

    This used to be a *search* predicate: the base was unknown, so the table had to earn belief by
    having several source packages, many object kinds and a high type-name share. The base is now
    derived from the header (`buffer_base`) and asserted against the name-table end, so the job
    here is much narrower and three of the old rules had to go because they rejected correct
    tables - each is recorded with the measurement that killed it:

      * "at least two distinct source packages": FALSE. `Engine.pcc` imports from `Core` alone
        (187/187 on ME3). A package that defines `Engine` has no reason to import from itself.
      * "ClassName is >= 75% engine type names": FALSE. Level packages import *classes*
        (`BioPowerManager`, `CylinderComponent`); `BioA_HorCr1_200FringeSouth.pcc` measures 32.9%.
        Across 54 correctly-based packages the minimum is 32.9%, so no threshold above that is safe.
      * "the FName number halves are all zero": FALSE, and so is any flat rate. A genuinely
        numbered name such as `Grid_3` has a non-zero number; the observed maximum on correct
        parses is 29% (`BioD_SunTlA_102Gatehouse_LOC_ITA.pcc`, whose imports are heavily
        duplicated with `_1`/`_2` suffixes).

    What remains is a single bound that a correct parse is nowhere near and a shifted read blows
    straight through: the dominant ClassPackage. Correct parses measure at least 44.8% across 54
    sampled packages; a shifted read spreads the column and drops far below. The `FName` number
    rate is REPORTED but NOT a gate - `BioD_SunTlA_102Gatehouse_LOC_ITA.pcc` reaches 29% because
    its imports are heavily duplicated with distinct suffixes (`SeqAct_SetInt_1`, `_2`, ...), which
    is a legitimate naming pattern. The rate was tried as a gate at 5% and then at 20% and rejected
    a correct package both times.

    The four structural closures in `verify_tables()` are what actually decide whether a table is
    right; this function only rejects what is impossible.
    """
    if not imports:
        return False, "empty table", {}
    n = len(imports)
    numbers_wrong = sum(1 for r in imports if any(w != 0 for w in (r["raw"][1], r["raw"][3],
                                                                  r["raw"][6])))
    typ = collections.Counter(r["type_name"] for r in imports if r["type_name"])
    src = collections.Counter(r["source_name"] for r in imports if r["source_name"])
    obj = collections.Counter(r["object_name"] for r in imports if r["object_name"])
    unresolved = sum(1 for r in imports if r["type_name_index"] < 0)
    share = sum(v for k, v in typ.items() if k in TYPE_NAMES) / n
    metrics = {"type_share": share, "number_rate": numbers_wrong / n,
               "unresolved_types": unresolved / n}

    if not typ or metrics["unresolved_types"] > 0.10:
        return False, ("%.0f%% of ClassName indices fall outside the name table"
                       % (metrics["unresolved_types"] * 100)), metrics
    if not src or not obj:
        return False, "no resolved ClassPackage or ObjectName", metrics
    top, dominant = src.most_common(1)[0]
    metrics["dominance"] = dominant / n
    if metrics["dominance"] < 0.20:
        return False, ("most common ClassPackage %r covers only %d of %d entries; correct parses "
                       "measure at least 44.8%%" % (top, dominant, n)), metrics
    return True, ("%r covers %d/%d (%d source(s), %d object kinds, %.0f%% engine type names, "
                  "%.1f%% numbered FNames)"
                  % (top, dominant, n, len(src), len(obj), share * 100,
                     metrics["number_rate"] * 100)), metrics
def exports_status(header):
    """The export region's geometry, all of it measured from the header rather than assumed."""
    count = header.get("export_count") or 0
    depends = header.get("depends_offset") or 0
    header_says = header.get("export_offset") or 0
    start = image(header, "export_offset")
    return {
        "export_count": count,
        "buffer_start": start,
        "image_start": header_says,
        "depends_buffer": depends - buffer_base(header),
        "depends_image": depends,
        "base_record_size": EXPORT_BASE_SIZE,
        "tail_field_offset": EXPORT_TAIL_FIELD,
        "fields": dict(EXPORT_FIELDS),
        "displacement": buffer_base(header),
        "parsed": True,
    }


RETRACTED = """
Four conclusions in this file were wrong for a whole session and are recorded so nobody
re-derives them. All four had the same cause: every table was read 142 bytes (ME3) / 133 bytes
(ME1, ME2) past its real start, because the header's offsets index the serialized IMAGE and the
decompressed buffer starts at image[name_offset].

  1. "the import layout differs per game; ME3 rotates three columns"
     -> ONE layout, the standard UE3 FObjectImport, on all three builds.

  2. "the export stride is 68.000 on GFxUI but 68.005 on SFXGame"
     -> 68 bytes on every build, plus a 4 * NetObjectCount TArray tail.

  3. "the export name field is at +15 / +35 / +26"
     -> the real object-name FName index is at +12 on every build.

  4. "class attribution is unavailable offline"
     -> it was always available; the class column is +0 and resolves through the package indices.

Sub-rule that produced all four: a measurement that lands on a boundary you did not derive is a
measurement of your own base error. The 68.005 figure was the tell - a stride that is not an
integer cannot be a stride - and it was written down as "entries differ in length" instead of
being treated as a contradiction.
"""


def verify_tables(header, buf, imports, exports, end_offset, names):
    """Four independent closures. Returns a list of (check_name, ok, detail).

    Each check fails on a mis-based table, and each is independent of the others - that is the
    only reason to believe a parse this large without a running game. `build()` raises when any
    of them fails, so a wrong table cannot be returned and silently used to aim a co-op hook.

      1. TABLE ADJACENCY. `import_offset + 28 * import_count == export_offset` in image space.
         Holds on every package and pins the import start to the byte.
      2. EXPORT WALK. Walking `export_count` records (68 bytes + 4 * NetObjectCount) from
         `export_offset` must land exactly on `depends_offset`. This is the check that proves the
         variable-length rule; it fails by 24 bytes on ME3 Engine and 288 on ME3 SFXGame when the
         tail is ignored.
      3. SERIAL CONTIGUITY. `serial_offset[k] + serial_size[k] == serial_offset[k + 1]` for every
         adjacent pair, and the last `serial_offset + serial_size` must equal the image length
         exactly. Serial data is packed with no gaps; nothing else in the record can fake this.
      4. ACCEPTANCE. Every class index resolves to an object whose class is itself null (the UE3
         chain always terminates in a class), and every `Default__X` export resolves its class to
         the export named `X`. Note what is NOT required: that the package define any class at
         all. Level packages (`Entry.pcc`) contain only actors and components, so they legitimately
         have zero `class_index == 0` exports, and a "must define a class" gate rejected a correct
         parse on a map.
    """
    out = []
    n = len(names)

    ic, ioff = header.get("import_count") or 0, header.get("import_offset") or 0
    ecount, eoff = header.get("export_count") or 0, header.get("export_offset") or 0
    ok = ioff + IMPORT_SIZE * ic == eoff
    out.append(("table_adjacency", ok,
                "import_offset + 28*%d == export_offset  (%d vs %d)" % (ic, ioff + IMPORT_SIZE * ic, eoff)))

    want = header.get("depends_offset") or 0
    got = end_offset + buffer_base(header)
    out.append(("export_walk", got == want,
                "walked to %d, depends_offset %d, delta %d" % (got, want, got - want)))

    if exports:
        breaks = [e["index"] for p, e in zip(exports, exports[1:])
                  if p["serial_offset"] + p["serial_size"] != e["serial_offset"]]
        last = exports[-1]
        serial_end = last["serial_offset"] + last["serial_size"]
        want_end = len(buf) + buffer_base(header)
        # The end-of-image equality is the strong form and it held on all 12,000+ packages tested
        # until ME1's maps. On some of those the tail of the export list points at serial data
        # *past* the end of the decompressed stream - `BIOA_NOR10_07wake_DSG.pcc` ends at
        # 50,771,444 against an 8,125,004-byte image, and the two entries that overshoot together
        # carry 42,646,440-byte sizes. Contiguity still holds with 0 gaps, so the entry WALK is
        # right and the sizes are the anomaly (a package that shipped with a dangling serial
        # range, most likely truncated during packaging). Requiring the equality would reject
        # correct parses on those maps; requiring only contiguity would have passed the wrong base
        # it was invented to catch. So: contiguity is mandatory, the exact end is reported.
        contiguous = not breaks
        exact = contiguous and serial_end == want_end
        out.append(("serial_contiguity", contiguous,
                    "%d gap(s) %s; serial end %d vs image length %d%s"
                    % (len(breaks), breaks[:4], serial_end, want_end,
                       "" if exact else " (end differs, walk itself is contiguous)")))
    else:
        out.append(("serial_contiguity", False, "no exports"))

    classes = {e["index"] for e in exports if e["class_index"] == 0}
    by_name = {}
    for e in exports:
        if e["object_name"]:
            by_name.setdefault(e["object_name"].lower(), []).append(e)
    bad_chain, bad_default, names_ok = 0, 0, 0
    for e in exports:
        if e["class_index"] > 0:
            k = e["class_index"] - 1
            if not (0 <= k < len(exports)) or exports[k]["class_index"] != 0:
                bad_chain += 1
        nm = e["object_name"] or ""
        if nm.startswith("Default__"):
            # Case-insensitive: ME3's Engine.pcc pairs `Default__ScoreBoard` with a class spelled
            # `Scoreboard`. A case-sensitive lookup rejected that package on one capital letter.
            base = nm[len("Default__"):].lower()
            cls_idx = e["class_index"]
            # The class may be either an export of this package or an IMPORT of it. A SeqVar
            # archetype such as `Default__BioSeqVar_ObjectFindByTag` takes its class from
            # `Core`, i.e. a negative index - and demanding an export there rejected two
            # correct packages.
            if cls_idx > 0:
                target = exports[cls_idx - 1] if cls_idx - 1 < len(exports) else None
                ok = target is not None and (target["object_name"] or "").lower() == base
            elif cls_idx < 0:
                k = -cls_idx - 1
                target = imports[k] if k < len(imports) else None
                ok = target is not None and (target["object_name"] or "").lower() == base
            else:
                ok = False
            if ok:
                names_ok += 1
            elif base not in by_name and not any(
                    (i["object_name"] or "").lower() == base for i in imports):
                bad_default += 1
    defaults = sum(1 for e in exports if (e["object_name"] or "").startswith("Default__"))
    ok = (bad_chain == 0 and bad_default == 0
          and (defaults == 0 or names_ok == defaults))
    out.append(("acceptance", ok,
                "%d class-ish exports; %d exports whose class is not itself a class; "
                "%d/%d Default__X whose class is X" % (len(classes), bad_chain, names_ok, defaults)))
    return out


def _container_info(chunks):
    """Chunk metadata for callers: count, and whether the package was compressed at all."""
    first = chunks[0] if chunks else {}
    return {
        "chunks": len(chunks),
        "compressed": not first.get("uncompressed", False),
        "empty": bool(first.get("empty", False)),
        "csize": sum(c["total_csize"] for c in chunks),
        "usize": sum(c["total_usize"] for c in chunks),
    }


def _empty_package(package, game, data, chunks, pkg):
    """An empty result for a header-only package, in the same shape `build()` returns.

    Ten ME1 files are exactly 133 bytes: the package summary with every table offset set to
    `name_offset` and every count zero. They are stripped packages, not corrupt ones, and the
    honest answer about their contents is "none" - not an exception that gets them counted as
    parse failures.
    """
    return {
        "package": os.path.basename(package),
        "path": package,
        "game": game,
        "displacement": chunks[0]["start"],
        "container": {"chunks": 0, "compressed": False, "empty": True, "csize": 0, "usize": 0},
        "names": 0,
        "name_table_end": 0,
        "header": pkg.parse_header_fields(data[:chunks[0]["start"]], 0, 1),
        "import_start": 0,
        "import_numbers_wrong": 0,
        "import_shape": "header-only package: no imports",
        "import_metrics": {},
        "exports_end": 0,
        "checks": [("table_adjacency", True, "no tables"),
                   ("export_walk", True, "no exports"),
                   ("serial_contiguity", True, "no serial data"),
                   ("acceptance", True, "empty package")],
        "imports": [],
        "exports": [],
        "exports_status": {"export_count": 0, "empty": True},
    }


def build(package, limit=None):
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
        # The name table can be one entry short of the header's count when it holds an entry the
        # self-terminating scan cannot read - `BIOA_LOS10_06a_DSG.pcc` has a one-character name -
        # and that makes `len(names)` the wrong search key. Retry without it; the header predicate
        # alone is enough to find the group.
        fields = pkg.parse_header_fields(header, None, len(chunks))
    if fields.get("name_count_at") is None:
        # A header-only package (133 bytes: the summary and nothing else) has no body to parse
        # names from. Accept it only when the summary itself says there is nothing to read.
        if chunks[0].get("empty") and len(names) == 0:
            return _empty_package(package, guess_game(package), data, chunks, pkg)
        raise SystemExit("header layout not recognised for %s" % os.path.basename(package))

    game = guess_game(package)
    disp = buffer_base(fields)
    imp_start = image(fields, "import_offset")
    exp_start = image(fields, "export_offset")

    # The header's own arithmetic pins the import start; the name table must end exactly there.
    # Asserting it rather than searching for it is what turns a 142-byte base error from a
    # plausible-looking table into an immediate failure. If it does not hold, re-read the table
    # using the header's own NameCount, which tolerates entries the scan rejects.
    if imp_start != name_end:
        counted, counted_end = pkg.parse_names_counted(buf, fields["name_count"])
        if counted_end == imp_start:
            names, name_end = counted, counted_end
    if imp_start != name_end:
        raise SystemExit(
            "import table should start at the name-table end 0x%x but the header puts it at 0x%x "
            "(after subtracting the %d-byte image displacement). The displacement or the header "
            "layout is wrong for %s - refusing to parse rather than returning a shifted table."
            % (name_end, imp_start, disp, os.path.basename(package)))

    imports, numbers_wrong = parse_imports(buf, imp_start, fields["import_count"], names)
    ok, why, metrics = _shape_ok(imports)
    if not ok:
        raise SystemExit(
            "import table at 0x%x does not have the shape of a real table: %s. Refusing to "
            "guess: a mis-attributed import sends a co-op hook to the wrong object silently."
            % (imp_start, why))

    exports, end_offset = parse_exports(buf, exp_start, fields["export_count"], names)
    checks = verify_tables(fields, buf, imports, exports, end_offset, names)
    failed = [c for c in checks if not c[1]]
    if failed:
        raise SystemExit(
            "export table at 0x%x failed %d closure check(s): %s"
            % (exp_start, len(failed), "; ".join("%s (%s)" % (c[0], c[2]) for c in failed)))

    return {
        "package": os.path.basename(package),
        "path": package,
        "game": game,
        "displacement": disp,
        "container": _container_info(chunks),
        "names": len(names),
        "name_table": names,
        "name_table_end": name_end,
        "header": fields,
        "import_start": imp_start,
        "import_numbers_wrong": numbers_wrong,
        "import_shape": why,
        "import_metrics": metrics,
        "exports_end": end_offset,
        "checks": checks,
        "imports": imports,
        "exports": exports,
        "exports_status": exports_status(fields),
        # The decompressed stream, kept so `signature()`/`param_order()` can read serial data.
        # A caller that only wants the tables can ignore it; nothing else stores it.
        "buf": buf,
    }


# ---------------------------------------------------------------------------------------
def cmd_summary(args):
    t = build(args.package)
    print("%s  [%s]" % (t["package"], t["game"]))
    print("  displacement       : %d bytes (header offsets are image-space)"
          % t["displacement"])
    print("  names              : %d (table ends at 0x%x)" % (t["names"], t["name_table_end"]))
    print("  imports            : %d at 0x%x (== name-table end)"
          % (len(t["imports"]), t["import_start"]))
    print("  shape check        : %s" % t["import_shape"])
    m = t["import_metrics"]
    print("  shape metrics      : type names %.0f%%, numbered FNames %.1f%%, dominance %.0f%%"
          % (m["type_share"] * 100, m["number_rate"] * 100, m.get("dominance", 0) * 100))
    by_src = collections.Counter(r["source_name"] for r in t["imports"])
    print("  sources            : %s"
          % ", ".join("%s %d" % (k, v) for k, v in by_src.most_common(6)))
    by_type = collections.Counter(r["type_name"] for r in t["imports"])
    print("  top import types   : %s"
          % ", ".join("%s %d" % (k, v) for k, v in by_type.most_common(6)))
    print()
    print("  exports            : %d at 0x%x, walked to 0x%x"
          % (len(t["exports"]), t["exports_status"]["buffer_start"], t["exports_end"]))
    by_cls = collections.Counter(
        describe_index(e["class_index"], t["exports"], t["imports"]) for e in t["exports"])
    print("  top export classes : %s"
          % ", ".join("%s %d" % (k, v) for k, v in by_cls.most_common(6)))
    named = [e["object_name"] for e in t["exports"] if e["object_name"]]
    print("  distinct export nm : %d of %d" % (len(set(named)), len(named)))
    print()
    print("  closure checks:")
    for name, ok, detail in t["checks"]:
        print("    %-18s %-4s %s" % (name, "ok" if ok else "FAIL", detail))
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
    print("  %-6s %-34s %-22s %-12s %s" % ("INDEX", "OBJECT", "TYPE", "SOURCE", "OUTER"))
    for r in rows[:args.limit]:
        print("  %-6d %-34s %-22s %-12s %s"
              % (r["index"], r["object_name"], r["type_name"], r["source_name"],
                 describe_index(r["outer_index"], t["exports"], t["imports"])))
    return 0


def cmd_find(args):
    """Find an object by name, in the export table (what this package DEFINES) or the imports."""
    t = build(args.package)
    want = args.name.lower()
    imp_hits = [r for r in t["imports"]
                if want in (r["object_name"] or "").lower()
                or want in (r["type_name"] or "").lower()]
    exp_hits = [e for e in t["exports"] if want in (e["object_name"] or "").lower()]
    if not imp_hits and not exp_hits:
        print("%s: nothing matches %r" % (t["package"], args.name))
        return 1
    if exp_hits:
        print("%s: %d export(s) matching %r" % (t["package"], len(exp_hits), args.name))
        for e in exp_hits[:args.limit]:
            print("  #%-6d %-34s class=%-26s outer=%-22s size=%d"
                  % (e["index"], e["object_name"],
                     describe_index(e["class_index"], t["exports"], t["imports"]),
                     describe_index(e["outer_index"], t["exports"], t["imports"]),
                     e["serial_size"]))
    if imp_hits:
        print("%s: %d import(s) matching %r" % (t["package"], len(imp_hits), args.name))
        for r in imp_hits[:args.limit]:
            print("  #%-6d object=%-34s type=%-22s source=%s"
                  % (r["index"], r["object_name"], r["type_name"], r["source_name"]))
    return 0


PROPERTY_SIZE = {
    # Byte widths of the C++ struct UE3 builds for a function's parms block. These are the sizes
    # written into `Parms` at dispatch, so they are what an out-of-process caller must lay out.
    # Cross-checked against the measured `CreatePlayer` layout in this project's notes:
    # 4 (InsertIndex) + 8 (NewPlayer) + 8 (ReturnValue) + 1 (bSpawnActor) + 16 (OutError) +
    # 4 (ControllerId) = 0x29 raw, and the notes place `ControllerId@0x00`,
    # `bSpawnActor@0x14`, `OutError@0x16`, `ReturnValue@0x28`, `NewPlayer@0x30` after the
    # engine's own alignment - consistent to the byte.
    "BoolProperty": 1, "ByteProperty": 1,
    "IntProperty": 4, "FloatProperty": 4,
    "ObjectProperty": 8, "ClassProperty": 8, "ComponentProperty": 8, "NameProperty": 8,
    "StructProperty": 16, "StrProperty": 16, "ArrayProperty": 16, "MapProperty": 16,
    "InterfaceProperty": 16, "DelegateProperty": 16, "FixedArrayProperty": 16,
}

# `UProperty.Next` sits at serial +16 in every property measured on all three builds. It is the
# package index of the next property in the owner's `Children` list, which is what preserves
# DECLARATION order - the export table does not, because exports are stored in name order.
UPROPERTY_NEXT = 16


PROPERTY_ALIGN = {
    # Alignment of each property struct inside the parms block. NOT `alignof` for everything:
    # a 16-byte FString (`StrProperty`, `ArrayProperty`, ...) is 4-aligned in this ABI, not
    # 8-aligned. Getting that wrong on ME3's `CreatePlayer` put every field after `ControllerId`
    # 4 bytes late and made the total 0x38 instead of the 0x2c the engine accepted.
    "BoolProperty": 1, "ByteProperty": 1,
    "IntProperty": 4, "FloatProperty": 4,
    "ObjectProperty": 8, "ClassProperty": 8, "ComponentProperty": 8,
    "NameProperty": 4,
    "StructProperty": 4, "StrProperty": 4, "ArrayProperty": 4, "MapProperty": 4,
    "InterfaceProperty": 4, "DelegateProperty": 4, "FixedArrayProperty": 4,
}

# The whole parms struct aligns to 4, not 8: `CreatePlayer` is 0x2c on the wire, which 0x30
# would not be.
PARMS_ALIGN = 4

# An out-parameter of property type is appended at its natural 4-alignment regardless of type.
OUT_PARAM_ALIGN = 4


def _align(n, a):
    return (n + a - 1) // a * a


def class_fields(table, buf, class_export):
    """A class's full field list in declaration order, INCLUDING inherited fields.

    Rule 13 of this project: a property walker that ignores `SuperStruct` cannot prove a field is
    absent. It bit this function on the first run - `PlayerController` reports 493 direct children
    and none of them is `Pawn`, because `Pawn` is declared on `Controller` further up the chain.

    **The NAMES, TYPES, DECLARATION ORDER and OWNER are exact.** The byte OFFSETS are not, and the
    measured evidence says so:

        PlayerController.Pawn, computed vs live-measured
            ME1  0x3A0  vs 0x3DC     ME2  0x2C0  vs 0x294     ME3  0x378  vs 0x3BC
        PlayerController.myHUD, computed vs live-measured
            ME3  0x640  vs 0x6D0

    The gap is not a constant - ME3 needs +0x44 to fix `Pawn` and +0x90 to fix `myHUD` - so a single
    rebase against one live anchor cannot repair the rest. A linear sum of `PROPERTY_SIZE` with 4/8
    alignment is too simple for a real UE3 class: native class boundaries insert padding, and
    `FName`/`TArray` members inside a class are laid out per the engine's own rules rather than by
    the sizes used for a function's parms block.

    So `how` reports `"offsets-unverified"` for any class with a base chain, and callers must not
    use these offsets to address memory. `struct_fields` (a `UScriptStruct` with only floats) IS
    exact and verified - see that function.

    Returns ([(owner, name, type, size, offset), ...], how).
    """
    chain, exact = [], True
    cur = class_export
    seen = set()
    while cur is not None and cur["index"] not in seen:
        seen.add(cur["index"])
        fields, level_how = _fields_of(table, buf, cur)
        if fields:
            if level_how != "next-chain":
                exact = False
            chain.append((cur["object_name"], fields))
        s = cur["super_index"]
        cur = table["exports"][s - 1] if s > 0 and s - 1 < len(table["exports"]) else None
    rows, off = [], 0
    for owner, fields in reversed(chain):
        for f in fields:
            t = describe_index(f["class_index"], table["exports"], table["imports"]).split(".")[-1]
            size = PROPERTY_SIZE.get(t, 0)
            off = _align(off, PROPERTY_ALIGN.get(t, 4))
            rows.append((owner, f["object_name"], t, size, off))
            off += size
    how = "next-chain" if exact else "partial"
    if len(chain) > 1:
        how = "offsets-unverified"
    return rows, how


def struct_fields(table, buf, struct_export):
    """A `UScriptStruct`'s fields in DECLARATION order, with byte offsets.

    Unlike `class_fields`, these offsets ARE exact, because a `UScriptStruct` has no native base
    class to introduce padding and `PerPlayerSplitscreenData` is four 4-byte floats:

        +0x00 SizeX   +0x04 SizeY   +0x08 OriginX   +0x0C OriginY

    A `UScriptStruct` has an empty super chain, so this shares `class_fields`' walk and simply
    reports without hedging. `Engine.SplitscreenData` is `{ PlayerData: TArray }` - the array of
    those, which is the pane layout.

    Returns [(name, type, size, offset), ...], empty when the chain does not resolve.
    """
    rows, _how = class_fields(table, buf, struct_export)
    if not rows:
        return []
    return [(r[1], r[2], r[3], r[4]) for r in rows if r[2] != "Function"]


def _fields_of(table, buf, owner_export):
    """The child exports of `owner_export`, in DECLARATION order via `UProperty.Next`.

    Returns (children, how) where `how` is `"next-chain"` when the chain resolved cleanly and
    `"export-order"` otherwise. Shared by function parameters and struct fields, because both are
    `UProperty` lists with the same ordering problem.
    """
    kids = [e for e in table["exports"] if e["outer_index"] == owner_export["index"] + 1]
    if not kids:
        return [], "empty"
    disp = buffer_base(table["header"])
    by_index = {p["index"]: p for p in kids}
    successor = {}
    for p in kids:
        if p["serial_size"] < UPROPERTY_NEXT + 4:
            continue
        at = p["serial_offset"] - disp + UPROPERTY_NEXT
        if at < 0 or at + 4 > len(buf):
            continue
        v = struct.unpack_from("<i", buf, at)[0]
        if v > 0 and (v - 1) in by_index and (v - 1) != p["index"]:
            successor[p["index"]] = v - 1
    if not successor:
        return kids, "export-order"
    pointed = set(successor.values())
    heads = [p for p in kids if p["index"] not in pointed]
    if len(heads) != 1:
        return kids, "export-order"
    order, cur, seen = [], heads[0], set()
    while cur is not None and cur["index"] not in seen:
        seen.add(cur["index"])
        order.append(cur)
        cur = by_index.get(successor.get(cur["index"]))
    if len(order) != len(kids):
        return kids, "export-order"
    return order, "next-chain"



    """A function's parameters (or a struct's fields) in DECLARATION order.

    The export table alone cannot give this. A function's parameters are its child exports, but
    exports are stored in name order, so `CreatePlayer` parses on ME2 as

        InsertIndex, NewPlayer, ReturnValue, bSpawnActor, OutError, ControllerId

    - correct here only by luck of the alphabet. Relying on that would be a latent bug that fires
    on the first function whose parameters are not alphabetical, and the symptom would be a
    dispatch with swapped arguments: a crash inside the engine with a plausible stack, which is
    the failure mode this project has spent the most time on.

    The real order is the `UProperty.Next` chain - see `_fields_of`, which is shared with
    `struct_fields` because a `UScriptStruct`'s fields are ordered by exactly the same mechanism.
    Returns (params, how) so a caller can tell whether it got the proven answer.
    """
    return _fields_of(table, buf, func_export)


def enum_members(table, buf, enum_export):
    """A `UEnum`'s member names, in declaration order.

    A UE3 enum's serial data is `UObject` fields then `TArray<FName> Names`. Measured on ME3's
    `Engine.pcc`, all 255 enums:

        +0   NetIndex (FName index)      - irrelevant
        +4   a constant (13240)
        +8   two zero dwords
        +16  StateIndex (FName index)
        +20  Names.Num()
        +24  Names[0 .. Num-1], each entry a 4-byte index plus a 4-byte number
        end  = 24 + 8 * Num, which is exactly `serial_size`

    The 8-byte stride is not a guess: ME:LE writes FNames with their number half, and every one of
    these is 0. `EPhysics` closes as 24 + 8*17 = 160 = its serial size.

    The alternative hypothesis - count at +16 with 4-byte names - closes on **zero** enums, which
    is what ruled it out rather than a plausibility argument.

    Returns a list of names, or [] if the structure does not close.
    """
    disp = buffer_base(table["header"])
    at = enum_export["serial_offset"] - disp
    size = enum_export["serial_size"]
    if at < 0 or at + 24 > len(buf) or size < 24:
        return []
    count = struct.unpack_from("<i", buf, at + 20)[0]
    if not (0 <= count <= 5000) or 24 + 8 * count != size:
        return []
    names = table.get("name_table")
    if not isinstance(names, list):
        return []
    out = []
    for i in range(count):
        idx = struct.unpack_from("<i", buf, at + 24 + 8 * i)[0]
        out.append(names[idx] if 0 <= idx < len(names) else "<%d>" % idx)
    return out


def param_order(table, buf, func_export):
    """A function's parameters (or a struct's fields) in DECLARATION order.

    The export table alone cannot give this. A function's parameters are its child exports, but
    exports are stored in name order, so `CreatePlayer` parses on ME2 as

        InsertIndex, NewPlayer, ReturnValue, bSpawnActor, OutError, ControllerId

    - correct here only by luck of the alphabet. Relying on that would be a latent bug that fires
    on the first function whose parameters are not alphabetical, and the symptom would be a
    dispatch with swapped arguments: a crash inside the engine with a plausible stack, which is
    the failure mode this project has spent the most time on.

    The real order is the `UProperty.Next` chain - see `_fields_of`, which is shared with
    `struct_fields` because a `UScriptStruct`'s fields are ordered by exactly the same mechanism.
    Returns (params, how) so a caller can tell whether it got the proven answer.
    """
    return _fields_of(table, buf, func_export)


def signature(table, buf, func_export):
    """Parameters in declaration order with the byte offset each occupies in the parms block.

    The offsets are computed with the alignment rule the LIVE measurement implies, which is not
    what a first guess gives. ME3's `CreatePlayer` was dispatched successfully with
    `ControllerId@0x00, OutError@0x04, bSpawnActor@0x14, ReturnValue@0x18, NewPlayer@0x20,
    InsertIndex@0x28`, total `0x2c`, so:

      * a base alignment of 4 (NOT 8) - which is why the 16-byte FString `OutError` starts at 0x04.
        A naive `alignof(FString) == 8` on Win64 puts every later field 4 bytes late and the total
        at 0x38 instead of 0x2c.
      * `ObjectProperty`/`ClassProperty` align to 8; `StructProperty` and the containers to 4.
      * the struct total is 4-aligned, not 8.

    `test_mele_tables.py` pins all six field offsets against that live record, so the rule is
    anchored to a layout the engine itself accepted rather than to a belief about MSVC.
    """
    params, how = param_order(table, buf, func_export)
    rows, off, ret = [], 0, None
    for p in params:
        t = describe_index(p["class_index"], table["exports"], table["imports"]).split(".")[-1]
        size = PROPERTY_SIZE.get(t, 0)
        a = OUT_PARAM_ALIGN if p["object_name"] == "ReturnValue" else PROPERTY_ALIGN.get(t, 4)
        off = _align(off, a)
        rows.append((p["object_name"], t, size, off))
        off += size
        if p["object_name"] == "ReturnValue":
            ret = t
    return {"name": func_export["object_name"], "params": rows, "returns": ret,
            "parms_size": _align(off, PARMS_ALIGN), "order": how}


def class_chain(table, name):
    """Walk `super_index` from an export until it leaves the package.

    Returns [(export_index, name, super_label), ...]. Stops at a null super, at a name it cannot
    follow (an import - which is a legitimate end: the chain continues in another package), or on a
    cycle. This is the operation the co-op work actually needs: "is the class I am about to spawn a
    descendant of the class the engine expects?"
    """
    exports, imports = table["exports"], table["imports"]
    by_name = {}
    for e in exports:
        if e["object_name"]:
            by_name.setdefault(e["object_name"], e)
    e = by_name.get(name)
    if e is None:
        return []
    out, seen = [], set()
    while e is not None and e["index"] not in seen:
        seen.add(e["index"])
        s = e["super_index"]
        label = describe_index(s, exports, imports)
        out.append((e["index"], e["object_name"], label))
        e = exports[s - 1] if s > 0 and s - 1 < len(exports) else None
    return out


def cmd_signature(args):
    """Print a function's parameter list in declaration order, with byte offsets."""
    t = build(args.package)
    hits = [e for e in t["exports"]
            if e["object_name"] == args.name
            and describe_index(e["class_index"], t["exports"], t["imports"]) == "Core.Function"]
    if not hits:
        # Distinguish the three reasons this misses, because "did you mean X" naming X itself is
        # what a caller saw before: a class export needs `members`, an import needs another
        # package, and only then is the name genuinely absent.
        same_name = [e for e in t["exports"] if e["object_name"] == args.name]
        if same_name:
            kinds = sorted({describe_index(e["class_index"], t["exports"], t["imports"])
                            for e in same_name})
            return _report_not_a_function(t, args.name, kinds)
        near = [e["object_name"] for e in t["exports"]
                if args.name.lower() in (e["object_name"] or "").lower()][:8]
        if near:
            print("%s: no function named %r; did you mean %s"
                  % (t["package"], args.name, near))
        else:
            print("%s: nothing named %r" % (t["package"], args.name))
        print("  (an object defined in another package is an IMPORT here - check the export"
              " with `find` on that package)")
        return 1
    for e in hits[:args.limit]:
        s = signature(t, t["buf"], e)
        inputs = [r for r in s["params"] if r[0] != "ReturnValue"]
        print("%s::%s(%s) -> %s  [parms 0x%x, order %s]"
              % (describe_index(e["outer_index"], t["exports"], t["imports"]),
                 s["name"],
                 ", ".join("%s: %s" % (n, ty) for n, ty, _, _ in inputs),
                 s["returns"] or "void", s["parms_size"], s["order"]))
        for n, ty, size, off in s["params"]:
            tag = "  (out)" if n == "ReturnValue" else ""
            print("    +0x%02x  %-18s %-18s %2d bytes%s" % (off, n, ty, size, tag))
        if s["order"] != "next-chain":
            print("    NOTE: declaration order unavailable (%s); parameters are in export"
                  " (alphabetical) order and MUST NOT be used to lay out a parms block"
                  % s["order"])
    return 0


def _report_not_a_function(t, name, kinds):
    """Explain why a name exists but is not a function export, instead of implying a typo."""
    real = [k for k in kinds if k != "None"]
    if real:
        print("%s: %r exists but is a %s, not a function"
              % (t["package"], name, ", ".join(real)))
    else:
        print("%s: %r is a class (its own class is null), not a function"
              % (t["package"], name))
    for e in [x for x in t["exports"] if x["object_name"] == name][:1]:
        kids = [x for x in t["exports"] if x["outer_index"] == e["index"] + 1]
        funcs = [x for x in kids
                 if describe_index(x["class_index"], t["exports"], t["imports"])
                 == "Core.Function"]
        defs = [x for x in kids if x["class_index"] == 0]
        print("  it declares %d child export(s): %d class(es), %d function(s)"
              % (len(kids), len(defs), len(funcs)))
        if funcs:
            print("  functions: %s" % ", ".join(f["object_name"] for f in funcs[:8]))
            print("  try: signature <package> <one of those names>"
                  "  (several exports may share it)")
        elif kids:
            print("  this is a Kismet action or a plain class: its properties are the children")
            for k in kids[:8]:
                print("     %-30s %s" % (k["object_name"],
                      describe_index(k["class_index"], t["exports"], t["imports"])))
    return 0


def bytecode_refs(table, buf, func_export):
    """Export indices referenced from inside a function's bytecode. A HEURISTIC, not a decode.

    UE3 operand references are a 1-byte token followed by a 4-byte package index, so scanning for
    dwords that land on a valid export index finds them - but it also finds noise, and the noise
    rate is high enough that a bare hit list is not evidence.

    Measured on ME3's `Engine.pcc`, which has 32,135 exports: **10.5% of random 4-byte windows**
    (2106 of 20000) contain a value in `1..export_count`. So in a function with ~14 candidate
    operands, about 1.5 false positives are expected. That is why this returns the rate alongside
    the hits, and why the caller is expected to check a hit against something independent - e.g.
    `AddLocalPlayer` references `GamePlayers`, and `CreatePlayer`'s own string constants say
    "inserted into GamePlayers array at index", which is a correlation across two unrelated sources
    rather than a scan result on its own.

    What this CANNOT do is distinguish a property reference from a local-variable reference: locals
    are indices into the function's own `Variables` array, a different index space, so they appear
    as ordinary small integers and are not matched here. Reading a full instruction stream needs
    the opcode table, which is unproven - see `ue3_bytecode.py`.

    Returns (hits, baseline_rate) where each hit is (offset, index, name, type, outer).
    """
    disp = buffer_base(table["header"])
    exports, imports = table["exports"], table["imports"]
    by_index = {e["index"]: e for e in exports}
    at = func_export["serial_offset"] - disp
    if at + 32 > len(buf):
        return [], 0.0
    length = struct.unpack_from("<i", buf, at + 28)[0]
    if length < 0 or at + 32 + length > len(buf):
        return [], 0.0
    body = buf[at + 32:at + 32 + length]
    hits = []
    for i in range(0, len(body) - 4):
        v = struct.unpack_from("<i", body, i)[0]
        if v > 0 and (v - 1) in by_index:
            t = by_index[v - 1]
            hits.append((i, v, t["object_name"],
                         describe_index(t["class_index"], exports, imports), t["outer_index"]))
    # Baseline: the chance a random 4-byte window in the buffer matches an export index.
    valid = len(exports)
    sample = 0
    matches = 0
    step = max(4, len(buf) // 4000)
    for i in range(0, len(buf) - 4, step):
        sample += 1
        if 0 < struct.unpack_from("<i", buf, i)[0] <= valid:
            matches += 1
    return hits, (matches / sample if sample else 0.0)


def cmd_refs(args):
    """Property references found inside a function's bytecode, with the noise rate stated."""
    t = build(args.package)
    hits = [e for e in t["exports"]
            if e["object_name"] == args.name
            and describe_index(e["class_index"], t["exports"], t["imports"]) == "Core.Function"]
    if not hits:
        print("%s: no function named %r" % (t["package"], args.name))
        return 1
    refs, baseline = bytecode_refs(t, t["buf"], hits[0])
    print("%s::%s: %d candidate reference(s)" % (t["package"], args.name, len(refs)))
    for off, idx, nm, ty, outer in refs[:args.limit]:
        print("  +0x%02x  #%-6d %-26s %-24s outer=%s"
              % (off, idx, nm, ty,
                 describe_index(outer, t["exports"], t["imports"])))
    print()
    print("  HEURISTIC: a random 4-byte window matches an export index %.1f%% of the time in this"
          % (baseline * 100))
    print("  package, so ~%.1f of these would be expected by chance alone. Treat a hit as a lead,"
          % (len(refs) * baseline))
    print("  not a finding - corroborate it against something independent.")
    print("  Local variables are NOT matched: they index the function's own Variables array.")
    return 0


def cmd_fields(args):
    """A class's field layout with byte offsets, including inherited fields."""
    t = build(args.package)
    hits = [e for e in t["exports"]
            if e["object_name"] == args.name and e["class_index"] == 0]
    if not hits:
        print("%s: no class named %r" % (t["package"], args.name))
        return 1
    rows, how = class_fields(t, t["buf"], hits[0])
    print("%s::%s: %d fields (with inheritance), order %s" % (t["package"], args.name,
                                                             len(rows), how))
    if args.grep:
        rows = [r for r in rows if args.grep.lower() in r[1].lower()]
        print("  filtered to %d matching %r" % (len(rows), args.grep))
    for owner, name, typ, size, off in rows[:args.limit]:
        print("  +0x%-4X %-28s %-18s %2d  (declared on %s)" % (off, name, typ, size, owner))
    if how != "next-chain":
        print()
        print("  WARNING: at least one level of the chain fell back to export (alphabetical) order,")
        print("  so these offsets are NOT reliable. Do not use them for a dispatch.")
    return 0


def cmd_enum(args):
    """Print a UEnum's members, in declaration order."""
    t = build(args.package)
    hits = [e for e in t["exports"]
            if e["object_name"] == args.name
            and describe_index(e["class_index"], t["exports"], t["imports"]) == "Core.Enum"]
    if not hits:
        near = [e["object_name"] for e in t["exports"]
                if e["class_index"] == 0
                and (e["object_name"] or "").startswith(("E", "e"))
                and args.name.lower() in (e["object_name"] or "").lower()][:10]
        print("%s: no enum named %r%s"
              % (t["package"], args.name, ("; near: %s" % near) if near else ""))
        return 1
    members = enum_members(t, t["buf"], hits[0])
    if not members:
        print("%s: enum %r did not close; structure unproven for this build" % (t["package"], args.name))
        return 1
    print("%s::%s (%d members)" % (t["package"], args.name, len(members)))
    for i, m in enumerate(members):
        print("  %2d  %s" % (i, m))
    return 0


def cmd_enums(args):
    """List every enum a package defines."""
    t = build(args.package)
    enums = [e for e in t["exports"]
             if describe_index(e["class_index"], t["exports"], t["imports"]) == "Core.Enum"]
    closed = 0
    print("%s: %d enum(s)" % (t["package"], len(enums)))
    for e in enums[:args.limit]:
        members = enum_members(t, t["buf"], e)
        if members:
            closed += 1
            print("  %-34s %2d  %s" % (e["object_name"], len(members),
                                       ", ".join(members[:4]) + ("..." if len(members) > 4 else "")))
        else:
            print("  %-34s structure did not close" % e["object_name"])
    shown = min(len(enums), args.limit)
    if shown:
        print()
        print("  %d of %d shown closed" % (closed, shown))
    return 0


def cmd_chain(args):
    """Print a class's inheritance chain, as far as this package can follow it."""
    t = build(args.package)
    chain = class_chain(t, args.name)
    if not chain:
        print("%s: no class named %r" % (t["package"], args.name))
        return 1
    print("%s: %s" % (t["package"], args.name))
    for i, (idx, nm, sup) in enumerate(chain):
        print("  %s %-40s (export #%d) -> %s" % ("   " if i else "  *", nm, idx, sup))
    if chain[-1][2].startswith(("export:",)):
        pass
    elif "." in chain[-1][2]:
        print("     (the chain continues in %s, which this package does not define)"
              % chain[-1][2].split(".")[0])
    return 0


def cmd_classes(args):
    """Every class this package DEFINES: exports whose own class index is 0.

    A class's class is null - `Core.Class` is itself an object in `Core`, reached through the
    import table, not through the export's own class slot. That is why "count the exports whose
    class resolves to `Core.Class`" returns 0 and is not a defect.
    """
    t = build(args.package)
    cls = [e for e in t["exports"] if e["class_index"] == 0]
    print("%s: %d class(es) defined, %d export(s) total"
          % (t["package"], len(cls), len(t["exports"])))
    for e in cls[:args.limit]:
        sup = describe_index(e["super_index"], t["exports"], t["imports"])
        print("  #%-6d %-40s super=%-30s size=%d"
              % (e["index"], e["object_name"], sup, e["serial_size"]))
    return 0


def cmd_objects(args):
    """List exported objects with their class and outer, optionally filtered by name."""
    t = build(args.package)
    rows = t["exports"]
    if getattr(args, "name", None):
        want = args.name.lower()
        rows = [e for e in rows if want in (e["object_name"] or "").lower()]
    print("%s: %d export(s)" % (t["package"], len(rows)))
    print("  %-6s %-40s %-28s %s" % ("INDEX", "OBJECT", "CLASS", "OUTER"))
    for e in rows[:args.limit]:
        print("  #%-6d %-40s %-28s %s"
              % (e["index"], e["object_name"],
                 describe_index(e["class_index"], t["exports"], t["imports"]),
                 describe_index(e["outer_index"], t["exports"], t["imports"])))
    return 0


def cmd_json(args):
    t = build(args.package)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"package": t["package"], "game": t["game"], "names": t["names"],
                   "displacement": t["displacement"], "header": t["header"],
                   "import_start": t["import_start"],
                   "imports": t["imports"], "exports": t["exports"],
                   "checks": t["checks"], "retracted": RETRACTED}, f, indent=1)
    print("wrote %s (%d bytes)" % (args.out, os.path.getsize(args.out)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("summary", help="counts, closure checks and a table overview")
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

    p = sub.add_parser("find", help="find an object or class by name")
    p.add_argument("package")
    p.add_argument("name")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("classes", help="classes this package DEFINES")
    p.add_argument("package")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_classes)

    p = sub.add_parser("chain", help="walk a class's inheritance chain")
    p.add_argument("package")
    p.add_argument("name")
    p.set_defaults(func=cmd_chain)

    p = sub.add_parser("enum", help="a UEnum's member names, in declaration order")
    p.add_argument("package")
    p.add_argument("name")
    p.set_defaults(func=cmd_enum)

    p = sub.add_parser("fields",
                       help="a class's field offsets, including inherited fields")
    p.add_argument("package")
    p.add_argument("name")
    p.add_argument("--grep", help="only fields whose name contains this")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_fields)

    p = sub.add_parser("refs",
                       help="property references in a function's bytecode (heuristic)")
    p.add_argument("package")
    p.add_argument("name")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_refs)

    p = sub.add_parser("enums", help="every enum a package defines")
    p.add_argument("package")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_enums)

    p = sub.add_parser("signature",
                       help="a function's parameters in declaration order, with byte offsets")
    p.add_argument("package")
    p.add_argument("name")
    p.add_argument("--limit", type=int, default=1)
    p.set_defaults(func=cmd_signature)

    p = sub.add_parser("objects", help="exported objects with class and outer")
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
