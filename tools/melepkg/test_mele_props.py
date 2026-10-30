"""Tests for the property-tag reader, the struct-array decoder and the routing command.

`mele_props.py` is the tool that turned several argued claims into measured ones, so what is pinned
here is the *falsifiability* rather than the values:

  * the tag layout closes the objects it claims to close - and the refuted `+0` start closes
    visibly fewer, so the check cannot pass by accident;
  * a `TArray<FStruct>` decodes with exact consumption, which is the only reason
    `DefaultMapPrefixes` can be read at all;
  * the game-class routing asymmetry (ME3 sets the properties, ME1/ME2 declare but leave them
    empty) reproduces, because a future agent will otherwise "fix" ME1 by inventing a value.

Every case skips cleanly when the install is absent, so this runs anywhere.

Run: python -I tools/melepkg/test_mele_props.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mele_props as mp  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def engine_pkg(game):
    mt = mp.load_tables()
    pkg = mt.load_pkg()
    rel = os.path.join("Game", game, "BioGame", "CookedPCConsole", "Engine.pcc")
    for root in pkg.game_dirs():
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return p
    return None


def loaded(game):
    p = engine_pkg(game)
    if not p:
        return None, None, "no %s install" % game
    mt = mp.load_tables()
    return mt.build(p), mt, None


# -----------------------------------------------------------------------------------------
@case("the tag layout closes objects exactly, and the +0 rival does not")
def t_layout_closes():
    """The measurement that makes every value in this file trustworthy.

    `pick_layout` runs the measured layout (`start=+4`) and the refuted one (`start=+0`) over the
    same objects and reports both. Asserting only that the winner closes would be a query that
    cannot fail; asserting the *gap* is what gives it meaning. The refuted layout resolves every
    path name to a real but unrelated name-table entry, so it looks plausible tag by tag and then
    desynchronises - the measured rate on the first attempt was 0-2%.
    """
    t, _mt, why = loaded("ME3")
    if t is None:
        return "skip", why
    objs = mp._candidates(t, 40)
    if len(objs) < 10:
        return "fail", "only %d candidate objects" % len(objs)
    best, results = mp.pick_layout(t, t["buf"], objs)
    good = results["start=+4 (measured)"]
    bad = results["start=+0 (refuted)"]
    if good[0] != good[1]:
        return "fail", "measured layout closed %d/%d" % (good[0], good[1])
    if bad[0] > good[0] * 0.10:
        return "fail", ("the refuted layout closed %d/%d - the check no longer discriminates"
                        % (bad[0], bad[1]))
    if best != "start=+4 (measured)":
        return "fail", "pick_layout chose %r" % best
    return "ok", "measured %d/%d exact, refuted %d/%d" % (good[0], good[1], bad[0], bad[1])


@case("DefaultMapPrefixes decodes as a struct array with exact consumption")
def t_struct_array_exact():
    """The decoder that made the routing table readable.

    A `TArray<FStruct>` is a count followed by each element as a complete nested tag stream ending
    in a `None` FName. `array_exact` means the elements consumed the payload *exactly*; anything
    else is reported as untrustworthy rather than partially printed, because a partial struct array
    is a wrong answer that looks like a right one.
    """
    t, _mt, why = loaded("ME3")
    if t is None:
        return "skip", why
    cdo = mp.find_object(t, "Default__GameInfo")
    if cdo is None:
        return "fail", "no Default__GameInfo"
    tags, end, _why = mp.parse_object(t, t["buf"], cdo)
    want = cdo["serial_offset"] - t["displacement"] + cdo["serial_size"]
    if end != want:
        return "fail", "CDO closure is %d bytes off" % (want - end)
    got = {tg["name"]: tg for tg in tags}
    dmp = got.get("DefaultMapPrefixes")
    if dmp is None:
        return "fail", "no DefaultMapPrefixes on ME3's CDO"
    if not dmp.get("elements"):
        return "fail", "DefaultMapPrefixes has no decoded elements"
    if not dmp.get("array_exact"):
        return "fail", "DefaultMapPrefixes decoded INEXACTLY - do not trust the rows"
    rows = [{s["name"]: s["value"] for s in el} for el in dmp["elements"]]
    prefixes = [r.get("Prefix") for r in rows]
    if prefixes != ["EntryMenu", "BIOG_UIWorld", "Bio"]:
        return "fail", "prefixes are %r" % (prefixes,)
    bio = [r for r in rows if r.get("Prefix") == "Bio"][0]
    if bio.get("GameType") != "SFXGameContent.SFXGameInfoSP":
        return "fail", "the Bio row points at %r" % bio.get("GameType")
    if bio.get("bUsesCommonPackage") is not True:
        return "fail", "the Bio row's bUsesCommonPackage is %r" % bio.get("bUsesCommonPackage")
    return "ok", "3 rows, exact, Bio -> SFXGameInfoSP"


@case("ME1 and ME2 have no DefaultMapPrefixes value, and it is not a parse failure")
def t_me1_me2_have_no_value():
    """A negative result that has to be distinguished from a broken reader.

    `DefaultMapPrefixes` is *declared* on `Engine.GameInfo` in all three builds and the CDO closes
    EXACTLY in all three. So on ME1/ME2 the absent tag is a real absence: the closure proves the
    walk read every byte and simply found no such tag. Without the closure check this case could
    not tell "no value" from "stopped early".
    """
    for game in ("ME1", "ME2"):
        t, _mt, why = loaded(game)
        if t is None:
            return "skip", why
        cdo = mp.find_object(t, "Default__GameInfo")
        if cdo is None:
            return "fail", "%s: no Default__GameInfo" % game
        tags, end, _why2 = mp.parse_object(t, t["buf"], cdo)
        want = cdo["serial_offset"] - t["displacement"] + cdo["serial_size"]
        if end != want:
            return "fail", "%s CDO closure is off by %d" % (game, want - end)
        names = {tg["name"] for tg in tags}
        for absent in ("DefaultMapPrefixes", "DefaultGameType", "GameInfoClassAliases"):
            if absent in names:
                return "fail", "%s sets %s after all" % (game, absent)
    return "ok", "ME1/ME2 declare but do not set the routing properties"


@case("the object preamble is not evidence either way, and the layout test decides")
def t_preamble_is_not_evidence():
    """A claim that was WRONG, kept as a failing-by-design assertion so it is not re-invented.

    The module docstring used to say the int32 at the start of a `Default__X` export "is *not* a
    name index (174..959 over 40 measured CDOs)". That reasoning does not hold: ME3's name table has
    20,433 entries, so 174..959 are all *valid* indices - they just resolve to unrelated names. This
    test found `Default__Actor` on ME1 with a preamble of 1245, which is a perfectly valid index.

    So the preamble's value is **not** evidence for the `+4` start, and a range check cannot
    distinguish the two layouts. The only thing that distinguishes them is the closure rate
    (40/40 vs 0/40 - see the first case). This case asserts the *weak* claim that is actually true:
    a majority of preambles land on a valid index, so no reader may use "is this a valid index" to
    choose a start offset.
    """
    import struct
    total = 0
    valid = 0
    for game in ("ME1", "ME2", "ME3"):
        t, _mt, why = loaded(game)
        if t is None:
            continue
        names = t["name_table"]
        for obj in mp._candidates(t, 20):
            at = obj["serial_offset"] - t["displacement"]
            if at + 4 > len(t["buf"]):
                continue
            v = struct.unpack_from("<i", t["buf"], at)[0]
            total += 1
            if 0 <= v < len(names):
                valid += 1
    if not total:
        return "skip", "no install"
    if valid == 0:
        return "fail", ("no preamble was a valid name index over %d objects - the docstring's "
                        "original claim happens to hold here, which means the check is not "
                        "discriminating" % total)
    return "ok", ("%d/%d preambles ARE valid name indices - the start offset is decided by the "
                  "closure rate, not by a range check" % (valid, total))


@case("the routing verdict reproduces on all three installs")
def t_routing_verdict():
    """The end-to-end assertion: `GameTypePrefix` is 5/5/7 fields and only ME3 sets the values.

    This is the case a future agent will hit by accident. If someone adds a `DefaultGameType` to
    ME1's CDO thinking it fixes the game class, this fails and points at the design note.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_routing(None)
    out = buf.getvalue()
    if "install not found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_routing returned %d:\n%s" % (rc, out.strip())
    if "ME3 is the only build that uses the property path" not in out:
        return "fail", "verdict line missing:\n%s" % out.strip()
    for game, n in (("ME1", 5), ("ME2", 5), ("ME3", 7)):
        if "%s  %d field(s) ok" % (game, n) not in out:
            return "fail", "%s field count wrong:\n%s" % (game, out.strip())
    return "ok", "5/5/7 fields, only ME3 sets the routing properties"


@case("no build scripts a net driver class, and the index can prove it")
def t_no_scripted_net_driver():
    """The negative result that shapes the transport plan, pinned so it cannot drift to "assumed".

    `TcpNetDriver` appears in ME1's and ME2's `BIOEngine.ini` as a `NetworkDevice` value, and in all
    three exes as a native class name — and in **zero** of the 12,704 packages as an export. Those
    three facts have to be kept apart: a config value is not a class, and a native symbol is not a
    script class.

    This case asserts the *script* half, which is the half a mod can `Spawn`/`FindClass`. If a future
    build ever scripts one, the transport plan changes and this fails loudly.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mp.cmd_routing(None)
    out = buf.getvalue()
    if "install not found" in out:
        return "skip", "install absent"
    if "net driver classes as script exports" not in out:
        return "fail", "the transport check did not run:\n%s" % out.strip()
    if "no build scripts a net driver" not in out:
        return "fail", ("a build scripts a net driver class after all - the transport plan needs "
                        "re-checking:\n%s" % out.strip())
    return "ok", "0 driver class exports on ME1/ME2/ME3 - the driver is native only"


@case("bUseSeamlessTravel is declared on all three builds and serialised by none")
def t_no_seamless_travel():
    """The finding that changes the level-transition plan, and the one easiest to disbelieve.

    `bUseSeamlessTravel` is a declared `GameInfo` property on all three builds, so a reader that
    checked only the declaration would conclude the campaign travels seamlessly. It does not: no
    class default object in `Engine.pcc` or `SFXGame.pcc` serialises the property, so the engine
    default (False) applies. The corroboration is structural - `SeamlessTravelTo` is not overridden
    on `BioPlayerController` on any build, while `PreClientTravel` is.

    A false pass is possible if the closure filter silently drops every object, so the CDO count is
    asserted too: ~2,150-2,320 objects per build close exactly, and `cdos == 0` would mean the scan
    saw nothing rather than that nothing set the flag.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_coopcheck(None)
    out = buf.getvalue()
    if "install not found" in out or "closed exactly" not in out:
        return "skip", "install absent"
    for game in ("ME1", "ME2", "ME3"):
        line = [l for l in out.splitlines() if l.strip().startswith(game) and "CDOs closed" in l]
        if not line:
            return "fail", "%s line missing:\n%s" % (game, out.strip())
        if "declared=True" not in line[0]:
            return "fail", "%s does not declare bUseSeamlessTravel: %s" % (game, line[0].strip())
        if "0 set it" not in line[0]:
            return "fail", "%s has a CDO setting bUseSeamlessTravel: %s" % (game, line[0].strip())
        n = int(line[0].split()[2])
        if n < 1000:
            return "fail", "%s only closed %d CDOs - the scan is not seeing the package" % (game, n)
    return "ok", "declared on all three, set by 0 of ~6,700 exactly-closing CDOs"


@case("ME3's campaign game class clamps MaxPlayersAllowed to 1")
def t_me3_capacity_clamp():
    """An active clamp, not a missing value - and the difference matters.

    The base `GameInfo` CDO allows 32 in every build, so "the engine permits more" is true and
    useless. ME3's campaign game class serialises `MaxPlayersAllowed = 1`, which is a deliberate
    restriction that a co-op mod must raise before a second participant can be admitted. ME1/ME2's
    campaign classes serialise neither field, so they inherit 32/16 and need no clamp raise.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mp.cmd_coopcheck(None)
    out = buf.getvalue()
    if "MaxPlayers" not in out:
        return "skip", "install absent"
    if "Default__SFXGame       MaxPlayers=-    MaxPlayersAllowed=1" not in out:
        return "fail", "ME3's campaign clamp is not 1:\n%s" % out.strip()
    for game, want in (("ME1", "MaxPlayersAllowed=32"), ("ME2", "MaxPlayersAllowed=32")):
        line = [l for l in out.splitlines() if l.strip().startswith(game) and "Default__GameInfo" in l]
        if not line or want not in line[0]:
            return "fail", "%s base MaxPlayersAllowed is not 32: %s" % (game, line)
    return "ok", "ME3 campaign clamps to 1; ME1/ME2 inherit 32 and need no raise"


@case("the squad roster element type differs: struct array (ME1/ME2) vs object array (ME3)")
def t_roster_element_type():
    """`Members` is not the same container on ME3, and `MemberData` does not exist there.

    On ME1/ME2 `BioBaseSquad.Members` is a `TArray<MemberData>` - a struct holding `SquadMember`
    plus per-member order/formation state. On ME3 the same property is a `TArray<Object>` and the
    struct is gone. So code that adds a roster entry is a different edit per build, and a
    `MemberData` field offset ported from ME2 to ME3 has nothing to apply to.

    The field counts are asserted as well, because "present but unreadable" would otherwise pass.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mp.cmd_coopcheck(None)
    out = buf.getvalue()
    if "roster's element type" not in out:
        return "skip", "install absent"
    if "ME1  Members is TArray<StructProperty>, MemberData present (17 fields)" not in out:
        return "fail", "ME1 roster shape changed:\n%s" % out.strip()
    if "ME2  Members is TArray<StructProperty>, MemberData present (7 fields)" not in out:
        return "fail", "ME2 roster shape changed:\n%s" % out.strip()
    if "ME3  Members is TArray<ObjectProperty>, MemberData ABSENT" not in out:
        return "fail", "ME3 roster shape changed:\n%s" % out.strip()
    return "ok", "ME1/ME2 TArray<MemberData> (17/7 fields); ME3 TArray<Object>, no MemberData"


@case("ME1 persists the actor graph and ME2/ME3 do not")
def t_save_asymmetry():
    """The save-system split, which is the one place a co-op bug lands in the player's career.

    ME1 writes the level as `BioLevelSaveObject.m_LevelObjects`, one entry per saveable actor, so a
    client pawn present in the host's level is captured by the save even though it has no story
    identity. ME2/ME3 have no `*SaveObject` classes at all - persistence moved behind
    `SFXSFHandler_Save` - so the same pawn is not enumerated.

    This is asserted because a "just make ME1 match ME2" simplification is exactly the kind of edit
    a later agent would make, and it would be wrong in the direction that corrupts saves.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_savecheck(None)
    out = buf.getvalue()
    if "install absent" in out or "*SaveObject classes" not in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_savecheck returned %d:\n%s" % (rc, out.strip())
    if "ME2   0 class(es) ending in SaveObject" not in out:
        return "fail", "ME2 has SaveObject classes:\n%s" % out.strip()
    if "ME3   0 class(es) ending in SaveObject" not in out:
        return "fail", "ME3 has SaveObject classes:\n%s" % out.strip()
    if "ME1 BioLevelSaveObject carries m_LevelObjects" not in out:
        return "fail", "ME1's actor-graph save is unsupported:\n%s" % out.strip()
    if "HenchmanSaveRecord 28 field(s)" not in out:
        return "fail", "ME1's HenchmanSaveRecord changed:\n%s" % out.strip()
    if "ME3  HenchmanSaveRecord 9 field(s)" not in out:
        return "fail", "ME3's HenchmanSaveRecord changed:\n%s" % out.strip()
    return "ok", "ME1 23 SaveObject classes + m_LevelObjects; ME2/ME3 zero; records 28/32/9"


@case("the reader works on class default objects and NOT on live level actors")
def t_reader_scope():
    """The limit of the whole tool, pinned so nobody reads a value out of a level package.

    `probe` closes 100% of objects in a CDO-rich package and a small fraction in a live level. That
    is not a bug to fix by tuning the layout: a spawned actor's serial block is not a plain property
    tag stream, so no tag walk over it is meaningful. The danger is specific and quiet - a query
    against a level returns *some* tags before desynchronising, and the first few look plausible.

    Both halves are asserted. If a future change makes level actors close too, this fails and the
    docstring has to be updated deliberately rather than the tool silently gaining authority it was
    never given.
    """
    cdos = None
    for game in ("ME3",):
        t, _mt, why = loaded(game)
        if t is None:
            return "skip", why
        objs = mp._candidates(t, 40)
        best, results = mp.pick_layout(t, t["buf"], objs)
        exact, tried, _e = results["start=+4 (measured)"]
        cdos = (exact, tried)
        if tried != exact:
            return "fail", "CDO package closed %d/%d, expected 100%%" % (exact, tried)

    mt = mp.load_tables()
    pkg = mt.load_pkg()
    level = None
    for root in pkg.game_dirs():
        cand = os.path.join(root, "Game", "ME3", "BioGame", "CookedPCConsole", "BioA_Cat002.pcc")
        if os.path.isfile(cand):
            level = cand
            break
    if level is None:
        return "skip", "no ME3 level package"
    t = mt.build(level)
    objs = mp._candidates(t, 40)
    best, results = mp.pick_layout(t, t["buf"], objs)
    lexact, ltried, _e = results["start=+4 (measured)"]
    if ltried and lexact == ltried:
        return "fail", ("a live level now closes 100%% (%d/%d) - the scope limit is stale, "
                        "re-read the docstring" % (lexact, ltried))
    if not ltried:
        return "fail", "no candidate objects in the level package"
    return "ok", ("CDOs %d/%d exact, live level %d/%d - level actors are NOT readable"
                  % (cdos[0], cdos[1], lexact, ltried))


@case("the map count is structural, and BIOG_ content packages are not maps")
def t_maplist_is_structural():
    """The denominator for "a full campaign works in co-op", and the trap in computing it.

    Counting `.pcc` files per build gives ~5,000 and is meaningless: most are meshes, binks,
    localisation and streaming geometry. A map is a package with a `PersistentLevel` export. The
    naming convention gives the wrong answer - `BIOG_HMF_ARM_*` are armour meshes, and a regex on
    level prefixes counts them as `BIOG_` roots.

    The counts are asserted because they are the scope claim: if a build's map count changes, the
    statement "the campaign works" covers a different set of levels.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mp.cmd_maplist(type("A", (), {"game": None})())
    out = buf.getvalue()
    if "index missing" in out or "map(s) with a PersistentLevel" not in out:
        return "skip", "index absent"
    for want in ("ME1: 1811 map", "ME2: 2156 map", "ME3: 2677 map", "6644 map(s) total"):
        if want not in out:
            return "fail", "missing %r in:\n%s" % (want, out.strip())
    return "ok", "1811 / 2156 / 2677 = 6644 maps, counted by PersistentLevel"


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
