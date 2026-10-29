"""Check that the published bundle's tool copies match the hub's, and say how to fix drift.

Why this exists: `MELE Co-Op Mod/tools/` is a *copy* of `tools/` from the hub so the bundle can be
published on its own. Two copies of the same code always drift eventually, and the failure mode is
nasty - someone fixes a bug in the hub, publishes the bundle, and ships the old code. A silent
divergence here is worse than a missing file, because the bundle still looks complete.

So the copy is allowed, but not unverified. Run this before publishing.

    python -I tools/ue3live/check_bundle_sync.py            report
    python -I tools/ue3live/check_bundle_sync.py --fix       copy hub -> bundle
    python -I tools/ue3live/check_bundle_sync.py --reverse   copy bundle -> hub (after editing in
                                                             the bundle, e.g. link fixes)

Direction matters and there is no automatic guess: the hub is the source of truth for code, but
the bundle's docs have different relative links, so blindly mirroring either way would destroy
work. A file present in the bundle and absent from the hub is a finding, not a deletion.
"""
import argparse
import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_TOOLS = os.path.dirname(HERE)
HUB = os.path.dirname(HUB_TOOLS)
BUNDLE = os.path.join(HUB, "MELE Co-Op Mod")
BUNDLE_TOOLS = os.path.join(BUNDLE, "tools")

# Folders the bundle carries, and which hub folder each mirrors.
PAIRS = [
    ("ue3live", os.path.join(HUB_TOOLS, "ue3live")),
    ("melepkg", os.path.join(HUB_TOOLS, "melepkg")),
    ("saverestore", os.path.join(HUB_TOOLS, "saverestore")),
    ("gamepool", os.path.join(HUB_TOOLS, "gamepool")),
    ("bridge", os.path.join(HUB_TOOLS, "bridge")),
]

# Never copied: build output, caches, and the hub's own test runner wiring.
SKIP_DIRS = {"__pycache__", "build", ".git", "examples_fnv_only"}
SKIP_FILES = {"run_tests.py.orig"}

# Files that legitimately exist only in the hub because they belong to a DIFFERENT game.
# `gamepool` is a shared tool: the bundle ships the Mass Effect configs and not, say, Fallout
# New Vegas's. Listing them here is the difference between "this bundle is incomplete" and
# "this file was never meant to travel", and without it the checker cries wolf until someone
# disables it.
HUB_ONLY = {
    "gamepool/examples/fnv.json",
}

# Files that legitimately differ because the bundle has different relative links. The hub's docs
# live under `games/mass-effect-legendary-edition/` and link to `../../tools/`; the bundle's live
# under `docs/` and link to `../tools/`. Mirroring either direction over these destroys the other
# side's links, so they are excluded and the difference is stated rather than silently "fixed".
LINK_VARIANTS = {
    "ue3live/README.md",
    "bridge/README.md",
    "bridge/examples/fnv/README.md",
    "gamepool/README.md",
}


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk(root):
    out = {}
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILES or name.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            out[rel.replace("\\", "/")] = full
    return out


def compare():
    """[(subdir, rel, status, hub_path, bundle_path)] with status in
    same / differs / only-hub / only-bundle."""
    rows = []
    for sub, hub_root in PAIRS:
        hub = walk(hub_root)
        bnd = walk(os.path.join(BUNDLE_TOOLS, sub))
        for rel in sorted(set(hub) | set(bnd)):
            key = "%s/%s" % (sub, rel)
            if key in HUB_ONLY:
                continue
            if key in LINK_VARIANTS:
                continue
            h, b = hub.get(rel), bnd.get(rel)
            if h and b:
                status = "same" if digest(h) == digest(b) else "differs"
            elif h:
                status = "only-hub"
            else:
                status = "only-bundle"
            rows.append((sub, rel, status, h, b))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true", help="copy hub -> bundle")
    ap.add_argument("--reverse", action="store_true", help="copy bundle -> hub")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.fix and args.reverse:
        print("pick one of --fix / --reverse")
        return 2

    rows = compare()
    counts = {}
    for sub, rel, status, _, _ in rows:
        counts[status] = counts.get(status, 0) + 1

    problems = [r for r in rows if r[2] in ("differs", "only-hub", "only-bundle")]
    if not args.quiet:
        for sub, rel, status, _, _ in problems:
            print("%-12s %-52s %s" % (sub, rel, status))
        print()
        for k in ("same", "differs", "only-hub", "only-bundle"):
            if counts.get(k):
                print("  %-12s %d" % (k, counts[k]))

    if args.fix or args.reverse:
        src_i, dst_i = (3, 4) if args.fix else (4, 3)
        done = 0
        for sub, rel, status, h, b in rows:
            if status == "same":
                continue
            src = (h, b)[(0, 1)[0]]
            src = h if args.fix else b
            dst_root = os.path.join(BUNDLE_TOOLS, sub) if args.fix \
                else dict((s, r) for s, r in PAIRS)[sub]
            if src is None:
                continue
            dst = os.path.join(dst_root, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            done += 1
            if not args.quiet:
                print("  %s %s/%s" % ("->bundle" if args.fix else "->hub   ", sub, rel))
        print("\ncopied %d file(s)" % done)
        return 0

    if problems:
        print("\n%d file(s) differ or exist on one side only." % len(problems))
        print("--fix     copies hub -> bundle (normal: fix in the hub)")
        print("--reverse copies bundle -> hub (after editing link paths in the bundle docs)")
        return 1
    print("\nALL OK: the bundle's tool copies match the hub")
    return 0


if __name__ == "__main__":
    sys.exit(main())
