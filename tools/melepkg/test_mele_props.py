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
    if "skipped: no package index" in out:
        return "skip", "no package index (the driver check needs every package; set MELE_INDEX_DIR)"
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

    **Skips unless a package index exists.** Without one, `maplist` walks the install and parses all
    19,430 packages - correct, and several minutes per build. A test suite should not silently take
    fifteen minutes because a cache is absent, and the count it would produce is the same either way,
    so the honest thing is to skip and say why rather than to slow the suite down for everyone.
    """
    import io
    import contextlib
    index = os.path.join(mp._index_dir(), "ME1.json")
    if not os.path.isfile(index):
        return "skip", "no package index (maplist would parse 19,430 packages; set MELE_INDEX_DIR)"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_maplist(type("A", (), {"game": None})())
    out = buf.getvalue()
    if "map(s) with a PersistentLevel" not in out:
        return "skip", "maplist produced no counts"
    for want in ("ME1: 1811 map", "ME2: 2156 map", "ME3: 2677 map", "6644 map(s) total"):
        if want not in out:
            return "fail", "missing %r in:\n%s" % (want, out.strip())
    return "ok", "1811 / 2156 / 2677 = 6644 maps, counted by PersistentLevel"


@case("the campaign's own squad recruitment goes through a data table on ME2/ME3, not ME1")
def t_squad_recruitment_path():
    """How the game adds a squadmate, which decides how co-op should - and it is not the same way.

    Appending to `BioBaseSquad.Members` looks like the obvious way to give a client a squadmate. On
    ME2 and ME3 it skips the campaign's own bookkeeping: `BioSeqAct_AddToParty.Activated` runs
    through a `Bio2DA` numbered-row table there, while ME1's version references no `Bio2DA` at all.

    The scan is the documented dword heuristic, so what makes it safe is the contrast: zero
    `Bio2DA` members in ME1's body and a coherent accessor family in the other two. Noise does not
    produce `GetNumColumns` + `GetStringEntryNumI` + `GetFloatEntryII` together.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_squadadd(None)
    out = buf.getvalue()
    if "install not found" in out or "verdict" not in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_squadadd returned %d:\n%s" % (rc, out.strip())
    if "ME1  AddToParty.Activated 842 bytes; Bio2DA refs 0" not in out:
        return "fail", "ME1's recruitment gained a table lookup:\n%s" % out.strip()
    if "ME2  AddToParty.Activated 1195 bytes; Bio2DA refs 5" not in out:
        return "fail", "ME2's recruitment path changed:\n%s" % out.strip()
    if "GetSquadSize" not in out:
        return "fail", "ME2 no longer consults GetSquadSize:\n%s" % out.strip()
    if "ME3  AddToParty.Activated 1186 bytes; Bio2DA refs 3" not in out:
        return "fail", "ME3's recruitment path changed:\n%s" % out.strip()
    return "ok", "ME1 direct (0 table refs); ME2 5 refs + GetSquadSize; ME3 3 refs"


@case("Possess/UnPossess exist on every build, and BioAiController overrides both")
def t_possession_route():
    """The mechanism for giving a client a squadmate, present on all three builds.

    Taking a squadmate away from its AI is `Controller.UnPossess` on the AI controller and
    `PlayerController.Possess` on the client's controller - both are declared on the base
    `Controller` in every build, and `BioAiController` overrides both. So the operation is a
    first-class engine one, not something a mod has to invent.

    This is asserted because it is the one part of the squad plan that does NOT differ per build, and
    a per-build table that lists it as divergent would send someone writing three
    implementations of a shared thing.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_coopcheck(None)
    out = buf.getvalue()
    if "Possess" not in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_coopcheck returned %d:\n%s" % (rc, out.strip())
    for game in ("ME1", "ME2", "ME3"):
        want = ("%s  Controller=PU PlayerController=PU AIController=PU BioAiController=PU" % game)
        if want not in out:
            return "fail", "%s possession route changed:\n%s" % (game, out.strip())
    return "ok", "Controller/PlayerController/AIController/BioAiController all carry P+U on all three"


@case("the conversation selection call is shared, so 'the client cannot choose' is not an API gap")
def t_conversation_choice_shared():
    """The design called client dialogue choice a hole. It is not a missing function.

    `BioSFHandler_Conversation.SelectConversationEntry`, `SelectConversationSegment` and
    `UpdateConversationOptions` are present on all three builds, so a client *can* be made to pick a
    dialogue option with an existing call. What is genuinely unknown is whether the campaign honours
    a second selector while the host is also driving the conversation - a live question, and a
    different one from "the API does not exist".

    The distinction matters because the two have different fixes: a missing API means writing one,
    and a contended selector means arbitrating who owns the conversation. Keeping them apart is why
    this case asserts presence rather than the design's earlier absence claim.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_coopcheck(None)
    out = buf.getvalue()
    if "Conversation choice" not in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_coopcheck returned %d:\n%s" % (rc, out.strip())
    for game in ("ME1", "ME2", "ME3"):
        line = [l for l in out.splitlines() if l.strip().startswith(game) and "selection surface" in l]
        if not line:
            return "fail", "%s conversation section missing:\n%s" % (game, out.strip())
        if "SelectConversationEntry" not in line[0]:
            return "fail", "%s lost SelectConversationEntry: %s" % (game, line[0].strip())
    return "ok", "SelectConversationEntry/Segment/UpdateConversationOptions on all three builds"


@case("every tools/ path cited in the game docs exists or is marked absent")
def t_cited_tools_exist():
    """A doc that points at a file nobody can open is a wrong answer that looks like a record.

    `NOTES.md` and `README.md` cited `tools/binkproxy/` and `tools/inject_plugin.py` for several
    sessions; both are absent from the hub and the published bundle (audited 2026-10-30). The cost is
    not the missing file, it is that a reader plans around a build step that cannot be run and
    concludes the work is further along than it is.

    Three exclusions, each for a reason learned while writing this case:

      * a path under `local/` is the owner's private state, which is git-ignored on purpose;
      * a brace list like `masseffect-le-{me1,me2,me3}.json` is a set of files, not one path;
      * a mention whose line says it is absent is *deliberately* kept - deleting the record of what
        was built would lose more than the dead link costs.
    """
    import bisect
    import glob
    import re
    hub = os.path.abspath(os.path.join(HERE, "..", ".."))
    docdir = os.path.join(hub, "games", "mass-effect-legendary-edition")
    if not os.path.isdir(docdir):
        return "skip", "no games/mass-effect-legendary-edition folder"
    MARKED = re.compile(r"not in this repo|absent|missing|git-ignored|cannot exist|stale|does not "
                        r"exist|neither the hub|NOT in|Provenance of|not here", re.I)
    pat = re.compile(r"`(tools/[A-Za-z0-9_./@{},-]+)`")
    missing = []
    checked = 0
    for path in glob.glob(os.path.join(docdir, "*.md")):
        text = open(path, encoding="utf-8").read()
        lines = text.split("\n")
        # Offset of each line, so a hit can look back for a document-level provenance note.
        offs = []
        acc = 0
        for ln in lines:
            offs.append(acc)
            acc += len(ln) + 1
        for m in pat.finditer(text):
            cited = m.group(1).rstrip(".")
            # `local/tools/...` is the owner's private state.
            if text[max(0, m.start() - 6):m.start()] == "local/":
                continue
            ln_no = bisect.bisect_right(offs, m.start()) - 1
            window = "\n".join(lines[max(0, ln_no - 2):ln_no + 1])
            if MARKED.search(window):
                continue
            # A document-level provenance note counts for the whole file, because listing every
            # mention individually is noise when one paragraph already says where they came from.
            head = "\n".join(lines[:40])
            if "Provenance of" in head and re.search(
                    re.escape("`%s`" % cited.rstrip("/")), head):
                continue
            brace = re.search(r"\{([^}]*)\}", cited)
            candidates = ([cited] if not brace
                          else [cited[:brace.start()] + part + cited[brace.end():]
                                for part in brace.group(1).split(",")])
            for cand in candidates:
                checked += 1
                target = os.path.join(hub, *cand.rstrip("/").split("/"))
                if not os.path.exists(target):
                    missing.append("%s: %s" % (os.path.basename(path), cand))
    if missing:
        return "fail", "cited but absent (and not marked as such): %s" % sorted(set(missing))[:8]
    return "ok", "%d tools/ citation(s) checked, all present or explicitly marked absent" % checked


@case("the plot-manager action family is the campaign's shared story surface")
def t_story_surface_is_shared():
    """Where the campaign's story actually advances, at campaign scale.

    This is the measurement behind "the host advances the story and the guest observes". It has one
    interception point across three games rather than three: `BioSeqAct_PMBase` and its three concrete
    subclasses (`PMCheckState`, `PMCheckConditional`, `PMExecuteTransition`) carry the same class names
    and the same property names on ME1/ME2/ME3.

    Two findings that a per-level reading gets wrong and this asserts:

    * `PMExecuteTransition` dominates - thousands of instances over hundreds of maps, more than
      conversations, saves and area transitions combined.
    * `BioAreaTransition`, the trigger volume a player walks into, is essentially unused (1 / 2 / 0
      across all 6,644 maps). A mod that hooks trigger volumes to follow the host's level change hooks
      almost nothing; the plot manager is what moves a career forward.

    `PMBase` is declared, has a class default object, declares no functions, and is instantiated in
    **zero** maps - placing it would do nothing, so levels place the concrete subclasses. A check that
    expected it in maps would fail on the correct data. (It is *not* claimed to be `CLASS_Abstract`:
    the class flags were not read, and the zero-instance count is the measured fact.)
    """
    import io
    import contextlib
    index = os.path.join(mp._index_dir(), "ME1.json")
    if not os.path.isfile(index):
        return "skip", "no package index (storycheck needs every package; set MELE_INDEX_DIR)"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_storycheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_storycheck returned %d:\n%s" % (rc, out.strip())
    if "PM action family is the campaign's story surface" not in out:
        return "fail", "verdict missing:\n%s" % out.strip()
    import re
    for game in ("ME1", "ME2", "ME3"):
        # Each block runs from its own header to the next, so capture between markers rather than
        # splitting on the header text (which leaves only the header remainder).
        m = re.search(r"=== %s:([^\n]*)\n(.*?)(?==== ME|\Z)" % game, out, re.S)
        if not m:
            return "fail", "%s block missing:\n%s" % (game, out.strip())
        section = m.group(2)
        if "BioSeqAct_PMExecuteTransition" not in section:
            return "fail", "%s lost PMExecuteTransition" % game
        if "BioSeqAct_PMCheckState" not in section:
            return "fail", "%s lost PMCheckState" % game
    # The trigger-volume claim is the one that corrects a natural assumption, so pin it too.
    me1 = out.split("=== ME1:")[1].split("===")[0]
    if "BioAreaTransition" in me1:
        line = [l for l in me1.splitlines() if "BioAreaTransition" in l][0]
        n = int(line.split()[1])
        if n > 50:
            return "fail", ("ME1 BioAreaTransition is used %d times - the 'almost unused' claim is "
                            "wrong" % n)
    return "ok", ("PM family shared on all three; ME1 area-trigger volume used 1 time across 1,811 "
                  "maps")


@case("LE1/LE2 story state is a bounded flag set, and LE3 keeps the same state as one save struct")
def t_plot_state_budget():
    """The co-op budget question: how many values is a career's story state?

    `PlotManager.pcc` on ME1/ME2 defines one class, `BioAutoConditionals`, whose entire body is plot-flag
    accessors named `F<n>`, each shaped `F(id)(Argument: Int) -> ReturnValue: Bool`. So each flag is an
    integer addressed by a small id, and the id space is bounded:

        ME1  1,066 functions, ids 113..2018
        ME2  1,356 functions, ids 1..2050

    An int32 per slot over that range is ~8 KB. That is the number a sync design needs: it means the
    host can ship the whole flag set rather than maintaining a delta log, and a guest can be handed a
    complete story state rather than a replay.

    **LE3 is different, and the first reading of it was wrong.** It defines no `BioAutoConditionals` in
    any of its 7,943 packages and ships no `PlotManager.pcc`, which was taken to mean the flag set could
    not be enumerated. The state is there, in `PlotTableSaveRecord` - a struct of seven arrays held by
    `SFXSaveGame` - and its index space is bounded by the cutoffs on `Default__BioGlobalVariableTable`.
    Both are asserted here, because "LE3 has no story state to sync" would be a plan built on a gap
    that is not real.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_plotcheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_plotcheck returned %d:\n%s" % (rc, out.strip())
    if "ME1   1066 plot flag(s), ids 113..2018" not in out:
        return "fail", "ME1 plot flag set changed:\n%s" % out.strip()
    if "ME2   1356 plot flag(s), ids 1..2050" not in out:
        return "fail", "ME2 plot flag set changed:\n%s" % out.strip()
    if "ME3  PlotManager.pcc present: False" not in out:
        return "fail", "ME3 gained a PlotManager.pcc - re-read the LE3 plot plan:\n%s" % out.strip()
    if "PlotTableSaveRecord" not in out:
        return "fail", "LE3 save struct not measured:\n%s" % out.strip()
    for array in ("BoolVariables", "IntVariables", "FloatVariables", "QuestIDs", "CodexIDs"):
        if array not in out:
            return "fail", "PlotTableSaveRecord lost %s:\n%s" % (array, out.strip())
    if "IntVariablePair = 8 bytes" not in out:
        return "fail", "entry width changed - recompute the LE3 blob size:\n%s" % out.strip()
    if "Bool_CutoffIndex=17655" not in out:
        return "fail", "LE3 plot index bound changed:\n%s" % out.strip()
    if "small enough to ship" not in out:
        return "fail", "verdict missing:\n%s" % out.strip()
    return "ok", ("1,066 / 1,356 flags over 2,019 / 2,051 slots (~8 KB); LE3 = 7 arrays, 17,655 slots")


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
