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
import importlib.util
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


@case("AddLocalPlayer is a GameViewportClient function on every build")
def t_addlocalplayer_owner():
    """This is the fact the ME2 second-player plan rests on, and it was previously unknown.

    `AddLocalPlayer` was established as *present* from the identifier table, but not *which object
    declares it* - and the obvious guess (the game info object, which owns `CreatePlayer` in the
    campaign's login path) is wrong. It is a `GameViewportClient` method, so a dispatch aimed at
    the game info goes nowhere.
    """
    found, skipped = {}, []
    for game in ("ME1", "ME2", "ME3"):
        p = package(game, "Engine")
        if not p:
            skipped.append(game)
            continue
        t = mele_tables.build(p)
        hit = [e for e in t["exports"] if e["object_name"] == "AddLocalPlayer"]
        if not hit:
            return "fail", "%s: Engine.pcc has no AddLocalPlayer export" % game
        e = hit[0]
        cls = mele_tables.describe_index(e["class_index"], t["exports"], t["imports"])
        out = e["outer_index"]
        if out > 0:
            owner = t["exports"][out - 1]["object_name"]
        elif out < 0:
            owner = t["imports"][-out - 1]["object_name"]
        else:
            owner = None
        if cls != "Core.Function":
            return "fail", "%s: class is %r, expected Core.Function" % (game, cls)
        if owner != "GameViewportClient":
            return "fail", "%s: outer is %r, expected GameViewportClient" % (game, owner)
        found[game] = e["index"]
    if not found:
        return "skip", "no Engine.pcc found (%s)" % ", ".join(skipped)
    return "ok", "GameViewportClient on %s" % ", ".join(
        "%s #%d" % (g, i) for g, i in sorted(found.items()))


@case("the viewport owns the whole local-player surface")
def t_viewport_owns_split_screen():
    """`CreatePlayer`, `RemovePlayer`, `LayoutPlayers` are viewport methods too - which is why
    aiming them at a player object in the command suite was a role bug when it shipped."""
    p = package("ME3", "Engine")
    if not p:
        return "skip", "no ME3 Engine.pcc"
    t = mele_tables.build(p)
    want = ("AddLocalPlayer", "CreatePlayer", "RemovePlayer", "LayoutPlayers")
    bad = {}
    for name in want:
        hit = [e for e in t["exports"] if e["object_name"] == name]
        if not hit:
            bad[name] = "absent"
            continue
        e = hit[0]
        out = e["outer_index"]
        owner = t["exports"][out - 1]["object_name"] if out > 0 else None
        if owner != "GameViewportClient":
            bad[name] = owner
    if bad:
        return "fail", "%s are not viewport methods" % bad
    return "ok", "all %d are GameViewportClient methods" % len(want)


@case("the controller and pawn chains match each build, and the builds differ")
def t_class_chains():
    """Pinned exactly, because the first draft of the note got two of three builds wrong - the
    chains are NOT the same shape across builds (rule 31 applied to class hierarchy)."""
    want = {
        ("ME1", "SFXPawn_Player"): ["SFXPawn_Player", "BioPawn", "Core.Pawn"],
        ("ME2", "SFXPawn_Player"): ["SFXPawn_Player", "SFXPawn", "BioPawn", "Core.Pawn"],
        ("ME3", "SFXPawn_Player"): ["SFXPawn_Player", "SFXPawn_PlayerParty", "SFXPawn",
                                    "BioPawn", "Core.Pawn"],
        ("ME2", "SFXPlayerController"): ["SFXPlayerController", "BioPlayerController",
                                         "BioBasePlayerController", "Core.PlayerController"],
        ("ME3", "SFXPlayerController"): ["SFXPlayerController", "BioPlayerController",
                                         "Core.PlayerController"],
    }
    got, skipped = {}, []
    for game in ("ME1", "ME2", "ME3"):
        p = package(game)
        if not p:
            skipped.append(game)
            continue
        t = mele_tables.build(p)
        for cls in ("SFXPawn_Player", "SFXPlayerController"):
            chain = mele_tables.class_chain(t, cls)
            got[(game, cls)] = [c[1] for c in chain] + ([chain[-1][2]] if chain else [])
    if not got:
        return "skip", "no build available (%s)" % ", ".join(skipped)
    bad = {k: v for k, v in want.items() if k in got and got[k] != v}
    if bad:
        return "fail", "%s" % bad
    # The builds must NOT agree: if they do, this test stopped discriminating.
    shapes = {tuple(v) for k, v in got.items() if k[1] == "SFXPlayerController" and v}
    if len(shapes) < 2:
        return "ok but surprising", ("all builds share one controller chain shape; rule 31 says "
                                     "check that is real")
    return "ok", "%d chains pinned, %d distinct non-empty controller shapes" % (
        len(got), len(shapes))


@case("ME1's SFXGame defines no SFXPlayerController")
def t_me1_no_controller():
    """Worth pinning because the absence is load-bearing: LE1 is the third target and it does not
    have the class the first two do."""
    p = package("ME1")
    if not p:
        return "skip", "no ME1 SFXGame.pcc"
    t = mele_tables.build(p)
    if [e for e in t["exports"] if e["object_name"] == "SFXPlayerController"]:
        return "fail", "ME1 has an SFXPlayerController after all"
    if not [e for e in t["exports"] if e["object_name"] == "SFXPawn_Player"]:
        return "fail", "ME1 has neither class; the package is probably wrong"
    return "ok", "absent, while SFXPawn_Player is present"


@case("an uncompressed package parses the same way as a compressed one")
def t_uncompressed_package():
    """380 ME1 packages have no chunk at all - the file is `header | body`, uncompressed.

    Scanning only for the chunk signature read those as "not a package", so they were skipped as
    junk. They are ordinary packages in a different storage mode, and `decompress()` now falls back
    to the uncompressed interpretation rather than raising.
    """
    p = package("ME1", "asari_ss")
    if not p:
        return "skip", "no ME1 asari_ss.pcc"
    t = mele_tables.build(p)
    c = t["container"]
    if c.get("compressed"):
        return "fail", "asari_ss.pcc parsed as compressed; it has no chunk"
    if c.get("usize", 0) <= 0:
        return "fail", "uncompressed package reports %r uncompressed bytes" % (c.get("usize"),)
    bad = [n for n, ok, _ in t["checks"] if not ok]
    if bad:
        return "fail", "failed %s" % bad
    # It must actually have content - an empty result would also satisfy the checks above.
    if not t["exports"] or not t["imports"]:
        return "fail", "%d exports / %d imports; the package has both" % (
            len(t["exports"]), len(t["imports"]))
    return "ok", "%d names, %d imports, %d exports, %d bytes, all checks pass" % (
        t["names"], len(t["imports"]), len(t["exports"]), c["usize"])


@case("a package whose serial data overshoots still parses, and says so")
def t_serial_overshoot():
    """ME1 maps exist whose export list points past the end of the decompressed stream.

    `BIOA_NOR10_07wake_DSG.pcc` has 4,777 exports, zero contiguity gaps, and a last
    `serial_offset + serial_size` of 50,771,444 against an 8,125,004-byte image. The WALK is right -
    contiguity proves it - so the sizes are the anomaly, not the parse. Requiring the end to equal
    the image length rejected correct parses on those maps. Contiguity is mandatory; the exact end
    is reported in the check's detail rather than enforced.
    """
    p = package("ME1", "BIOA_NOR10_07wake_DSG")
    if not p:
        return "skip", "no ME1 BIOA_NOR10_07wake_DSG.pcc"
    t = mele_tables.build(p)
    detail = dict((n, d) for n, _, d in t["checks"])["serial_contiguity"]
    if "gap(s) [];" not in detail:
        return "fail", "expected zero gaps, got %r" % detail
    if "end differs" not in detail:
        return "ok but surprising", ("this map's serial data now closes exactly; the fixture "
                                     "stops exercising the overshoot case")
    if not detail.startswith("0 gap"):
        return "fail", detail
    return "ok", detail[:80]


@case("CreatePlayer's parms layout reproduces the LIVE-measured one, byte for byte")
def t_createplayer_layout():
    """The strongest check in this file: an offline parse vs a live memory read.

    `CreatePlayer` was dispatched successfully in a running game using the layout
    `ControllerId@0x00, OutError@0x04, bSpawnActor@0x14, ReturnValue@0x18, NewPlayer@0x20,
    InsertIndex@0x28`, total `0x2c`. That record came from reading the engine's own parms struct
    and the engine accepted it. Reproducing it from the package alone means the offline model and
    the runtime disagree about nothing.

    Two rules had to be right for this to match, and both were wrong on the first attempt:
      * declaration order is the `UProperty.Next` chain, not the export (alphabetical) order
      * a 16-byte FString is 4-aligned here, and the struct total is 4-aligned, not 8
    """
    LIVE = {"ControllerId": 0x00, "OutError": 0x04, "bSpawnActor": 0x14,
            "ReturnValue": 0x18, "NewPlayer": 0x20, "InsertIndex": 0x28}
    LIVE_TOTAL = 0x2c
    results, skipped = {}, []
    for game in ("ME1", "ME2", "ME3"):
        p = package(game, "Engine")
        if not p:
            skipped.append(game)
            continue
        t = mele_tables.build(p)
        hit = [e for e in t["exports"] if e["object_name"] == "CreatePlayer"]
        if not hit:
            return "fail", "%s: no CreatePlayer export in Engine.pcc" % game
        s = mele_tables.signature(t, t["buf"], hit[0])
        if s["order"] != "next-chain":
            return "fail", "%s: fell back to %r; declaration order is required" % (
                game, s["order"])
        got = {n: off for n, _ty, _sz, off in s["params"]}
        if got != LIVE:
            return "fail", "%s: %s, expected %s" % (game, got, LIVE)
        if s["parms_size"] != LIVE_TOTAL:
            return "fail", "%s: parms 0x%x, expected 0x%x" % (
                game, s["parms_size"], LIVE_TOTAL)
        results[game] = s["parms_size"]
    if not results:
        return "skip", "no Engine.pcc found (%s)" % ", ".join(skipped)
    return "ok", "6 offsets + 0x2c on %s" % ", ".join(sorted(results))


@case("declaration order differs from export order, so the fallback is detectable")
def t_param_order_is_not_export_order():
    """`CreatePlayer`'s parameters happen to be alphabetical in NO useful sense - the export order
    is InsertIndex, NewPlayer, ReturnValue, bSpawnActor, OutError, ControllerId, while the real
    declaration order starts with ControllerId. If `param_order` ever silently falls back to
    export order, the layout test above turns red; this case explains why."""
    p = package("ME3", "Engine")
    if not p:
        return "skip", "no ME3 Engine.pcc"
    t = mele_tables.build(p)
    e = [x for x in t["exports"] if x["object_name"] == "CreatePlayer"][0]
    chain, how = mele_tables.param_order(t, t["buf"], e)
    export_order = [x for x in t["exports"] if x["outer_index"] == e["index"] + 1]
    if how != "next-chain":
        return "fail", "order came from %r" % how
    a = [x["object_name"] for x in chain]
    b = [x["object_name"] for x in export_order]
    if a == b:
        return "ok but surprising", ("chain order equals export order for CreatePlayer, so this "
                                     "case no longer discriminates")
    return "ok", "chain %s != export %s" % (a[:3], b[:3])


@case("AddLocalPlayer's signature: (NewPlayer, InsertIndex) -> int")
def t_addlocalplayer_signature():
    """The function the ME2 plan wants to call. Its parms block is 0x10 and the out-parameter is
    an `IntProperty`, so a caller must supply 16 bytes with `NewPlayer` at 0."""
    p = package("ME3", "Engine")
    if not p:
        return "skip", "no ME3 Engine.pcc"
    t = mele_tables.build(p)
    e = [x for x in t["exports"] if x["object_name"] == "AddLocalPlayer"][0]
    s = mele_tables.signature(t, t["buf"], e)
    names = [n for n, _ty, _sz, _o in s["params"]]
    if "NewPlayer" not in names or "InsertIndex" not in names:
        return "fail", "params are %s" % names
    if s["returns"] != "IntProperty":
        return "fail", "returns %r" % (s["returns"],)
    offs = {n: o for n, _t, _s, o in s["params"]}
    if offs["NewPlayer"] != 0:
        return "fail", "NewPlayer at 0x%x, expected 0" % offs["NewPlayer"]
    if s["parms_size"] % 4:
        return "fail", "parms size 0x%x is not 4-aligned" % s["parms_size"]
    return "ok", "NewPlayer@0, parms 0x%x, returns int" % s["parms_size"]


@case("EWhoTriggers members are decoded, and ME3's numbering differs from ME1/ME2")
def t_who_triggers():
    """The enum TRIGGER-DELAY.md needed and could not get.

    ME1/ME2: WT_PlayerOnly, WT_PlayerAndSquad, WT_Everyone, WT_TagList, WT_MAX
    ME3:     WT_PlayerOnly, WT_PlayerOnlyLocal, WT_PlayerAndSquad, WT_Everyone, WT_TagList, WT_MAX

    ME3 inserts `WT_PlayerOnlyLocal`, which shifts every later value by one. So a mod that writes
    ME2's `WT_Everyone` (2) into ME3 gets `WT_PlayerAndSquad`, and ME2's `WT_PlayerAndSquad` (1)
    becomes `WT_PlayerOnlyLocal`. This is rule 31 with a byte-level consequence: the same number
    means different things per build.
    """
    want = {
        "ME1": ["WT_PlayerOnly", "WT_PlayerAndSquad", "WT_Everyone", "WT_TagList", "WT_MAX"],
        "ME2": ["WT_PlayerOnly", "WT_PlayerAndSquad", "WT_Everyone", "WT_TagList", "WT_MAX"],
        "ME3": ["WT_PlayerOnly", "WT_PlayerOnlyLocal", "WT_PlayerAndSquad", "WT_Everyone",
                "WT_TagList", "WT_MAX"],
    }
    got, skipped = {}, []
    for game in ("ME1", "ME2", "ME3"):
        p = package(game, "Engine")
        if not p:
            skipped.append(game)
            continue
        t = mele_tables.build(p)
        hit = [e for e in t["exports"] if e["object_name"] == "EWhoTriggers"]
        if not hit:
            return "fail", "%s: no EWhoTriggers export in Engine.pcc" % game
        members = mele_tables.enum_members(t, t["buf"], hit[0])
        if not members:
            return "fail", "%s: EWhoTriggers did not close" % game
        got[game] = members
    if not got:
        return "skip", "no Engine.pcc found (%s)" % ", ".join(skipped)
    bad = {g: v for g, v in got.items() if v != want[g]}
    if bad:
        return "fail", "%s" % bad
    if got.get("ME1") == got.get("ME3"):
        return "fail", "ME1 and ME3 members are identical; the fixture stopped discriminating"
    return "ok", ("ME1/ME2 5 members, ME3 6 (inserts WT_PlayerOnlyLocal at 1); "
                  "WT_Everyone is 2 on ME1/ME2 and 3 on ME3")


@case("every enum in Engine.pcc closes as (count@+20, 8-byte names)")
def t_enums_all_close():
    """The structural check behind `enum_members`: if the stride or the count offset were wrong,
    most enums would fail to close. Measured 255/255 on ME3 before this was written, so this is
    pinning a result rather than seeking one - which is why the threshold is 100%, not 90%."""
    p = package("ME3", "Engine")
    if not p:
        return "skip", "no ME3 Engine.pcc"
    t = mele_tables.build(p)
    enums = [e for e in t["exports"]
             if mele_tables.describe_index(e["class_index"], t["exports"], t["imports"])
             == "Core.Enum"]
    if len(enums) < 100:
        return "fail", "only %d enums found" % len(enums)
    closed = sum(1 for e in enums if mele_tables.enum_members(t, t["buf"], e))
    if closed != len(enums):
        return "fail", "%d/%d enums closed; the stride or count offset is wrong" % (
            closed, len(enums))
    return "ok", "%d/%d enums closed" % (closed, len(enums))


@case("AddLocalPlayer's bytecode references GamePlayers, above chance by structure")
def t_addlocalplayer_refs():
    """A HEURISTIC result, corroborated rather than trusted.

    Scanning a function's bytecode for dwords that land on export indices finds real references -
    and also noise: 10.9% of random 4-byte windows in ME3's Engine.pcc match an export index, so a
    ~13-hit list has ~1.4 expected by chance. Count alone is therefore weak.

    What makes this credible is STRUCTURE, not count: the three parameters of `AddLocalPlayer`
    appear three times each at distinct offsets (read for the comparison, read for the insert, read
    for the return), and `GamePlayers` appears four times - the array `CreatePlayer`'s string
    constants independently name ("inserted into GamePlayers array at index"). Two unrelated sources
    agreeing is evidence; a hit list is not.
    """
    p = package("ME3", "Engine")
    if not p:
        return "skip", "no ME3 Engine.pcc"
    t = mele_tables.build(p)
    hit = [e for e in t["exports"] if e["object_name"] == "AddLocalPlayer"]
    if not hit:
        return "fail", "no AddLocalPlayer"
    refs, baseline = mele_tables.bytecode_refs(t, t["buf"], hit[0])
    names = [r[2] for r in refs]
    if "GamePlayers" not in names:
        return "fail", "GamePlayers is not referenced; got %s" % sorted(set(names))
    # Every parameter must appear more than once: a single occurrence would be consistent with a
    # chance match, three is not.
    for param in ("InsertIndex", "NewPlayer"):
        n = names.count(param)
        if n < 2:
            return "fail", "%s appears %d time(s); expected a read and a write" % (param, n)
    if baseline >= 0.5:
        return "fail", "baseline %.2f is implausibly high; the sampling is broken" % baseline
    expected = len(refs) * baseline
    if len(refs) < 4 * expected:
        return "ok but surprising", ("%d hits vs %.1f expected by chance; the margin is thin"
                                     % (len(refs), expected))
    return "ok", "%d hits vs %.1f by chance; GamePlayers x%d, params repeated" % (
        len(refs), expected, names.count("GamePlayers"))


@case("the refs heuristic reports its noise rate rather than implying certainty")
def t_refs_reports_baseline():
    p = package("ME3", "Engine")
    if not p:
        return "skip", "no ME3 Engine.pcc"
    t = mele_tables.build(p)
    hit = [e for e in t["exports"] if e["object_name"] == "CreatePlayer"]
    if not hit:
        return "fail", "no CreatePlayer"
    refs, baseline = mele_tables.bytecode_refs(t, t["buf"], hit[0])
    if not (0.0 < baseline < 1.0):
        return "fail", "baseline is %r, outside (0,1)" % baseline
    if not refs:
        return "fail", "no references found in CreatePlayer"
    return "ok", "%d hits, %.1f%% chance baseline reported" % (len(refs), baseline * 100)


@case("the split-screen pane geometry is fully specified offline")
def t_splitscreen_structs():
    """`GameViewportClient`'s direct children include the two structs that define pane layout, and
    a `UScriptStruct` has no native base class, so its field offsets are exact:

        PerPlayerSplitscreenData { SizeX, SizeY, OriginX, OriginY : float }   (0x00, 0x04, 0x08, 0x0C)
        SplitscreenData          { PlayerData : TArray<PerPlayerSplitscreenData> }

    That is the geometry a split-screen implementation has to write, recovered with no game
    running. It is also why "add a second view instead of a player" was wrong: the pane data is
    indexed by local player, as `SPLITSCREEN-PLAN.md` already records.
    """
    want = {"PerPlayerSplitscreenData": [("SizeX", 0x00), ("SizeY", 0x04),
                                         ("OriginX", 0x08), ("OriginY", 0x0C)],
            "SplitscreenData": [("PlayerData", 0x00)]}
    results, skipped = {}, []
    for game in ("ME1", "ME2", "ME3"):
        p = package(game, "Engine")
        if not p:
            skipped.append(game)
            continue
        t = mele_tables.build(p)
        for sname, fields in want.items():
            hit = [e for e in t["exports"] if e["object_name"] == sname]
            if not hit:
                return "fail", "%s: no %s export" % (game, sname)
            got = [(f[0], f[3]) for f in mele_tables.struct_fields(t, t["buf"], hit[0])]
            if got != fields:
                return "fail", "%s %s: %s, expected %s" % (game, sname, got, fields)
        results[game] = len(want)
    if not results:
        return "skip", "no Engine.pcc found (%s)" % ", ".join(skipped)
    return "ok", "4 float offsets + 1 array on %s" % ", ".join(sorted(results))


@case("a class's field NAMES include inherited ones, but its offsets are declared unverified")
def t_class_fields_hedged():
    """Rule 13: a walker that ignores `SuperStruct` cannot prove a field is absent. `Pawn` is
    declared on `Controller`, not `PlayerController`, so the direct-children walk finds 493 fields
    and no `Pawn`.

    The offsets must NOT be presented as usable. Measured against live memory they are wrong by
    different amounts at different points - ME3 needs +0x44 for `Pawn` and +0x90 for `myHUD` - so a
    single anchor cannot rebase them, and `how` says `offsets-unverified`.
    """
    p = package("ME3", "Engine")
    if not p:
        return "skip", "no ME3 Engine.pcc"
    t = mele_tables.build(p)
    hit = [e for e in t["exports"] if e["object_name"] == "PlayerController"]
    if not hit:
        return "fail", "no PlayerController"
    rows, how = mele_tables.class_fields(t, t["buf"], hit[0])
    names = [r[1] for r in rows]
    if "Pawn" not in names:
        return "fail", "Pawn is absent; the super chain is not being walked"
    owner = [r[0] for r in rows if r[1] == "Pawn"][0]
    if owner == "PlayerController":
        return "fail", "Pawn reported as declared on PlayerController; it is on Controller"
    if how != "offsets-unverified":
        return "fail", "how=%r; class offsets must be flagged, not trusted" % (how,)
    if len(rows) < 1000:
        return "fail", "only %d fields; inheritance is not accumulated" % len(rows)
    return "ok", "%d fields, Pawn declared on %s, offsets flagged unverified" % (
        len(rows), owner)


@case("game_dirs() reports each install once, even when two folder spellings resolve to it")
def t_game_dirs_deduped():
    """`_steam_roots` deliberately tries both `SteamLibrary` and `Steamlibrary`. On Windows both
    resolve to the same directory, so this generator used to yield the install TWICE and anything
    walking every root did all its work twice.

    Measured before the fix: `mele_index.build` reported "9892 parsed" for a 4,946-package build -
    exactly 2x, which is what exposed it. After the fix: 4,946. The assertion is on the
    de-duplication, not the count, because the count depends on what is installed.
    """
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("melepkg_probe",
                                        os.path.join(HERE, "melepkg.py"))
    pkg = _ilu.module_from_spec(spec)
    spec.loader.exec_module(pkg)
    roots = list(pkg.game_dirs())
    if not roots:
        return "skip", "no install found"
    keys = [os.path.normcase(os.path.realpath(r)) for r in roots]
    if len(keys) != len(set(keys)):
        return "fail", "%d roots but only %d distinct: %s" % (
            len(keys), len(set(keys)), roots)
    return "ok", "%d distinct root(s)" % len(keys)


@case("coop_targets generates a reproducible map, and the committed one matches")
def t_targets_reproducible():
    """A generated document that does not regenerate identically is worse than a hand-written
    one: every run would show a spurious diff, and a real change would hide in it.

    Two failure modes this catches:
      * nondeterminism - dict iteration, sets, or a timestamp leaking into the output
      * staleness - the committed `TARGETS.generated.md` no longer matching the packages, which
        is exactly how the prose versions drifted four times
    """
    spec = importlib.util.spec_from_file_location(
        "coop_targets", os.path.join(HERE, "coop_targets.py"))
    ct = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ct)
    try:
        first = ct.render_markdown(ct.build_targets())
        second = ct.render_markdown(ct.build_targets())
    except SystemExit:
        return "skip", "install not found"
    if first != second:
        return "fail", "two runs produced different output; the generator is nondeterministic"
    if "GENERATED by tools/melepkg/coop_targets.py" not in first:
        return "fail", "the output does not carry its provenance banner"
    committed = os.path.join(os.path.dirname(HERE), "..", "games",
                             "mass-effect-legendary-edition", "TARGETS.generated.md")
    committed = os.path.abspath(committed)
    if not os.path.isfile(committed):
        return "ok", "deterministic (no committed copy to compare)"
    with open(committed, encoding="utf-8") as f:
        on_disk = f.read()
    if on_disk != first:
        return "fail", ("the committed TARGETS.generated.md is STALE - regenerate it "
                        "(python -I tools/melepkg/coop_targets.py --out <path>)")
    return "ok", "byte-identical across runs and matches the committed copy"


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
