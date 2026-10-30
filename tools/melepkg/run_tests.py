#!/usr/bin/env python3
"""Run every game-free test in tools/melepkg. Prints ALL OK only when all pass.

    python -I tools/melepkg/run_tests.py

`test_container_format.py` skips cleanly when the game install or its Oodle DLL is absent, so
this is safe to run on a machine with no Mass Effect copy - which is the point, because the
container model is what makes offline package work possible and it should be checkable anywhere.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

SUITES = [
    # The container invariants and the single-block decode. Skips without an install.
    "test_container_format.py",
    # The import and export tables: the coordinate space, the four closures, and the
    # ownership answers the co-op work depends on.
    "test_mele_tables.py",
    # Bytecode extents, and the deliberate absence of a disassembler.
    "test_ue3_bytecode.py",
    # The property-tag reader: the layout's falsifiable closure gap, the struct-array decoder,
    # and the game-class routing asymmetry across the three installs.
    "test_mele_props.py",
    # The install index: compact reference format and its query semantics.
    "test_mele_index.py",
]


def run(script):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    t = time.time()
    p = subprocess.run([sys.executable, "-I", os.path.join(HERE, script)],
                       cwd=os.path.dirname(HERE), env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    lines = [l.strip() for l in (p.stdout or "").splitlines() if l.strip()]
    ok = p.returncode == 0 and bool(lines) and lines[-1].startswith("ALL OK")
    if ok:
        return True, "%.1fs, %s" % (time.time() - t, lines[-1])
    tail = "\n".join(((p.stdout or "") + (p.stderr or "")).splitlines()[-15:])
    return False, "%.1fs, exit %d\n%s" % (time.time() - t, p.returncode, tail)


def main():
    failed = []
    for script in SUITES:
        path = os.path.join(HERE, script)
        if not os.path.isfile(path):
            print("FAIL  %-34s (missing)" % script)
            failed.append(script)
            continue
        ok, note = run(script)
        print("%s  %-34s (%s)" % ("PASS" if ok else "FAIL", script, note), flush=True)
        if not ok:
            failed.append(script)
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
