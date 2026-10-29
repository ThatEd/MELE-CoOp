"""Verify the ME:LE container model against real packages.

The container was derived from byte evidence, and this file is what stops that derivation from
drifting: it asserts the three invariants that make the model right rather than merely
consistent, and it reproduces the single-block decode.

Invariants, per chunk:
  1. (chunk_span - 16 - total_csize) is a positive multiple of 8   -> the table size is derivable
  2. sum(block csize) == total_csize                               -> the data is the table's sum
  3. sum(block usize) == total_usize                               -> ditto, uncompressed

Any single one of those could pass with a wrong model. Passing all three on every chunk of every
package is what makes it a verified format.

The single-block test is the minimal reproduction: one block, from disk, decompressed to exactly
its declared size. It is the first thing to check on a new build.

Skips cleanly when the game install or the Oodle DLL is absent, so it is safe to run anywhere.

Run: python -I tools/melepkg/test_container_format.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

INSTALLS = ()          # filled in from the game's own install resolver, not hardcoded
REL = os.path.join("Game", "{g}", "BioGame", "CookedPCConsole", "SFXGame.pcc")


def load():
    spec = importlib.util.spec_from_file_location(
        "melepkg", os.path.join(HERE, "melepkg.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_package(game, pkg=None):
    """Locate SFXGame.pcc for a game, using the module's own install resolver.

    A hardcoded drive list is a property of one machine. `melepkg.game_dirs()` reads
    `$MELE_GAME_DIR`, then the Steam registry, then probes the likely libraries - so the same
    code finds the install on a different PC.
    """
    pkg = pkg or load()
    for root in pkg.game_dirs():
        p = os.path.join(root, REL.format(g=game))
        if os.path.isfile(p):
            return p
    return None


def check_invariants(m, data, label, failures):
    chunks = m.find_chunks(data)
    if not chunks:
        failures.append("%s: no valid chunk found" % label)
        return None
    for i, c in enumerate(chunks):
        span, tc, tu = c["span"], c["total_csize"], c["total_usize"]
        table_bytes = span - m.CHUNK_HEADER - tc
        if table_bytes <= 0 or table_bytes % m.BLOCK_ENTRY:
            failures.append("%s chunk %d: table size %d not a multiple of %d"
                            % (label, i, table_bytes, m.BLOCK_ENTRY))
        if sum(cs for cs, _ in c["table"]) != tc:
            failures.append("%s chunk %d: sum(csize) != total_csize" % (label, i))
        if sum(us for _, us in c["table"]) != tu:
            failures.append("%s chunk %d: sum(usize) != total_usize" % (label, i))
    print("  %-4s %3d chunks, %4d blocks, %d -> %d bytes"
          % (label, len(chunks), sum(c["blocks"] for c in chunks),
             sum(c["total_csize"] for c in chunks),
             sum(c["total_usize"] for c in chunks)))
    return chunks


def main():
    m = load()
    failures = []
    oodle_path = m.find_oodle()
    if not oodle_path:
        print("SKIP: no oo2core*.dll found (game install absent)")
        return 0
    print("oodle: %s" % oodle_path)
    oodle = m.Oodle(oodle_path)

    found = 0
    for game in ("ME1", "ME2", "ME3"):
        pkg = find_package(game)
        if not pkg:
            print("%-4s SKIP: package not found" % game)
            continue
        found += 1
        data = open(pkg, "rb").read()
        chunks = check_invariants(m, data, game, failures)
        if not chunks:
            continue

        # Minimal reproduction: decompress block 0 only and require the declared size.
        off, csize, usize = m.block_offsets(data, chunks[0])[0]
        src = data[off:off + csize]
        out = oodle.decompress(src, usize)
        ok = len(out) == usize
        print("  %-4s block0 @0x%-6x %7d -> %7d  %s"
              % (game, off, csize, len(out), "OK" if ok else "SIZE MISMATCH"))
        if not ok:
            failures.append("%s: block0 decoded %d, expected %d" % (game, len(out), usize))

        # Last chunk is the small edge case; require it too.
        last = chunks[-1]
        if last["blocks"] < 1:
            failures.append("%s: last chunk has no blocks" % game)

    if not found:
        print("SKIP: no game packages found")
        return 0

    print()
    if failures:
        print("%d FAILURE(S):" % len(failures))
        for f in failures[:20]:
            print("   %s" % f)
        return 1
    print("ALL OK: container invariants hold and block 0 decodes on every package found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
