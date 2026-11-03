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


def _pkg_dirs():
    return mp.load_tables().load_pkg().game_dirs()


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


@case("the squad roster element differs: a MemberData record (ME1/ME2) vs the Pawn itself (ME3)")
def t_roster_element_type():
    """`Members` is not the same container on ME3, and `MemberData` does not exist there.

    On ME1/ME2 `BioBaseSquad.Members` is a `TArray<MemberData>` - a struct holding `SquadMember`
    plus per-member order/formation state. On ME3 the same property is a `TArray<Pawn>`: the element
    class resolves, via `property_class`, to the imported `Pawn` class, so a roster entry there **is**
    the pawn rather than a description of one. A `MemberData` field offset ported from ME2 to ME3 has
    nothing to apply to.

    The store differs, but `rostercheck` (a separate case) pins that the call in front of it does not.

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
    if "element class: Pawn (import)" not in out:
        return "fail", "ME3 roster element is no longer Pawn:\n%s" % out.strip()
    return "ok", "ME1/ME2 TArray<MemberData> (17/7 fields); ME3 TArray<Pawn>, no MemberData"


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


@case("the join path is script on every build, and LE3 still has a guest-pawn spawn")
def t_join_chain_is_script():
    """Whether accepting a second player needs a native patch or a class override.

    The UE3 join chain is implemented in script on `Engine.GameInfo`, so a mod can decide about a
    second player in script on all three builds. That is the difference between "co-op is a class
    override" and "co-op is a binary patch", so it is pinned rather than assumed.

    On LE3 the campaign game class is `SFXGameInfoSP`, which inherits the multiplayer machinery
    `SFXGame` still carries - teams, a player count, join-in-progress delegates and
    `SpawnNewPlayerPawn`, which takes a `PlayerController` and a `PlayerArchetype` and returns a pawn.
    The guest pawn therefore has an existing, script-visible spawn path; what is missing is a caller
    that accepts a second player.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_joincheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_joincheck returned %d:\n%s" % (rc, out.strip())
    for game in ("ME1", "ME2", "ME3"):
        line = [l for l in out.splitlines() if l.strip().startswith(game) and "9/9 in script" in l]
        if not line:
            return "fail", "%s join chain is not fully in script:\n%s" % (game, out.strip())
        for fn in ("Login", "AtCapacity", "RestartPlayer"):
            if fn not in line[0]:
                return "fail", "%s lost %s from its join chain" % (game, fn)
    if "SFXGameInfoSP lives in BIO_COMMON.pcc" not in out:
        return "fail", "LE3 campaign game class moved:\n%s" % out.strip()
    if "MPContent" not in out:
        return "fail", "the MPContent provenance of BIO_COMMON.pcc is gone:\n%s" % out.strip()
    if "SpawnNewPlayerPawn: 17 parameter(s)" not in out:
        return "fail", "SpawnNewPlayerPawn changed shape:\n%s" % out.strip()
    return "ok", "9/9 chain functions in script x3; LE3 SpawnNewPlayerPawn(17 params, 0x90)"


@case("a guest pawn needs only a character description on LE2/LE3, and LE1 has no such path")
def t_spawn_payload():
    """What "the client takes a squadmate" actually requires, measured rather than assumed.

    Two payloads and one function decide it:

    * `PlayerInfoEx` is the player identity block - `FirstName`, `FaceCode`, `CharacterClass`,
      `MorphHead`, `BonusTalentClass`, `bIsFemale`, `Origin`, `Notoriety`, and `CharacterGUID` on LE3.
      Seven, eight and nine fields respectively.
    * `PlotTableSaveRecord` is the plot save payload, and it is the **same struct on all three builds**:
      seven arrays plus a `QuestProgressCounter`. It is held only by `SFXSaveGame.PlotRecord` everywhere
      and by no function parameter, so it is read and written through the save object.

    And the campaign's own spawn takes the identity block on LE2/LE3 but not on LE1, which is the
    per-build difference in effort that this test exists to keep visible.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_spawncheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_spawncheck returned %d:\n%s" % (rc, out.strip())
    for game, count in (("ME1", 7), ("ME2", 8), ("ME3", 9)):
        line = [l for l in out.splitlines() if l.strip().startswith(game) and "PlayerInfoEx" in l]
        if not line:
            return "fail", "%s PlayerInfoEx row missing:\n%s" % (game, out.strip())
        if "%2d field(s)" % count not in line[0]:
            return "fail", "%s PlayerInfoEx field count changed: %s" % (game, line[0].strip())
    for game in ("ME1", "ME2", "ME3"):
        line = [l for l in out.splitlines() if l.strip().startswith(game) and "PlotTableSaveRecord" in l]
        if not line:
            return "fail", "%s plot payload row missing" % game
        for field in ("BoolVariables", "IntVariables", "FloatVariables", "QuestIDs", "CodexIDs"):
            if field not in line[0]:
                return "fail", "%s plot payload lost %s" % (game, field)
        holder = [l for l in out.splitlines()
                  if l.strip().startswith(game) and "PlotRecord" in l]
        if not holder or "SFXSaveGame.PlotRecord" not in holder[0]:
            return "fail", "%s plot payload is not held by SFXSaveGame.PlotRecord" % game
    if "ME1  BioSPGame .SpawnDefaultPawnFor  5 param(s), parms 0x28, PlayerInfo: False" not in out:
        return "fail", "LE1 campaign spawn changed shape:\n%s" % out.strip()
    if "ME3  SFXGame   .SpawnDefaultPawnFor 33 param(s), parms 0xf4, PlayerInfo: True" not in out:
        return "fail", "LE3 campaign spawn changed shape:\n%s" % out.strip()
    if "ME2  SFXGame   .SpawnDefaultPawnFor 29 param(s), parms 0xdc, PlayerInfo: True" not in out:
        return "fail", "LE2 campaign spawn changed shape:\n%s" % out.strip()
    return "ok", "PlayerInfoEx 7/8/9 fields; plot payload 1 struct, 1 holder; LE2/LE3 spawn take it"


@case("the roster stores a record on LE1/LE2 and the pawn itself on LE3, behind one shared call")
def t_roster_store_and_api():
    """`Members` differs per build, and that reads like three different edits. It is one.

    The store: `TArray<MemberData>` on LE1/LE2, `TArray<Pawn>` on LE3 - `MemberData` is absent from LE3
    entirely, and its roster entry *is* the pawn.

    The API in front of the store: `AddMember(Pawn) -> int` on every build, and `RemoveMember(pPawn) ->
    bool`. So adding a guest is the same call everywhere; only the engine's array code differs. What
    does differ per build is notification and control transfer - `MemberAdded` exists on LE1 only, LE1
    alone has a spare-controller trio, LE3 alone has `SetPlayerPawn`.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_rostercheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_rostercheck returned %d:\n%s" % (rc, out.strip())
    for game in ("ME1", "ME2"):
        line = [l for l in out.splitlines()
                if l.strip().startswith(game) and "Members is" in l]
        if not line or "element struct MemberData" not in line[0]:
            return "fail", "%s roster store changed:\n%s" % (game, out.strip())
    me3 = [l for l in out.splitlines() if l.strip().startswith("ME3") and "Members is" in l]
    if not me3 or "element class Pawn" not in me3[0]:
        return "fail", "LE3 roster no longer holds Pawns:\n%s" % out.strip()
    if "BioBaseSquad   AddMember            (Pawn" not in out:
        return "fail", "AddMember no longer starts with a Pawn:\n%s" % out.strip()
    if "ME1  MemberAdded(notification); spare-controller slot" not in out:
        return "fail", "LE1 notification/spare-controller surface changed:\n%s" % out.strip()
    if "ME3  no MemberAdded - adding a member is silent; SetPlayerPawn" not in out:
        return "fail", "LE3 surface changed:\n%s" % out.strip()
    return "ok", "MemberData on LE1/LE2 vs Pawn on LE3; AddMember(Pawn) on all three"


@case("a guest shares the host's spawn point, because most maps have none or exactly one")
def t_guest_has_nowhere_of_its_own():
    """Section 11: where a second player could stand.

    A first pass at this counted only `PlayerStart` and reported zero spawn points in 94-99% of maps,
    which looked alarming and was measuring the wrong class. The campaign spawns from
    **`BioStartLocation`** (plus `BioStartPoint` on LE1/LE2), and most packages are streaming sublevels
    that never receive a player at all.

    The number that matters is the shape of the distribution over maps that *do* have a spawn point, and
    LE3 is the tight one: exactly one in ~60% of them. A guest therefore cannot expect a start of its
    own, which is what `SpawnDefaultPawnFor`'s `bNoCollisionFail` parameter is for.
    """
    import io
    import contextlib
    import os
    index_dir = mp._index_dir()
    if not os.path.isfile(os.path.join(index_dir, "ME1.json")):
        return "skip", "no package index (startcheck needs every map; set MELE_INDEX_DIR)"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_startcheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_startcheck returned %d:\n%s" % (rc, out.strip())
    import re
    lines = out.splitlines()
    for game in ("ME1", "ME2", "ME3"):
        idx = [i for i, l in enumerate(lines) if l.startswith(game)]
        if not idx:
            return "fail", "%s row missing:\n%s" % (game, out.strip())
        # The summary line is followed by the "by class:" line, which is indented.
        following = lines[idx[0] + 1] if idx[0] + 1 < len(lines) else ""
        if "BioStartLocation" not in following:
            return "fail", "%s no longer defines its spawn class as BioStartLocation:\n%s" % (
                game, out.strip())
    counts = {}
    for game in ("ME1", "ME2", "ME3"):
        line = [l for l in out.splitlines() if l.startswith(game)][0]
        maps = int(re.search(r"(\d+) map\(s\)", line).group(1))
        with_start = int(re.search(r"(\d+) with a spawn point", line).group(1))
        counts[game] = (maps, with_start)
        if with_start == 0 or with_start > maps:
            return "fail", "%s spawn-point counts are impossible: %s" % (game, line)
    if counts["ME3"][0] < 2000:
        return "fail", "LE3 map count collapsed: %s" % (counts["ME3"],)
    if "SpawnDefaultPawnFor's bNoCollisionFail" not in out:
        return "fail", "verdict missing the bNoCollisionFail consequence:\n%s" % out.strip()
    return "ok", ("spawn points are BioStartLocation; LE3 %d of %d maps have one, and mostly just one"
                  % (counts["ME3"][1], counts["ME3"][0]))


@case("the campaign's replication info is multiplayer-shaped on LE3 only, and its flags are local")
def t_gri_surface():
    """What the campaign's game-replication-info carries, and what actually replicates.

    `Default__SFXGameInfoSP` sets one property, `GameReplicationInfoClass=SFXGRISP`, and `SFXGRISP` is a
    zero-override alias of `SFXGRI` - 35 properties, 32 script functions, CDO closing exactly. That is a
    multiplayer-shaped state bag shipped inside a single-player campaign.

    The claim worth pinning is the negative: of those 35 properties exactly four carry `CPF_Net`, and
    none of them is a capability flag. So `bCanSave` / `bCanShowJournal` / `bCanShowCodex` /
    `bCanShowMap` are **local settings**, not a replicated capability gate - "the guest cannot save"
    must be enforced where the save happens. If a future read shows those flags replicated, the gating
    plan gets simpler and this test says so by failing.

    All three campaigns have a replication-info class; the scale differs by an order of magnitude, and
    the test asserts the per-build numbers so a rewrite cannot quietly change them.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_gricheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_gricheck returned %d:\n%s" % (rc, out.strip())
    if "GameReplicationInfoClass=<BioGameReplicationInfo>" not in out:
        return "fail", "ME1 no longer points at its own replication info:\n%s" % out.strip()
    if "inherited (stock GameReplicationInfo)" not in out:
        return "fail", "ME2 now overrides GameReplicationInfoClass:\n%s" % out.strip()
    if "GameReplicationInfoClass=<SFXGRISP>" not in out:
        return "fail", "ME3 no longer points at SFXGRISP:\n%s" % out.strip()
    if "SFXGRISP                  0 property(ies),  0 function(s)" not in out:
        return "fail", "SFXGRISP is no longer a pure alias:\n%s" % out.strip()
    if "35 property(ies), 32 function(s) (27 with a real body)" not in out:
        return "fail", "SFXGRI shape changed:\n%s" % out.strip()
    if "closure EXACT" not in out:
        return "fail", "Default__SFXGRI no longer closes exactly:\n%s" % out.strip()
    rep = [ln for ln in out.splitlines() if "replicated: MaxUniquePlayers" in ln]
    if not rep:
        return "fail", "the CPF_Net set changed:\n%s" % out.strip()
    for flag in ("bCanSave", "bCanShowCodex", "bCanShowJournal", "bCanShowMap"):
        if flag not in out.split("capability flags present")[-1].split("\n")[0]:
            return "fail", "%s is no longer reported local-only:\n%s" % (flag, out.strip())
    if "CopyProperties       (OldGRI)" not in out:
        return "fail", "SFXGRI.CopyProperties changed:\n%s" % out.strip()
    if "5. The per-player replication info, SFXPRI" not in out:
        return "fail", "SFXPRI section missing:\n%s" % out.strip()
    if "10 property(ies), 27 function(s) of which 23 have a real body" not in out:
        return "fail", "SFXPRI shape changed:\n%s" % out.strip()
    if "ReplicatedScoreInfo" not in out:
        return "fail", "SFXPRI no longer replicates score info:\n%s" % out.strip()
    return "ok", ("LE3 GRI 35 props / 32 fns behind an alias; 4 net props, capability flags local-only; "
                  "SFXPRI 10 props with server RPCs")


@case("an import's package is its outer Package import, not its source_name")
def t_import_origin():
    """`describe_index` mislabels the package of an import, and that produced a wrong conclusion here.

    For an import, `describe_index` prints `source_name.object_name`. But `source_name` is the source file
    the package was cooked under: BIO_COMMON.pcc's reference to `SFXGame` reads
    `type=Class, obj=SFXGame, source=Core`, so the cheap label says `Core.SFXGame` when the class lives in
    `SFXGame.pcc`. `import_origin` follows the outer chain and gets it right, and `qualified_name` uses it.

    The rates are asserted because a fix that only worked on a handful of imports would not be a fix:
    every import must resolve one way or the other, and the `Core.Actor -> Engine` case must still hold.
    """
    import io
    import contextlib
    mt = mp.load_tables()
    pkg = mt.load_pkg()
    base = None
    for root in pkg.game_dirs():
        cand = os.path.join(root, "Game", "ME3", "BioGame", "CookedPCConsole", "BIO_COMMON.pcc")
        if os.path.isfile(cand):
            base = cand
            break
    if not base:
        return "skip", "install absent"
    t = mt.build(base)
    owner = [e for e in t["exports"] if e["object_name"] == "SFXGameInfoSP" and e["class_index"] == 0]
    if not owner:
        return "fail", "SFXGameInfoSP class missing from BIO_COMMON.pcc"
    cheap = mt.describe_index(owner[0]["super_index"], t["exports"], t["imports"])
    good = mt.qualified_name(t, owner[0]["super_index"])
    if cheap != "Core.SFXGame":
        return "fail", "the cheap label changed to %r - re-read this test" % cheap
    if good != "SFXGame.SFXGame":
        return "fail", "qualified_name gave %r, expected SFXGame.SFXGame" % good
    # The classification must be exhaustive over a package's imports.
    kinds = {}
    for index, imp in enumerate(t["imports"]):
        _p, how = mt.import_origin(t, imp)
        kinds[how] = kinds.get(how, 0) + 1
    if kinds.get("unresolved"):
        return "fail", "%d import(s) did not resolve to any origin: %s" % (
            kinds["unresolved"], kinds)
    if kinds.get("outer-package", 0) < 100:
        return "fail", "almost no import resolved through an outer package: %s" % kinds
    return "ok", ("Core.SFXGame -> SFXGame.SFXGame; imports with a package outer: %d of %d"
                  % (kinds.get("outer-package", 0), len(t["imports"])))


@case("routing knows no multiplayer class, and the campaign class already supplies the player stack")
def t_prefix_and_player_stack():
    """Two things that decide how much co-op has to add on LE3.

    First, routing: `Default__GameInfo.DefaultMapPrefixes` is three rows and **every one is
    `bIsMultiplayer=False`**, with `GameInfoClassAliases` holding exactly one alias, `SP`. So nothing in
    the engine's own routing will hand a campaign map to a networked game class - the mod has to be the
    thing that makes it one.

    Second, the player stack: `Default__SFXGame` sets `PlayerControllerClass=SFXPlayerController`,
    `PlayerReplicationInfoClass=SFXPRI` and `HUDType=BioHUD`, so a player spawned through the stock path
    gets a Bio controller and HUD with no co-op code. That is what makes a second player cheap to try.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_prefixcheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_prefixcheck returned %d:\n%s" % (rc, out.strip())
    if "DefaultMapPrefixes: 3 row(s)" not in out:
        return "fail", "the prefix table changed shape:\n%s" % out.strip()
    if "mp=True" in out:
        return "fail", "a prefix is now marked multiplayer:\n%s" % out.strip()
    if "Bio            -> SFXGameContent.SFXGameInfoSP           common=True  mp=False" not in out:
        return "fail", "the Bio prefix row changed:\n%s" % out.strip()
    if "GameInfoClassAliases: 1 row(s)" not in out or "SP     -> SFXGameContent.SFXGameInfoSP" not in out:
        return "fail", "the SP alias changed:\n%s" % out.strip()
    if "PlayerControllerClass        <SFXPlayerController>" not in out:
        return "fail", "the campaign game class stopped setting the player controller:\n%s" % out.strip()
    for want in ("PlayerReplicationInfoClass   <SFXPRI>", "HUDType                      <BioHUD>",
                 "MaxPlayersAllowed            1"):
        if want not in out:
            return "fail", "the player stack changed (%s):\n%s" % (want, out.strip())
    return "ok", "3 SP prefixes, one SP alias, and SFXGame supplies PC/PRI/HUD to any spawned player"


@case("the engine replicates the transform, a whole player pawn, and the stock join handshake")
def t_replication_surface():
    """What co-op gets for free, measured rather than assumed.

    Only ~1.8% of the engine's properties replicate, and they are the right ones: `Actor.Role`/`RemoteRole`
    with `Location`/`Rotation`/`Velocity`, and a whole `Pawn` - `Controller`, `Health`, `FiringMode`,
    `FlashCount`, `FlashLocation`, `PlayerReplicationInfo`, the movement rates.

    The stock client-join handshake is also intact in script on every build: `ServerGivePawn`,
    `ServerAcknowledgePossession`, `ServerMove`, `ServerSetSpectatorLocation`, `ServerUpdatePing`, plus
    60-odd `Client*` counterparts. So a guest does not need new networking.

    The negative is asserted too: `Projectile` replicates **nothing** - weapon fire travels as
    `Pawn.FlashCount`/`FlashLocation` and each client spawns its own projectile, so visuals are free and
    damage has to be authority-checked.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_netcheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_netcheck returned %d:\n%s" % (rc, out.strip())
    for game in ("ME1", "ME2", "ME3"):
        line = [l for l in out.splitlines() if l.startswith(game)]
        if not line:
            return "fail", "%s density row missing:\n%s" % (game, out.strip())
        if "replicate (" not in line[0]:
            return "fail", "%s row malformed: %s" % (game, line[0])
        if "5/5 present: ServerGivePawn, ServerAcknowledgePossession, ServerMove" not in out:
            return "fail", "the join handshake changed:\n%s" % out.strip()
    if out.count("transform spelling") != 3:
        return "fail", "the transform spelling was not reported per build:\n%s" % out.strip()
    # ME3 spells it lowercase; LE1/LE2 do not. That difference is the reason the lookup is case-insensitive.
    if "transform spelling: Actor.Location" not in out:
        return "fail", "LE1/LE2 no longer spell the transform Location:\n%s" % out.strip()
    if "transform spelling: Actor.location" not in out:
        return "fail", "LE3 no longer spells the transform location:\n%s" % out.strip()
    if "Projectile replicates 0 propert(ies)" not in out:
        return "fail", "Projectile started replicating - re-read the damage authority plan:\n%s" % out.strip()
    # The chain claim: ServerMove/ClientRestart are inherited untouched; only possession is replaced.
    if "the campaign's own controller chain, and what it replaces" not in out:
        return "fail", "the controller chain was not measured:\n%s" % out.strip()
    for line in out.splitlines():
        if "replaces:" in line and "ServerMove" in line and "PlayerController   " not in line:
            return "fail", "a Bio/SP controller now replaces ServerMove: %s" % line.strip()
    if "replaces: Possess" not in out:
        return "fail", "possession is no longer the overridden seam:\n%s" % out.strip()
    if "the possession signatures a mod has to match" not in out:
        return "fail", "possession signatures were not measured:\n%s" % out.strip()
    # LE1's Possess is not squad-aware; LE2/LE3's is, matching the recruitment-path difference.
    if out.count("BioPlayerController.Possess squad-aware:") != 3:
        return "fail", "squad-awareness not reported per build:\n%s" % out.strip()
    if "SFXPlayerController.Possess is" in out:
        return "fail", "SFXPlayerController.Possess no longer has the stock signature:\n%s" % out.strip()
    return "ok", ("1.8% of engine properties replicate; 5/5 join RPCs x3; ServerMove never replaced; "
                  "Possess is the seam, squad-aware on LE2/LE3 only; Projectile replicates nothing")


@case("a native function carries a 2-3 byte stub, so bytecode length is the script test")
def t_native_stub_discriminator():
    """`serial_size > 4` cannot tell an implementation from a declaration, and this module used it.

    A UE3 native function still emits a return, so its body is `0b 53` (2 bytes) or `04 0b 53` (3 bytes).
    Measured across `Engine.pcc`: 1,102 of 5,416 functions on ME1 and 1,070 of 5,279 on ME3 are that
    shape - **about 20% of what the old test called "script functions" is native**.

    `mt.is_implemented` uses the bytecode length instead. The check here is that the two populations are
    both non-empty and that the stub shapes are exactly the three known ones, because a change to those
    would silently reclassify a fifth of the engine.
    """
    import collections
    import io
    import contextlib
    mt = mp.load_tables()
    pkg = mt.load_pkg()
    base = None
    for root in pkg.game_dirs():
        cand = os.path.join(root, "Game", "ME3", "BioGame", "CookedPCConsole", "Engine.pcc")
        if os.path.isfile(cand):
            base = cand
            break
    if not base:
        return "skip", "install absent"
    t = mt.build(base)
    funcs = [e for e in t["exports"]
             if mt.describe_index(e["class_index"], t["exports"], t["imports"]) == "Core.Function"]
    if len(funcs) < 1000:
        return "fail", "only %d function(s) found in Engine.pcc" % len(funcs)
    shapes = collections.Counter()
    stubs = real = 0
    for e in funcs:
        body = mt.function_body(t, e)
        if len(body) <= 4:
            stubs += 1
            shapes[body.hex(" ")] += 1
        else:
            real += 1
    if not stubs or not real:
        return "fail", "no separation between stubs and implementations: %d / %d" % (stubs, real)
    unexpected = {k: v for k, v in shapes.items() if k not in ("0b 53", "04 0b 53", "0b 04 0b 53", "")}
    if unexpected:
        return "fail", "unexpected stub body shape(s): %s" % unexpected
    share = 100.0 * stubs / len(funcs)
    if not 10.0 < share < 30.0:
        return "fail", "stub share is %.1f%%, outside the measured 10-30%% band" % share
    # And the functions this project relies on really are implemented, not stubs.
    for cls, fn in (("GameInfo", "Login"), ("GameInfo", "AtCapacity"),
                    ("PlayerController", "ServerMove"), ("GameInfo", "SpawnDefaultPawnFor")):
        owner = mp._class_export(mt, t, cls)
        if owner is None:
            return "fail", "%s missing from Engine.pcc" % cls
        fns, _rows = mp._script_functions(mt, t, cls)
        e = fns.get(fn)
        if e is None:
            return "fail", "%s.%s missing" % (cls, fn)
        if not mt.is_implemented(t, e):
            return "fail", "%s.%s is only a stub - a claim in the docs rests on it" % (cls, fn)
    return "ok", ("%d of %d Engine.pcc function(s) are stubs (%.0f%%); shapes %s; the join chain is real"
                  % (stubs, len(funcs), share, dict(shapes.most_common(3))))


@case("a level change is a scripted broadcast, so a connected guest is moved by the engine")
def t_travel_is_a_broadcast():
    """Section 15: does the engine move every connected player, or only one?

    `ProcessServerTravel` (704-718 bytes) is the loop and `ProcessClientTravel` (180-185 bytes) takes the
    `PlayerController` to move as an out parameter. So a level change is already a broadcast to every player
    the game knows about - a connected guest is transported by the engine, not by co-op code.

    The negative is asserted too: `PlayerController.ClientTravel` has a ~28-byte body, far too small to hold
    a travel loop, and its only package references are its own four parameters - the shape of a forwarder
    onto a native implementation. So the script layer decides and the native layer performs, which means a
    mod should observe the change at `Process*Travel`.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_travelcheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_travelcheck returned %d:\n%s" % (rc, out.strip())
    for game in ("ME1", "ME2", "ME3"):
        block = out.split("== %s" % game)
        if len(block) < 2:
            return "fail", "%s section missing:\n%s" % (game, out.strip())
        section = block[1].split("== ME")[0]
        if "ProcessServerTravel" not in section or "ProcessClientTravel" not in section:
            return "fail", "%s lost a travel function:\n%s" % (game, section)
        if "body=28" not in section:
            return "fail", "%s PlayerController.ClientTravel changed size:\n%s" % (game, section)
    if out.count("PlayerController   ClientTravel                 script  body=28") != 3:
        return "fail", "ClientTravel is no longer the same tiny forwarder on all three:\n%s" % out.strip()
    if "P, LP" not in out:
        return "fail", "ProcessClientTravel no longer takes the controller to move:\n%s" % out.strip()
    # The dead seamless path is what would preserve a pawn, so its absence must be visible too.
    if "never called, because no build enables it" not in out:
        return "fail", "the dead seamless path is no longer reported:\n%s" % out.strip()
    return "ok", ("ProcessServerTravel 704-718 / ProcessClientTravel 180-185; ClientTravel is a 28-byte "
                  "forwarder; the seamless path is scripted and dead")


@case("the capacity refusal is InitGame's clamp plus AtCapacity's comparison, not one flag")
def t_capacity_chain():
    """Where the second player is actually refused, from the option string to the message.

    `coopcheck` called `MaxPlayersAllowed=1` "an active clamp" and that was right in effect but not in
    mechanism: `AtCapacity` never reads it. The chain is

        InitGame parses 'MaxPlayers' from the options string, clamps it to MaxPlayersAllowed,
        AtCapacity(bSpectator) compares NumPlayers against MaxPlayers,
        Login refuses using .MaxedOutMessage.

    Identical on all three builds, which is why the assertion is per build rather than a single check. The
    value to raise is still `MaxPlayersAllowed` on the campaign class - but a `?MaxPlayers=` in the travel
    URL on its own is clamped straight back while the ceiling is 1, and that distinction decides the fix.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_capacitycheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_capacitycheck returned %d:\n%s" % (rc, out.strip())
    for game in ("ME1", "ME2", "ME3"):
        section = out.split("== %s" % game)
        if len(section) < 2:
            return "fail", "%s section missing:\n%s" % (game, out.strip())
        body = section[1].split("== ME")[0]
        if "GameInfo.AtCapacity" not in body.split("GameInfo.MaxPlayers")[1].split("\n")[0]:
            return "fail", "%s AtCapacity no longer reads MaxPlayers:\n%s" % (game, body)
        if "AtCapacity(bSpectator, ReturnValue)" not in body:
            return "fail", "%s AtCapacity lost bSpectator:\n%s" % (game, body)
        if "capacity literals: ['MaxPlayers', 'MaxSpectators']" not in body:
            return "fail", "%s InitGame no longer parses those options:\n%s" % (game, body)
        if ".MaxedOutMessage" not in body:
            return "fail", "%s Login no longer refuses via MaxedOut:\n%s" % (game, body)
    # AtCapacity must NOT read the ceiling - that is the whole point of the correction.
    for line in out.splitlines():
        if "MaxPlayersAllowed read by:" in line and "AtCapacity" in line:
            return "fail", "AtCapacity reads MaxPlayersAllowed directly now:\n%s" % line.strip()
    if "Default__SFXGame      {'MaxPlayersAllowed': 1}" not in out:
        return "fail", "the campaign ceiling is no longer 1:\n%s" % out.strip()
    return "ok", "InitGame clamps to MaxPlayersAllowed; AtCapacity reads MaxPlayers; Login uses MaxedOut"

@case("the mutator chain is a supported in-game hook into the join path, open in retail")
def t_mutator_route():
    """Is there a way to hook the join path without patching a binary? Yes, and it has always shipped.

    `GameInfo` calls the mutator chain at every moment co-op needs: `InitGame` -> `AddMutator`/`InitMutator`,
    `Login` -> `ModifyLogin`, `GenericPlayerInitialization` -> `NotifyLogin`, `SetPlayerDefaults` ->
    `ModifyPlayer`, `Logout` -> `NotifyLogout`. `MutatorIsAllowed` is `!WorldInfo.IsDemoBuild()`, so the chain
    is permitted in a retail build.

    The scan has to look for the hook's **name index**, not its export index: a virtual call emits an
    `FName`, so an export-reference scan reports "no mutator calls anywhere" - which is the false negative
    that made this look like dead code in the first place. Both the wiring and the signatures are asserted,
    because identical signatures are what make one implementation portable across all three builds.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_mutatorcheck(None)
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_mutatorcheck returned %d:\n%s" % (rc, out.strip())
    for game in ("ME1", "ME2", "ME3"):
        section = out.split("== %s" % game)
        if len(section) < 2:
            return "fail", "%s section missing:\n%s" % (game, out.strip())
        body = section[1].split("== ME")[0]
        # Parsed by content, not by column padding.
        wiring = {}
        for line in body.splitlines():
            if "->" in line and line.strip().startswith("GameInfo."):
                caller = line.strip().split("->")[0].strip().replace("GameInfo.", "")
                hooks_called = [h.strip() for h in line.split("->")[1].split(",")]
                wiring[caller] = hooks_called
        for caller, hook in (("InitGame", "AddMutator"), ("InitGame", "InitMutator"),
                             ("Login", "ModifyLogin"),
                             ("GenericPlayerInitialization", "NotifyLogin"),
                             ("SetPlayerDefaults", "ModifyPlayer"),
                             ("Logout", "NotifyLogout")):
            if hook not in wiring.get(caller, []):
                return "fail", ("%s: %s no longer calls %s; wiring is %s"
                                % (game, caller, hook, wiring))
        for fn, params, parms in (("ModifyLogin", "(Portal, Options)", "0x20"),
                                  ("ModifyPlayer", "(Other)", "0x8"),
                                  ("NotifyLogin", "(NewPlayer)", "0x8"),
                                  ("InitMutator", "(Options, ErrorMessage)", "0x20")):
            want = "Mutator.%s" % fn
            got = [ln for ln in body.splitlines()
                   if ln.strip().startswith(want) and "%s parms=%s" % (params, parms) in ln]
            if not got:
                return "fail", "%s: %s changed shape (want %s parms=%s)\n%s" % (
                    game, want, params, parms, body)
        if "WorldInfo.IsDemoBuild" not in body:
            return "fail", "%s gate no longer mentions IsDemoBuild:\n%s" % (game, body)
        if "mutator runs AFTER the clamp" not in body:
            return "fail", "%s InitMutator no longer runs after the clamp:\n%s" % (game, body)
    # The shipped template, and the loader that reads the conflict field. The claim is deliberately narrow:
    # AddMutator reads GroupNames for the stock conflict check, which is NOT the same as routing a travel
    # URL through it - stock UE3 resolves the URL value by class name (DynamicLoadObject(mutname,
    # class'Class')), so GroupNames is a conflict field, not a URL class alias.
    if "SFXMutator_DifficultySpeed   super=export:SFXMutator 2 override(s): MutatorIsAllowed, SetGameSpeed" not in out:
        return "fail", "the shipped mutator template changed:\n%s" % out.strip()
    if "reads Mutator.GroupNames - the stock conflict check, not a URL class alias" not in out:
        return "fail", ("AddMutator no longer reads GroupNames, or the wording reverted to the alias claim:\n%s"
                        % out.strip())
    return "ok", ("6/6 join-path hooks x3, gate !IsDemoBuild, InitMutator after the clamp, and the shipped "
                  "SFXMutator template sets GroupNames, which AddMutator reads for the stock conflict check - "
                  "not a URL class alias")


@case("UE3 script pointers are 32-bit here, measured by bytecode closure rate")
def t_bytecode_pointer_width():
    """The solid facts from the bytecode work, and the honest limit around them.

    `UStruct::SerializeExpr` uses `XFERPTR` for property and function pointers, width `sizeof(ScriptPointerType)`
    - machine dependent. Trying both over every function with a body and requiring an exact landing:

        ME1: ptr=8  6.7%   ptr=4 39.5%
        ME3: ptr=8  2.4%   ptr=4 14.2%

    4 wins by 6x, so script pointers were serialized 32-bit even though the game is 64-bit. `FName` in bytecode
    is 8 bytes (name=4 scores 28.9% vs 39.5% on ME1).

    **And the rate must stay low**, so nobody mistakes this for a disassembler. It is ~40% on ME1/ME2 and ~14%
    on ME3 because ME3 uses opcodes the stock UE3 enum does not name (`0x5b`-`0x65`, `0x2b`, `0x4c`-`0x50`) and
    their operand sizes did not converge when derived empirically.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("ue3_decode", os.path.join(HERE, "ue3_decode.py"))
    dec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dec)
    tables = mp.load_tables()
    pkg = tables.load_pkg()
    results = {}
    for game in ("ME1", "ME3"):
        base = None
        for root in pkg.game_dirs():
            cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole", "Engine.pcc")
            if os.path.isfile(cand):
                base = cand
                break
        if not base:
            return "skip", "install absent"
        t = tables.build(base)
        c4, _d, _u, total = dec.closure(t, 4, 8)
        c8, _d8, _u8, _total8 = dec.closure(t, 8, 8)
        c4n4, _dn, _un, _tn = dec.closure(t, 4, 4)
        results[game] = (c4, c8, c4n4, total)
        if c4 <= c8 * 2:
            return "fail", ("%s: ptr=4 no longer clearly better (%d vs %d)" % (game, c4, c8))
        # The FName width is only separable where the decoder gets far enough to be informative; on ME3 the
        # difference was 597 vs 598, i.e. no signal. Assert it only on the build that measured it.
        if game == "ME1" and c4 <= c4n4:
            return "fail", "%s: name=8 no longer better than name=4 (%d vs %d)" % (game, c4, c4n4)
        share = 100.0 * c4 / total
        if share > 70.0:
            return "fail", ("%s: decoder now closes on %.1f%% - the 'partial, not a disassembler' claim "
                            "should be revisited" % (game, share))
    # The bug that was worth 2x: the native-token path must not raise before consuming parameters.
    if results["ME1"][0] < 1500:
        return "fail", ("ME1 closes only %d functions - the native-final-function path may have regressed to "
                        "raising before consuming its parameters" % results["ME1"][0])
    return "ok", ("ptr=4 & name=8 confirmed (ME1 %d/%d, ME3 %d/%d); partial decoder, native path consumes "
                  "parameters" % (results["ME1"][0], results["ME1"][3],
                                  results["ME3"][0], results["ME3"][3]))


@case("the progression step is identified by an integer index, and its string field is never set")
def t_transition_payload():
    """What a co-op story sync has to carry, read from real instances rather than from class shape.

    `storycheck` counted placements. This decodes them: every `BioSeqAct_PMExecuteTransition` on the install
    that closes exactly, and what its fields contain.

    The finding is that **`m_nIndex` is the payload** - an integer over a bounded range - while
    `m_sObjectType` never appears on a real instance and `Region`/`Plot`/`Transition` read `None` because their
    enums are two-member stubs. That rules out three easier designs (a string key, a region enum, a transition
    enum) and settles the hand-off as "send the last index the host executed".
    """
    index_dir = os.environ.get("MELE_INDEX_DIR") or os.path.join(
        HERE, "..", "..", "local", "MELE", "index")
    if not os.path.isfile(os.path.join(index_dir, "ME3.json")):
        return "skip", "no package index (transitioncheck opens every map)"
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_transitioncheck(
            __import__("argparse").Namespace(game="ME3"))
    out = buf.getvalue()
    if "no Legendary Edition install found" in out:
        return "skip", "install absent"
    if rc != 0:
        return "fail", "cmd_transitioncheck returned %d:\n%s" % (rc, out.strip())
    import re
    line = [l for l in out.splitlines() if l.startswith("ME3")]
    if not line:
        return "fail", "ME3 row missing:\n%s" % out.strip()
    match = re.search(r"(\d+) instance\(s\) closed exactly, (\d+) skipped; m_nIndex (-?\d+)\.\.(-?\d+), "
                      r"(\d+) distinct", line[0])
    if not match:
        return "fail", "row did not parse: %s" % line[0]
    total, skipped, low, high, distinct = (int(g) for g in match.groups())
    if total < 10000:
        return "fail", "only %d instance(s) closed - the reader regressed" % total
    if skipped > total * 0.05:
        return "fail", "%d of %d instances failed to close - too many to trust the survey" % (skipped, total)
    if distinct < 1000:
        return "fail", "only %d distinct indices, expected thousands" % distinct
    if low != -1:
        return "fail", "minimum m_nIndex is %d, was -1" % low
    # The negatives are the point: three fields are stubs, one is never set, and ME1/ME2's extra fields are
    # sentinel - every one of which would have looked like usable story state from the declaration alone.
    if "m_sObjectType 0" not in out:
        return "fail", "m_sObjectType is now present on real instances:\n%s" % out.strip()
    if "m_sVarName 0" not in out:
        return "fail", "m_sVarName is now present on real instances:\n%s" % out.strip()
    if "Region 11216, Plot 10108, Transition 9676" not in out:
        return "fail", "the Region/Plot/Transition presence counts changed:\n%s" % out.strip()
    # ME1/ME2 add three fields the declaration makes look like extra story state. This case scans ME3 only,
    # so their absence is asserted here and their sentinel values are covered by running the command per build.
    if "m_sVarName 0" not in out:
        return "fail", "m_sVarName is now present on real instances:\n%s" % out.strip()
    return "ok", ("%d instances, m_nIndex %d..%d over %d distinct; m_sObjectType/m_sVarName never set"
                  % (total, low, high, distinct))


@case("transitioncheck fails nonzero when no instance could be decoded, and prints no verdict")
def t_transition_all_fail():
    """The maintainer's case: the sole instance raises during parsing.

    `total == 0` and `unclosed > 0`. Before the fix the command printed "no instance closed exactly",
    added no problem (because `unclosed` was nonzero), and fell through to the story-sync verdict with an
    empty summary - reporting a payload on the strength of zero decoded instances. A tool that could not
    read the build must not return success.

    This does not need an install or an index: the install check is stubbed, the index-dir check is
    satisfied with a real file in a temporary directory, and the per-build package iteration and the
    instance parser are stubbed, so the command takes the all-fail path by construction. The stubs are
    restored in a `finally`, so a failure inside the command cannot leave the module patched for the next
    case.
    """
    import argparse
    import contextlib
    import io as _io
    import json as _json
    import shutil
    import tempfile

    # A real index file, so the command's own "no package index" guard is satisfied rather than stubbed.
    index_dir = tempfile.mkdtemp(prefix="mele-transition-fail-")
    with open(os.path.join(index_dir, "ME3.json"), "w", encoding="utf-8") as fh:
        _json.dump({"version": 2, "game": "ME3", "packages": {}}, fh)

    saved = (mp._require_install, mp._index_dir, mp._iter_rel_index, mp.parse_object)

    class _FakeExport(dict):
        def __init__(self):
            super().__init__(object_name="BioSeqAct_PMExecuteTransition")

    class _FakeTable(dict):
        def __init__(self):
            super().__init__(exports=[_FakeExport()], buf=b"", displacement=0)

    def fake_require_install(games=("ME1", "ME2", "ME3")):
        return True

    def fake_index_dir():
        return index_dir

    def fake_iter_rel_index(mt, game):
        yield {"path": "/fake/%s.pcc" % game}

    def fake_parse_object(table, buf, obj, start_offset=4, max_tags=40000):
        raise ValueError("the sole instance failed to parse")

    def fake_build(package, limit=None):
        return _FakeTable()

    mp._require_install = fake_require_install
    mp._index_dir = fake_index_dir
    mp._iter_rel_index = fake_iter_rel_index
    mp.parse_object = fake_parse_object
    # load_tables() builds a fresh module object per call, so patching mele_tables.build would not be seen.
    # Stub load_tables itself to return an object whose build is the fake.
    saved_load_tables = mp.load_tables

    class _FakeTables:
        build = staticmethod(fake_build)

        @staticmethod
        def load_pkg():
            class _Pkg:
                def game_dirs(self):
                    return []
            return _Pkg()

    def fake_load_tables():
        return _FakeTables()

    try:
        mp.load_tables = fake_load_tables
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mp.cmd_transitioncheck(argparse.Namespace(game="ME3"))
        out = buf.getvalue()
    finally:
        (mp._require_install, mp._index_dir, mp._iter_rel_index,
         mp.parse_object) = saved
        mp.load_tables = saved_load_tables
        shutil.rmtree(index_dir, ignore_errors=True)

    if rc == 0:
        return "fail", "all instances failed to parse but the command returned 0:\n%s" % out.strip()
    if "verdict: the campaign's progression step" in out:
        return "fail", "a story-sync verdict was printed with no decoded instance:\n%s" % out.strip()
    if "failed to parse" not in out:
        return "fail", "the parse failure was not reported as a problem:\n%s" % out.strip()
    return "ok", ("no decoded instance -> nonzero exit (%d), no story-sync verdict, the failure is stated"
                  % rc)


@case("scene state is per-controller and unreplicated, and LE2/LE3 already loop the squad")
def t_scene_state():
    """The conversation/cutscene seam, measured rather than assumed.

    Three things are pinned, and the third is the one that changes the plan:

      * `PlayerController.bCinematicMode`, `ViewTarget` and `WorldInfo.bPlayersOnly` carry **no**
        `CPF_Net`, so a client is never told by the engine that the host entered a scene. The check
        cannot pass by accident, because two properties that *do* replicate on the same classes are
        asserted positive in the same run - a broken flag read fails the positive control first.
      * `PlayerController.ClientSetCinematicMode` exists, is script (not a native stub), and is reached
        from `SetCinematicMode` by an **FName** call at `+0xef / +0xef / +0xfc`. That is why an
        export-index scan reports "nobody calls it": a virtual call emits a name, not a package index.
      * `BioPlayerController.SetCinematicMode` - the override the campaign's own
        `BioSeqAct_BioToggleCinematicMode` reaches - is 131 / 747 / 775 bytes, and the LE2/LE3 bodies
        reference the squad (`m_playerSquad`, `GetMember`, `Members`) while LE1's 131-byte body
        references none of it.

    The last point is the one a plan would otherwise get wrong in the expensive direction: LE2/LE3
    already put every squad member into the scene, so a guest possessing a squadmate rides an existing
    code path; LE1 has no such path and needs the loop written.
    """
    import contextlib
    import io
    import re
    if not mp._require_install():
        return "skip", "install absent"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mp.cmd_scenecheck(__import__("argparse").Namespace())
    out = buf.getvalue()
    if rc != 0:
        return "fail", "cmd_scenecheck returned %d:\n%s" % (rc, out.strip()[-1500:])
    # Every unreplicated scene field, on every build.
    for field in ("bCinematicMode       CPF_Net=False", "ViewTarget           CPF_Net=False",
                  "bPlayersOnly         CPF_Net=False"):
        if out.count(field) != 3:
            return "fail", ("%r appears %d time(s), expected 3 - the scene state now replicates on some "
                            "build" % (field.strip(), out.count(field)))
    # Positive controls: the flag reader must find CPF_Net on the same classes.
    if out.count("positive control       TargetViewRotation   CPF_Net=True") != 3:
        return "fail", "the CPF_Net positive control failed - the negative above is unsupported"
    if out.count("positive control       Pauser               CPF_Net=True") != 3:
        return "fail", "the Pauser positive control failed - the flag offset is wrong"
    # The RPC and its call site.
    sites = re.findall(r"SetCinematicMode -> ClientSetCinematicMode at \+0x([0-9a-f]+) \(FName call\)", out)
    if sites != ["ef", "ef", "fc"]:
        return "fail", "ClientSetCinematicMode call sites changed: %s" % sites
    if out.count("ClientSetCinematicMode 116 B script") != 2 or "ClientSetCinematicMode 119 B script" not in out:
        return "fail", "ClientSetCinematicMode body sizes changed:\n%s" % out.strip()[-1200:]
    # The split that matters: LE1 has no squad loop, LE2/LE3 do.
    rows = re.findall(r"BioPlayerController\.SetCinematicMode\s+(\d+) B, \d+ exact ref\(s\), "
                      r"(\d+) squad ref\(s\)", out)
    sizes = [int(r[0]) for r in rows]
    squad = [int(r[1]) for r in rows]
    if sizes != [131, 747, 775]:
        return "fail", "BioPlayerController.SetCinematicMode sizes changed: %s" % sizes
    if squad[0] != 0:
        return "fail", ("LE1's cinematic override now references the squad (%d refs) - the 'LE1 needs the "
                        "loop written' finding no longer holds" % squad[0])
    if min(squad[1:]) == 0:
        return "fail", ("LE2/LE3's cinematic override no longer references the squad (%s) - the 'newer "
                        "builds already loop it' finding no longer holds" % squad)
    # Placement counts must be measured, not hardcoded: the stock action is a declaration, the Bio one
    # is what the campaign places. `(?!\d)` keeps "1" from matching "12".
    if "NOT MEASURED (no package index)" in out:
        # The tool said it could not measure, which is not a zero.
        if out.count("NOT MEASURED (no package index)") != 3:
            return "fail", "only some builds reported unmeasured placements"
    elif len(re.findall(r"SeqAct_ToggleCinematicMode 1(?!\d)", out)) != 3:
        return "fail", "the stock action is now placed more than once per build - re-read the scene plan"
    return "ok", ("unreplicated on all 3 (positive controls hold); RPC call sites +0xef/+0xef/+0xfc; "
                  "override %s B with %s squad refs - LE1 has none"
                  % (sizes, squad))


@case("a native function has two shapes, and a declaration is not an implementation")
def t_native_shapes():
    """`0b 53` is not the only way a native function appears - and the second way broke published counts.

    UE3 emits an empty body for a native function, and this project tested `len > 4` for it. But a native
    function that *takes parameters* gets a different shape:

        29 <u32> 0b 53     EX_NativeParm, a per-build native index, EX_Nothing, EX_EndOfScript

    The old test counted that as script. The dword is a native index rather than a name index, and that is
    what makes it decidable without decoding anything: all 590 / 571 / 552 values are distinct, a large
    minority fall outside the name table, and of 455 functions present on both ME1 and ME3 with the same
    name **zero** share a value.

    The correction is asserted rather than described, because "BioPlayerController has 236 script
    functions" was published and is wrong on ME3; the real number is 198.
    """
    mt = mp.load_tables()
    want = {"ME1": (305, 67, 34, 123), "ME2": (302, 106, 19, 120), "ME3": (302, 198, 28, 121)}
    # Engine.pcc owns PlayerController and Actor; the campaign package owns the Bio classes, so the two
    # have to be read separately - a single-package scan silently reports None for half the row.
    origin = {"PlayerController": "Engine.pcc", "Actor": "Engine.pcc",
              "BioPlayerController": "SFXGame.pcc", "BioWorldInfo": "SFXGame.pcc"}
    rows = {}
    for game in ("ME1", "ME2", "ME3"):
        shapes = {}
        for cls, package in origin.items():
            path = None
            for root in _pkg_dirs():
                cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole", package)
                if os.path.isfile(cand):
                    path = cand
                    break
            if path is None:
                return "skip", "no %s for %s" % (package, game)
            t = mt.build(path)
            _funcs, decls = mp._script_functions(mt, t, cls)
            if not decls:
                return "fail", "%s.%s not found in %s - the counts cannot be measured" % (game, cls, package)
            shapes[cls] = sum(1 for f in decls if mt.function_shape(t, f) == "script")
        rows[game] = (shapes["PlayerController"], shapes["BioPlayerController"],
                      shapes["BioWorldInfo"], shapes["Actor"])
        # Both native shapes must be present in volume, or the classification is not being exercised.
        stubs = decls = scripts = 0
        for f in t["exports"]:
            if not mt.describe_index(f["class_index"], t["exports"], t["imports"]).endswith("Function"):
                continue
            kind = mt.function_shape(t, f)
            stubs += kind == "stub"
            decls += kind == "native-decl"
            scripts += kind == "script"
        if stubs < 500 or decls < 500 or scripts < 2000:
            return "fail", ("%s: shapes look wrong (stub %d, native-decl %d, script %d) - both native "
                            "shapes must be present in volume" % (game, stubs, decls, scripts))
    if rows != want:
        return "fail", "script counts changed: %s, expected %s" % (rows, want)
    return "ok", ("Engine.pcc: ~1,070 stubs and ~980 native declarations per build; script counts "
                  "PC %s, BioPC %s, BioWorldInfo %s, Actor %s (was 1,000+ higher before the fix)"
                  % (rows["ME3"][0], rows["ME3"][1], rows["ME3"][2], rows["ME3"][3]))


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
