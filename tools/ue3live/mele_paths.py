"""Where this project's Mass Effect data lives, resolved in one place.

Why this module exists: `local/` is shared with other games' work in this same hub (city-game-
studio, f1-25, f1-manager-2024, xcom-2, re). Mass Effect's own files now live under
`local/MELE/`, and every tool that used to say `local/maps/...` has to agree about that. Scattering
the new path through four tools is how one of them keeps reading the old location and silently
finds nothing.

Resolution order, first hit wins:

1. `$MELE_DATA_DIR` - so a test or a second checkout can point somewhere else without editing code.
2. `<repo>/local/MELE` - the normal location.
3. `<repo>/local` - the pre-move layout. Kept deliberately: a tool that cannot find its data when
   the directory was merely moved is a worse failure than one that looks in two places, and the
   legacy layout is cheap to support. When `local/MELE` exists it always wins, so this cannot
   mask a partial move.

`maps/` holds the decompressed identifier tables (ME1/2/3.json); `offsets/` holds the verified
per-game offsets files; `probes/` holds raw live-inspection dumps; `refs/` holds upstream source.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def _find_up(name, start=HERE, limit=6):
    """Walk up from a directory until `name` is found; return it, or None.

    Why upward rather than a fixed depth: these tools live in two places - `tools/ue3live` in
    the shared hub, and `MELE Co-Op Mod/tools/ue3live` in the standalone bundle - and the
    directory that contains `local/` or `tools/bridge` is a different number of levels up in
    each. A fixed `dirname(dirname())` hardcodes one of the two, and the failure it produces is
    the quiet kind: the tools run, find nothing, and report "not present" rather than "wrong
    path". Searching upward is what makes a copy of this folder work anywhere.
    """
    cur = start
    for _ in range(limit):
        cand = os.path.join(cur, name)
        if os.path.exists(cand):
            return cand
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    return None


def repo_root():
    """Nearest ancestor that looks like a project root, else a sensible default.

    A project root is recognised by `local/` or a `tools/bridge` folder. Starting the search at
    the *highest* `tools` ancestor matters: in the standalone bundle the path is
    `MELE Co-Op Mod/tools/ue3live`, and searching from `ue3live` would look in
    `tools/local` - a directory that is never a project root and that a naive upward search will
    happily return because it does not exist yet.

    When nothing is found (a fresh clone of the bundle with no `local/` at all) the answer is the
    bundle root, not `tools/`: `local/` is where a user would create one, and reporting
    `<root>/local` lets `data_root()` fall through to the "not present" message instead of
    inventing a path under `tools/`.
    """
    start = HERE
    # Climb out of any enclosing `tools` directory. HERE is `<root>/tools/ue3live`, so the
    # basename of HERE is not `tools` - the ancestors are what have to be inspected, and the
    # highest `tools` in the chain is the one to escape.
    cur = HERE
    for _ in range(6):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        if os.path.basename(parent) == "tools":
            start = os.path.dirname(parent)
        cur = parent
    # `local/` is the best signal: its parent IS the project root.
    found = _find_up("local", start=start)
    if found:
        return os.path.dirname(found)
    # `tools/bridge` is the fallback signal, and its root is TWO levels up, not one -
    # taking a single dirname returns `<root>/tools`, which is where a fresh clone ended up
    # inventing `<root>/tools/local`. That is the bug this branch exists to not have.
    found = _find_up(os.path.join("tools", "bridge"), start=start)
    if found:
        return os.path.dirname(os.path.dirname(found))
    return start


def tools_dir():
    """The directory holding sibling tool folders (`bridge`, `ue3live`, ...).

    In the hub that is `<repo>/tools`; in the standalone bundle it is
    `MELE Co-Op Mod/tools`. Found by looking for this file's own parent's parent, which is
    `tools` in both layouts - so this one does not need to search.
    """
    return os.path.dirname(HERE)


def data_root():
    """The MELE data directory. Never raises; the caller reports if a file is missing."""
    env = os.environ.get("MELE_DATA_DIR")
    if env:
        return env
    root = repo_root()
    moved = os.path.join(root, "local", "MELE")
    if os.path.isdir(moved):
        return moved
    return os.path.join(root, "local")


def subdir(name):
    return os.path.join(data_root(), name)


def maps_dir():
    return subdir("maps")


def offsets_dir():
    return subdir("offsets")


def probes_dir():
    return subdir("probes")


def refs_dir():
    return subdir("refs")


def map_file(game):
    """<data>/maps/ME1.json and friends."""
    return os.path.join(maps_dir(), "%s.json" % game)


def offsets_file(game):
    """<data>/offsets/offsets_LE1.json and friends.

    Two spellings exist in the wild because the LE2 discovery dump is named
    `offsets_LE2.discovery.json`. Both are tried rather than guessing.
    """
    base = offsets_dir()
    for name in ("offsets_%s.json" % game, "offsets_%s.discovery.json" % game,
                 "offsets_%s.raw.json" % game):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            return p
    return os.path.join(base, "offsets_%s.json" % game)


def explain():
    """A short human-readable report, for a tool that cannot find what it needs."""
    root = data_root()
    env = os.environ.get("MELE_DATA_DIR")
    return ("MELE data root: %s (%s)\n"
            "  maps    : %s\n"
            "  offsets : %s\n"
            "  probes  : %s"
            % (root, "from $MELE_DATA_DIR" if env else "default", maps_dir(),
               offsets_dir(), probes_dir()))
