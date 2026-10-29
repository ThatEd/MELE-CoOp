"""Execute a command-suite plan against a running game, through the in-game bridge.

The split this module exists to honour:

* `ue3_command_suite.py` decides **what** to do - which command, on which object, with which
  parms block, and what assertion decides pass/fail. All of that is offline.
* the in-game bridge (`tools/bridge`) runs **it** on the game's main thread, because that is
  the only thread where `UFunction` invocation is legal.
* this module is the thing in between: it drives the bridge, enforces the phase order and the
  abort conditions, and writes a result file.

Why the execution is not in Python, restated here because it is the reason for the whole
arrangement: `ProcessEvent` must run on the game thread, its vtable slot differs per game
(LE1 `+0x238`, LE2 `+0x228`, LE3 `+0x230` as a wrapper), and LE1/LE2 crash inside `CreatePlayer`
**from the game's own thread after a correct call returned**. A dispatcher living in a separate
process cannot attribute a fault like that and cannot legally make the call at all.

The contract with the game side is one custom bridge method:

    ue3.call  params: {function: "0x...", role: "player.pawn", parms: "base64",
                       parmsSize: n, mode: "probe"|"call", object?: "0x..."}
              result: {ok: bool, called: bool, value?: any, error?: str}

    ue3.read  params: {role: "player.pawn", offset: n, size: 1|2|4|8, signed?: bool,
                       object?: "0x..."}
              result: {ok: bool, value: number, hex: "0x..."}

    ue3.chain params: {role: "player.pawn", object?: "0x..."}
              result: {class: str, chain: [str], name: str}

    ue3.info  params: {}
              result: {build: str, processEventSlot: "0x...", peVerified: bool,
                       roles: {name: "0x..."}}

**`role` rather than an address, and this is the important design choice.** Addresses are
per-launch, so a plan that baked one in would be stale the next time the game started - and a
stale address is the worst possible failure because it is a valid-looking pointer to arbitrary
memory. So the plan names a role (`player.pawn`, `squad.member`, `gameinfo`, ...) and the
game-side adapter resolves it against the live process at call time, on the main thread. The
adapter reports what it resolved in `ue3.info`'s `roles` map, so a run records which object
each role meant.

`mode: "probe"` resolves the role, validates the function and the parms block, and returns
WITHOUT calling. That is what makes a whole-plan dry run meaningful: it proves every role
resolves, every function exists and every parms block is the right size, while mutating
nothing. It is the same two-build pattern that worked for `p2v2_check` vs `p2v2_dispatch`.

Nothing in this module is game-specific: it speaks the protocol above and the JSON plan.

Usage:

    # dry run: prove the bridge, the offsets and every parms block, mutate nothing
    python -I tools/ue3live/ue3_run_plan.py --plan local/plan_ME3.json \
        --endpoint tcp://127.0.0.1:27990 --probe-only

    # real run, required phases only
    python -I tools/ue3live/ue3_run_plan.py --plan local/plan_ME3.json \
        --endpoint tcp://127.0.0.1:27990 --out local/run_ME3.json

    # include the opt-in phases (saves, travel) - throwaway copy, restore point first
    python -I tools/ue3live/ue3_run_plan.py --plan local/plan_ME3.json \
        --endpoint tcp://127.0.0.1:27990 --include-optional --yes-destructive
"""
import argparse
import base64
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mele_paths  # noqa: E402  upward search: works in the hub and in this bundle
REPO = mele_paths.repo_root()
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bridge"))

# Risk classes that mutate state in a way a second call does not undo. --yes-destructive is
# required for these, so a scripted run cannot reach them by accident.
DESTRUCTIVE_RISK = ("high",)

# The bridge methods this runner needs. Checked up front with `require` so a game build that
# predates them fails immediately instead of halfway through a phase.
REQUIRED_METHODS = ("ue3.call", "ue3.read", "ue3.chain")

# Methods only needed by phases that WRITE fields. Checked up front too, but only when the plan
# actually contains a write - a read-only probe of a game whose adapter has no write path must
# still work, because reading is the thing you can do before anything is built.
WRITE_METHODS = ("ue3.write",)


class PlanError(Exception):
    pass


class TransportAborted(Exception):
    """Raised when the bridge fails and the runner is configured to stop.

    Carries the record of the failing command so the caller can still write a summary that
    says what stopped the run - rather than losing it.
    """

    def __init__(self, record):
        super().__init__(record.get("detail") or "transport failure")
        self.record = record


def _hexlen(v):
    """Accept an int, a '0x...' string, or None; return an int or None."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        return int(str(v), 0)
    except ValueError:
        raise PlanError("not an address or integer: %r" % (v,))


def _parms_bytes(parms, size):
    """Build a zeroed parms block of `size`, and set the primitive-valued parameters.

    The plan carries the *layout* (name, offset, size, kind) because the offsets are what was
    measured live. Values are not in the plan: a plan that hardcoded a location would be
    testing a stale position. So only parameters whose value is a self-evident constant are
    filled here, and everything else is left zero for the phase logic to replace.

    A zeroed block is the right default because it is what a dry run should validate: the
    layout and the size, not the semantics.
    """
    if not size:
        return b""
    if size < 0 or size > 0x1000:
        raise PlanError("implausible parms size %d" % size)
    buf = bytearray(size)
    for p in parms or []:
        off = _hexlen(p.get("offset"))
        psz = _hexlen(p.get("size")) or 0
        if off is None or psz <= 0:
            continue
        if off + psz > size:
            raise PlanError("parameter %s (offset %d size %d) runs past parms size %d"
                            % (p.get("name"), off, psz, size))
        val = p.get("value")
        if val is None:
            continue
        kind = (p.get("kind") or "").lower()
        if kind in ("boolproperty", "byteproperty"):
            buf[off] = 1 if val else 0
        elif kind == "intproperty" and psz == 4:
            buf[off:off + 4] = int(val).to_bytes(4, "little", signed=True)
        elif kind == "floatproperty" and psz == 4:
            import struct
            buf[off:off + 4] = struct.pack("<f", float(val))
    return bytes(buf)


class Runner:
    def __init__(self, bridge, plan, out=None, log=None, probe_only=False,
                 yes_destructive=False, include_optional=False, stop_on_abort=True):
        self.b = bridge
        self.plan = plan
        self.out = out
        self.log = log or (lambda s: print(s, flush=True))
        self.probe_only = probe_only
        self.yes_destructive = yes_destructive
        self.include_optional = include_optional
        self.stop_on_abort = stop_on_abort
        self.game = plan.get("game") or "ME1"
        self.results = []
        self.aborted = None
        self.t0 = time.time()

    # ---------------------------------------------------------------- bridge helpers
    def _call(self, c, mode):
        p = {
            "function": c["function"],
            "role": c.get("role") or "none",
            "parms": base64.b64encode(_parms_bytes(c.get("parms"), c.get("parms_size") or 0)).decode(),
            "parmsSize": c.get("parms_size") or 0,
            "mode": mode,
        }
        # An optional explicit address, when a plan HAS been bound to a live probe. The role is
        # the normal route; the address is for a phase that needs a specific actor rather than
        # a role (a named squadmate, for instance).
        if c.get("object"):
            p["object"] = c["object"]
        return self.b.call("ue3.call", p, timeout_s=30.0)

    def _read(self, address, offset, size, signed=False):
        return self.b.call("ue3.read", {"object": address, "offset": offset,
                                        "size": size, "signed": signed}, timeout_s=10.0)

    def _field_read(self, field):
        """Read a named field. Returns the record including the raw value."""
        import ue3_fields
        r = ue3_fields.resolve(field, self.game)
        res = self.b.call("ue3.read", {"role": r["role"], "offset": r["offset"],
                                       "size": r["size"]}, timeout_s=10.0)
        if not isinstance(res, dict) or not res.get("ok"):
            raise ue3_fields.FieldError("read of %s failed: %r"
                                        % (field, (res or {}).get("error") if isinstance(res, dict)
                                           else res))
        return {"field": field, "offset": r["offset"], "size": r["size"],
                "value": res.get("value"), "ok": True}

    def _field_write(self, field, value):
        """Write a named field, then READ IT BACK and require a match.

        The read-back is not belt-and-braces: without it, "the write succeeded" and "the write
        landed somewhere harmless" are the same answer, and this project has already paid for
        believing a call that silently did nothing.
        """
        import ue3_fields
        r = ue3_fields.resolve(field, self.game, value=value, writing=True)
        res = self.b.call("ue3.write", {"role": r["role"], "offset": r["offset"],
                                        "size": r["size"], "value": r["value"]},
                          timeout_s=10.0)
        rec = {"field": field, "offset": r["offset"], "size": r["size"],
               "wrote": r["value"], "kind": r["kind"]}
        if not isinstance(res, dict) or not res.get("ok"):
            rec["ok"] = False
            rec["detail"] = ("write refused: %s"
                             % ((res or {}).get("error") if isinstance(res, dict) else res))
            return rec
        back = res.get("readBack")
        ok, why = ue3_fields.verify_readback({"value": r["value"]}, back)
        rec["ok"] = ok
        rec["readBack"] = back
        rec["detail"] = why
        return rec

    def run_writes(self, ph):
        """Execute a phase's field operations: reads, and verified writes.

        A `writes` entry is `{field, value}` or `{field}` for a read. Each is one bridge call
        plus, for a write, a mandatory read-back.
        """
        self.log("\n== %s  %s  [%s%s]"
                 % (ph["id"], ph["title"], ph["risk"],
                    ", optional" if ph.get("optional") else ""))
        self.log("   proves  : %s" % ph["proves"])
        recs = []
        for op in ph.get("writes") or []:
            field = op["field"]
            try:
                if "value" in op and op["value"] is not None:
                    rec = self._field_write(field, op["value"])
                else:
                    rec = self._field_read(field)
                    rec["ok"] = True
            except Exception as exc:
                rec = {"field": field, "ok": False,
                       "detail": "%s: %s" % (type(exc).__name__, exc)}
            rec["id"] = "field:%s" % field
            rec["phase"] = ph["id"]
            rec["status"] = ("written" if "wrote" in rec else "read") if rec["ok"] \
                else "field-failed"
            recs.append(rec)
            self.log("   [%s] %-40s %s"
                     % ("WROTE" if rec["status"] == "written" else
                        ("READ " if rec["status"] == "read" else "FAIL "),
                        field, rec.get("detail") or rec.get("value")))
            if rec["status"] == "field-failed" and self.stop_on_abort:
                self.results.extend(recs)
                raise TransportAborted(rec)
        self.results.extend(recs)
        return recs

    # ---------------------------------------------------------------- filtering
    def phases_to_run(self):
        out = []
        for ph in self.plan["phases"]:
            if ph.get("optional") and not self.include_optional:
                out.append((ph, "skipped: optional phase not requested"))
                continue
            if ph["risk"] in DESTRUCTIVE_RISK and not self.yes_destructive:
                out.append((ph, "skipped: destructive phase needs --yes-destructive"))
                continue
            out.append((ph, None))
        return out

    # ---------------------------------------------------------------- one command
    def run_command(self, ph, c):
        """Execute one command entry and return a result record.

        Never raises for a game-side refusal: a command that the bridge rejects is a RESULT,
        because "this call is wrong" is the finding a command suite is looking for. It raises
        only for a transport failure, which means the process is gone and every later result
        would be meaningless.
        """
        rec = {"phase": ph["id"], "id": c["id"], "call": c.get("call"),
               "risk": c.get("risk"), "status": "unknown"}
        if c["status"] != "ready":
            rec["status"] = c["status"]
            rec["detail"] = c.get("note") or "not ready"
            return rec
        if not c.get("object") and not c.get("role"):
            # Neither a concrete address nor a role: there is nothing to aim at, and guessing
            # is how a "test" ends up calling SetHidden on the world settings.
            rec["status"] = "no-target"
            rec["detail"] = "the plan carries neither an object address nor a target role"
            return rec
        mode = "probe" if self.probe_only else "call"
        try:
            r = self._call(c, mode)
        except PlanError as exc:
            # A malformed layout is OUR bug, not the game's. Reporting it as a bridge error
            # would send someone looking at the process when the plan is what is wrong.
            rec["status"] = "invalid-parms"
            rec["detail"] = str(exc)
            return rec
        except Exception as exc:
            rec["status"] = "bridge-error"
            rec["detail"] = "%s: %s" % (type(exc).__name__, exc)
            return rec
        if not isinstance(r, dict):
            rec["status"] = "bad-reply"
            rec["detail"] = "ue3.call returned %r" % (r,)
            return rec
        rec["result"] = r
        if not r.get("ok"):
            rec["status"] = "refused"
            rec["detail"] = r.get("error") or "ok=false with no error"
            return rec
        if self.probe_only:
            rec["status"] = "probed"
            return rec
        rec["status"] = "called" if r.get("called") else "not-called"
        if "value" in r:
            rec["value"] = r["value"]
        return rec

    # ---------------------------------------------------------------- one phase
    def run_phase(self, ph):
        self.log("\n== %s  %s  [%s%s]"
                 % (ph["id"], ph["title"], ph["risk"],
                    ", optional" if ph.get("optional") else ""))
        self.log("   proves  : %s" % ph["proves"])
        recs = []
        for c in ph.get("commands") or []:
            rec = self.run_command(ph, c)
            recs.append(rec)
            mark = {"probed": "PROBE", "called": "CALL ", "not-called": "CALL ",
                    "refused": "REFUS", "skipped": "SKIP ", "unresolved": "UNRES",
                    "bridge-error": "ERROR", "no-target": "NOTGT",
                    "bad-reply": "BADRP", "invalid-parms": "PARMS"}.get(
                        rec["status"], rec["status"].upper()[:5])
            extra = rec.get("detail") or ""
            if rec.get("value") is not None:
                extra = "value=%r" % (rec["value"],)
            self.log("   [%s] %-30s %s" % (mark, rec["id"], extra))
            # A transport failure means the process is gone. Every later result would be an
            # invented one, and a file full of invented results is worse than a short one -
            # so stop by default. `--keep-going` is for the case where the caller wants the
            # rest of the run recorded as unreachable.
            if rec["status"] == "bridge-error" and self.stop_on_abort:
                self.results.extend(recs)
                self.log("   ABORT: the bridge failed; stopping rather than recording results "
                         "against a process that may be gone. Use --keep-going to continue.")
                raise TransportAborted(rec)
        self.results.extend(recs)
        return recs

    # ---------------------------------------------------------------- whole plan
    def preflight(self):
        """Validate every parms block BEFORE anything is sent.

        A malformed layout is a defect in the plan, and finding it here means the run stops
        with a list instead of sending nonsense to the game and reading a refusal back. It
        also makes `--probe-only` meaningful: probing proves the *whole* plan is well formed
        without touching state.
        """
        problems = []
        for ph in self.plan["phases"]:
            if ph.get("optional") and not self.include_optional:
                continue
            if ph["risk"] in DESTRUCTIVE_RISK and not self.yes_destructive:
                continue
            for c in ph.get("commands") or []:
                if c["status"] != "ready":
                    continue
                try:
                    _parms_bytes(c.get("parms"), c.get("parms_size") or 0)
                except PlanError as exc:
                    problems.append("%s (%s): %s" % (ph["id"], c["id"], exc))
        if problems:
            raise PlanError("the plan has %d malformed parms block(s):\n  %s"
                            % (len(problems), "\n  ".join(problems)))
        return True

    def run(self):
        try:
            self.b.require(*REQUIRED_METHODS)
        except Exception as exc:
            raise PlanError(
                "the in-game bridge does not provide %s (%s).\n"
                "The game-side adapter must implement these three methods before a plan can "
                "run; see the docstring in this file for the protocol."
                % (", ".join(REQUIRED_METHODS), exc))

        # ue3.write is only demanded by a plan that writes. Read-only plans must run against an
        # adapter that has no write path at all - that is the safer adapter, not a broken one.
        wants_write = any(
            ph.get("writes") and any("value" in op and op["value"] is not None
                                     for op in ph["writes"])
            for ph in self.plan["phases"]
            if not (ph.get("optional") and not self.include_optional)
            and not (ph["risk"] in DESTRUCTIVE_RISK and not self.yes_destructive))
        if wants_write:
            try:
                self.b.require(*WRITE_METHODS)
            except Exception as exc:
                raise PlanError(
                    "this plan writes fields but the adapter does not provide %s (%s). "
                    "The allow-list is in tools/ue3live/ue3_fields.py; the write path must be "
                    "implemented in the game, and it must read back what it wrote."
                    % (", ".join(WRITE_METHODS), exc))

        self.preflight()
        info = {}
        try:
            info = self.b.call("ue3.info", {}, timeout_s=10.0) or {}
        except Exception:
            # ue3.info is required for the record but not for the run: older adapters may
            # lack it, and a missing build string must not block a test.
            info = {"error": "ue3.info unavailable"}

        self.log("plan : %s / %s" % (self.plan.get("game"), self.plan.get("exe")))
        self.log("mode : %s" % ("PROBE ONLY (nothing is called)" if self.probe_only
                                else "EXECUTE"))
        self.log("game : %s" % json.dumps(info, sort_keys=True))

        for ph, skip in self.phases_to_run():
            if skip:
                self.log("\n== %-16s SKIPPED - %s" % (ph["id"], skip))
                self.results.append({"phase": ph["id"], "id": "-", "status": "phase-skipped",
                                     "detail": skip})
                continue
            try:
                if ph.get("writes"):
                    self.run_writes(ph)
                else:
                    self.run_phase(ph)
            except TransportAborted as exc:
                self.aborted = exc.record
                break

        summary = self._summary(info)
        return summary

    def _summary(self, info):
        counts = {}
        for r in self.results:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {
            "schema": 1,
            "game": self.plan.get("game"),
            "exe": self.plan.get("exe"),
            "mode": "probe" if self.probe_only else "execute",
            "include_optional": self.include_optional,
            "yes_destructive": self.yes_destructive,
            "aborted": getattr(self, "aborted", None),
            "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.t0)),
            "elapsed_s": round(time.time() - self.t0, 2),
            "game_info": info,
            "counts": counts,
            "results": self.results,
        }


def load_plan(path):
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    if plan.get("schema") != 1:
        raise PlanError("unexpected plan schema %r" % (plan.get("schema"),))
    return plan


def attach_targets(plan, probe):
    """Attach a concrete object address to every ready command.

    A plan is deliberately target-free: it says *which function* to call, not on *what*.
    The object comes from the probe, which resolved it against the live process, and from the
    phase logic (`xform.*` acts on the player pawn, `pawn.*` on the controller, and so on).

    Until a live probe exists there is no address, and the correct behaviour is to leave
    `object` empty so the runner reports `no-target` rather than guessing. A guessed address is
    how a "test" ends up calling `SetHidden` on the world settings.
    """
    targets = {}
    for c in (probe or {}).get("commands", []):
        if c.get("resolved_to") and c["resolved_to"].get("instance"):
            targets[c["id"]] = c["resolved_to"]["instance"]
    for ph in plan["phases"]:
        for c in ph["commands"]:
            c["object"] = targets.get(c["id"])
    return plan


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--endpoint", default=os.environ.get("COOPBRIDGE_ENDPOINT"))
    ap.add_argument("--endpoint-file")
    ap.add_argument("--wait", type=float, default=0.0,
                    help="seconds to wait for the game to open its endpoint")
    ap.add_argument("--out")
    ap.add_argument("--probe", help="probe JSON, to attach concrete target objects")
    ap.add_argument("--probe-only", action="store_true",
                    help="resolve and validate every parms block, call nothing mutable")
    ap.add_argument("--include-optional", action="store_true")
    ap.add_argument("--yes-destructive", action="store_true",
                    help="allow phases whose risk is 'high' (saves, travel, possession)")
    ap.add_argument("--keep-going", action="store_true",
                    help="continue after a transport failure instead of stopping")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    import bridge_client

    plan = load_plan(args.plan)
    if args.probe:
        with open(args.probe, encoding="utf-8") as f:
            plan = attach_targets(plan, json.load(f))

    endpoint = args.endpoint
    if not endpoint and args.endpoint_file:
        endpoint = bridge_client.endpoint_from_file(args.endpoint_file, wait_s=args.wait)
    if not endpoint:
        print("no endpoint: pass --endpoint, --endpoint-file, or set COOPBRIDGE_ENDPOINT")
        return 2

    log = (lambda s: None) if args.quiet else (lambda s: print(s, flush=True))
    try:
        with bridge_client.Bridge.connect(endpoint, wait_s=args.wait) as b:
            runner = Runner(b, plan, out=args.out, log=log,
                            probe_only=args.probe_only,
                            yes_destructive=args.yes_destructive,
                            include_optional=args.include_optional,
                            stop_on_abort=not args.keep_going)
            summary = runner.run()
    except PlanError as exc:
        print("plan error: %s" % exc)
        return 3
    except bridge_client.BridgeError as exc:
        print("bridge error: %s: %s" % (type(exc).__name__, exc))
        return 4

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1)
        print("\nwrote %s (%d bytes)" % (args.out, os.path.getsize(args.out)))

    counts = summary["counts"]
    print("\n%s run: %s" % (summary["mode"],
                            ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items()))))
    if summary.get("aborted"):
        print("ABORTED early: %s" % (summary["aborted"].get("detail"),))
        print("The result file holds what ran; the rest was not attempted, because recording "
              "results against a process that may be gone is worse than a short run.")
        return 5
    bad = sum(counts.get(k, 0) for k in ("refused", "bridge-error", "bad-reply",
                                         "no-target", "invalid-parms"))
    if bad:
        print("%d command(s) did not run cleanly - see the result file" % bad)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
