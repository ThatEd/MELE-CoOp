"""Index every package in the ME:LE install, so a class can be located offline.

Why this exists: the table parser answers questions about ONE package. Mod work asks the opposite
question - "which `.pcc` do I have to load, or hook, to get `BioTriggerVolume`?" - and answering it
by hand means opening packages until one matches. This walks the install once, caches the result,
and answers from the index.

## What is stored, and what that costs

Per package: its build, its export count, and for every export the object name, its class name, its
super name and its outer name. That is enough to answer "who defines X", "what is X's chain" and
"what does X contain" without reopening anything.

The index is a JSON file per build under `local/MELE/index/`. It is regenerated incrementally: a
package whose size and mtime have not changed is not re-parsed, so a re-run after adding one tool
change costs seconds rather than the ~20 minutes a cold walk of 12,704 packages takes.

`local/` is git-ignored, and deliberately: this is derived from the game's own data.

## Why the answers can be trusted

Every entry comes from the export table, which `mele_tables.build()` refuses to return unless four
independent closures pass - and those closures hold on all 12,704 packages. A package that fails is
recorded as an error in the index rather than skipped silently, so a query that returns nothing can
be distinguished from a query over an incomplete index.

Usage:

    python -I tools/melepkg/mele_index.py build   [--game ME3] [--limit N] [--rebuild]
    python -I tools/melepkg/mele_index.py find    <name> [--exact] [--game ME3] [--limit 40]
    python -I tools/melepkg/mele_index.py who     <class>            # which packages DEFINE it
    python -I tools/melepkg/mele_index.py chain   <class>            # superclass chain
    python -I tools/melepkg/mele_index.py members <class>            # its child exports
    python -I tools/melepkg/mele_index.py stats
"""
import argparse
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Bump this whenever the KEY FORMAT or the record shape changes, not only the record shape.
# Version 1 was keyed by basename; version 2 is keyed by the path relative to the game's own
# BioGame folder (so every DLC has a distinct key). A non-`--rebuild` run across that change kept
# the old basename records and added new relative records beside them, so `stats` reported ME1
# with 9,892 packages instead of 4,946 - exactly twice the truth, with no error and no warning.
# `cmd_build` now prunes keys that do not correspond to a file on disk, which makes the index a
# real mirror of the install instead of an append-only log.
INDEX_VERSION = 2


def load_tables():
    spec = importlib.util.spec_from_file_location(
        "mele_tables", os.path.join(HERE, "mele_tables.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_paths():
    """The offset/data-dir resolver, so the index lands where the rest of the tools look.

    `mele_paths` searches upward for `local/`, which makes the same script work from the hub and
    from the published bundle. Falling back to a directory beside the script keeps the tool usable
    when neither exists.
    """
    for cand in (os.path.join(HERE, "..", "ue3live", "mele_paths.py"),
                 os.path.join(HERE, "mele_paths.py")):
        cand = os.path.abspath(cand)
        if os.path.isfile(cand):
            spec = importlib.util.spec_from_file_location("mele_paths", cand)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    return None


def index_dir():
    paths = load_paths()
    base = None
    if paths is not None:
        try:
            base = paths.data_dir()
        except Exception:
            base = None
    if not base:
        base = os.path.join(HERE, "..", "..", "local", "MELE")
    d = os.path.join(os.path.abspath(base), "index")
    os.makedirs(d, exist_ok=True)
    return d


def package_dirs(game):
    """The base game's CookedPCConsole plus every DLC's, because a career runs through DLC.

    The first version yielded only `<game>/BioGame/CookedPCConsole`, which silently left out
    2,833 ME2 and 3,893 ME3 packages - every DLC mission. Nothing failed when it did: the index
    simply did not contain `DLC_EXP_Pack001`, so a query asked inside a DLC wrote "not present in
    this build", which is a *wrong answer that looks like a measurement*. Rule: a directory that
    holds packages is part of the population, and the population has to be counted.
    """
    mt = load_tables()
    pkg = mt.load_pkg()
    rel = os.path.join("Game", game, "BioGame", "CookedPCConsole")
    dlc_rel = os.path.join("Game", game, "BioGame", "DLC")
    for root in pkg.game_dirs():
        d = os.path.join(root, rel)
        if os.path.isdir(d):
            yield d
        dlc_root = os.path.join(root, dlc_rel)
        if os.path.isdir(dlc_root):
            for sub in sorted(os.listdir(dlc_root)):
                cand = os.path.join(dlc_root, sub, "CookedPCConsole")
                if os.path.isdir(cand):
                    yield cand


def index_path(game):
    return os.path.join(index_dir(), "%s.json" % game)


def load_index(game):
    p = index_path(game)
    if not os.path.isfile(p):
        return {"version": INDEX_VERSION, "game": game, "packages": {}}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != INDEX_VERSION:
        return {"version": INDEX_VERSION, "game": game, "packages": {}}
    return data


def save_index(game, data):
    p = index_path(game)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f)
    os.replace(tmp, p)


def _sig(path):
    st = os.stat(path)
    return [st.st_size, int(st.st_mtime)]


def pack_package(mt, path, game):
    """Parse one package into its index record. Raises SystemExit on a parse refusal.

    Only records what a query needs: name, class, super and outer per export. The full export dict
    carries offsets and raw words that would multiply the index size several-fold for no query
    benefit - ME1's build produced a 591 MB index before this was trimmed.
    """
    t = mt.build(path)
    exports, imports = t["exports"], t["imports"]
    rows = []
    for e in exports:
        rows.append([
            e["object_name"],
            describe_short(e["class_index"], exports, imports),
            describe_short(e["super_index"], exports, imports),
            describe_short(e["outer_index"], exports, imports),
        ])
    return {
        "exports": rows,
        "names": t["names"],
        "displacement": t["displacement"],
        "compressed": t["container"].get("compressed", True),
        "checks": [n for n, ok, _ in t["checks"] if ok],
    }


def describe_short(value, exports, imports):
    """Same resolution as `describe_index`, but compact enough to store 20 million times.

    `describe_index` returns `export:SFXGame` or `Core.Function`; the prefix is redundant because a
    package index is either positive (an export in this package) or negative (an import). So an
    export reference is stored by NAME and an import as `pkg.obj`, and a class-only query does not
    pay for the prefix.
    """
    if value == 0:
        return "None"
    if value > 0:
        k = value - 1
        if 0 <= k < len(exports):
            return exports[k]["object_name"] or "?"
        return "?%d" % k
    k = -value - 1
    if 0 <= k < len(imports):
        return "%s.%s" % (imports[k]["source_name"], imports[k]["object_name"])
    return "?i%d" % k


def _iter_entries(args):
    """Stream index records one build at a time.

    A full ME1 index is hundreds of megabytes, so loading all three at once would be pointless
    memory pressure for a query that prints forty lines. This yields per package and lets each
    build's dict go out of scope before the next is opened.
    """
    games = [args.game] if args.game else ["ME1", "ME2", "ME3"]
    for game in games:
        p = index_path(game)
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        pkgs = data.get("packages", {})
        for rel, rec in pkgs.items():
            if "error" in rec:
                continue
            yield game, rel, rec
        del data, pkgs


def cmd_build(args):
    mt = load_tables()
    games = [args.game] if args.game else ["ME1", "ME2", "ME3"]
    grand = 0
    for game in games:
        dirs = list(package_dirs(game))
        if not dirs:
            print("%s: install not found" % game)
            continue
        data = {"version": INDEX_VERSION, "game": game, "packages": {}}
        fresh = not args.rebuild and os.path.isfile(index_path(game))
        if fresh:
            data = load_index(game)
            fresh = data.get("version") == INDEX_VERSION
        if not fresh:
            # A version mismatch means the keys do not mean the same thing any more, so merging is
            # not safe. `load_index` already returns empty, but state it here so the reason is local.
            data = {"version": INDEX_VERSION, "game": game, "packages": {}}
        pkgs = data["packages"]
        files = []
        for d in dirs:
            for name in sorted(os.listdir(d)):
                if name.lower().endswith(".pcc"):
                    files.append(os.path.join(d, name))
        # Every key must be unique across the base game AND every DLC, so it is relative to the
        # game's own BioGame folder. Keying on `dirname(dirs[0])` would have made every DLC's
        # CookedPCConsole collide with the base game's, silently overwriting base records.
        game_root = None
        for d in dirs:
            up = d
            for _ in range(3):
                up = os.path.dirname(up)
            if game_root is None:
                game_root = up
        keys = {}
        for path in files:
            rel = os.path.relpath(path, game_root) if game_root else os.path.basename(path)
            keys[rel] = path
        # The index is a MIRROR of the install, so a key with no file behind it is stale and must
        # go. Without this a key-format change silently doubles every count (see INDEX_VERSION).
        stale = [k for k in pkgs if k not in keys]
        for k in stale:
            del pkgs[k]
        if stale:
            print("  %s: pruned %d stale key(s) with no file on disk" % (game, len(stale)),
                  flush=True)
        t0 = time.time()
        done = reused = 0
        errors = 0
        for i, (rel, path) in enumerate(keys.items()):
            sig = _sig(path)
            old = pkgs.get(rel)
            if old and old.get("sig") == sig and not args.rebuild:
                reused += 1
                continue
            try:
                rec = pack_package(mt, path, game)
            except SystemExit as exc:
                pkgs[rel] = {"sig": sig, "error": str(exc)[:200]}
                errors += 1
            except Exception as exc:
                pkgs[rel] = {"sig": sig, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
                errors += 1
            else:
                rec["sig"] = sig
                pkgs[rel] = rec
            done += 1
            if args.limit and done >= args.limit:
                break
            if done and done % 200 == 0:
                print("  %s: %d parsed, %d reused, %d errors (%.0fs)"
                      % (game, done, reused, errors, time.time() - t0), flush=True)
        save_index(game, data)
        ok = sum(1 for v in pkgs.values() if "error" not in v)
        print("%s: %d packages indexed (%d ok, %d error), %d reused, %d parsed in %.0fs"
              % (game, len(pkgs), ok, len(pkgs) - ok, reused, done, time.time() - t0))
        grand += len(pkgs)
    print()
    print("index at %s (%d package records total)" % (index_dir(), grand))
    return 0


def cmd_find(args):
    """Every export whose name matches, with the package that defines it."""
    want = args.name.lower()
    hits = 0
    for game, rel, rec in _iter_entries(args):
        for name, cls, sup, outer in rec["exports"]:
            if not name:
                continue
            ok = (name == args.name) if args.exact else (want in name.lower())
            if not ok:
                continue
            hits += 1
            if hits <= args.limit:
                print("%-4s %-46s %-34s %-22s outer=%s"
                      % (game, os.path.basename(rel), name, cls.split(".")[-1], outer))
            if args.exact and hits >= args.limit:
                break
    if not hits:
        print("no export matches %r%s" % (args.name, " (exact)" if args.exact else ""))
        return 1
    print()
    print("%d match(es)%s" % (hits, "" if hits <= args.limit else ", showing %d" % args.limit))
    return 0


def cmd_who(args):
    """Which packages DEFINE a class. A class is an export whose own class resolves to null."""
    found = 0
    for game, rel, rec in _iter_entries(args):
        for name, cls, sup, outer in rec["exports"]:
            if name != args.class_name:
                continue
            # A class's own class is null, which `describe_index` renders as "None".
            if cls != "None":
                continue
            found += 1
            if found <= args.limit:
                print("%-4s %-46s super=%-30s" % (game, os.path.basename(rel), sup))
    if not found:
        print("%r is not defined as a class in the index" % args.class_name)
        print("(it may still be an IMPORT - try the `find` command)")
        return 1
    print()
    print("defined in %d package(s)" % found)
    return 0


def cmd_chain(args):
    """Walk the superclass chain through the index, across packages."""
    name = args.class_name
    seen = set()
    depth = 0
    while name and name != "None" and name not in seen and depth < 24:
        seen.add(name)
        where = None
        sup = None
        for game, rel, rec in _iter_entries(args):
            for n, cls, s, outer in rec["exports"]:
                if n == name and cls == "None":
                    where = "%s:%s" % (game, os.path.basename(rel))
                    sup = s
                    break
            if where:
                break
        print("  %s%s -> %s" % ("  " * depth, name, sup or "(not in a package export)"))
        if not where:
            print("  %s(tail of the chain: %r is not defined by any indexed package)"
                  % ("  " * depth, name))
            return 0
        name = sup
        depth += 1
    return 0


def cmd_members(args):
    """A class's child exports in the package that defines it."""
    for game, rel, rec in _iter_entries(args):
        for name, cls, sup, outer in rec["exports"]:
            if name == args.class_name and cls == "None":
                self_ref = "export:%s" % name
                kids = [r for r in rec["exports"] if r[3] == self_ref]
                print("%s %s: %d child export(s)" % (game, os.path.basename(rel), len(kids)))
                for n, c, s, o in kids[:args.limit]:
                    print("   %-34s %-20s" % (n, c.split(".")[-1]))
                return 0
    print("%r is not defined as a class in the index" % args.class_name)
    return 1


def cmd_census(args):
    """Class definitions across builds: what is shared, what is per-build.

    "Does every build have X" is the question a port asks first, and counting class definitions -
    exports whose own class is null - across the whole index answers it without opening a package.
    """
    per = {}
    games = [args.game] if args.game else ["ME1", "ME2", "ME3"]
    for game in games:
        counts = {}
        for _g, _rel, rec in _iter_entries(type("A", (), {"game": game})()):
            for row in rec["exports"]:
                if row[1] == "None" and row[0]:
                    counts[row[0]] = counts.get(row[0], 0) + 1
        per[game] = counts
    if not per:
        print("no index found; run `build` first")
        return 1
    print("distinct class names defined, per build:")
    for game in games:
        print("  %-4s %6d" % (game, len(per[game])))
    if len(per) > 1:
        union = set()
        for c in per.values():
            union |= set(c)
        shared = sum(1 for c in union if all(c in per[g] for g in per))
        print("  union %d ; in all %d build(s): %d" % (len(union), len(per), shared))
        for game in games:
            others = [g for g in per if g != game]
            only = sum(1 for c in per[game]
                       if all(c not in per[o] for o in others))
            print("  %-4s only: %d" % (game, only))
    if args.names:
        want = args.names.lower()
        print()
        print("names containing %r:" % args.names)
        for game in games:
            hits = sorted(c for c in per[game] if want in c.lower())
            print("  %-4s %d%s" % (game, len(hits),
                                   (": " + ", ".join(hits[:args.limit])) if hits else ""))
    if args.shared:
        union = set()
        for c in per.values():
            union |= set(c)
        shared = sorted(c for c in union if all(c in per[g] for g in per))
        if args.names:
            shared = [c for c in shared if args.names.lower() in c.lower()]
        print()
        print("classes present in every build (%d):" % len(shared))
        for c in shared[:args.limit]:
            print("   %s" % c)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(per, f, indent=1)
        print()
        print("wrote %s" % args.json)
    return 0


def cmd_stats(args):
    for game in ("ME1", "ME2", "ME3"):
        idx = load_index(game)
        pkgs = idx.get("packages", {})
        ok = [v for v in pkgs.values() if "error" not in v]
        err = len(pkgs) - len(ok)
        exports = sum(len(v["exports"]) for v in ok)
        unc = sum(1 for v in ok if not v.get("compressed", True))
        print("%-4s %5d packages (%d error), %7d exports, %d uncompressed"
              % (game, len(pkgs), err, exports, unc))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build", help="index packages (incremental)")
    p.add_argument("--game", choices=["ME1", "ME2", "ME3"])
    p.add_argument("--limit", type=int, default=0, help="stop after this many PARSED packages")
    p.add_argument("--rebuild", action="store_true", help="ignore the cache")
    p.set_defaults(func=cmd_build)

    for name, fn, helptext, extra in (
            ("find", cmd_find, "find an export by name across the install", True),
            ("who", cmd_who, "which packages DEFINE a class", False),
            ("chain", cmd_chain, "walk a superclass chain across packages", False),
            ("members", cmd_members, "a class's child exports", False)):
        p = sub.add_parser(name, help=helptext)
        if name == "find":
            p.add_argument("name")
            p.add_argument("--exact", action="store_true")
        else:
            p.add_argument("class_name")
        p.add_argument("--game", choices=["ME1", "ME2", "ME3"])
        p.add_argument("--limit", type=int, default=40)
        p.set_defaults(func=fn)

    p = sub.add_parser("stats", help="what the index holds")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("census",
                       help="class definitions across builds: shared vs per-build")
    p.add_argument("--game", choices=["ME1", "ME2", "ME3"])
    p.add_argument("--names", help="only class names containing this substring")
    p.add_argument("--shared", action="store_true",
                   help="list classes present in every build")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--json", help="write the full per-build class map here")
    p.set_defaults(func=cmd_census)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
