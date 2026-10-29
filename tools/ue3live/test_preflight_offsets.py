"""Tests for the offsets preflight. No game needed.

The preflight's job is to stop "I have an offsets file" being mistaken for "I have verified
offsets". So these tests are about the refusals:

1. **A missing key names the tool it breaks**, not just the key.
2. **A per-build offset taken from the wrong game is caught.** `PlayerController.Pawn` is
   `+0x3DC`/`+0x294`/`+0x3BC`, so a file copied from LE1 and relabelled LE3 is wrong by 0x20 and
   nothing else in the toolchain would notice.
3. **An unverified file is loud, and `--strict` refuses it outright.**
4. **The real files in this checkout pass**, so the preflight is not red for its own reasons.

Run: python -I tools/ue3live/test_preflight_offsets.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ue3_preflight_offsets as pf  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


GOOD = {
    "game": "LE3", "exe": "MassEffect3.exe", "verified": True,
    "objects_rva": "0x1887E40", "name_pools_rva": "0x17B33D0",
    "engine_global_rva": "0x18B31D0",
    "engine_game_players": "0x498", "local_player_actor": "0x68",
    "controller_pawn": "0x3BC", "pawn_controller": "0x374", "pawn_physics": "0x268",
    "process_event_vtable_slot": "0x230", "engine_game_viewport": "0x7A0",
}


class Temp:
    def __init__(self, doc):
        self.doc = doc

    def __enter__(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "offsets.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.doc, f)
        return self.path

    def __exit__(self, *a):
        shutil.rmtree(self.d, ignore_errors=True)


@case("a complete, verified file passes")
def t_good():
    with Temp(GOOD) as p:
        problems, warnings, info = pf.check("LE3", p)
    if problems:
        return "fail", "rejected a good file: %s" % problems
    if info.get("verified") is not True:
        return "fail", "verified flag not read"
    return "ok", "no problems"


@case("a missing RVA names the tool it breaks")
def t_missing_rva():
    d = dict(GOOD)
    del d["engine_global_rva"]
    with Temp(d) as p:
        problems, _, _ = pf.check("LE3", p)
    hit = [x for x in problems if "engine_global_rva" in x]
    if not hit:
        return "fail", "did not notice a missing engine_global_rva"
    if "chain" not in hit[0] and "player" not in hit[0]:
        return "fail", "the message does not say what breaks: %s" % hit[0]
    return "ok", hit[0][:70]


@case("a missing chain offset is a problem, not a warning")
def t_missing_chain():
    d = dict(GOOD)
    del d["controller_pawn"]
    with Temp(d) as p:
        problems, warnings, _ = pf.check("LE3", p)
    if not [x for x in problems if "controller_pawn" in x]:
        return "fail", "controller_pawn absence was only a warning"
    return "ok", "reported as a problem"


@case("an offset borrowed from another build is caught, and named")
def t_borrowed_offset():
    """The failure this whole file exists for."""
    d = dict(GOOD)
    d["controller_pawn"] = "0x3DC"          # LE1's value
    with Temp(d) as p:
        problems, _, _ = pf.check("LE3", p)
    hit = [x for x in problems if "controller_pawn" in x]
    if not hit:
        return "fail", "accepted LE1's controller_pawn in an LE3 file"
    if "LE1" not in hit[0]:
        return "fail", "did not say which build the value belongs to: %s" % hit[0]
    return "ok", hit[0][:80]


@case("the ProcessEvent slot is checked per build")
def t_pe_slot():
    """LE2's +0x228 in an LE3 file: the exact mistake that crashed LE2 the first time."""
    d = dict(GOOD)
    d["process_event_vtable_slot"] = "0x228"
    with Temp(d) as p:
        problems, _, _ = pf.check("LE3", p)
    if not [x for x in problems if "process_event" in x]:
        return "fail", "accepted LE2's ProcessEvent slot in an LE3 file"
    return "ok", "rejected 0x228 for LE3"


@case("an unverified file warns, and --strict refuses it")
def t_unverified():
    d = dict(GOOD)
    d["verified"] = False
    with Temp(d) as p:
        p1, w1, _ = pf.check("LE3", p, strict=False)
        p2, w2, _ = pf.check("LE3", p, strict=True)
    if p1:
        return "fail", "an unverified file was a hard problem without --strict"
    if not [x for x in w1 if "does NOT claim to be verified" in x]:
        return "fail", "no warning about the unverified flag"
    if not [x for x in p2 if "strict" in x.lower()]:
        return "fail", "--strict did not refuse it"
    return "ok", "warned by default, refused with --strict"


@case("a file whose game field disagrees is refused")
def t_wrong_game():
    d = dict(GOOD)
    d["game"] = "LE1"
    with Temp(d) as p:
        problems, _, _ = pf.check("LE3", p)
    if not [x for x in problems if "game=" in x]:
        return "fail", "accepted a file that says game=LE1 when checking LE3"
    return "ok", "refused"


@case("missing optional keys are warnings, not problems")
def t_optional():
    d = dict(GOOD)
    for k in ("controller_player", "controller_player_input", "pawn_location",
              "objects_count"):
        d.pop(k, None)
    with Temp(d) as p:
        problems, warnings, _ = pf.check("LE3", p)
    if problems:
        return "fail", "optional absences were hard failures: %s" % problems
    named = sum(1 for w in warnings if "optional" in w)
    if named < 3:
        return "fail", "optional absences were not reported (%d)" % named
    return "ok", "%d optional warnings, no problems" % named


@case("an absent file is reported, not crashed on")
def t_no_file():
    problems, _, _ = pf.check("LE3", os.path.join(HERE, "nope.json"))
    if not problems or "no offsets file" not in problems[0]:
        return "fail", "did not report a missing file: %s" % problems
    return "ok", problems[0][:50]


@case("invalid JSON is reported, not crashed on")
def t_bad_json():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "bad.json")
        open(p, "w", encoding="utf-8").write("{not json")
        problems, _, _ = pf.check("LE3", p)
        if not problems or "cannot read" not in problems[0]:
            return "fail", "did not report invalid JSON: %s" % problems
        return "ok", problems[0][:50]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@case("the real offsets files in this checkout preflight cleanly")
def t_real_files():
    """Catches the preflight going red on its own repository, which is how a check gets ignored."""
    import mele_paths
    missing = []
    for game in ("LE1", "LE2", "LE3"):
        path = mele_paths.offsets_file(game)
        if not os.path.isfile(path):
            missing.append(game)
            continue
        problems, _, info = pf.check(game, path)
        if problems:
            return "fail", "%s: %s" % (game, problems)
    if missing:
        return "skip", "no offsets file for %s" % ", ".join(missing)
    return "ok", "LE1, LE2 and LE3 all structurally valid"


@case("LE1 is marked verified and LE2/LE3 are not")
def t_honesty():
    """The flag is the whole point: a file nobody re-checked must not claim otherwise."""
    import mele_paths
    seen = {}
    for game in ("LE1", "LE2", "LE3"):
        path = mele_paths.offsets_file(game)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            seen[game] = json.load(f).get("verified")
    if not seen:
        return "skip", "no offsets files"
    if seen.get("LE1") is not True:
        return "fail", "LE1 is verified but the file does not say so: %r" % (seen,)
    for g in ("LE2", "LE3"):
        if seen.get(g) is True:
            return "fail", ("%s claims verified=true, but no live re-check has been run here. "
                            "That flag is what stops an untested offset being used." % g)
    return "ok", "LE1 true, %s false" % ", ".join(g for g in seen if g != "LE1")


def main():
    failures = skips = 0
    for name, fn in CASES:
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = "fail", "%s: %s" % (type(exc).__name__, exc)
        mark = {"ok": "OK  ", "fail": "FAIL", "skip": "SKIP"}[status]
        print("%s %-58s %s" % (mark, name, detail))
        if status == "fail":
            failures += 1
        elif status == "skip":
            skips += 1
    print()
    if failures:
        print("%d/%d FAILED" % (failures, len(CASES)))
        return 1
    print("ALL OK: %d cases%s" % (len(CASES), ", %d skipped" % skips if skips else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
