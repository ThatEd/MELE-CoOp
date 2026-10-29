"""Tests for the command suite. Runs without a game.

Three things are worth testing, and each one is tested by BREAKING it:

1. **The drift check can fail.** `verify` recomputes `present` from the decompressed
   identifier tables and compares. A check that cannot report failure is worse than none
   (rule 23/25 territory), so one case feeds it a map file with an identifier removed and
   requires a non-zero result and a named discrepancy.
2. **The taxonomy is internally consistent.** Unique ids, every category declared, every
   `blocked` entry carrying the crash it is blocked by, and presence strings that are real
   three-way answers rather than a single flag copied three times.
3. **The phase order is safe.** Everything destructive comes after everything reversible,
   and no phase claims to be read-only while listing a mutating command.

Run: python -I tools/ue3live/test_command_suite.py
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mele_paths  # noqa: E402  upward search: works in the hub and in this bundle
REPO = mele_paths.repo_root()
sys.path.insert(0, HERE)
import mele_paths  # noqa: E402  (data-dir resolver: local/MELE, not local/)


def load():
    spec = importlib.util.spec_from_file_location(
        "cmdsuite", os.path.join(HERE, "ue3_command_suite.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_main(mod, argv):
    """Call main() with argv and capture (code, stdout)."""
    old_argv, old_out = sys.argv, sys.stdout
    sys.argv = ["ue3_command_suite.py"] + argv
    buf = io.StringIO()
    try:
        sys.stdout = buf
        code = mod.main()
    finally:
        sys.argv, sys.stdout = old_argv, old_out
    return code, buf.getvalue()


CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ---------------------------------------------------------------------------------------
@case("drift check reports a removed identifier")
def t_drift_detects_removal(mod):
    """Take the real map files, delete one identifier from ME3, and require a failure that
    names exactly that command and game."""
    src = mele_paths.maps_dir()
    if not os.path.isfile(os.path.join(src, "ME3.json")):
        return "skip", mele_paths.map_file("ME3") + " not present"
    tmp = tempfile.mkdtemp()
    try:
        for g in mod.GAMES:
            shutil.copy(os.path.join(src, "%s.json" % g), os.path.join(tmp, "%s.json" % g))
        path = os.path.join(tmp, "ME3.json")
        doc = json.load(open(path, encoding="utf-8"))
        # The real name table holds BOTH 'Possess' and 'possess', so removing one spelling
        # leaves the command present and a correct verify would still pass. Strip every
        # case variant, which is what a build without the command actually looks like.
        target = "Possess"
        variants = [i for i in doc["identifiers"] if i.lower() == target.lower()]
        if not variants:
            return "fail", "test depends on Possess being in the ME3 map"
        doc["identifiers"] = [i for i in doc["identifiers"]
                              if i.lower() != target.lower()]
        json.dump(doc, open(path, "w", encoding="utf-8"))

        code, out = run_main(mod, ["verify", "--maps", tmp])
        if code == 0:
            return "fail", "removing Possess from the ME3 map still verified clean"
        if "possess" not in out.lower():
            return "fail", "failed but did not name the affected command:\n" + out
        if "ME3" not in out:
            return "fail", "failed but did not name the affected game:\n" + out
        return "ok", "non-zero exit, named pawn.possess and ME3"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@case("drift check reports an ADDED identifier")
def t_drift_detects_addition(mod):
    """The harder direction: the catalogue says absent, the data says present. A one-way
    check would pass this and still be wrong."""
    src = mele_paths.maps_dir()
    if not os.path.isfile(os.path.join(src, "ME1.json")):
        return "skip", mele_paths.map_file("ME1") + " not present"
    tmp = tempfile.mkdtemp()
    try:
        for g in mod.GAMES:
            shutil.copy(os.path.join(src, "%s.json" % g), os.path.join(tmp, "%s.json" % g))
        path = os.path.join(tmp, "ME1.json")
        doc = json.load(open(path, encoding="utf-8"))
        # CreatePlayer is catalogued as LE3-only and blocked-crash on LE1. Pretend a build
        # has it and require verify to notice.
        doc["identifiers"].append("CreatePlayer")
        json.dump(doc, open(path, "w", encoding="utf-8"))

        code, out = run_main(mod, ["verify", "--maps", tmp])
        if code == 0:
            return "fail", "adding CreatePlayer to the ME1 map still verified clean"
        if "pawn.create_player" not in out:
            return "fail", "failed but did not name pawn.create_player:\n" + out
        return "ok", "non-zero exit, named pawn.create_player"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@case("drift check passes on the real data")
def t_drift_clean(mod):
    """Skips when the map files are absent, like its two neighbours.

    A fresh clone of the bundle has no `local/MELE/maps` - the data is regenerable from the game
    and is deliberately not committed. Failing here would make the suite red on every clean
    checkout, which teaches a reader to ignore red. The three skip messages say which file and
    which command regenerates it, so the gap is visible rather than silent.
    """
    missing = [g for g in mod.GAMES if not os.path.isfile(mele_paths.map_file(g))]
    if missing:
        return "skip", ("no identifier table for %s under %s. Regenerate with "
                        "tools/melepkg/mele_script_map.py --package <SFXGame.pcc>"
                        % (", ".join(missing), mele_paths.maps_dir()))
    code, out = run_main(mod, ["verify"])
    if code != 0:
        return "fail", "verify failed on the real maps:\n" + out
    if "ALL OK" not in out:
        return "fail", "exit 0 but no ALL OK line:\n" + out
    return "ok", "ALL OK"


@case("catalogue generation refuses to overwrite on disagreement")
def t_catalogue_refuses(mod):
    """`catalogue` must not silently record a taxonomy that disagrees with the data."""
    src = mele_paths.maps_dir()
    if not os.path.isfile(os.path.join(src, "ME2.json")):
        return "skip", mele_paths.map_file("ME2") + " not present"
    tmp = tempfile.mkdtemp()
    try:
        for g in mod.GAMES:
            shutil.copy(os.path.join(src, "%s.json" % g), os.path.join(tmp, "%s.json" % g))
        path = os.path.join(tmp, "ME2.json")
        doc = json.load(open(path, encoding="utf-8"))
        # Must be a name the taxonomy claims IS in ME2; removing an already-absent one
        # would make this test pass while testing nothing (the first draft did exactly
        # that with 'Ghost', which is ME3-only).
        target = "Slomo"
        if not [i for i in doc["identifiers"] if i.lower() == target.lower()]:
            return "fail", "test depends on Slomo being in the ME2 map"
        doc["identifiers"] = [i for i in doc["identifiers"] if i.lower() != target.lower()]
        json.dump(doc, open(path, "w", encoding="utf-8"))
        out_path = os.path.join(tmp, "out.json")
        code, out = run_main(mod,
                             ["catalogue", "--maps", tmp, "--out", out_path])
        if code == 0:
            return "fail", "catalogue wrote despite a disagreement"
        if os.path.exists(out_path):
            return "fail", "catalogue wrote the file anyway"
        if "console.slomo" not in out:
            return "fail", "failed but did not name console.slomo:\n" + out
        return "ok", "refused, explained, wrote nothing"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@case("every id is unique and every category is declared")
def t_taxonomy(mod):
    ids = [c["id"] for c in mod.COMMANDS]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        return "fail", "duplicate ids: %s" % dupes
    undeclared = sorted({c["category"] for c in mod.COMMANDS} - set(mod.CATEGORIES))
    if undeclared:
        return "fail", "categories used but not documented: %s" % undeclared
    unused = sorted(set(mod.CATEGORIES) - {c["category"] for c in mod.COMMANDS})
    if unused:
        return "fail", "categories documented but empty: %s" % unused
    bad_risk = sorted({c["risk"] for c in mod.COMMANDS} - set(mod.RISK_ORDER))
    if bad_risk:
        return "fail", "unknown risk levels: %s" % bad_risk
    return "ok", "%d commands, %d categories, all risks known" % (len(ids), len(mod.CATEGORIES))


@case("a 'blocked' command names the game it crashed on")
def t_blocked(mod):
    bad = [c["id"] for c in mod.COMMANDS if c["risk"] == "blocked" and not c["crashes"]]
    if bad:
        return "fail", "'blocked' with no crash record: %s" % bad
    blocked = [c for c in mod.COMMANDS if c["risk"] == "blocked"]
    # The one blocked entry must not claim to be present on a game it crashed.
    for c in blocked:
        for g in c["crashes"]:
            if c["present"][g] and c["id"] == "pawn.create_player" and g != "ME3":
                return "fail", ("%s is marked blocked on %s yet present there; a missing "
                                "identifier is not the same as a crash" % (c["id"], g))
    return "ok", "%d blocked, each with its crash recorded" % len(blocked)


@case("presence is a real three-way answer, not one flag copied")
def t_presence_varies(mod):
    """If every presence string were 'TTT' the per-game claim would be worthless. Require
    that per-game-only and two-of-three entries both exist."""
    patterns = {}
    for c in mod.COMMANDS:
        k = "".join("T" if c["present"][g] else "F" for g in mod.GAMES)
        patterns[k] = patterns.get(k, 0) + 1
    if patterns.get("TTT", 0) < 20:
        return "fail", "too few commands present in all three (%d)" % patterns.get("TTT", 0)
    only = {k: v for k, v in patterns.items() if k.count("T") == 1}
    if not only:
        return "fail", "no command is present in exactly one build; per-game claims are untested"
    pairs = {k: v for k, v in patterns.items() if k.count("T") == 2}
    if not pairs:
        return "fail", "no command is present in exactly two builds"
    return "ok", "%d patterns, %d three-way, %d single-build" % (
        len(patterns), patterns.get("TTT", 0), sum(only.values()))


@case("required phases put reversible before destructive")
def t_phase_order(mod):
    """The ordering guarantee is about REQUIRED phases. An optional phase may sit anywhere,
    because it only runs when someone opts in; a required destructive phase followed by a
    required read phase would mean the suite compares a read against mutated state."""
    req = [p for p in mod.PHASES if not p.get("optional")]
    idx = next((i for i, p in enumerate(req) if p["risk"] == "high"), None)
    if idx is None:
        return "fail", "no required high-risk phase, so the ordering is untested"
    later_reads = [p["id"] for p in req[idx:] if p["risk"] == "read"]
    if later_reads:
        return "fail", "required read-only phases after the first required destructive " \
                       "one: %s" % later_reads
    # Baseline must be first: every later phase's result depends on it.
    if mod.PHASES[0]["id"] != "P00-baseline":
        return "fail", "the first phase is %r, not the baseline" % mod.PHASES[0]["id"]
    # Cleanup must exist and be last.
    if "cleanup" not in mod.PHASES[-1]["id"]:
        return "fail", "the last phase is %r, not a cleanup" % mod.PHASES[-1]["id"]
    return "ok", "%d required phases, first required destructive at index %d of %d, " \
                 "cleanup last" % (len(req), idx, len(req))


@case("every phase says how to abort")
def t_phase_abort(mod):
    bad = [p["id"] for p in mod.PHASES if not p.get("abort_if")]
    if bad:
        return "fail", "phases with no abort condition: %s" % bad
    return "ok", "%d phases, each with an abort condition" % len(mod.PHASES)


@case("the one destructive known crash is never in a plan")
def t_plan_excludes_blocked(mod):
    """A plan built from a synthetic all-resolved probe must not carry a blocked command."""
    fake = {"exe": "MassEffect1.exe", "pid": 1,
            "commands": [{"id": c["id"], "resolved": True, "call": c["call"],
                          "resolved_to": {"owner": "X", "ptr": "0x1", "parms": [],
                                          "parms_size": 0}}
                         for c in mod.COMMANDS]}
    cat = {"commands": mod.COMMANDS}
    plan = mod.build_plan(fake, cat, mod.PHASES, "ME1")
    ids = [c["id"] for ph in plan["phases"] for c in ph["commands"]]
    if "pawn.create_player" in ids and any(
            c["status"] == "ready" for ph in plan["phases"] for c in ph["commands"]
            if c["id"] == "pawn.create_player"):
        return "fail", "CreatePlayer is dispatchable in an ME1 plan, where it crashes 3 ways"
    if "pawn.create_player" in ids:
        entry = next(c for ph in plan["phases"] for c in ph["commands"]
                     if c["id"] == "pawn.create_player")
        if entry["status"] != "skipped" or "blocked" not in (entry.get("note") or ""):
            return "fail", "CreatePlayer present but not marked skipped/blocked: %r" % entry
        return "ok", "%d planned, CreatePlayer skipped-as-blocked" % len(ids)
    return "ok", "%d planned, blocked command excluded entirely" % len(ids)


@case("a phase's first-time commands never repeat; only `repeats` may")
def t_phase_uses_unique(mod):
    """A command must be scheduled once, EXCEPT where a phase explicitly lists it in
    `repeats` - cleanup has to re-run what it restores. Making the exception explicit is
    the point: an accidental duplicate is otherwise indistinguishable from a deliberate
    one, and a duplicated mutating command is how a suite corrupts its own baseline."""
    seen = {}
    dupes = []
    illegal = []
    for p in mod.PHASES:
        allowed = set(p.get("repeats", []))
        for cid in p["uses"]:
            if cid in seen and cid not in allowed:
                dupes.append("%s in both %s and %s (not declared as repeats)"
                             % (cid, seen[cid], p["id"]))
            seen[cid] = p["id"]
        for cid in allowed:
            if cid not in seen:
                illegal.append("%s declares %s as a repeat but never used it before"
                               % (p["id"], cid))
    if dupes:
        return "fail", "; ".join(dupes[:5])
    if illegal:
        return "fail", "; ".join(illegal[:5])
    return "ok", "%d commands first-scheduled once across %d phases; repeats declared" % (
        len(seen), len(mod.PHASES))


@case("every phase command exists in the catalogue")
def t_phase_uses_known(mod):
    known = {c["id"] for c in mod.COMMANDS}
    bad = sorted({cid for p in mod.PHASES for cid in p["uses"]} - known)
    if bad:
        return "fail", "phases reference unknown command ids: %s" % bad
    return "ok", "all phase references resolve"


@case("optional phases are the ones that touch shared or session-losing state")
def t_optional_phases(mod):
    """The dangerous phases must be opt-in. If a save or travel phase were required, a
    routine run could destroy the player's profile or drop both clients."""
    opt = {p["id"] for p in mod.PHASES if p.get("optional")}
    required_uses = {cid for p in mod.PHASES if not p.get("optional")
                     for cid in p["uses"]}
    hazard = {"save.save_game", "save.load_game", "save.save_profile",
              "travel.server_travel", "travel.client_travel", "travel.open_map",
              "travel.switch_level", "travel.restart_level", "console.open",
              "console.login", "console.logout"}
    leaked = sorted(hazard & required_uses)
    if leaked:
        return "fail", "hazardous commands in required phases: %s" % leaked
    if not opt:
        return "fail", "no phase is optional, so nothing dangerous is opt-in"
    return "ok", "%d optional phase(s): %s" % (len(opt), ", ".join(sorted(opt)))


@case("an unresolved command is reported, not dropped from the plan")
def t_plan_keeps_unresolved(mod):
    fake = {"exe": "MassEffect3.exe", "pid": 1, "commands": []}
    cat = {"commands": mod.COMMANDS}
    plan = mod.build_plan(fake, cat, mod.PHASES, "ME3")
    entries = [c for ph in plan["phases"] for c in ph["commands"]]
    if not entries:
        return "fail", "a probe that resolved nothing produced an empty plan"
    if any(c["status"] == "ready" for c in entries):
        return "fail", "commands marked ready with nothing resolved"
    # Absent-from-build entries are correctly skipped; everything present must be unresolved.
    statuses = {c["status"] for c in entries}
    if statuses - {"unresolved", "skipped"}:
        return "fail", "unexpected statuses from an empty probe: %s" % statuses
    return "ok", "%d entries, no command silently missing" % len(entries)


@case("a plan reports its own coverage gap")
def t_plan_coverage(mod):
    """Commands present in a build but used by no phase must be listed, not implied absent."""
    fake = {"exe": "MassEffect3.exe", "pid": 1,
            "commands": [{"id": c["id"], "resolved": True, "call": c["call"],
                          "resolved_to": {"owner": "X", "ptr": "0x1", "parms": [],
                                          "parms_size": 0}}
                         for c in mod.COMMANDS]}
    cat = {"commands": mod.COMMANDS}
    plan = mod.build_plan(fake, cat, mod.PHASES, "ME3")
    if "uncovered" not in plan:
        return "fail", "the plan has no coverage report"
    used = {c["id"] for ph in plan["phases"] for c in ph["commands"]}
    for cid in plan["uncovered"]:
        if cid in used:
            return "fail", "%s reported uncovered but is used by a phase" % cid
    present = {c["id"] for c in mod.COMMANDS
               if c["present"]["ME3"] and c["risk"] != "blocked"}
    expect = present - used
    if set(plan["uncovered"]) != expect:
        return "fail", "uncovered set is wrong: %d reported vs %d expected" % (
            len(plan["uncovered"]), len(expect))
    return "ok", "%d uncovered, exactly the present-but-unused set" % len(plan["uncovered"])


@case("every command has a declared target role")
def t_roles(mod):
    """A command with no role is a command the runner cannot aim, so it would report
    `no-target` at run time. Catching it here means the plan is complete before a game is
    opened."""
    bad = [c["id"] for c in mod.COMMANDS
           if mod.role_of(c) not in mod.ROLES]
    if bad:
        return "fail", "commands with an undeclared role: %s" % bad
    unknown_override = sorted(set(mod.ROLE_OVERRIDES) - {c["id"] for c in mod.COMMANDS})
    if unknown_override:
        return "fail", "ROLE_OVERRIDES names commands that do not exist: %s" % unknown_override
    counts = {}
    for c in mod.COMMANDS:
        counts[mod.role_of(c)] = counts.get(mod.role_of(c), 0) + 1
    none = counts.get("none", 0)
    if none > len(mod.COMMANDS) // 2:
        return "fail", "%d of %d commands have no specific target - the plan cannot aim " \
                       "most of itself" % (none, len(mod.COMMANDS))
    return "ok", "%d roles used, %d commands are role-addressed, %d are 'none'" % (
        len(counts), len(mod.COMMANDS) - none, none)


@case("every query command is aimed at something")
def t_query_aimed(mod):
    """A query with role `none` asks the game side to guess. `IsLocallyControlled` was
    exactly that until the end-to-end test caught it, and it is the single most useful query
    for a co-op client: it must be asked OF a controller."""
    bad = [c["id"] for c in mod.COMMANDS
           if c["category"] == "query" and mod.role_of(c) == "none"
           and c["id"] != "q.local_player_controller"]
    if bad:
        return "fail", "queries with no target: %s" % bad
    by_id = {c["id"]: c for c in mod.COMMANDS}
    if mod.role_of(by_id["q.local_controlled"]) != "player.controller":
        return "fail", "IsLocallyControlled is not aimed at a controller"
    return "ok", "queries aimed, IsLocallyControlled -> controller"


@case("viewport methods are aimed at the viewport")
def t_viewport_roles(mod):
    """RemovePlayer and LayoutPlayers are GameViewportClient methods. Aiming them at a player
    would call through the wrong object; the end-to-end test noticed this class of mistake."""
    by_id = {c["id"]: c for c in mod.COMMANDS}
    for cid in ("pawn.remove_player", "pawn.layout_players"):
        if mod.role_of(by_id[cid]) != "viewport":
            return "fail", "%s aims at %r" % (cid, mod.role_of(by_id[cid]))
    return "ok", "both aim at the viewport"

@case("possession targets the controller, not the pawn")
def t_possess_role(mod):
    """The single most dangerous role mistake in the catalogue: calling Possess on a pawn
    instead of a controller is what the LE1 Actor.Spawn route effectively did, and it faulted
    calling through a null PlayerInput. A role regression here is not cosmetic."""
    by_id = {c["id"]: c for c in mod.COMMANDS}
    for cid in ("pawn.possess", "pawn.unpossess"):
        got = mod.role_of(by_id[cid])
        if got != "player.controller":
            return "fail", "%s targets %r, expected player.controller" % (cid, got)
    return "ok", "Possess and UnPossess both aim at the controller"


@case("the committed commands.json carries the same roles")
def t_roles_committed(mod):
    path = os.path.join(HERE, "commands.json")
    if not os.path.isfile(path):
        return "fail", "commands.json is missing"
    doc = json.load(open(path, encoding="utf-8"))
    have = {c["id"]: c for c in doc["commands"]}
    for c in mod.COMMANDS:
        want = mod.role_of(c)
        if "role" not in have.get(c["id"], {}):
            return "fail", "%s has no role in commands.json; regenerate it" % c["id"]
        if have[c["id"]]["role"] != want:
            return "fail", "%s role is %r in json, %r in source" % (
                c["id"], have[c["id"]]["role"], want)
    return "ok", "%d roles agree" % len(mod.COMMANDS)


@case("plan entries carry a role so the runner can aim them")
def t_plan_roles(mod):
    fake = {"exe": "MassEffect3.exe", "pid": 1,
            "commands": [{"id": c["id"], "resolved": True, "call": c["call"],
                          "resolved_to": {"owner": "X", "ptr": "0x1", "parms": [],
                                          "parms_size": 0}}
                         for c in mod.COMMANDS]}
    plan = mod.build_plan(fake, {"commands": mod.COMMANDS}, mod.PHASES, "ME3")
    missing = []
    for ph in plan["phases"]:
        for c in ph["commands"]:
            if c["status"] == "ready" and not c.get("role"):
                missing.append(c["id"])
    if missing:
        return "fail", "%d ready entries have no role: %s" % (len(missing), missing[:5])
    return "ok", "every ready entry is role-addressed"

@case("the committed commands.json matches the taxonomy")
def t_committed_matches(mod):
    """commands.json is generated. If someone edits the taxonomy and forgets to regenerate,
    `list` would show stale data - so the file and the source must agree."""
    path = os.path.join(HERE, "commands.json")
    if not os.path.isfile(path):
        return "fail", "commands.json is missing"
    doc = json.load(open(path, encoding="utf-8"))
    have = {c["id"]: c for c in doc["commands"]}
    want = {c["id"]: c for c in mod.COMMANDS}
    if set(have) != set(want):
        return "fail", ("id sets differ: only in json %s, only in source %s"
                        % (sorted(set(have) - set(want))[:5],
                           sorted(set(want) - set(have))[:5]))
    for cid in want:
        for field in ("call", "category", "risk"):
            if have[cid][field] != want[cid][field]:
                return "fail", "%s.%s is %r in json, %r in source" % (
                    cid, field, have[cid][field], want[cid][field])
        for g in mod.GAMES:
            if have[cid]["present"][g] != want[cid]["present"][g]:
                return "fail", "%s presence differs for %s" % (cid, g)
    return "ok", "%d ids agree on call/category/risk/presence" % len(want)


# ---------------------------------------------------------------------------------------
# The live resolver's SELECTION logic.
#
# This is the part most likely to be silently wrong and the part that decides which
# UFunction a plan dispatches. The selection is a single pure function (`resolve_target`) so
# it is tested here directly, not through a copy of itself: a test that reimplements the
# algorithm only proves the two copies agree today.
# ---------------------------------------------------------------------------------------
def _cands(*pairs):
    return [{"call": p[0].split(".")[-1], "owner": p[0].split(".")[0], "ptr": "0x%x" % (i + 1)}
            for i, p in enumerate(pairs)]


def _chain(table):
    def chain_of(owner):
        return table.get(owner, [])
    return chain_of


@case("resolver prefers the targeted owner over the first hit")
def t_resolver_prefers_target(mod):
    """Three classes define SetLocation. A resolver that takes the first hit picks
    SceneManager and the plan dispatches a function that does nothing to a pawn."""
    cands = _cands(("SceneManager.SetLocation",), ("Interface.SetLocation",),
                   ("Actor.SetLocation",))
    chosen, why = mod.resolve_target(cands, ["Actor"], _chain({}))
    if not chosen or chosen["owner"] != "Actor":
        return "fail", "picked %r instead of Actor" % (chosen and chosen["owner"],)
    if not why or "verify" not in why:
        return "fail", "resolved from 3 candidates without flagging the ambiguity: %r" % (why,)
    return "ok", "chose Actor from 3 candidates and flagged it"


@case("resolver matches a superclass through the chain")
def t_resolver_super(mod):
    """A target naming `Controller` must accept a `BioPlayerController` owner: that is how
    the game actually declares Possess."""
    cands = _cands(("BioPlayerController.Possess",))
    chosen, _ = mod.resolve_target(cands, ["Controller"],
                                   _chain({"BioPlayerController": ["Controller", "Object"]}))
    if not chosen:
        return "fail", "rejected BioPlayerController as a Controller subclass"
    return "ok", "BioPlayerController accepted as Controller"


@case("resolver rejects an owner that is not targeted nor a subclass")
def t_resolver_wrong_owner(mod):
    cands = _cands(("TotallyUnrelated.SetHidden",))
    chosen, why = mod.resolve_target(cands, ["Actor"], _chain({"TotallyUnrelated": ["Object"]}))
    if chosen is not None:
        return "fail", "accepted an unrelated owner"
    if not why or "none owned by" not in why:
        return "fail", "rejected without saying why: %r" % (why,)
    return "ok", "rejected and named the expected owners"


@case("resolver flags an ambiguous name that has no targets")
def t_resolver_ambiguous(mod):
    cands = _cands(("Actor.SetHidden",), ("Interface.SetHidden",))
    chosen, why = mod.resolve_target(cands, [], _chain({}))
    if not chosen:
        return "fail", "did not resolve a two-owner name with no targets"
    if not why or "unverified" not in why:
        return "fail", "ambiguous name not flagged (why=%r)" % (why,)
    if "Interface" not in why:
        return "fail", "the ambiguity report does not name the other owner: %r" % (why,)
    return "ok", "resolved, flagged, other owner named"


@case("resolver returns nothing for no candidates")
def t_resolver_empty(mod):
    chosen, why = mod.resolve_target([], ["Actor"], _chain({}))
    if chosen is not None:
        return "fail", "invented a candidate from an empty list"
    # The caller supplies the "absent" wording, so a None reason here is correct and the
    # probe substitutes its own message. Assert that contract rather than the wording.
    if why is not None:
        return "fail", "supplied a reason the caller also supplies: %r" % (why,)
    return "ok", "None/None, caller supplies the absent message"


@case("a single unambiguous candidate is not flagged")
def t_resolver_clean(mod):
    cands = _cands(("Actor.SetHidden",))
    chosen, why = mod.resolve_target(cands, ["Actor"], _chain({}))
    if not chosen:
        return "fail", "rejected the only candidate"
    if why is not None:
        return "fail", "flagged a single unambiguous candidate: %r" % (why,)
    return "ok", "resolved cleanly with no warning"


@case("probe fails cleanly when the process is not running")
def t_probe_no_process(mod):
    """The most likely way a user meets this tool is with no game open. That path must be a
    sentence, not a traceback: `module_base` returns None and the error must be returned
    rather than raised, so a wrapper script can report it."""
    p = mod.probe(999999, "MassEffect1.exe", mele_paths.offsets_file("LE1"))
    if "error" not in p:
        return "fail", "no error reported for a pid that does not exist"
    if "MassEffect1.exe" not in p["error"]:
        return "fail", "the error does not name the executable: %r" % (p["error"],)
    return "ok", "returned %r" % (p["error"],)


@case("probe does not write and does not call into the game")
def t_probe_read_only(mod):
    """Structural check on the source: probe's import path must only reach the read-only
    reader, and no call site may be a WriteProcessMemory / CreateRemoteThread / VirtualAllocEx.
    A source scan is weak evidence, but it is the only offline evidence available and it
    would catch the specific mistake of pasting in a dispatch helper."""
    src = open(os.path.join(HERE, "ue3_command_suite.py"), encoding="utf-8").read()
    banned = ["WriteProcessMemory", "CreateRemoteThread", "VirtualAllocEx",
              "VirtualProtectEx", "PROCESS_VM_WRITE", "PROCESS_VM_OPERATION",
              "CreateProcess"]
    hits = [b for b in banned if b in src]
    if hits:
        return "fail", "source mentions %s" % hits
    if "ReadProcessMemory" in src:
        return "fail", "source reads process memory directly instead of via ue3_proc.Reader"
    return "ok", "no write APIs, no direct memory reads"


def main():
    mod = load()
    failures = 0
    for name, fn in CASES:
        try:
            status, detail = fn(mod)
        except Exception as exc:  # a test that throws is a failure, not a skip
            status, detail = "fail", "%s: %s" % (type(exc).__name__, exc)
        mark = {"ok": "OK  ", "fail": "FAIL", "skip": "SKIP"}[status]
        print("%s %-58s %s" % (mark, name, detail))
        if status == "fail":
            failures += 1
            if detail and "\n" in detail:
                for line in detail.splitlines():
                    print("       " + line)
    print()
    if failures:
        print("%d/%d FAILED" % (failures, len(CASES)))
        return 1
    print("ALL OK: %d cases" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
