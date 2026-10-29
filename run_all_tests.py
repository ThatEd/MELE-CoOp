#!/usr/bin/env python3
"""Run every game-free test in this bundle and print ALL OK when all pass.

    python -I run_all_tests.py            every suite
    python -I run_all_tests.py ue3live    one suite

A suite passes only on exit 0 AND a final `ALL OK` line, so a suite that crashes after printing
some OK lines is not counted as a pass - the same rule the hub's `tools/test_all.py` applies.

Nothing here needs a game running. Two suites skip themselves when their data or the game install
is missing, and say which file and which command regenerates it, so a fresh clone is green with
visible gaps rather than red for a reason that has nothing to do with the code.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "tools")

SUITES = [
    ("ue3live", os.path.join(TOOLS, "ue3live", "run_tests.py")),
    ("melepkg", os.path.join(TOOLS, "melepkg", "run_tests.py")),
]


def run(path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    t = time.time()
    p = subprocess.run([sys.executable, "-I", path], cwd=HERE, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [l.strip() for l in (p.stdout or "").splitlines() if l.strip()]
    ok = p.returncode == 0 and bool(lines) and lines[-1] == "ALL OK"
    if ok:
        return True, "%.1fs" % (time.time() - t)
    tail = "\n".join(((p.stdout or "") + (p.stderr or "")).splitlines()[-20:])
    return False, "%.1fs, exit %d\n%s" % (time.time() - t, p.returncode, tail)


def main():
    wanted = sys.argv[1:] or [n for n, _ in SUITES]
    failed = []
    for name, path in SUITES:
        if name not in wanted:
            continue
        if not os.path.isfile(path):
            print("FAIL  %-12s (missing %s)" % (name, path))
            failed.append(name)
            continue
        ok, note = run(path)
        print("%s  %-12s (%s)" % ("PASS" if ok else "FAIL", name, note), flush=True)
        if not ok:
            failed.append(name)
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
