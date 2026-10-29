"""Create and restore cheap save restore points, so a failed experiment costs seconds.

Why this exists: every mutation experiment ended the same way when it went wrong - the game
died and the owner had to re-watch minutes of unskippable cutscene to get back to the test
point. That cost dominated the whole effort: a handful of crashes meant a handful of
reloads, and in one game's case roughly twenty minutes each. The missing piece was never an
offset; it was a restore point.

This tool snapshots the save directories that make a reload cheap, and puts them back.

Design rules it follows, because save data is the one thing here that cannot be regenerated:

* **Read-only by default.** With no `--restore` it only lists and reports. Nothing is copied
  over an existing file unless `--restore` is passed.
* **Never deletes.** A restore moves the current state into a timestamped quarantine folder
  beside the snapshot instead of overwriting it, so a wrong restore is itself reversible.
* **Writes a manifest.** Every snapshot records each file's size and SHA-256, and a restore
  verifies the files it lays down against that manifest. A restore that silently half-worked
  would be worse than no restore at all.
* **Refuses to guess the path.** The save root is passed in, or found via the known
  Legendary Edition layout under the *real* Documents folder - which is frequently not on C:.

Finding the save root deserves its own warning: Documents is often redirected to another
drive, and on the machine this was written for it lived on `F:`. Searching
`%USERPROFILE%\\Documents` therefore finds nothing at all, which reads as "this game has no
saves" rather than "looked in the wrong place". Resolve the real path from the registry
shell folders, never from the user profile directory.

Usage:

    python -I tools/saverestore/saverestore.py --list
    python -I tools/saverestore/saverestore.py --snapshot before-p2-test --games ME1
    python -I tools/saverestore/saverestore.py --restore before-p2-test

Windows only: it reads the registry to resolve the real Documents path.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

SAVE_RELATIVE = os.path.join("BioWare", "Mass Effect Legendary Edition", "Save")
SNAPSHOT_DIRNAME = "saverestore-snapshots"


def real_documents():
    """The actual Documents folder, which is often not under the user profile.

    Windows lets a user relocate Documents to any drive, so shell-folder resolution is the
    only reliable way to find it. Defaulting to %USERPROFILE%\\Documents gives a path where
    nothing exists, which then reads as "no saves" instead of "wrong place".
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        try:
            value = winreg.QueryValueEx(key, "Personal")[0]
        finally:
            winreg.CloseKey(key)
        return os.path.expandvars(value)
    except OSError:
        return os.path.join(os.path.expanduser("~"), "Documents")


def find_save_root(explicit=None):
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates.append(os.path.join(real_documents(), SAVE_RELATIVE))
    candidates.append(os.path.join(os.path.expanduser("~"), "Documents", SAVE_RELATIVE))
    for path in candidates:
        if path and os.path.isdir(path):
            return os.path.abspath(path)
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def game_dirs(save_root, games):
    """{game: [save subdirectories]} for the requested games.

    Each child of Save/<GAME>/ (e.g. ME1/Ed00, ME2/Ed_33_...) is one save slot directory.
    Profile folders sit alongside them and are included, because losing the profile also
    means a login or character rebuild.
    """
    out = {}
    for game in games:
        base = os.path.join(save_root, game)
        if not os.path.isdir(base):
            continue
        out[game] = sorted(
            name for name in os.listdir(base)
            if os.path.isdir(os.path.join(base, name)))
    return out


def list_saves(save_root, games):
    print("save root: %s" % save_root)
    for game, dirs in game_dirs(save_root, games).items():
        total = 0
        for d in dirs:
            total += len([f for f in os.listdir(os.path.join(save_root, game, d))
                          if f.lower().endswith(".pcsav")])
        print("\n%s: %d save(s) in %d folder(s)" % (game, total, len(dirs)))
        for d in dirs:
            path = os.path.join(save_root, game, d)
            files = sorted((f for f in os.listdir(path) if f.lower().endswith(".pcsav")),
                           key=lambda f: os.path.getmtime(os.path.join(path, f)),
                           reverse=True)
            if not files:
                continue
            newest = files[0]
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(
                os.path.getmtime(os.path.join(path, newest))))
            print("   %-40s %2d file(s), newest %s (%s)" % (d, len(files), newest, when))


def snapshot(save_root, games, label):
    if not label or "/" in label or "\\" in label:
        print("label must be a plain name, got %r" % label)
        return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = os.path.join(save_root, SNAPSHOT_DIRNAME, "%s__%s" % (stamp, label))
    manifest = {"label": label, "created": stamp, "save_root": save_root, "files": {}}
    copied = 0
    for game, dirs in game_dirs(save_root, games).items():
        for d in dirs:
            src = os.path.join(save_root, game, d)
            for name in os.listdir(src):
                sp = os.path.join(src, name)
                if not os.path.isfile(sp):
                    continue
                rel = os.path.relpath(sp, save_root)
                dst = os.path.join(target, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(sp, dst)
                manifest["files"][rel] = {"size": os.path.getsize(sp),
                                          "sha256": sha256(sp)}
                copied += 1
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print("snapshot '%s' -> %s" % (label, target))
    print("copied %d file(s); size and SHA-256 recorded in manifest.json" % copied)
    return 0


def find_snapshot(save_root, label):
    base = os.path.join(save_root, SNAPSHOT_DIRNAME)
    if not os.path.isdir(base):
        return None
    matches = sorted(d for d in os.listdir(base) if d.endswith("__" + label))
    return os.path.join(base, matches[-1]) if matches else None


def restore(save_root, label):
    snap = find_snapshot(save_root, label)
    if not snap:
        print("no snapshot labelled %r under %s" % (label, save_root))
        return 1
    with open(os.path.join(snap, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)

    # Quarantine the current state first. Overwriting saves in place, with no way back, is
    # how a careful tool becomes the thing that lost the data.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    quarantine = os.path.join(save_root, SNAPSHOT_DIRNAME, "quarantine__%s" % stamp)
    moved = 0
    for rel in manifest["files"]:
        current = os.path.join(save_root, rel)
        if os.path.isfile(current):
            dest = os.path.join(quarantine, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(current, dest)
            moved += 1
    print("current state quarantined at %s" % quarantine)
    print("   %d file(s) moved there, not deleted" % moved)

    restored, bad = 0, []
    for rel, info in manifest["files"].items():
        src = os.path.join(snap, rel)
        dst = os.path.join(save_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        if sha256(dst) != info["sha256"]:
            bad.append(rel)
        restored += 1
    print("restored %d file(s) from %s" % (restored, snap))
    if bad:
        print("MISMATCH after restore - do not trust this state:")
        for rel in bad:
            print("   %s" % rel)
        return 2
    print("all restored files match the manifest")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save-root", help="Save/ directory (otherwise resolved automatically)")
    ap.add_argument("--games", nargs="+", default=["ME1", "ME2", "ME3"],
                    choices=["ME1", "ME2", "ME3"])
    ap.add_argument("--list", action="store_true", help="report saves (default, read-only)")
    ap.add_argument("--snapshot", metavar="LABEL", help="snapshot saves under LABEL")
    ap.add_argument("--restore", metavar="LABEL", help="restore the snapshot named LABEL")
    args = ap.parse_args()

    save_root = find_save_root(args.save_root)
    if not save_root:
        print("could not find the Legendary Edition save directory.")
        print("Documents resolves to: %s" % real_documents())
        print(r"pass --save-root to point at ...\BioWare\Mass Effect Legendary Edition\Save")
        return 1

    if args.restore:
        return restore(save_root, args.restore)
    if args.snapshot:
        return snapshot(save_root, args.games, args.snapshot)
    list_saves(save_root, args.games)
    return 0


if __name__ == "__main__":
    sys.exit(main())
