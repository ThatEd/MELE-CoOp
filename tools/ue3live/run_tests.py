#!/usr/bin/env python3
"""Run every game-free test in tools/ue3live and print ALL OK only when all of them pass.

    python -I tools/ue3live/run_tests.py

Nothing here starts or touches a game. Each child process is run with `-I` for the same
reason the README documents `-I`: these tools are meant to survive a stripped sys.path, and
running them any other way in CI would hide an import bug until the moment a game is open
and a restart is expensive.

A suite passes only on exit 0 AND a final line of `ALL OK` - the same rule tools/test_all.py
applies, so a suite that crashes after printing some OK lines is not counted as a pass.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mele_paths  # noqa: E402  upward search: works in the hub and in this bundle
REPO = mele_paths.repo_root()

SUITES = [
    # The conversation verdict decides whether a scene owns the player, and a mechanism
    # tested against a wrong detector proves nothing.
    "test_conversation_verdict.py",
    # Catalogue presence, target roles, dispatch safety, phase ordering.
    "test_command_suite.py",
    # The runner, driven against a real bridge server, so a marshalling bug shows up here
    # rather than in a crashed game.
    "test_run_plan.py",
    # The write path: refusal cases, because a general write primitive is the most dangerous
    # thing this tool could contain.
    "test_fields.py",
    # The shared-data-dir resolver. local/ is shared with other games, so a wrong answer here
    # means a tool silently finds nothing rather than failing.
    "test_mele_paths.py",
    # Offsets preflight: catches a file copied from another build before a game is launched.
    "test_preflight_offsets.py",
]


def run(script):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    t = time.time()
    p = subprocess.run([sys.executable, "-I", os.path.join(HERE, script)],
                       cwd=REPO, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    lines = [l.strip() for l in (p.stdout or "").splitlines() if l.strip()]
    ok = p.returncode == 0 and bool(lines) and lines[-1].startswith("ALL OK")
    if ok:
        return True, "%.1fs, %s" % (time.time() - t, lines[-1])
    tail = "\n".join(((p.stdout or "") + (p.stderr or "")).splitlines()[-15:])
    return False, "%.1fs, exit %d\n%s" % (time.time() - t, p.returncode, tail)


def main():
    failed = []
    for script in SUITES:
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
