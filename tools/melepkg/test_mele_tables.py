"""Tests for the import-table parser. No game needed.

The import table is what makes a co-op target nameable offline: "this name exists" comes from the
identifier table, but "this object is a `Class` imported from `Core`" needs the table. So the tests
are about the two ways this parse goes wrong silently:

1. **The wrong table start.** The header's `import_offset` points 126 bytes past the real start on
   ME3 and 28 past on ME1/ME2, so trusting it resolves 1 of 4,189 entries. The start is found by
   content instead.
2. **The wrong column order.** ME3 stores the class and object names the other way round from
   ME1/ME2. Reading ME3 with ME1's order gives `object=PlayerController, class=Class` - backwards -
   and is internally consistent enough to look fine.

The acceptance test therefore checks SHAPE, not ranges. Three weaker tests were tried and all pass
on garbage; they are named in the module docstring so they are not tried again.

Cases that need the game install skip cleanly and say which file is missing.

Run: python -I tools/melepkg/test_mele_tables.py
"""
import collections
import os
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


def package(game):
    """Locate SFXGame.pcc for a game via the module's own install resolver."""
    pkg = mele_tables.load_pkg()
    rel = os.path.join("Game", game, "BioGame", "CookedPCConsole", "SFXGame.pcc")
    for root in pkg.game_dirs():
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return p
    return None


# ---------------------------------------------------------------------------------------
@case("the layouts are declared per game, and are not identical")
def t_layouts_differ():
    if len(mele_tables.LAYOUTS) != 3:
        return "fail", "expected layouts for ME1, ME2 and ME3"
    a = mele_tables.LAYOUTS["ME1"]
    c = mele_tables.LAYOUTS["ME3"]
    if a == c:
        return "fail", ("ME1 and ME3 share a layout. They do not: reading ME3 with ME1's order "
                        "gives object/class swapped, which is how that was discovered")
    if a != mele_tables.LAYOUTS["ME2"]:
        return "ok but surprising", "ME1 and ME2 layouts differ; check that is real"
    return "ok", "ME1 == ME2, ME3 rotated (%s vs %s)" % (a, c)


@case("every layout names all four columns and three pads")
def t_layout_shape():
    for game, lay in mele_tables.LAYOUTS.items():
        words = [lay["cls"], lay["outer"], lay["obj"], lay["src"]] + list(lay["pad"])
        if sorted(words) != list(range(7)):
            return "fail", "%s does not use each of the 7 words exactly once: %s" % (game, words)
    return "ok", "3 layouts x 7 words, all distinct"


@case("guess_game reads the game from a path, case and separator insensitively")
def t_guess_game():
    cases = [
        (r"B:\Steam\Game\ME3\BioGame\CookedPCConsole\SFXGame.pcc", "ME3"),
        ("/games/me2/BioGame/CookedPCConsole/SFXGame.pcc", "ME2"),
        ("b:\\steamlibrary\\game\\me1\\x.pcc", "ME1"),
        ("/somewhere/else/SFXGame.pcc", "ME1"),          # documented fallback
    ]
    for path, want in cases:
        got = mele_tables.guess_game(path)
        if got != want:
            return "fail", "%r -> %s, expected %s" % (path, got, want)
    return "ok", "3 recognised + 1 documented fallback"


@case("the shape check rejects a single-value source column")
def t_shape_single_source():
    """The failure that a range check cannot catch: every entry resolving to one name."""
    rows = [{"source_name": "!_Dup", "object_name": "Obj%d" % i} for i in range(100)]
    ok, why = mele_tables._shape_ok(rows)
    if ok:
        return "fail", "accepted a table whose every source is '!_Dup': %s" % why
    return "ok", "rejected: %s" % why


@case("the shape check rejects thousands of distinct sources")
def t_shape_many_sources():
    rows = [{"source_name": "S%d" % i, "object_name": "Obj%d" % i} for i in range(500)]
    ok, why = mele_tables._shape_ok(rows)
    if ok:
        return "fail", "accepted 500 distinct sources"
    return "ok", "rejected: %s" % why


@case("the shape check rejects one object kind")
def t_shape_one_kind():
    rows = ([{"source_name": "Core", "object_name": "Thing"} for _ in range(90)]
            + [{"source_name": "Engine", "object_name": "Other"} for _ in range(10)])
    ok, why = mele_tables._shape_ok(rows)
    if ok:
        return "fail", "accepted a table with only 2 object kinds"
    return "ok", "rejected: %s" % why


@case("the shape check accepts a realistic table")
def t_shape_good():
    rows = ([{"source_name": "Core", "object_name": "K%d" % (i % 40)} for i in range(90)]
            + [{"source_name": "Engine", "object_name": "E%d" % (i % 20)} for i in range(10)])
    ok, why = mele_tables._shape_ok(rows)
    if not ok:
        return "fail", "rejected a realistic table: %s" % why
    return "ok", why


@case("exports are reported as unparsed, with their measurements")
def t_exports_honest():
    st = mele_tables.exports_status({"export_count": 100, "depends_offset": 6800,
                                     "export_offset": 1000})
    if st["parsed"]:
        return "fail", "claims to have parsed exports"
    if st["measured_stride"] != 68:
        return "fail", "stride %r" % st["measured_stride"]
    if not st["closes_exactly"]:
        return "fail", "100 * 68 should close on 6800"
    if st["closure_base"] != 0:
        return "fail", "closure base %r, expected 0" % st["closure_base"]
    if not st.get("why_not") or not st.get("next_step"):
        return "fail", "the gap is not explained"
    return "ok", "unparsed, stride 68, closes, gap explained"


@case("a package that is not ME gives no import table rather than a guessed one")
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
def _build_or_skip(game):
    p = package(game)
    if not p:
        return None, "no %s SFXGame.pcc found (set MELE_GAME_DIR)" % game
    try:
        return mele_tables.build(p), None
    except SystemExit as exc:
        return None, "build refused: %s" % exc


@case("ME1 imports resolve to real packages and pass the shape check")
def t_me1():
    t, why = _build_or_skip("ME1")
    if t is None:
        return "skip", why
    if t["game"] != "ME1":
        return "fail", "detected as %s" % t["game"]
    src = collections.Counter(r["source_name"] for r in t["imports"])
    if "Core" not in src or "Engine" not in src:
        return "fail", "sources are %s" % src.most_common(3)
    if t["import_pads_wrong"] != 0:
        return "fail", "%d entries have a non-zero pad word" % t["import_pads_wrong"]
    return "ok", "%d imports, %s" % (len(t["imports"]), src.most_common(2))


@case("ME2 imports resolve to real packages and pass the shape check")
def t_me2():
    t, why = _build_or_skip("ME2")
    if t is None:
        return "skip", why
    if t["game"] != "ME2":
        return "fail", "detected as %s" % t["game"]
    src = collections.Counter(r["source_name"] for r in t["imports"])
    if "Core" not in src or "Engine" not in src:
        return "fail", "sources are %s" % src.most_common(3)
    return "ok", "%d imports, %s" % (len(t["imports"]), src.most_common(2))


@case("ME3 imports use the rotated layout and still resolve")
def t_me3():
    t, why = _build_or_skip("ME3")
    if t is None:
        return "skip", why
    if t["game"] != "ME3":
        return "fail", "detected as %s" % t["game"]
    if t["layout"] != mele_tables.LAYOUTS["ME3"]:
        return "fail", "used layout %r" % (t["layout"],)
    src = collections.Counter(r["source_name"] for r in t["imports"])
    if "Core" not in src:
        return "fail", "sources are %s" % src.most_common(3)
    return "ok", "%d imports, %s" % (len(t["imports"]), src.most_common(2))


@case("a known engine import reads correctly on every game")
def t_playercontroller():
    """`PlayerController` is an import in all three builds: a Class from Core. If the columns are
    swapped this reads as `object=PlayerController, class=Class`, which is backwards - so this case
    is the direct test of the per-game column order."""
    found = {}
    missing = []
    for game in ("ME1", "ME2", "ME3"):
        t, why = _build_or_skip(game)
        if t is None:
            missing.append(game)
            continue
        hit = [r for r in t["imports"] if r["object_name"] == "PlayerController"]
        if not hit:
            found[game] = "ABSENT"
            continue
        r = hit[0]
        found[game] = "%s/%s" % (r["class_name"], r["source_name"])
    if not found:
        return "skip", "no build available (%s)" % ", ".join(missing)
    bad = {g: v for g, v in found.items() if v != "Class/Core"}
    if bad:
        return "fail", "%s; expected Class/Core everywhere" % bad
    return "ok", "Class/Core on %s" % ", ".join(sorted(found))


def main():
    failures = 0
    skips = 0
    for name, fn in CASES:
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = "fail", "%s: %s" % (type(exc).__name__, exc)
        mark = {"ok": "OK  ", "fail": "FAIL", "skip": "SKIP"}.get(status, "OK  ")
        print("%s %-58s %s" % (mark, name, detail))
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
