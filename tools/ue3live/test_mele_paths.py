"""Tests for the shared-data-dir resolver.

`local/` is shared with other games' work, so Mass Effect's files live under `local/MELE/`. The
resolver is the single place that knows this, and these tests exist because a wrong answer here is
silent: a tool that resolves to the wrong directory finds *nothing* and reports "no data" rather
than "wrong path", which is the failure mode that cost hours earlier in this project.

What is pinned:

1. `$MELE_DATA_DIR` wins over everything.
2. `local/MELE` wins when it exists.
3. The pre-move `local` layout still resolves, so a tool cannot break merely because a directory
   was reorganised.
4. The offset-file spelling fallback works - the LE2 dump is `offsets_LE2.discovery.json`, not
   `offsets_LE2.json`.
5. `explain()` names the root, so an error message can say where it looked.

Run: python -I tools/ue3live/test_mele_paths.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mele_paths  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


class Sandbox:
    """A fake project root with a chosen layout, and the env restored afterwards.

    Patches `repo_root`, not `REPO`: `data_root()` resolves through the upward search now, and a
    test that patched the old constant would pass while exercising nothing - the exact shape of
    mistake this project keeps having to correct.
    """

    def __init__(self, layout):
        self.layout = layout

    def __enter__(self):
        self.tmp = tempfile.mkdtemp()
        for d in self.layout:
            os.makedirs(os.path.join(self.tmp, d), exist_ok=True)
        self._old_root = mele_paths.repo_root
        self._old_env = os.environ.pop("MELE_DATA_DIR", None)
        mele_paths.repo_root = lambda: self.tmp
        return self.tmp

    def __exit__(self, *a):
        mele_paths.repo_root = self._old_root
        if self._old_env is not None:
            os.environ["MELE_DATA_DIR"] = self._old_env
        else:
            os.environ.pop("MELE_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


@case("the moved layout wins when it exists")
def t_moved_wins():
    with Sandbox(["local/MELE/maps", "local/maps"]) as tmp:
        got = mele_paths.data_root()
        if got != os.path.join(tmp, "local", "MELE"):
            return "fail", "resolved to %r" % (got,)
        return "ok", "local/MELE"


@case("the pre-move layout still resolves")
def t_legacy():
    with Sandbox(["local/maps"]) as tmp:
        got = mele_paths.data_root()
        if got != os.path.join(tmp, "local"):
            return "fail", "resolved to %r" % (got,)
        return "ok", "fell back to local"


@case("$MELE_DATA_DIR overrides both")
def t_env():
    with Sandbox(["local/MELE/maps"]) as tmp:
        other = os.path.join(tmp, "elsewhere")
        os.makedirs(other)
        os.environ["MELE_DATA_DIR"] = other
        try:
            got = mele_paths.data_root()
            if got != other:
                return "fail", "resolved to %r, expected %r" % (got, other)
            if mele_paths.maps_dir() != os.path.join(other, "maps"):
                return "fail", "maps_dir ignored the override"
            return "ok", "override honoured"
        finally:
            del os.environ["MELE_DATA_DIR"]


@case("the offset-file spelling fallback finds the LE2 discovery name")
def t_offsets_spelling():
    with Sandbox(["local/MELE/offsets"]) as tmp:
        d = os.path.join(tmp, "local", "MELE", "offsets")
        open(os.path.join(d, "offsets_LE2.discovery.json"), "w").write("{}")
        got = mele_paths.offsets_file("LE2")
        if os.path.basename(got) != "offsets_LE2.discovery.json":
            return "fail", "resolved to %r" % (os.path.basename(got),)
        # A plain name must still be found.
        open(os.path.join(d, "offsets_LE1.json"), "w").write("{}")
        if os.path.basename(mele_paths.offsets_file("LE1")) != "offsets_LE1.json":
            return "fail", "did not find offsets_LE1.json"
        # And a missing one must return a path, not raise: the caller reports absence.
        p = mele_paths.offsets_file("LE3")
        if not p.endswith("offsets_LE3.json"):
            return "fail", "unexpected missing-file path %r" % (p,)
        return "ok", "found both spellings; missing returns a path"


@case("explain() names the root and the subdirectories")
def t_explain():
    with Sandbox(["local/MELE/maps"]) as tmp:
        text = mele_paths.explain()
        for want in ("maps", "offsets", "probes", "MELE"):
            if want not in text:
                return "fail", "explain() omits %r:\n%s" % (want, text)
        return "ok", "names root and three subdirectories"


@case("the real data directory resolves to the moved layout")
def t_real():
    """Not a sandbox: this asserts the project as it actually is.

    Accepts either layout, because this file ships in two shapes - the hub
    (`<repo>/tools/ue3live`) and the standalone bundle (`MELE Co-Op Mod/tools/ue3live`), which
    resolves back to the hub's `local/MELE` when it can. A clean clone of the bundle has no data
    at all (it is regenerable and deliberately not committed), so that is a SKIP with the
    command to fix it - not a failure, which would make every fresh checkout red.

    What must never happen is resolving to a directory that is not a plausible data root.
    """
    root = mele_paths.data_root()
    if os.path.basename(root) not in ("MELE", "local"):
        return "fail", "resolved to an unexpected directory: %r" % (root,)
    missing = [g for g in ("ME1", "ME2", "ME3") if not os.path.isfile(mele_paths.map_file(g))]
    if missing:
        return "skip", ("%s: no identifier table for %s. Regenerate with "
                        "tools/melepkg/mele_script_map.py"
                        % (root, ", ".join(missing)))
    return "ok", "%s with all three map files present" % root


@case("the tool dir is found without a depth guess")
def t_tools_dir():
    """`tools_dir()` must be the folder holding `bridge`, in both layouts."""
    t = mele_paths.tools_dir()
    if not os.path.isdir(os.path.join(t, "bridge")):
        return "fail", "%r does not contain a bridge folder" % (t,)
    if os.path.basename(t) != "tools":
        return "fail", "tools_dir is %r" % (t,)
    return "ok", "sibling tools folder found next to ue3live"


@case("a fresh clone with no local/ resolves to its own root, not tools/")
def t_fresh_clone():
    """The bug this pins: without a `local/` above it, the `tools/bridge` fallback took ONE
    dirname of `<root>/tools/bridge` and got `<root>/tools`, so the data root became
    `<root>/tools/local` - a path that cannot exist and reads as "no data".

    The sandbox puts a `local/` at the temp root on purpose. Without it, the upward search
    legitimately keeps climbing past the clone to whatever contains it, which is correct
    behaviour but not the scenario under test. With it, `local/` wins immediately and the
    assertion is about the depth of the root, nothing else.
    """
    with Sandbox(["local/maps", "work/MELE Co-Op Mod/tools/bridge",
                  "work/MELE Co-Op Mod/tools/ue3live"]) as tmp:
        fake_here = os.path.join(tmp, "work", "MELE Co-Op Mod", "tools", "ue3live")
        saved = mele_paths.HERE
        mele_paths.HERE = fake_here
        try:
            root = mele_paths.repo_root()
        finally:
            mele_paths.HERE = saved
        if root != tmp:
            return "fail", "root is %r, expected %r" % (root, tmp)
        if os.path.basename(root) == "tools":
            return "fail", "resolved to tools/, which always means a wrong depth"
        return "ok", "climbed past the bundle to the root holding local/, not to tools/"


@case("the upward search finds a root from a nested start")
def t_upward():
    """The reason `repo_root` searches: the bundle sits at a different depth than the hub.

    Root has `local/MELE`; the start point is three levels down inside the bundle, which is
    exactly the hub-vs-bundle shape.
    """
    with Sandbox(["local/MELE", "MELE Co-Op Mod/tools/ue3live"]) as tmp:
        deep = os.path.join(tmp, "MELE Co-Op Mod", "tools", "ue3live")
        found = mele_paths._find_up("local", start=deep)
        if not found:
            return "fail", "upward search did not find local/ from %r" % (deep,)
        if found != os.path.join(tmp, "local"):
            return "fail", "found %r, expected the root's local/" % (found,)
        if os.path.dirname(found) != tmp:
            return "fail", "the found root is %r, not the sandbox root" % (os.path.dirname(found),)
        # And a name that does not exist must terminate rather than loop or raise.
        if mele_paths._find_up("definitely-not-here", start=deep):
            return "fail", "found something that does not exist"
        return "ok", "found the root's local/ from 3 levels down; terminates when absent"


def main():
    failures = 0
    for name, fn in CASES:
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = "fail", "%s: %s" % (type(exc).__name__, exc)
        mark = {"ok": "OK  ", "fail": "FAIL", "skip": "SKIP"}[status]
        print("%s %-58s %s" % (mark, name, detail))
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
