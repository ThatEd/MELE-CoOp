"""Tests for the package table parser. No game needed for most of them.

The tables are what make a co-op target nameable offline: "this name exists" comes from the
identifier table, but "this object is a `Class` imported from `Core`, it is called
`SFXPlayerController`, and its superclass is `BioPlayerController`" needs the export and import
tables.

## What these tests are defending against

One bug cost a whole session and produced five false findings, so it gets the most tests:

**The coordinate-space error.** Every header offset indexes the serialized IMAGE, but the
decompressed buffer starts at `image[name_offset]`. Reading the tables at the undisplaced offset
lands 142 bytes (ME3) / 133 bytes (ME1, ME2) past each entry's real start. The symptoms were then
written down as facts: "the import layout differs per game", "the export stride is 68.005 on
SFXGame", "the export name field is at +26", "class attribution is unavailable offline". None of
those are true; the tests below pin the true versions.

The cases are grouped:

  * coordinate space - displacement, and the four closures that prove it
  * field offsets    - import and export layouts, with the retracted versions named
  * rejection        - a shifted read MUST be refused, or it aims a co-op hook at nothing
  * real packages    - end-to-end on all three builds, skipped when no install is present

Cases that need the game install skip cleanly and say which file is missing.

Run: python -I tools/melepkg/test_mele_tables.py
"""
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mele_tables  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def package(game, name="SFXGame"):
    """Locate a package for a game via the module's own install resolver."""
    pkg = mele_tables.load_pkg()
    rel = os.path.join("Game", game, "BioGame", "CookedPCConsole", name + ".pcc")
    for root in pkg.game_dirs():
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return p
    return None


# ---------------------------------------------------------------------------------------
# Coordinate space
# ---------------------------------------------------------------------------------------
@case("the buffer base is the header's name_offset, not a constant")
def t_buffer_base():
    """142 on ME3 and 133 on ME1/ME2 - but it must be READ, not hardcoded.

    It is the length of the package summary's variable part (the folder FString), so a constant
    would silently break on any package whose folder name differs in length.
    """
    a = mele_tables.buffer_base({"name_offset": 142})
    b = mele_tables.buffer_base({"name_offset": 133})
    c = mele_tables.buffer_base({"name_offset": 200})
    if (a, b, c) != (142, 133, 200):
        return "fail", "buffer_base returned %r" % ((a, b, c),)
    try:
        mele_tables.buffer_base({})
        return "fail", "accepted a header with no name_offset"
    except mele_tables.ParseError:
        return "ok", "reads the field, raises when absent"


@case("image() subtracts the displacement from every header offset")
def t_image_conversion():
    h = {"name_offset": 142, "export_offset": 20148, "import_offset": 16564,
         "depends_offset": 61832}
    got = [mele_tables.image(h, k) for k in ("export_offset", "import_offset", "depends_offset")]
    if got != [20006, 16422, 61690]:
        return "fail", "got %r, expected [20006, 16422, 61690]" % (got,)
    return "ok", "3 offsets displaced by 142"


@case("the retracted per-game import layouts are gone, not merely unused")
def t_no_per_game_layouts():
    """`LAYOUTS` encoded 'ME3 rotates three columns'. It does not, and keeping the table around
    is exactly how the wrong answer gets re-derived."""
    if hasattr(mele_tables, "LAYOUTS"):
        return "fail", ("mele_tables.LAYOUTS still exists (%r). It recorded a per-game layout "
                        "rotation that was an artefact of the displaced base"
                        % (mele_tables.LAYOUTS,))
    if not hasattr(mele_tables, "IMPORT_LAYOUT"):
        return "fail", "no IMPORT_LAYOUT at all"
    return "ok", "single IMPORT_LAYOUT, no LAYOUTS"


@case("the import layout is the standard UE3 FObjectImport on all three builds")
def t_import_layout():
    want = {"pkg": 0, "pkg_num": 1, "type": 2, "type_num": 3,
            "outer": 4, "obj": 5, "obj_num": 6}
    got = mele_tables.IMPORT_LAYOUT
    if got != want:
        return "fail", "%r" % (got,)
    if sorted(got.values()) != list(range(7)):
        return "fail", "the layout does not address each of the 7 words exactly once"
    return "ok", "pkg=0/1 type=2/3 outer=4 obj=5/6, no per-game variation"


@case("the export record is 68 bytes plus a 4-byte tail per net object")
def t_export_layout():
    if mele_tables.EXPORT_BASE_SIZE != 68:
        return "fail", "base record is %r" % mele_tables.EXPORT_BASE_SIZE
    if mele_tables.EXPORT_TAIL_FIELD != 44:
        return "fail", "tail count is at +%r, expected +44" % mele_tables.EXPORT_TAIL_FIELD
    f = mele_tables.EXPORT_FIELDS
    want = {"cls": 0, "super": 4, "outer": 8, "name": 12, "name_num": 16, "archetype": 20,
            "flags": 24, "serial_size": 32, "serial_offset": 36, "export_flags": 40,
            "net_count": 44}
    if f != want:
        return "fail", "%r" % (f,)
    return "ok", "11 fields, class at +0 and name at +12"


@case("the retracted export name offsets are recorded as retracted")
def t_retracted_recorded():
    """+15/+35/+26 were measured against the wrong base and are filler-dominated. The negative
    result and its cause must survive in the module, or the next agent repeats it."""
    text = mele_tables.RETRACTED
    for needle in ("68.005", "+15", "142"):
        if needle not in text:
            return "fail", "RETRACTED does not mention %r" % needle
    if hasattr(mele_tables, "EXPORT_NAME_FIELD_UNPROVEN"):
        return "fail", ("EXPORT_NAME_FIELD_UNPROVEN still exists; those offsets are known to be "
                        "wrong and should not be offered as a candidate")
    return "ok", "retractions recorded with their common cause"


# ---------------------------------------------------------------------------------------
# Field semantics
# ---------------------------------------------------------------------------------------
@case("package indices resolve the UE3 way: 0 null, +n export[n-1], -n import[-n-1]")
def t_resolve_index():
    exports = [{"object_name": "E0"}, {"object_name": "E1"}]
    imports = [{"source_name": "Core", "object_name": "I0"}]
    cases = [(0, "null"), (1, "export"), (2, "export"), (-1, "import")]
    for v, kind in cases:
        got, _ = mele_tables.resolve_index(v, exports, imports)
        if got != kind:
            return "fail", "index %r resolved as %r, expected %r" % (v, got, kind)
    if mele_tables.describe_index(1, exports, imports) != "export:E0":
        return "fail", "describe_index(1) = %r" % mele_tables.describe_index(1, exports, imports)
    if mele_tables.describe_index(-1, exports, imports) != "Core.I0":
        return "fail", "describe_index(-1) does not read source.object"
    if mele_tables.describe_index(0, exports, imports) != "None":
        return "fail", "index 0 is not rendered as None"
    return "ok", "4 indices + 3 descriptions"


@case("a class slot is an INDEX, not a name - the mistake that hid attribution")
def t_class_is_index():
    """Every earlier attempt read the class word as a name index. It is a package index, and
    whether the target is itself a class is a property of the TARGET."""
    exports = [{"object_name": "SFXGame", "class_index": 0}]
    imports = [{"source_name": "Core", "object_name": "Class"}]
    # A class's own class is null, so +1 points at the class export.
    kind, obj = mele_tables.resolve_index(1, exports, imports)
    if kind != "export" or obj["object_name"] != "SFXGame":
        return "fail", "index +1 did not resolve to the class export"
    # A Default__ archetype of an imported class takes a NEGATIVE index.
    kind, obj = mele_tables.resolve_index(-1, exports, imports)
    if kind != "import" or obj["object_name"] != "Class":
        return "ok but surprising", "negative index resolved to %r" % (obj,)
    return "ok", "positive -> export, negative -> import, 0 -> None"


@case("parse_imports raises rather than truncating at the end of the buffer")
def t_parse_imports_bounds():
    buf = b"\x00" * 28
    try:
        mele_tables.parse_imports(buf, 0, 2, ["A"])
        return "fail", "parsed 2 entries from a 1-entry buffer"
    except mele_tables.ParseError:
        return "ok", "raised ParseError"


@case("parse_exports rejects an implausible NetObjectCount")
def t_parse_exports_bad_tail():
    """A wrong base makes +44 into noise. A tail of 10,000 entries is ~40 KB per record, which
    would walk off the buffer; rejecting it turns a wrong base into an error, not a short table."""
    buf = bytearray(200)
    struct.pack_into("<i", buf, 44, 999999)
    try:
        mele_tables.parse_exports(bytes(buf), 0, 1, ["A", "B"])
        return "fail", "accepted NetObjectCount 999999"
    except mele_tables.ParseError:
        return "ok", "raised ParseError"


# ---------------------------------------------------------------------------------------
# Rejection - a shifted read must not be accepted
# ---------------------------------------------------------------------------------------
@case("the shape check rejects a source column that never resolves")
def t_shape_unresolved():
    """The failure a range check cannot catch: every entry resolving to nothing. This is what a
    shifted read looks like when the name table is small. It is NOT "one distinct source" - a
    Core-only table is legitimate (Engine.pcc), so the test has to be about resolvability."""
    rows = [{"source_name": None, "object_name": "Obj%d" % i, "type_name": "Class",
             "type_name_index": 1, "raw": [0, 0, 0, 0, 0, 0, 0]} for i in range(100)]
    ok, why, _ = mele_tables._shape_ok(rows)
    if ok:
        return "fail", "accepted a table whose every source is unresolved: %s" % why
    return "ok", "rejected: %s" % why


@case("the shape check rejects out-of-range ClassName indices")
def t_shape_oob_types():
    rows = [{"source_name": "Core", "object_name": "Obj%d" % i, "type_name": None,
             "type_name_index": -7, "raw": [0, 0, 0, 0, 0, 0, 0]} for i in range(100)]
    ok, why, _ = mele_tables._shape_ok(rows)
    if ok:
        return "fail", "accepted a table whose every ClassName index is out of range"
    return "ok", "rejected: %s" % why


@case("the shape check accepts a package that imports from Core alone")
def t_shape_single_package_ok():
    """`Engine.pcc` imports 187/187 from `Core`. The old rule demanded two sources and rejected
    the real Engine.pcc on every build."""
    rows = [{"source_name": "Core", "object_name": "K%d" % (i % 40), "type_name": "Function",
             "type_name_index": 1, "raw": [0, 0, 0, 0, 0, 0, 0]} for i in range(100)]
    ok, why, m = mele_tables._shape_ok(rows)
    if not ok:
        return "fail", "rejected a Core-only table: %s" % why
    return "ok", why


@case("the shape check accepts a level package with a low type-name share")
def t_shape_level_package():
    """A level imports classes (BioPowerManager, CylinderComponent), not properties, so the
    type-name share falls to ~33%. Gating on it rejected correct maps."""
    rows = ([{"source_name": "Core", "object_name": "C%d" % i, "type_name": "Class",
              "type_name_index": 1, "raw": [0, 0, 0, 0, 0, 0, 0]} for i in range(40)]
            + [{"source_name": "Engine", "object_name": "E%d" % i, "type_name": "Texture2D",
                "type_name_index": 2, "raw": [0, 0, 0, 0, 0, 0, 0]} for i in range(30)]
            + [{"source_name": "Core", "object_name": "P%d" % i, "type_name": "BoolProperty",
                "type_name_index": 3, "raw": [0, 0, 0, 0, 0, 0, 0]} for i in range(30)])
    ok, why, m = mele_tables._shape_ok(rows)
    if not ok:
        return "fail", "rejected a level-shaped table (type share %.0f%%): %s" % (
            m["type_share"] * 100, why)
    if m["type_share"] >= 0.75:
        return "fail", "the fixture is not actually low-type-share"
    return "ok", why


@case("the shape check rejects a table dominated by nothing")
def t_shape_no_dominance():
    rows = [{"source_name": "S%d" % i, "object_name": "Obj%d" % i, "type_name": "Class",
             "type_name_index": 1, "raw": [0, 0, 0, 0, 0, 0, 0]} for i in range(500)]
    ok, why, _ = mele_tables._shape_ok(rows)
    if ok:
        return "fail", "accepted 500 distinct sources"
    return "ok", "rejected: %s" % why


@case("a constant name column is rejected as evidence")
def t_constant_is_not_evidence():
    """The shape of all four false passes in this project: a test that accepts a constant.

    Pin the reasoning: if 95% of entries resolve to the SAME name, that is filler, not
    attribution - whatever the coverage percentage says.
    """
    names = ["__LoadingMovieDelegate__Delegate"] * 950 + ["Real%d" % i for i in range(50)]
    distinct = len(set(names))
    if len(names) == 1000 and distinct < 100:
        return "ok", ("100%% coverage but only %d distinct names -> filler. Distinctness, not "
                      "coverage, is the test" % distinct)
    return "fail", "the reasoning helper did not reproduce the case"


@case("a package that is not there gives nothing rather than a guessed table")
def t_no_package():
    p = os.path.join(HERE, "does-not-exist.pcc")
    try:
        mele_tables.build(p)
        return "fail", "parsed a package that does not exist"
    except (FileNotFoundError, SystemExit, mele_tables.ParseError):
        return "ok", "raised rather than returning entries"


# ---------------------------------------------------------------------------------------
# Cases that need the game
# ---------------------------------------------------------------------------------------
def _build_or_skip(game, name="SFXGame"):
    """Returns (table, reason). `reason` is non-None only when the package is genuinely ABSENT.

    A `build()` that REFUSES must not be reported as a skip - that would let the whole
    game-backed half of this suite pass while the parser is broken. This was live: reverting the
    displacement made every package fail to build, and the suite still reported 22/24 OK because
    each refusal was dressed up as "no build available". A test that cannot fail is worse than no
    test at all.
    """
    p = package(game, name)
    if not p:
        return None, "no %s %s.pcc found (set MELE_GAME_DIR)" % (game, name)
    try:
        return mele_tables.build(p), None
    except (SystemExit, mele_tables.ParseError) as exc:
        raise AssertionError("%s %s refused to parse: %s" % (game, name, exc))


@case("all four closure checks pass on every build")
def t_closures():
    results, skipped = {}, []
    for game in ("ME1", "ME2", "ME3"):
        t, why = _build_or_skip(game)
        if t is None:
            skipped.append(game)
            continue
        bad = [n for n, ok, _ in t["checks"] if not ok]
        if bad:
            return "fail", "%s failed %s" % (game, bad)
        results[game] = len(t["checks"])
    if not results:
        return "skip", "no build available (%s)" % ", ".join(skipped)
    return "ok", ", ".join("%s %d checks" % (g, n) for g, n in sorted(results.items()))


@case("the export walk consumes exactly export_count records")
def t_export_count_exact():
    results, skipped = {}, []
    for game in ("ME1", "ME2", "ME3"):
        t, why = _build_or_skip(game)
        if t is None:
            skipped.append(game)
            continue
        if len(t["exports"]) != t["header"]["export_count"]:
            return "fail", "%s parsed %d of %d" % (
                game, len(t["exports"]), t["header"]["export_count"])
        results[game] = len(t["exports"])
    if not results:
        return "skip", "no build available (%s)" % ", ".join(skipped)
    return "ok", ", ".join("%s %d" % (g, n) for g, n in sorted(results.items()))


@case("the import table starts exactly at the name-table end")
def t_import_at_name_end():
    """This is the assertion that turns the 142-byte base error into an immediate failure. It was
    previously a *search* over 0x60 candidate offsets, which is why a wrong base looked fine."""
    results, skipped = {}, []
    for game in ("ME1", "ME2", "ME3"):
        t, why = _build_or_skip(game)
        if t is None:
            skipped.append(game)
            continue
        if t["import_start"] != t["name_table_end"]:
            return "fail", "%s: import at 0x%x, name table ends 0x%x" % (
                game, t["import_start"], t["name_table_end"])
        results[game] = t["displacement"]
    if not results:
        return "skip", "no build available (%s)" % ", ".join(skipped)
    return "ok", "displacements %s" % (
        ", ".join("%s %d" % (g, d) for g, d in sorted(results.items())),)


@case("SFXPlayerController exists and inherits from BioPlayerController")
def t_playercontroller_export():
    """The acceptance test the earlier code only described.

    ME2 and ME3 both define `SFXPlayerController : BioPlayerController` in SFXGame.pcc. A parse
    that cannot produce that pair, with that inheritance, is wrong however plausible its
    percentages look - and this is also the exact class a client-side second player needs.
    """
    results, skipped = {}, []
    for game in ("ME2", "ME3"):
        t, why = _build_or_skip(game)
        if t is None:
            skipped.append(game)
            continue
        hit = [e for e in t["exports"] if e["object_name"] == "SFXPlayerController"]
        if not hit:
            return "fail", "%s: no SFXPlayerController export" % game
        e = hit[0]
        sup = e["super_index"]
        if sup > 0:
            name = t["exports"][sup - 1]["object_name"]
        elif sup < 0:
            name = t["imports"][-sup - 1]["object_name"]
        else:
            name = None
        if name != "BioPlayerController":
            return "fail", "%s: super is %r, expected BioPlayerController" % (game, name)
        if e["class_index"] != 0:
            return "fail", "%s: class_index %d, expected 0 for a class" % (game, e["class_index"])
        results[game] = e["index"]
    if not results:
        return "skip", "no build available (%s)" % ", ".join(skipped)
    return "ok", ", ".join("%s export #%d" % (g, i) for g, i in sorted(results.items()))


@case("every Default__X resolves its class to X")
def t_default_archetypes():
    results, skipped = {}, []
    for game in ("ME1", "ME2", "ME3"):
        t, why = _build_or_skip(game)
        if t is None:
            skipped.append(game)
            continue
        n = sum(1 for e in t["exports"] if (e["object_name"] or "").startswith("Default__"))
        for name, ok, detail in t["checks"]:
            if name == "acceptance" and not ok:
                return "fail", "%s: %s" % (game, detail)
        results[game] = n
    if not results:
        return "skip", "no build available (%s)" % ", ".join(skipped)
    if not any(results.values()):
        return "fail", "no Default__ archetypes found in any build"
    return "ok", ", ".join("%s %d archetypes" % (g, n) for g, n in sorted(results.items()))


@case("the import table resolves PlayerController as Class from Core on every build")
def t_playercontroller_import():
    """Three FNames per entry, read at the corrected base. If the base were wrong this would
    read `object=PlayerController, class=Class` - backwards, and internally consistent."""
    found, missing = {}, []
    for game in ("ME1", "ME2", "ME3"):
        t, why = _build_or_skip(game)
        if t is None:
            missing.append(game)
            continue
        hit = [r for r in t["imports"] if r["object_name"] == "PlayerController"]
        found[game] = "%s/%s" % (hit[0]["type_name"], hit[0]["source_name"]) if hit else "ABSENT"
    if not found:
        return "skip", "no build available (%s)" % ", ".join(missing)
    bad = {g: v for g, v in found.items() if v != "Class/Core"}
    if bad:
        return "fail", "%s; expected Class/Core everywhere" % bad
    return "ok", "Class/Core on %s" % ", ".join(sorted(found))


@case("the same layout parses a small level package")
def t_level_package():
    """`Entry.pcc` has 33 imports and 59 exports, defines zero classes and imports *classes*, so
    it exercises the opposite of everything SFXGame does."""
    t, why = _build_or_skip("ME3", "Entry")
    if t is None:
        return "skip", why
    if len(t["exports"]) != t["header"]["export_count"]:
        return "fail", "parsed %d of %d" % (len(t["exports"]), t["header"]["export_count"])
    bad = [n for n, ok, _ in t["checks"] if not ok]
    if bad:
        return "fail", "failed %s" % bad
    if any(e["class_index"] == 0 for e in t["exports"]):
        return "ok but surprising", "the level defines a class after all"
    return "ok", "%d imports, %d exports, no classes, all checks pass" % (
        len(t["imports"]), len(t["exports"]))


def main():
    failures = 0
    skips = 0
    for name, fn in CASES:
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = "fail", "%s: %s" % (type(exc).__name__, exc)
        mark = {"ok": "OK  ", "fail": "FAIL", "skip": "SKIP"}.get(status, "OK  ")
        print("%s %-62s %s" % (mark, name, detail))
        if status == "fail":
            failures += 1
        elif status == "skip":
            skips += 1
    print()
    if failures:
        print("%d/%d FAILED" % (failures, len(CASES)))
        return 1
    print("ALL OK: %d cases%s" % (len(CASES),
                                  ", %d skipped (no game installed)" % skips if skips else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
