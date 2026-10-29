"""Tests for the plan runner, driven against a real bridge server.

Why these matter more than the catalogue tests: the runner is the component that finally
*calls into the game*, and the failure mode it must not have is calling something at the wrong
address or with the wrong parms block. So the tests use the real `bridge_client` against the
real `fake_game` server, with a fake `ue3.call` that validates the parms block the runner
sends. A bug in marshalling shows up here rather than in a crashed game.

What is pinned:

1. **probe-only calls nothing mutable.** The mode must be `probe` and the game must record
   that no call happened.
2. **the parms block has the measured size**, and a parameter that runs past that size is
   refused *before* being sent, not after the game rejects it.
3. **a missing bridge method fails immediately** with a message naming what is missing, rather
   than halfway through a phase.
4. **a game-side refusal is a result, not a crash.** One bad command does not lose the rest.
5. **destructive phases are opt-in.** Without `--yes-destructive` a high-risk phase does not
   run, and the record says why.
6. **an empty target is reported, not guessed.** A command with no object address must come
   back `no-target`.

Run: python -I tools/ue3live/test_run_plan.py
"""
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mele_paths  # noqa: E402  upward search: works in the hub and in this bundle
REPO = mele_paths.repo_root()
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bridge"))

import bridge_client          # noqa: E402
import fake_game              # noqa: E402
import ue3_run_plan           # noqa: E402


def plan_with(commands, phase_risk="low", optional=False, phase_id="P-test"):
    return {"schema": 1, "game": "ME1", "exe": "MassEffect1.exe", "pid": 1,
            "phases": [{
                "id": phase_id, "title": "test phase", "risk": phase_risk,
                "optional": optional, "does": "nothing", "proves": "the runner works",
                "abort_if": "a test fails",
                "commands": commands,
            }]}


def cmd(cid="c.one", status="ready", obj=None, role="player.pawn", parms_size=0, parms=None,
        risk="low", call="SetHidden"):
    return {"id": cid, "call": call, "status": status, "object": obj, "role": role,
            "risk": risk, "owner": "Actor", "function": "0x2000", "parms": parms or [],
            "parms_size": parms_size, "sync": "read it back"}


def install_ue3(fake, calls):
    """A fake game-side adapter: records every call and validates the parms block."""
    def ue3_call(p):
        parms = base64.b64decode(p.get("parms") or "")
        calls.append({"object": p.get("object"), "role": p.get("role"),
                      "function": p.get("function"), "mode": p.get("mode"),
                      "parms_len": len(parms), "parms_size": p.get("parmsSize")})
        if len(parms) != p.get("parmsSize"):
            return {"ok": False, "error": "parms length %d != parmsSize %r"
                                          % (len(parms), p.get("parmsSize"))}
        if p.get("mode") == "probe":
            return {"ok": True, "called": False}
        return {"ok": True, "called": True, "value": 42}

    def ue3_read(p):
        return {"ok": True, "value": 7, "hex": "0x7"}

    def ue3_chain(p):
        return {"ok": True, "class": "BioPawn", "chain": ["BioPawn", "Pawn", "Actor"],
                "name": "Kaidan"}

    fake.custom["ue3.call"] = ue3_call
    fake.custom["ue3.read"] = ue3_read
    fake.custom["ue3.chain"] = ue3_chain


def run(plan, **kw):
    """Start a fake game and run the plan against it. Returns (summary, calls, rc)."""
    calls = []
    fake = fake_game.FakeGame(name="ue3test")
    install_ue3(fake, calls)
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(b, plan, log=lambda s: None, **kw)
            return r.run(), calls
    finally:
        fake.stop()


CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("probe-only sends mode=probe and calls nothing")
def t_probe_only():
    summary, calls = run(plan_with([cmd()]), probe_only=True)
    if not calls:
        return "fail", "no command reached the game at all"
    modes = {c["mode"] for c in calls}
    if modes != {"probe"}:
        return "fail", "modes sent were %s, expected only 'probe'" % modes
    statuses = [r["status"] for r in summary["results"]]
    if statuses != ["probed"]:
        return "fail", "statuses %s, expected ['probed']" % statuses
    return "ok", "1 command probed, mode=probe, nothing called"


@case("execute sends mode=call")
def t_execute():
    summary, calls = run(plan_with([cmd()]))
    if [c["mode"] for c in calls] != ["call"]:
        return "fail", "modes %s" % [c["mode"] for c in calls]
    if summary["results"][0]["status"] != "called":
        return "fail", "status %r" % summary["results"][0]["status"]
    if summary["results"][0].get("value") != 42:
        return "fail", "the returned value was dropped: %r" % (summary["results"][0],)
    return "ok", "called, value 42 recorded"


@case("the parms block matches the declared size")
def t_parms_size():
    parms = [{"name": "bNewHidden", "kind": "BoolProperty", "offset": 0, "size": 1}]
    summary, calls = run(plan_with([cmd(parms_size=4, parms=parms)]), probe_only=True)
    if not calls:
        return "fail", "nothing sent"
    if calls[0]["parms_len"] != 4 or calls[0]["parms_size"] != 4:
        return "fail", "length %d / size %r" % (calls[0]["parms_len"], calls[0]["parms_size"])
    return "ok", "4 bytes sent for a 4-byte block"


@case("a parameter past the end of the block stops the run before anything is sent")
def t_parms_overflow():
    """A malformed layout is a defect in the PLAN. It must be caught in preflight, so no
    command reaches the game and the message points at the plan rather than the process."""
    parms = [{"name": "TooBig", "kind": "IntProperty", "offset": 4, "size": 4}]
    calls = []
    fake = fake_game.FakeGame(name="ue3test")
    install_ue3(fake, calls)
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(b, plan_with([cmd(parms_size=4, parms=parms)]),
                                    log=lambda s: None, probe_only=True)
            try:
                r.run()
                return "fail", "ran despite a parameter past the end of its block"
            except ue3_run_plan.PlanError as exc:
                msg = str(exc)
                if "TooBig" not in msg:
                    return "fail", "the error does not name the offending parameter: %s" % msg
                if calls:
                    return "fail", "%d command(s) reached the game before the plan was " \
                                   "validated" % len(calls)
                return "ok", "refused in preflight, named TooBig, sent nothing"
    finally:
        fake.stop()


@case("preflight covers every phase that will run, not just the first")
def t_preflight_all_phases():
    """A bad block in the LAST phase must stop the run before the FIRST phase mutates
    anything, or a defect discovered late leaves the copy in a half-tested state."""
    good = cmd("c.good")
    bad = cmd("c.bad", parms_size=2,
              parms=[{"name": "Overrun", "kind": "IntProperty", "offset": 0, "size": 4}])
    plan = {"schema": 1, "game": "ME1", "exe": "MassEffect1.exe", "pid": 1, "phases": [
        {"id": "P0", "title": "first", "risk": "low", "does": "", "proves": "",
         "abort_if": "", "commands": [good]},
        {"id": "P1", "title": "last", "risk": "low", "does": "", "proves": "",
         "abort_if": "", "commands": [bad]},
    ]}
    calls = []
    fake = fake_game.FakeGame(name="ue3test")
    install_ue3(fake, calls)
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(b, plan, log=lambda s: None)
            try:
                r.run()
                return "fail", "ran with a malformed block in the last phase"
            except ue3_run_plan.PlanError as exc:
                if "P1" not in str(exc):
                    return "fail", "the error does not say which phase: %s" % exc
                if calls:
                    return "fail", "the first phase ran before validation finished"
                return "ok", "refused before phase P0 ran, named phase P1"
    finally:
        fake.stop()


@case("a plan with no defects passes preflight")
def t_preflight_clean():
    summary, calls = run(plan_with([cmd(parms_size=4,
                                        parms=[{"name": "b",
                                                "kind": "BoolProperty",
                                                "offset": 0, "size": 1}])]),
                         probe_only=True)
    if not calls:
        return "fail", "a valid plan was refused"
    return "ok", "valid block accepted and sent"


@case("a missing bridge method fails immediately, naming it")
def t_missing_method():
    fake = fake_game.FakeGame(name="ue3test")
    # No custom methods at all.
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(b, plan_with([cmd()]), log=lambda s: None)
            try:
                r.run()
                return "fail", "ran without ue3.call registered"
            except ue3_run_plan.PlanError as exc:
                msg = str(exc)
                if "ue3.call" not in msg:
                    return "fail", "error does not name the missing method: %s" % msg
                if "ue3.read" not in msg:
                    return "fail", "error names only one of the missing methods"
                return "ok", "refused up front, named the missing methods"
    finally:
        fake.stop()


@case("a game-side refusal is a result, not a lost run")
def t_refusal_is_a_result():
    fake = fake_game.FakeGame(name="ue3test")
    calls = []

    def ue3_call(p):
        calls.append(p.get("role"))
        if p.get("role") == "bogus.role":
            return {"ok": False, "error": "unknown role: bogus.role"}
        return {"ok": True, "called": True, "value": 1}

    fake.custom["ue3.call"] = ue3_call
    fake.custom["ue3.read"] = lambda p: {"ok": True, "value": 0}
    fake.custom["ue3.chain"] = lambda p: {"ok": True, "chain": []}
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(
                b, plan_with([cmd("c.bad", role="bogus.role"),
                              cmd("c.good", role="player.pawn")]),
                log=lambda s: None)
            summary = r.run()
    finally:
        fake.stop()
    st = {x["id"]: x["status"] for x in summary["results"]}
    if st.get("c.bad") != "refused":
        return "fail", "bad command status %r" % st.get("c.bad")
    if st.get("c.good") != "called":
        return "fail", "the good command after a refusal did not run: %r" % st.get("c.good")
    return "ok", "bad -> refused, good -> called, run continued"


@case("the role is sent, and an explicit address is sent only when present")
def t_role_vs_object():
    summary, calls = run(plan_with([cmd("c.role", role="squad.member", obj=None)]),
                         probe_only=True)
    if calls[0]["role"] != "squad.member":
        return "fail", "role not sent: %r" % (calls[0]["role"],)
    if calls[0]["object"] is not None:
        return "fail", "an object address was sent when the plan had none"
    # Now with an explicit address: both must be present, so the game side can prefer it.
    summary2, calls2 = run(plan_with([cmd("c.addr", role="player.pawn", obj="0xABCD")]),
                           probe_only=True)
    if calls2[0]["object"] != "0xABCD":
        return "fail", "explicit address not sent: %r" % (calls2[0]["object"],)
    if calls2[0]["role"] != "player.pawn":
        return "fail", "role dropped when an address was present"
    return "ok", "role always sent; address only when the plan has one"


@case("a command marked unresolvable is reported, never sent")
def t_unresolved_not_sent():
    calls = []
    fake = fake_game.FakeGame(name="ue3test")
    install_ue3(fake, calls)
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(
                b, plan_with([cmd("c.unres", status="unresolved")]), log=lambda s: None)
            summary = r.run()
    finally:
        fake.stop()
    if calls:
        return "fail", "an unresolved command was sent to the game"
    if summary["results"][0]["status"] != "unresolved":
        return "fail", "status %r" % summary["results"][0]["status"]
    return "ok", "reported unresolved, nothing sent"


@case("a command with neither address nor role is reported, not guessed")
def t_no_target():
    calls = []
    fake = fake_game.FakeGame(name="ue3test")
    install_ue3(fake, calls)
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(b, plan_with([cmd(obj=None, role=None)]),
                                    log=lambda s: None)
            summary = r.run()
    finally:
        fake.stop()
    if calls:
        return "fail", "sent a call with no target at all"
    rec = summary["results"][0]
    if rec["status"] != "no-target":
        return "fail", "status %r" % rec["status"]
    return "ok", "no-target, nothing sent"


@case("a high-risk phase needs --yes-destructive")
def t_destructive_gate():
    summary, calls = run(plan_with([cmd()], phase_risk="high"))
    if calls:
        return "fail", "a high-risk phase ran without --yes-destructive"
    statuses = [r["status"] for r in summary["results"]]
    if statuses != ["phase-skipped"]:
        return "fail", "statuses %s, expected ['phase-skipped']" % statuses
    if "yes-destructive" not in summary["results"][0]["detail"]:
        return "fail", "the skip reason does not say what to pass"
    return "ok", "skipped, reason names the flag"


@case("--yes-destructive lets a high-risk phase run")
def t_destructive_allowed():
    summary, calls = run(plan_with([cmd()], phase_risk="high"), yes_destructive=True)
    if not calls:
        return "fail", "the phase still did not run"
    return "ok", "ran as instructed"


@case("an optional phase is skipped unless requested")
def t_optional_gate():
    summary, calls = run(plan_with([cmd()], optional=True))
    if calls:
        return "fail", "an optional phase ran unrequested"
    if summary["results"][0]["status"] != "phase-skipped":
        return "fail", "status %r" % summary["results"][0]["status"]
    return "ok", "skipped"


@case("an optional high-risk phase needs both flags")
def t_optional_destructive():
    # optional only: still skipped
    s1, c1 = run(plan_with([cmd()], phase_risk="high", optional=True))
    if c1:
        return "fail", "ran with neither flag"
    # include_optional only: skipped, because it is still high risk
    s2, c2 = run(plan_with([cmd()], phase_risk="high", optional=True),
                 include_optional=True)
    if c2:
        return "fail", "ran with --include-optional but without --yes-destructive"
    # both: runs
    s3, c3 = run(plan_with([cmd()], phase_risk="high", optional=True),
                 include_optional=True, yes_destructive=True)
    if not c3:
        return "fail", "did not run with both flags"
    return "ok", "requires both flags, then runs"


@case("the summary counts every status it produced")
def t_summary_counts():
    summary, _ = run(plan_with([cmd(), cmd("c.unres", status="unresolved")]),
                     probe_only=True)
    counts = summary["counts"]
    total = sum(counts.values())
    if total != len(summary["results"]):
        return "fail", "counts total %d != %d results" % (total, len(summary["results"]))
    if counts.get("probed") != 1 or counts.get("unresolved") != 1:
        return "fail", "unexpected counts %r" % (counts,)
    return "ok", "%d results, counts agree" % total


@case("the result records the game's own info")
def t_game_info():
    fake = fake_game.FakeGame(name="ue3test")
    calls = []
    install_ue3(fake, calls)
    fake.custom["ue3.info"] = lambda p: {"build": "10352613",
                                         "processEventSlot": "0x238",
                                         "peVerified": True}
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(b, plan_with([cmd()]), log=lambda s: None,
                                    probe_only=True)
            summary = r.run()
    finally:
        fake.stop()
    info = summary.get("game_info") or {}
    if info.get("build") != "10352613":
        return "fail", "game info not recorded: %r" % (info,)
    if info.get("peVerified") is not True:
        return "fail", "the PE verification flag was lost"
    return "ok", "build and processEventSlot recorded"


@case("a missing ue3.info does not block the run")
def t_no_info():
    """An older adapter without ue3.info must still run: the build string is a record, not a
    prerequisite."""
    summary, calls = run(plan_with([cmd()]), probe_only=True)
    if not calls:
        return "fail", "the run did not proceed"
    if "error" not in (summary.get("game_info") or {}):
        return "fail", "expected an explicit note that ue3.info was unavailable, got %r" % (
            summary.get("game_info"),)
    return "ok", "ran anyway, recorded why info is missing"


@case("plan schema is checked")
def t_plan_schema():
    import tempfile
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, "bad.json")
        json.dump({"schema": 99, "phases": []}, open(p, "w", encoding="utf-8"))
        try:
            ue3_run_plan.load_plan(p)
            return "fail", "accepted an unknown schema"
        except ue3_run_plan.PlanError as exc:
            if "schema" not in str(exc):
                return "fail", "refused without saying why: %s" % exc
            return "ok", "refused an unknown schema"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


@case("attach_targets leaves an unprobed command targetless")
def t_attach_targets():
    plan = plan_with([cmd("c.a", obj=None), cmd("c.b", obj=None)])
    probe = {"commands": [
        {"id": "c.a", "resolved_to": {"owner": "Actor", "instance": "0xDEAD"}},
        {"id": "c.b", "resolved_to": {"owner": "Actor"}},          # no instance
    ]}
    got = ue3_run_plan.attach_targets(plan, probe)
    objs = {c["id"]: c["object"] for c in got["phases"][0]["commands"]}
    if objs["c.a"] != "0xDEAD":
        return "fail", "c.a got %r" % (objs["c.a"],)
    if objs["c.b"] is not None:
        return "fail", "c.b was given a target from a probe with no instance: %r" % (
            objs["c.b"],)
    return "ok", "resolved target attached, unresolved left empty"


@case("a transport failure stops the run by default")
def t_abort_on_transport():
    """A bridge error means the process is gone. Continuing would record results against a
    dead process - invented data, which is worse than a short run."""
    fake = fake_game.FakeGame(name="ue3test")

    def ue3_call(p):
        raise RuntimeError("process went away")

    fake.custom["ue3.call"] = ue3_call
    fake.custom["ue3.read"] = lambda p: {"ok": True, "value": 0}
    fake.custom["ue3.chain"] = lambda p: {"ok": True, "chain": []}
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(
                b, plan_with([cmd("c.1"), cmd("c.2"), cmd("c.3")]), log=lambda s: None)
            summary = r.run()
    finally:
        fake.stop()
    if not summary.get("aborted"):
        return "fail", "no abort recorded despite a transport failure"
    # The runner stops after the failing command, so it must NOT have tried all three.
    tried = [x for x in summary["results"] if x["status"] == "bridge-error"]
    if len(tried) > 1:
        return "fail", "kept calling after the bridge failed (%d attempts)" % len(tried)
    return "ok", "aborted after 1 attempt, reason recorded"


@case("--keep-going continues past a transport failure")
def t_keep_going():
    fake = fake_game.FakeGame(name="ue3test")
    seen = []

    def ue3_call(p):
        seen.append(1)
        raise RuntimeError("process went away")

    fake.custom["ue3.call"] = ue3_call
    fake.custom["ue3.read"] = lambda p: {"ok": True, "value": 0}
    fake.custom["ue3.chain"] = lambda p: {"ok": True, "chain": []}
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(
                b, plan_with([cmd("c.1"), cmd("c.2"), cmd("c.3")]), log=lambda s: None,
                stop_on_abort=False)
            summary = r.run()
    finally:
        fake.stop()
    if summary.get("aborted"):
        return "fail", "aborted despite --keep-going"
    if len(seen) != 3:
        return "fail", "attempted %d of 3 commands" % len(seen)
    return "ok", "attempted all 3, no abort"


@case("a refusal does NOT abort the run")
def t_refusal_no_abort():
    """A refusal is the game saying 'not that', which is a finding. Only a transport failure
    stops the run."""
    fake = fake_game.FakeGame(name="ue3test")

    def ue3_call(p):
        return {"ok": False, "error": "not that"}

    fake.custom["ue3.call"] = ue3_call
    fake.custom["ue3.read"] = lambda p: {"ok": True, "value": 0}
    fake.custom["ue3.chain"] = lambda p: {"ok": True, "chain": []}
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(
                b, plan_with([cmd("c.1"), cmd("c.2"), cmd("c.3")]), log=lambda s: None)
            summary = r.run()
    finally:
        fake.stop()
    if summary.get("aborted"):
        return "fail", "a refusal aborted the run"
    if len(summary["results"]) != 3:
        return "fail", "%d results, expected 3" % len(summary["results"])
    return "ok", "all 3 recorded as refusals, run continued"


@case("a field write is verified by read-back")
def t_field_write_verified():
    """The write path, end to end. The fake adapter applies the value and returns the read-back;
    the runner must accept it only because the values match."""
    import ue3_fields
    fake = fake_game.FakeGame(name="ue3test")
    store = {"viewport": 0}
    writes = []

    def ue3_write(p):
        writes.append(dict(p))
        store[p["role"]] = p["value"]
        return {"ok": True, "readBack": store.get(p["role"])}

    fake.custom["ue3.call"] = lambda p: {"ok": True, "called": True}
    fake.custom["ue3.read"] = lambda p: {"ok": True, "value": store.get(p.get("role"), 0)}
    fake.custom["ue3.chain"] = lambda p: {"ok": True, "chain": ["GameViewportClient"]}
    fake.custom["ue3.write"] = ue3_write
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            plan = {"schema": 1, "game": "ME1", "exe": "MassEffect1.exe", "pid": 1,
                    "phases": [{"id": "S01", "title": "request split", "risk": "medium",
                                "does": "", "proves": "", "abort_if": "",
                                "writes": [{"field": "viewport.desired_splitscreen_type",
                                            "value": 1}]}]}
            r = ue3_run_plan.Runner(b, plan, log=lambda s: None)
            summary = r.run()
    finally:
        fake.stop()
    if not writes:
        return "fail", "no write reached the game"
    if writes[0]["offset"] != 0x1C0 or writes[0]["size"] != 1 or writes[0]["value"] != 1:
        return "fail", "wrong write payload: %r" % (writes[0],)
    rec = summary["results"][0]
    if rec["status"] != "written":
        return "fail", "status %r" % rec["status"]
    return "ok", "wrote offset 0x1C0 size 1 value 1, read back and matched"


@case("a write whose read-back disagrees is reported as a failure")
def t_field_write_lie():
    """The most important case here: the game says ok but the field does not hold the value.
    That is exactly 'the write went somewhere else', and it must not be recorded as success."""
    fake = fake_game.FakeGame(name="ue3test")

    def ue3_write(p):
        return {"ok": True, "readBack": 0}          # claims success, stores nothing

    fake.custom["ue3.call"] = lambda p: {"ok": True, "called": True}
    fake.custom["ue3.read"] = lambda p: {"ok": True, "value": 0}
    fake.custom["ue3.chain"] = lambda p: {"ok": True, "chain": []}
    fake.custom["ue3.write"] = ue3_write
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            plan = {"schema": 1, "game": "ME1", "exe": "MassEffect1.exe", "pid": 1,
                    "phases": [{"id": "S01", "title": "x", "risk": "medium", "does": "",
                                "proves": "", "abort_if": "",
                                "writes": [{"field": "viewport.desired_splitscreen_type",
                                            "value": 1}]}]}
            r = ue3_run_plan.Runner(b, plan, log=lambda s: None)
            summary = r.run()
    finally:
        fake.stop()
    rec = summary["results"][0]
    if rec["status"] == "written":
        return "fail", "a write that read back 0 was reported as written"
    if "did not land" not in (rec.get("detail") or ""):
        return "fail", "failed without saying why: %r" % (rec.get("detail"),)
    return "ok", "reported failure: %s" % rec["detail"]


@case("a plan that writes demands ue3.write up front")
def t_write_method_required():
    """An adapter without a write path must be rejected before the first phase, not halfway."""
    fake = fake_game.FakeGame(name="ue3test")
    fake.custom["ue3.call"] = lambda p: {"ok": True, "called": True}
    fake.custom["ue3.read"] = lambda p: {"ok": True, "value": 0}
    fake.custom["ue3.chain"] = lambda p: {"ok": True, "chain": []}
    # no ue3.write
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            plan = {"schema": 1, "game": "ME1", "exe": "MassEffect1.exe", "pid": 1,
                    "phases": [{"id": "S01", "title": "x", "risk": "medium", "does": "",
                                "proves": "", "abort_if": "",
                                "writes": [{"field": "viewport.desired_splitscreen_type",
                                            "value": 1}]}]}
            r = ue3_run_plan.Runner(b, plan, log=lambda s: None)
            try:
                r.run()
                return "fail", "ran a writing plan with no write method"
            except ue3_run_plan.PlanError as exc:
                if "ue3.write" not in str(exc):
                    return "fail", "refused without naming the method: %s" % exc
                return "ok", "refused up front, named ue3.write"
    finally:
        fake.stop()


@case("a read-only plan does not demand ue3.write")
def t_readonly_no_write_needed():
    """The safer adapter has no write path. A read-only plan must still work against it."""
    summary, calls = run(plan_with([cmd()]), probe_only=True)
    if not calls:
        return "fail", "a read-only plan needed ue3.write, which the fake adapter lacks"
    return "ok", "ran with no write method present"


@case("a field phase reads without writing when no value is given")
def t_field_read_only_op():
    fake = fake_game.FakeGame(name="ue3test")
    writes = []
    fake.custom["ue3.call"] = lambda p: {"ok": True, "called": True}
    fake.custom["ue3.read"] = lambda p: {"ok": True, "value": 0}
    fake.custom["ue3.chain"] = lambda p: {"ok": True, "chain": []}
    fake.custom["ue3.write"] = lambda p: writes.append(p) or {"ok": True, "readBack": 0}
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            plan = {"schema": 1, "game": "ME1", "exe": "MassEffect1.exe", "pid": 1,
                    "phases": [{"id": "S00", "title": "x", "risk": "read", "does": "",
                                "proves": "", "abort_if": "",
                                "writes": [{"field": "gameinfo.num_players"}]}]}
            r = ue3_run_plan.Runner(b, plan, log=lambda s: None)
            summary = r.run()
    finally:
        fake.stop()
    if writes:
        return "fail", "a value-less field op wrote something"
    if summary["results"][0]["status"] != "read":
        return "fail", "status %r" % summary["results"][0]["status"]
    return "ok", "read only, nothing written"


@case("a REAL generated plan runs end to end against a bridge")
def t_real_plan_end_to_end():
    """The test that ties the two halves together.

    It builds a plan with the real `build_plan` from the real catalogue, feeds it to the real
    runner over the real protocol, and requires every command in a phase to reach the game
    with a role the adapter understands. This is what catches a schema drift between the two
    modules - a plan field renamed in the generator and not in the runner would pass every
    unit test above and fail on the first real run.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cmdsuite", os.path.join(HERE, "ue3_command_suite.py"))
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)

    # Pretend everything resolved, so the plan is fully populated.
    fake_probe = {"exe": "MassEffect1.exe", "pid": 1,
                  "commands": [{"id": c["id"], "resolved": True, "call": c["call"],
                                "resolved_to": {"owner": "Actor", "ptr": "0x1D0",
                                                "parms": [{"name": "bNewHidden",
                                                           "kind": "BoolProperty",
                                                           "offset": 0, "size": 1}],
                                                "parms_size": 1}}
                               for c in suite.COMMANDS]}
    # One phase only, so the test is fast: the baseline, which is read-only.
    phase = next(p for p in suite.PHASES if p["id"] == "P00-baseline")
    plan = suite.build_plan(fake_probe, {"commands": suite.COMMANDS}, [phase], "ME1")
    plan["phases"][0]["risk"] = "read"          # not gated by --yes-destructive

    calls = []
    fake = fake_game.FakeGame(name="ue3test")
    install_ue3(fake, calls)
    fake.start()
    try:
        with bridge_client.Bridge.connect(fake.endpoint, wait_s=5) as b:
            r = ue3_run_plan.Runner(b, plan, log=lambda s: None, probe_only=True)
            summary = r.run()
    finally:
        fake.stop()

    ready = [c for c in plan["phases"][0]["commands"] if c["status"] == "ready"]
    if not ready:
        return "fail", "the generated plan had no runnable commands for P00-baseline"
    if len(calls) != len(ready):
        return "fail", "%d of %d ready commands reached the game" % (len(calls), len(ready))
    bad_role = [c["role"] for c in calls if not c.get("role")]
    if bad_role:
        return "fail", "%d calls carried no role" % len(bad_role)
    unknown = {c["role"] for c in calls} - set(suite.ROLES)
    if unknown:
        return "fail", "the plan used roles the catalogue does not declare: %s" % unknown
    statuses = {x["status"] for x in summary["results"]}
    if statuses - {"probed"}:
        return "fail", "not every command probed cleanly: %s" % statuses
    return "ok", "%d commands from a generated plan probed as %s" % (
        len(calls), sorted({c["role"] for c in calls}))


def main():
    failures = 0
    for name, fn in CASES:
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = "fail", "%s: %s" % (type(exc).__name__, exc)
        mark = {"ok": "OK  ", "fail": "FAIL", "skip": "SKIP"}[status]
        print("%s %-56s %s" % (mark, name, detail))
        if status == "fail":
            failures += 1
    print()
    if failures:
        print("%d/%d FAILED" % (failures, len(CASES)))
        return 1
    print("ALL OK: %d cases" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
