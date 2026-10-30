#!/usr/bin/env python
"""Query the install index as SQL, so reverse engineering stops costing 90 seconds a question.

## Why this exists

`mele_index.py` answers one question per process, and each process reloads the build's JSON
index - 520 MB for ME1, 776 MB for ME3. `json.load` on that is the dominant cost, so a
five-question probe takes seven or eight minutes and the answers arrive faster than they can be
followed up. That is a tooling defect, not a fact about the game, and it was the actual blocker
on the co-op reverse engineering.

This module converts the same data the index already proved into SQLite once, then answers
queries from it in milliseconds. Nothing here re-parses a package: it is a re-shaping of
`mele_index`'s output, so every closure that `mele_tables.build()` enforced still holds.

## What is stored

    classes(game, name, package, super)     exports whose own class is null - class definitions
    objects(game, name, cls, super, outer, package)   every export
    packages(game, package, exports, ok)    one row per package, so a gap is visible
    meta(key, value)                        index version, build time, row counts

`objects` uses a covering index on `(game, name)` and `(game, cls)`; the queries the co-op work
actually asks are "who defines X", "what does X inherit from", "what is inside package Y" and
"which packages mention X at all".

## Why a count is always reported with a name

A name that appears 9,000 times in a build is not a useful hook target, and a name that appears
once is a different kind of object. Every lookup prints its row count so a surprising result is
visible rather than plausible.

Usage:

    python -I tools/melepkg/mele_db.py build [--rebuild]
    python -I tools/melepkg/mele_db.py sql "<query>"
    python -I tools/melepkg/mele_db.py who   <class>          # which packages DEFINE it
    python -I tools/melepkg/mele_db.py chain <class> [--game ME3]
    python -I tools/melepkg/mele_db.py has   <name>[,<name>...]   # presence across all builds
    python -I tools/melepkg/mele_db.py kids  <class>           # child exports of a class
    python -I tools/melepkg/mele_db.py pkg   <substring>       # packages matching a name
    python -I tools/melepkg/mele_db.py funcs <class>           # functions declared by a class
    python -I tools/melepkg/mele_db.py stats
"""
import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DB_VERSION = 1
GAMES = ("ME1", "ME2", "ME3")

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE packages (
    game TEXT NOT NULL, package TEXT NOT NULL,
    exports INTEGER, ok INTEGER,
    PRIMARY KEY (game, package)
);
CREATE TABLE objects (
    game TEXT NOT NULL, name TEXT, cls TEXT, super TEXT, outer TEXT, package TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX ix_obj_name  ON objects(game, name);
CREATE INDEX ix_obj_cls   ON objects(game, cls);
CREATE INDEX ix_obj_pkg   ON objects(game, package);
CREATE INDEX ix_cls_name  ON classes(game, name);
"""


def load_index_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mele_index", os.path.join(HERE, "mele_index.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def db_path():
    m = load_index_module()
    return os.path.join(m.index_dir(), "install.db")


def _class_name(cls, super_):
    """A class definition is an export whose own class is null.

    `describe_short` renders that as the literal string "None", which is also what an
    out-of-range index renders as - so this is the same convention the index already uses, kept
    deliberately rather than re-derived, because 22 million rows were packed under it.
    """
    return cls == "None"


def build(rebuild=False):
    m = load_index_module()
    out = db_path()
    if os.path.isfile(out) and not rebuild:
        try:
            con = sqlite3.connect(out)
            v = con.execute("SELECT value FROM meta WHERE key='version'").fetchone()
            con.close()
            if v and v[0] == str(DB_VERSION):
                print("database is current at %s (use --rebuild to force)" % out)
                return out
        except sqlite3.DatabaseError:
            pass
    tmp = out + ".tmp"
    if os.path.isfile(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.execute("CREATE TABLE classes (game TEXT NOT NULL, name TEXT, package TEXT, super TEXT)")
    total_obj = 0
    total_cls = 0
    for game in GAMES:
        p = m.index_path(game)
        if not os.path.isfile(p):
            print("%-4s no index; skipped" % game)
            continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        pkgs = data.get("packages", {})
        pkg_rows, obj_rows, cls_rows = [], [], []
        for rel, rec in pkgs.items():
            short = os.path.basename(rel)
            if "error" in rec:
                pkg_rows.append((game, short, 0, 0))
                continue
            rows = rec.get("exports", [])
            pkg_rows.append((game, short, len(rows), 1))
            for name, cls, sup, outer in rows:
                obj_rows.append((game, name, cls, sup, outer, short))
                if _class_name(cls, sup) and name:
                    cls_rows.append((game, name, short, sup))
            del rows
        con.executemany("INSERT INTO packages VALUES (?,?,?,?)", pkg_rows)
        con.executemany("INSERT INTO objects VALUES (?,?,?,?,?,?)", obj_rows)
        con.executemany("INSERT INTO classes VALUES (?,?,?,?)", cls_rows)
        total_obj += len(obj_rows)
        total_cls += len(cls_rows)
        print("%-4s %5d packages, %8d exports, %6d class definitions"
              % (game, len(pkg_rows), len(obj_rows), len(cls_rows)))
        del data, pkgs, pkg_rows, obj_rows, cls_rows
    con.executescript(INDEXES)
    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("version", str(DB_VERSION)),
        ("objects", str(total_obj)),
        ("classes", str(total_cls)),
    ])
    con.commit()
    con.close()
    if os.path.isfile(out):
        os.remove(out)
    os.replace(tmp, out)
    size = os.path.getsize(out) / (1024 * 1024)
    print()
    print("wrote %s  (%.0f MB, %d exports, %d classes)" % (out, size, total_obj, total_cls))
    return out


def connect():
    p = db_path()
    if not os.path.isfile(p):
        p = build()
    con = sqlite3.connect(p)
    return con


def games_from(args):
    g = getattr(args, "game", None)
    return [g] if g else list(GAMES)


def cmd_build(args):
    build(rebuild=args.rebuild)
    return 0


def cmd_sql(args):
    con = connect()
    cur = con.execute(args.query)
    cols = [d[0] for d in cur.description] if cur.description else []
    if cols:
        print(" | ".join(cols))
        print("-+-".join("-" * len(c) for c in cols))
    n = 0
    for row in cur:
        n += 1
        print(" | ".join("" if v is None else str(v) for v in row))
        if n >= args.limit:
            print("... (limit %d)" % args.limit)
            break
    if cols:
        print()
        print("%d row(s)%s" % (n, " shown" if n >= args.limit else ""))
    con.close()
    return 0


def cmd_who(args):
    """Which packages DEFINE the class, per build. A definition is an export whose class is null."""
    con = connect()
    rows = con.execute(
        "SELECT game, package, super FROM classes WHERE lower(name)=lower(?) "
        "ORDER BY game, package", (args.name,)).fetchall()
    if not rows:
        near = con.execute(
            "SELECT DISTINCT name FROM classes WHERE lower(name) LIKE lower(?) LIMIT 8",
            ("%" + args.name + "%",)).fetchall()
        print("%r is not defined as a class in any build." % args.name)
        if near:
            print("near: %s" % ", ".join(r[0] for r in near))
        con.close()
        return 1
    print("%-4s %-46s %s" % ("GAME", "PACKAGE", "SUPER"))
    for game, pkg, sup in rows:
        print("%-4s %-46s %s" % (game, pkg, sup))
    print()
    per = {}
    for game, _p, _s in rows:
        per[game] = per.get(game, 0) + 1
    print("defined in %d package(s): %s"
          % (len(rows), ", ".join("%s=%d" % (g, per.get(g, 0)) for g in GAMES)))
    missing = [g for g in GAMES if g not in per]
    if missing:
        print("ABSENT from: %s" % ", ".join(missing))
    con.close()
    return 0


def cmd_chain(args):
    con = connect()
    name = args.name
    seen = set()
    depth = 0
    while name and name != "None" and name not in seen and depth < 24:
        seen.add(name)
        row = con.execute(
            "SELECT game, package, super FROM classes WHERE lower(name)=lower(?) LIMIT 1",
            (name,)).fetchone()
        if not row:
            print("  %s%s -> (not defined by any indexed package)" % ("  " * depth, name))
            break
        game, pkg, sup = row
        print("  %s%-34s -> %-34s  [%s:%s]" % ("  " * depth, name, sup, game, pkg))
        name = sup
        depth += 1
    con.close()
    return 0


def cmd_has(args):
    """Presence of one or more names across every build, in one query.

    This is the cross-build question asked most often, and answering it one build at a time in
    separate processes is what made the per-build divergence table slow to build.
    """
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    con = connect()
    rows = con.execute(
        "SELECT game, name, COUNT(*), MIN(package), SUM(CASE WHEN cls='None' THEN 1 ELSE 0 END) "
        "FROM objects WHERE lower(name) IN (%s) GROUP BY game, name"
        % ",".join("?" * len(names)),
        [n.lower() for n in names]).fetchall()
    want = {n.lower(): n for n in names}
    table = {}
    for game, name, cnt, pkg, ncls in rows:
        table[(game, want[name.lower()])] = (cnt, pkg, ncls)
    print("%-38s %-14s %-14s %-14s" % ("NAME", "ME1", "ME2", "ME3"))
    for n in names:
        cells = []
        for g in GAMES:
            hit = table.get((g, n))
            cells.append("%d@%s" % (hit[0], hit[1][:9]) if hit else "-")
        mark = "" if all((g, n) in table for g in GAMES) else "   <-- per-build"
        print("%-38s %-14s %-14s %-14s%s" % (n, cells[0], cells[1], cells[2], mark))
    print()
    print("cell = export count @ defining package; '-' means the name is absent from that build")
    con.close()
    return 0


def cmd_kids(args):
    """Child exports of a class, per build.

    A class's children are the exports whose `outer` is that class's own export, which the index
    stores as the bare object name. Functions therefore show up here alongside subobjects.
    """
    con = connect()
    games = games_from(args)
    for game in games:
        pkg = con.execute(
            "SELECT package FROM classes WHERE game=? AND lower(name)=lower(?)",
            (game, args.name)).fetchone()
        if not pkg:
            print("%-4s %s: not defined in this build" % (game, args.name))
            continue
        rows = con.execute(
            "SELECT name, cls FROM objects WHERE game=? AND package=? AND outer=? "
            "ORDER BY cls, name", (game, pkg[0], args.name)).fetchall()
        funcs = [r for r in rows if r[1] == "Core.Function"]
        print("%-4s %s in %s: %d child export(s), %d function(s)"
              % (game, args.name, pkg[0], len(rows), len(funcs)))
        if args.functions:
            for n, c in funcs[:args.limit]:
                print("     fn %s" % n)
        else:
            for n, c in rows[:args.limit]:
                print("     %-40s %s" % (n, c))
    con.close()
    return 0


def cmd_funcs(args):
    """Every function a class declares, per build, as a comparable set."""
    con = connect()
    games = games_from(args)
    per = {}
    for game in games:
        pkg = con.execute(
            "SELECT package FROM classes WHERE game=? AND lower(name)=lower(?)",
            (game, args.name)).fetchone()
        if not pkg:
            per[game] = None
            continue
        rows = con.execute(
            "SELECT name FROM objects WHERE game=? AND package=? AND outer=? AND cls='Core.Function'",
            (game, pkg[0], args.name)).fetchall()
        per[game] = {r[0] for r in rows}
    present = {g: s for g, s in per.items() if s is not None}
    for g, s in per.items():
        print("%-4s %s" % (g, "absent" if s is None else "%d function(s)" % len(s)))
    if len(present) > 1:
        common = set.intersection(*present.values())
        union = set.union(*present.values())
        print()
        print("shared by all queried builds: %d" % len(common))
        print("union: %d" % len(union))
        for g in present:
            only = present[g] - set().union(*[present[o] for o in present if o != g])
            print("  %-4s only: %d%s" % (g, len(only),
                                         (": " + ", ".join(sorted(only)[:12])) if only else ""))
    if args.show:
        for g in present:
            print()
            print("%s functions:" % g)
            for n in sorted(present[g])[:args.limit]:
                print("   %s" % n)
    con.close()
    return 0


def cmd_pkg(args):
    con = connect()
    rows = con.execute(
        "SELECT game, package, exports, ok FROM packages WHERE lower(package) LIKE lower(?) "
        "ORDER BY game, package", ("%" + args.substring + "%",)).fetchall()
    for game, pkg, exports, ok in rows[:args.limit]:
        print("%-4s %-44s %7d exports%s" % (game, pkg, exports, "" if ok else "   (PARSE ERROR)"))
    print()
    print("%d package(s)" % len(rows))
    con.close()
    return 0


def cmd_stats(args):
    con = connect()
    for game in GAMES:
        p = con.execute("SELECT COUNT(*), SUM(ok) FROM packages WHERE game=?", (game,)).fetchone()
        o = con.execute("SELECT COUNT(*) FROM objects WHERE game=?", (game,)).fetchone()
        c = con.execute("SELECT COUNT(*) FROM classes WHERE game=?", (game,)).fetchone()
        print("%-4s %5d packages (%d ok), %8d exports, %6d classes"
              % (game, p[0], p[1] or 0, o[0], c[0]))
    size = os.path.getsize(db_path()) / (1024 * 1024)
    print()
    print("database: %s  (%.0f MB)" % (db_path(), size))
    con.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build", help="convert the JSON index into SQLite (incremental)")
    p.add_argument("--rebuild", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("sql", help="run a query")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_sql)

    p = sub.add_parser("who", help="which packages define a class")
    p.add_argument("name")
    p.set_defaults(func=cmd_who)

    p = sub.add_parser("chain", help="walk a superclass chain")
    p.add_argument("name")
    p.add_argument("--game", choices=GAMES)
    p.set_defaults(func=cmd_chain)

    p = sub.add_parser("has", help="presence of names across all builds")
    p.add_argument("names")
    p.set_defaults(func=cmd_has)

    p = sub.add_parser("kids", help="child exports of a class")
    p.add_argument("name")
    p.add_argument("--game", choices=GAMES)
    p.add_argument("--functions", action="store_true")
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(func=cmd_kids)

    p = sub.add_parser("funcs", help="functions declared by a class, per build")
    p.add_argument("name")
    p.add_argument("--game", choices=GAMES)
    p.add_argument("--show", action="store_true")
    p.add_argument("--limit", type=int, default=80)
    p.set_defaults(func=cmd_funcs)

    p = sub.add_parser("pkg", help="packages matching a substring")
    p.add_argument("substring")
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(func=cmd_pkg)

    p = sub.add_parser("stats", help="what the database holds")
    p.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
