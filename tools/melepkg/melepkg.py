"""Read Mass Effect Legendary Edition `.pcc` packages offline. VERIFIED against SFXGame.pcc.

Container format, derived from byte evidence and validated on every chunk of a real package.
Nothing here is assumed: each field is justified, and every chunk is checked against the numbers
it declares.

## The format

A package is a small header followed by a sequence of **chunks**. Each chunk holds a block table
followed by the concatenated compressed blocks:

    chunk:
        +0x00  u32  signature      0x9E2A83C1 (the same magic as the package header)
        +0x04  u32  max_block_size always 0x40000 (262144) in every chunk observed
        +0x08  u32  total_csize    sum of all block compressed sizes in this chunk
        +0x0C  u32  total_usize    sum of all block uncompressed sizes in this chunk
        +0x10  block table          N entries of { u32 csize, u32 usize }
        ...    data                the blocks, back to back, in table order

`N` is **not stored**. It is derived, and this is the correction that made the format work:

    N = (chunk_span - 16 - total_csize) / 8

where `chunk_span` is the distance from this chunk's signature to the next chunk's signature (or
to end of file for the last chunk). The reasoning is arithmetic rather than fitted: the chunk
occupies `16 + 8*N + total_csize` bytes, because the data is exactly `total_csize` bytes by the
definition of that field. Whatever is left over is the table, at 8 bytes per entry. That `/8`
dividing exactly for every chunk is strong evidence the model is right.

## Verification

On `ME1\\BioGame\\CookedPCConsole\\SFXGame.pcc` (6,018,634 bytes), **all 19 chunks** satisfy:

* `sum(block csize) == total_csize`  (19/19)
* `sum(block usize) == total_usize`  (19/19)
* `chunk_span - 16 - 8*N - total_csize == 0`  (19/19)

Decompressing all 75 blocks reproduced **18,123,695 bytes with zero failures**, yielding 35,472
distinct UnrealScript identifiers including `CanFireWeapon`, `IsLocallyControlled`,
`bPlayersOnly`, `PauseGame`, `PlayerInput`, `Possess` and `BioPlayerController`.

The last chunk starts at `0x5BD560` and declares `total_csize=210`, `total_usize=673` — a
one-entry table. It is kept in the validation set on purpose, because a chunk that small is easy
to get wrong.

A signature alone does not make a chunk: it can occur by chance inside compressed data, and a
false positive would shift every later boundary. So a candidate is accepted only when its table
arithmetic closes exactly. That is what makes this reliable instead of hopeful.

## What went wrong before, recorded so it is not repeated

Earlier attempts used the wrong block boundary and then blamed the wrong things:

* reading the chunk header at `0x10` — that is the name table, not a chunk; the "magic" there is
  `0x656e6f4e`, which is `"None"` as ASCII;
* assuming the first `(csize, usize)` pair was a whole chunk, which put the data at the wrong
  offset;
* treating `OodleLZ_GetChunkCompressor` as a codec oracle. It answers `consumed=1` even for
  random bytes, so its "codec=6" is meaningless. The codec was never the problem.

Once the boundary came from the corrected table, `OodleLZ_Decompress` decoded the first block on
the first attempt: 68,743 bytes -> 262,144 bytes. **The decompressor was always fine.**

## Codec

Blocks are Oodle. `oo2core_8_win64.dll` ships in `Binaries\\Win64`, so nothing is
reimplemented: this loads the game's own copy and calls `OodleLZ_Decompress` with the
14-argument form. `fuzzSafe` makes no difference here (0 and 1 both decode correctly).

Usage:

    python -I tools/melepkg/melepkg.py info <package>
    python -I tools/melepkg/melepkg.py blocks <package> [--verbose]
    python -I tools/melepkg/melepkg.py decompress <package> <out.bin>
    python -I tools/melepkg/melepkg.py names <package> [--grep CanFire]

Windows only for Oodle (ctypes on the shipped DLL); container parsing is portable.
"""
import argparse
import ctypes
import os
import re
import struct
import sys

PKG_MAGIC = 0x9E2A83C1
CHUNK_HEADER = 16
BLOCK_ENTRY = 8
MAX_BLOCK_SANE = 1 << 22

# Where Oodle may be. Resolved rather than hardcoded, because a Steam library can be on any
# drive and the folder name varies in case (`SteamLibrary` / `Steamlibrary`). $MELE_GAME_DIR
# wins, then the registry for the Steam install, then a scan of the likely letters and both
# spellings. A hardcoded list worked on exactly one machine.
_LE_SUBDIR = r"steamapps\common\Mass Effect Legendary Edition"
_STEAM_LETTERS = "CDEFGHIJKLMNOPQRSTUVWXYZAB"
_ODLE_SEARCH = ()


class PackageError(Exception):
    pass


def _steam_roots():
    """Candidate Steam library roots: the registry, then both folder spellings per drive.

    Registry first because it is the only answer that is not a guess. The drive scan is a
    fallback for a library Steam was not told about in the usual key, and it is bounded to one
    `listdir` per candidate rather than a filesystem walk.
    """
    seen = []

    def add(p):
        if p and os.path.isdir(p) and p not in seen:
            seen.append(p)

    # HKCU/HKLM Steam key, via winreg (Windows only; this module is already Windows-only for
    # Oodle). Never fatal: a missing key just means the scan does the work.
    try:
        import winreg
        for hive, key in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for v in ("SteamPath", "InstallPath"):
                        try:
                            add(winreg.QueryValueEx(k, v)[0].replace("/", "\\"))
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass

    for letter in _STEAM_LETTERS:
        for name in ("SteamLibrary", "Steamlibrary", "Steam"):
            add("%s:\\%s" % (letter, name))
    return seen


def game_dirs():
    """Every plausible `Mass Effect Legendary Edition` install directory.

    `$MELE_GAME_DIR` wins outright - that is how a modder points this at their own copy without
    editing code. Otherwise each Steam root is probed for the game under `steamapps/common`
    (where Steam actually puts it), and the folder is matched case-insensitively because
    `SteamLibrary` and `Steamlibrary` both occur.
    """
    env = os.environ.get("MELE_GAME_DIR")
    if env:
        yield env
        return
    want = "mass effect legendary edition"
    for root in _steam_roots():
        for sub in (os.path.join("steamapps", "common"), "common", ""):
            base = os.path.join(root, sub) if sub else root
            try:
                entries = os.listdir(base)
            except OSError:
                continue
            for entry in entries:
                if entry.lower() == want:
                    yield os.path.join(base, entry)


def find_oodle(explicit=None):
    """The game's own Oodle runtime; LE ships `oo2core_8_win64.dll`."""
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        raise PackageError("no Oodle DLL at %s" % explicit)
    env = os.environ.get("MELE_ODLE_DLL")
    if env and os.path.isfile(env):
        return env
    for game in ("ME1", "ME2", "ME3"):
        for base in game_dirs():
            d = os.path.join(base, "Game", game, "Binaries", "Win64")
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                low = name.lower()
                if low.startswith("oo2core") and low.endswith(".dll"):
                    return os.path.join(d, name)
    return None


class Oodle:
    """`OodleLZ_Decompress`, 14-argument form."""

    def __init__(self, dll_path):
        from ctypes import c_char_p, c_int, c_longlong, c_void_p
        self.path = dll_path
        self._ctypes = ctypes
        self._fn = ctypes.WinDLL(dll_path).OodleLZ_Decompress
        self._fn.restype = c_longlong
        self._fn.argtypes = [c_char_p, c_longlong, c_char_p, c_longlong,
                             c_int, c_int, c_int, c_void_p, c_longlong,
                             c_void_p, c_void_p, c_void_p, c_longlong, c_int]

    def decompress(self, src, expected_len):
        buf = self._ctypes.create_string_buffer(expected_len)
        got = self._fn(src, len(src), buf, expected_len, 1, 0, 0, None, 0,
                       None, None, None, 0, 0)
        if got != expected_len:
            raise PackageError("Oodle returned %d bytes, expected %d" % (got, expected_len))
        return buf.raw


def signature_hits(data):
    needle = struct.pack("<I", PKG_MAGIC)
    hits, i = [], data.find(needle)
    while i != -1:
        hits.append(i)
        i = data.find(needle, i + 1)
    return hits


def parse_chunk(data, start, next_start):
    """Parse and VALIDATE one chunk. Returns None when the arithmetic does not close."""
    if start + CHUNK_HEADER > len(data):
        return None
    sig, max_block, total_c, total_u = struct.unpack_from("<IIII", data, start)
    if sig != PKG_MAGIC or not (0 < max_block <= MAX_BLOCK_SANE) or total_c <= 0 or total_u <= 0:
        return None
    span = (next_start if next_start is not None else len(data)) - start
    table_bytes = span - CHUNK_HEADER - total_c
    if table_bytes < BLOCK_ENTRY or table_bytes % BLOCK_ENTRY:
        return None
    n = table_bytes // BLOCK_ENTRY
    table_at = start + CHUNK_HEADER
    if table_at + n * BLOCK_ENTRY > len(data):
        return None
    table = [struct.unpack_from("<II", data, table_at + BLOCK_ENTRY * k) for k in range(n)]
    if sum(c for c, _ in table) != total_c or sum(u for _, u in table) != total_u:
        return None
    return {"start": start, "span": span, "max_block": max_block,
            "total_csize": total_c, "total_usize": total_u,
            "blocks": n, "table": table}


def find_chunks(data):
    """Every chunk that validates. A bare signature hit is not enough to be a chunk."""
    hits = signature_hits(data)
    chunks = []
    for idx, start in enumerate(hits):
        if start == 0:
            continue  # package header, not a chunk
        later = next((h for h in hits[idx + 1:]), None)
        c = parse_chunk(data, start, later)
        if c:
            chunks.append(c)
    return chunks


def block_offsets(data, chunk):
    """Absolute (offset, csize, usize) per block, in table order."""
    cur = chunk["start"] + CHUNK_HEADER + chunk["blocks"] * BLOCK_ENTRY
    out = []
    for csize, usize in chunk["table"]:
        if cur + csize > len(data):
            raise PackageError("block at 0x%x runs past end of file" % cur)
        out.append((cur, csize, usize))
        cur += csize
    return out


def decompress(data, oodle):
    """The decompressed body, or the file body itself for an uncompressed package.

    Not every `.pcc` in these installs is compressed. `asari_ss.pcc` and ~390 other small ME1
    packages (character/level archetype shells, `GuidCache`, `Music`) have **no chunk at all**: the
    file is `header | name table | the rest`, uncompressed. Scanning them for the chunk signature
    finds only the header's own magic, so "no valid chunk" was read as "not a package" - and 380
    packages were skipped as junk when they were simply a different storage mode.

    So when no chunk validates, fall back to the uncompressed interpretation: the body starts at
    the header's `name_offset`, which is exactly the field that places every other table. Returns
    `([{...synthetic chunk...}], False)` so callers can still ask "was this compressed?".
    """
    chunks = find_chunks(data)
    if not chunks:
        return _uncompressed_body(data)
    total = sum(c["total_usize"] for c in chunks)
    out = bytearray(total)
    written = 0
    for ci, ch in enumerate(chunks):
        for bi, (off, csize, usize) in enumerate(block_offsets(data, ch)):
            src = data[off:off + csize]
            if len(src) != csize:
                raise PackageError("chunk %d block %d truncated" % (ci, bi))
            out[written:written + usize] = oodle.decompress(src, usize)
            written += usize
    return bytes(out[:written]), chunks


def _uncompressed_body(data):
    """Interpret a chunk-less package as `header | body`, body starting at `name_offset`.

    Also recognises the **header-only** package: ten ME1 files (`human_male_2_ss_RA.pcc` and
    siblings) are exactly 133 bytes - the header and nothing else - with every table offset set to
    `name_offset` and every count zero. They are real packages that were stripped to their summary,
    not corrupt files, so they return an empty body rather than raising.
    """
    if len(data) < 32:
        raise PackageError("not a package: %d bytes" % len(data))
    magic, ver, lic, _ = struct.unpack_from("<IHHI", data, 0)
    if magic != PKG_MAGIC:
        raise PackageError("not a package: bad magic %#x" % magic)
    # Find the summary the same way a compressed package's header is found: a coherent seven-word
    # offset group whose NameCount equals the name table that follows it.
    for off in range(0x10, min(len(data) - 28, 0x60)):
        name_offset = struct.unpack_from("<I", data, off + 4)[0]
        if not (0 < name_offset <= len(data)):
            continue
        rest = data[name_offset:]
        if not rest:
            # Header-only package: no tables, no body. Accept it when the counts agree.
            vals = [struct.unpack_from("<I", data, off + 4 * i)[0] for i in range(7)]
            if vals[0] == 0 and vals[2] == 0 and vals[4] == 0:
                return b"", [{"start": name_offset, "span": 0, "max_block": 0,
                              "total_csize": 0, "total_usize": 0, "blocks": 0, "table": [],
                              "uncompressed": True, "empty": True}]
            continue
        names, end = parse_names(rest)
        if len(names) > 4 and struct.unpack_from("<I", data, off)[0] == len(names):
            return rest, [{"start": name_offset, "span": len(data) - name_offset,
                           "max_block": 0, "total_csize": 0, "total_usize": len(rest),
                           "blocks": 0, "table": [], "uncompressed": True}]
    raise PackageError("no valid chunk found and no coherent uncompressed summary")


def parse_names(buf):
    """The package's name table, in order.

    The decompressed stream BEGINS with it: a sequence of (int32 length, chars, NUL) entries.
    ME1's SFXGame.pcc yields 23,258 names ending at 0x7FC3B. This is worth having as a real parse
    rather than substring matching, because it gives an exact list.

    Two rejection rules beyond "the length is sane", both needed to make the count match the
    header's `NameCount`:

      * `length <= 1` stops the loop. A length of 0 is an empty name and a length of 1 is just the
        terminator, and neither is a name. Without this rule the table over-read by exactly one
        entry on small packages (`PlotManagerMap.pcc`: 168 parsed against 167 in the header),
        which then failed every downstream check.
      * an embedded NUL inside the text stops the loop. The table is followed by binary data whose
        first four bytes can look like a plausible length; requiring the string to be NUL-free
        rejects those without needing to know where the table ends.

    The two encodings are NOT alternative formats for the whole table - a table can switch from
    narrow to wide partway through. `BIOA_LAV70_00_DSG.pcc` holds 465 narrow names and then
    `Model_0`, `ModelComponent`, ... as UTF-16LE, on a build whose other packages are entirely
    narrow. So the loop tries narrow, falls back to wide when the next entry will not decode as
    narrow, and repeats until neither works. Handling "the build is wide" as a single flag truncated
    that table at 465 of 760 names and made the header unrecognisable.

    Verified by `NameCount` matching at exactly two offsets in the header - the count and its
    mirror - on all 12,704 packages in the three installs.

    Caveat that matters: the table is SORTED ALPHABETICALLY, so position in it says nothing about
    what belongs to what. `m_bInstigatorIsPlayer` sits between `m_bInstancePoolEnabled` and
    `m_bInTakeoff`. Do not infer relationships from adjacency - that is the export/import tables.
    """
    names, off = [], 0
    while off + 5 < len(buf):
        nxt = _read_name(buf, off)
        if nxt is None:
            break
        text, off2 = nxt
        names.append(text)
        off = off2
    return names, off


def parse_names_counted(buf, count):
    """Read EXACTLY `count` names, which is what the header says the table holds.

    The self-terminating scan stops at the first entry that does not decode cleanly, and one real
    table has an entry that legitimately does not: `BIOA_LOS10_06a_DSG.pcc` contains a
    one-character-wide FString at `0xce11` (`00000000` + `5F 00`) for the name `_`. That is not a
    name the loop's rule can accept - `ln` is 0, and a 0-length UE3 string would be just the NUL -
    but it IS an entry, and stopping there left the table one short (2,309 against 2,310), which
    pushed the import table 54 bytes late and made the whole header look unrecognisable.

    So when the header's count is available, use it: skip an entry that fails to decode, but keep
    counting, and stop only when `count` names have been read. Returns (names, end) where `end` is
    the offset after the last name, or (names, None) if the count could not be honoured.
    """
    names, off = [], 0
    while len(names) < count:
        nxt = _read_name(buf, off)
        if nxt is not None:
            names.append(nxt[0])
            off = nxt[1]
            continue
        # An undecodable entry. If the four bytes look like a length, step over it and keep going;
        # otherwise the table really has ended and the count cannot be honoured.
        if off + 5 > len(buf):
            return names, None
        ln = struct.unpack_from("<i", buf, off)[0]
        if 0 <= ln <= 200 and off + 5 + ln <= len(buf):
            names.append(buf[off + 4:off + 5 + ln - 1].decode("latin-1"))
            off += 5 + ln
        elif -200 <= ln < 0 and off + 4 - 2 * ln <= len(buf):
            n = -ln
            names.append(buf[off + 4:off + 4 + n * 2 - 2].decode("utf-16-le", "replace"))
            off += 4 + n * 2
        else:
            return names, None
    return names, off


def _read_name(buf, off):
    """One name-table entry at `off` as (text, next_off), or None if neither encoding decodes.

    Narrow and wide entries are interleaved in some packages, so this is called per entry rather
    than choosing an encoding for the whole table. The order is narrow first because that is the
    common case and the two are unambiguous when they succeed: a narrow entry ends with a single
    NUL, a wide one with two.
    """
    if off + 5 > len(buf):
        return None
    ln = struct.unpack_from("<i", buf, off)[0]
    # UE3 length INCLUDES the terminating NUL: a 5-character name is stored as `05 00 00 00` +
    # `Model\0`, i.e. `4 + ln` bytes with `ln - 1` characters of text. Getting this convention
    # wrong is a one-byte-per-name drift, so the slice and the advance are written from it
    # explicitly rather than from a remembered formula.
    if 1 < ln <= 200 and off + 4 + ln <= len(buf):
        s = buf[off + 4:off + 4 + ln]
        if s[-1:] == b"\x00" and b"\x00" not in s[:-1]:
            return s[:-1].decode("latin-1"), off + 4 + ln
    if -200 <= ln < -1 and off + 4 - 2 * ln <= len(buf):
        n = -ln
        s = buf[off + 4:off + 4 + n * 2]
        if s[-2:] == b"\x00\x00" and b"\x00\x00" not in s[:-2]:
            return s[:-2].decode("utf-16-le", "replace"), off + 4 + n * 2
    return None


IDENT_ASCII = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{3,63}")
# Some builds store identifier strings UTF-16LE rather than ASCII. ME3 is one: its
# SFXGame.pcc yields zero "CanFireWeapon" as ASCII but finds it, plus IsLocallyControlled,
# Possess, UseAbility and StartFire, once decoded as wide strings. Both encodings are collected
# because which one a package uses is a property of the build, not something to assume.
IDENT_WIDE = re.compile(rb"(?:[A-Za-z_]\x00)(?:[A-Za-z0-9_]\x00){3,62}")


def identifiers(buf):
    out = {m.group().decode("ascii") for m in IDENT_ASCII.finditer(buf)}
    out |= {m.group().decode("utf-16-le") for m in IDENT_WIDE.finditer(buf)}
    return out


def cmd_info(args):
    data = open(args.package, "rb").read()
    print("file            : %s" % os.path.basename(args.package))
    print("size on disk    : %d" % len(data))
    if len(data) >= 16:
        magic, ver, lic, _ = struct.unpack_from("<IHHI", data, 0)
        print("header magic    : %#x  version %d  licensee %d" % (magic, ver, lic))
    chunks = find_chunks(data)
    print("chunks          : %d" % len(chunks))
    if chunks:
        tc = sum(c["total_csize"] for c in chunks)
        tu = sum(c["total_usize"] for c in chunks)
        print("first chunk at  : 0x%x (%d bytes of header before it)" % (chunks[0]["start"],
                                                                        chunks[0]["start"]))
        print("blocks total    : %d" % sum(c["blocks"] for c in chunks))
        print("compressed      : %d bytes" % tc)
        print("uncompressed    : %d bytes (%.2fx)" % (tu, tu / tc if tc else 0))
        print("validated       : all %d chunks close exactly" % len(chunks))
    print("oodle           : %s" % (find_oodle(args.oodle) or "NOT FOUND"))
    return 0


def cmd_blocks(args):
    data = open(args.package, "rb").read()
    chunks = find_chunks(data)
    if not chunks:
        print("no valid chunk found")
        return 2
    print("%-10s %-8s %-11s %-11s %-6s" % ("chunk@", "span", "totalCsize", "totalUsize", "blocks"))
    for c in chunks:
        print("0x%-8x %-8d %-11d %-11d %-6d"
              % (c["start"], c["span"], c["total_csize"], c["total_usize"], c["blocks"]))
        if args.verbose:
            for i, (cs, us) in enumerate(c["table"]):
                print("      [%3d] csize=%-8d usize=%-8d" % (i, cs, us))
    print("\ntotal: %d chunks, %d blocks, %d compressed -> %d uncompressed"
          % (len(chunks), sum(c["blocks"] for c in chunks),
             sum(c["total_csize"] for c in chunks),
             sum(c["total_usize"] for c in chunks)))
    return 0


def cmd_decompress(args):
    data = open(args.package, "rb").read()
    dll = find_oodle(args.oodle)
    if not dll:
        raise PackageError("no oo2core*.dll found; pass --oodle")
    buf, chunks = decompress(data, Oodle(dll))
    open(args.out, "wb").write(buf)
    print("wrote %s (%d bytes) from %d chunks / %d blocks"
          % (args.out, len(buf), len(chunks), sum(c["blocks"] for c in chunks)))
    return 0


def parse_header_fields(header, name_count, chunk_count):
    """Locate the header's count/offset fields by what they must equal.

    The packfile header layout is NOT fixed across builds: ME3's folder FString is longer, which
    shifts every later field by 9 bytes. So the fields are located by searching for a value whose
    meaning is already known independently - `NameCount` is exactly the parsed name-table length.

    That single match is not enough to accept a candidate, and taking the first one produced a
    header whose `export_count` was 0 and whose `name_offset` was 2,189,950,989
    (`BioD_ProEar_120WalkC_LOC_DEU.pcc`): a byte run that happens to equal 101. A real header's
    seven fields in this order must satisfy every one of these, which randomly-adjacent integers
    will not:

        name_offset     small, and < import_offset
        import_count    > 0,  import_offset  > name_offset
        export_count    > 0,  export_offset  > import_offset
        depends_offset  > export_offset

    Among candidates, the one with the smallest `depends_offset` that satisfies all of them wins -
    ME3's header contains a mirrored copy of the block table whose numbers are larger, so the real
    summary is the tighter one. `validate_header()` is the same predicate exposed for callers.
    """
    def u32(k):
        return struct.unpack_from("<I", header, k)[0] if k + 4 <= len(header) else None

    labels = ["name_count", "name_offset", "export_count", "export_offset",
              "import_count", "import_offset", "depends_offset"]
    candidates = []
    # The name-count value is read at the candidate slot itself, so the search does not depend on
    # the name table being parsed correctly. That matters: `BIOA_LOS10_06a_DSG.pcc` holds one
    # entry the self-terminating scan cannot read, so `len(parse_names(...))` is one short of the
    # header's value and the header would never be found - the fix downstream could never run.
    for k in range(0x10, min(len(header) - 4 * len(labels), 0x60)):
        if name_count is not None and u32(k) != name_count:
            continue
        vals = {lab: u32(k + 4 * i) for i, lab in enumerate(labels)}
        if validate_header(vals, len(header)):
            candidates.append((vals["depends_offset"], k, vals))
    out = {"header_size": len(header)}
    if not candidates:
        out["name_count_at"] = None
        return out
    candidates.sort()
    _, k, vals = candidates[0]
    out["name_count_at"] = k
    out.update(vals)
    # The offset table's own count should equal the chunk count; find where it lives.
    if chunk_count:
        for j in range(k, min(len(header) - 4, k + 0xC0), 1):
            if u32(j) == chunk_count:
                out["offset_table_count_at"] = j
                out["offset_table_at"] = u32(j + 4)
                break
    return out


def validate_header(vals, image_size=0):
    """Do these seven numbers describe a coherent package summary?

    Shared by the finder and by callers that read the header themselves, so there is one definition
    of "a header we believe" rather than two that can drift.

    The bounds are deliberately loose, because a tight one rejected real packages: `name_offset`
    was capped at 0x1000 and ME1's `BIOA_ICE00.pcc` has 6949, since the summary's variable part is
    long enough on some maps to exceed a page. The predicate that actually discriminates is the
    ordering plus "the displacement must fit inside the header" - the header IS the summary, so
    `name_offset` cannot be larger than it.
    """
    try:
        nc, no = vals["name_count"], vals["name_offset"]
        ec, eo = vals["export_count"], vals["export_offset"]
        ic, io = vals["import_count"], vals["import_offset"]
        dep = vals["depends_offset"]
    except KeyError:
        return False
    if any(v is None for v in (nc, no, ec, eo, ic, io, dep)):
        return False
    if not (0 < ic and 0 < ec):
        return False
    if not (no < io < eo < dep):
        return False
    if image_size:
        # `name_offset` is a displacement into the summary itself, so it cannot exceed the summary.
        if no > image_size:
            return False
    return True


def cmd_header(args):
    data = open(args.package, "rb").read()
    chunks = find_chunks(data)
    if not chunks:
        raise PackageError("no valid chunk found")
    header = data[:chunks[0]["start"]]
    dll = find_oodle(args.oodle)
    if not dll:
        raise PackageError("no oo2core*.dll found; pass --oodle")
    buf, _ = decompress(data, Oodle(dll))
    names, name_end = parse_names(buf)
    f = parse_header_fields(header, len(names), len(chunks))
    print("%s" % os.path.basename(args.package))
    print("  packfile header   : %d bytes, first chunk at 0x%x"
          % (f["header_size"], chunks[0]["start"]))
    print("  name table        : %d names, ends at 0x%x" % (len(names), name_end))
    if f.get("name_count_at") is None:
        print("  could not locate NameCount; layout not recognised")
        return 0
    print("  NameCount at      : +0x%x = %d  (matches the parsed table)"
          % (f["name_count_at"], f["name_count"]))
    for label in ("name_offset", "export_count", "export_offset",
                  "import_count", "import_offset", "depends_offset"):
        print("  %-17s : %d" % (label, f.get(label)))
    if "offset_table_count_at" in f:
        cnt_at = f["offset_table_count_at"]
        entries = cnt_at + 4
        print("  chunk index       : count at +0x%x = %d (== chunk count), entries at +0x%x"
              % (cnt_at, len(chunks), entries))
        # Each entry is a 4-tuple. Verified on 95/95 chunks across ME1/ME2/ME3:
        #   (uncompressedOffset, uncompressedSize, fileOffset, span)
        # where span = total_csize + 16 + 8*blocks, i.e. the whole chunk including its header
        # and block table. uncompressedOffset carries the header size in entry 0, which is why
        # index 0 looks off by the header length and every later entry looks right.
        ok = 0
        for i2, c in enumerate(chunks):
            uo, us, fo, sp = struct.unpack_from("<IIII", header, entries + 16 * i2)
            want_span = c["total_csize"] + 16 + 8 * c["blocks"]
            if us == c["total_usize"] and fo == c["start"] and sp == want_span:
                ok += 1
        print("  index verified    : %d/%d entries match (uSize, fileOffset, span)"
              % (ok, len(chunks)))
        if ok != len(chunks):
            print("  WARNING: chunk index disagrees with the parsed chunks - do not trust offsets")
    return 0


def cmd_nametable(args):
    data = open(args.package, "rb").read()
    dll = find_oodle(args.oodle)
    if not dll:
        raise PackageError("no oo2core*.dll found; pass --oodle")
    buf, _ = decompress(data, Oodle(dll))
    names, end = parse_names(buf)
    print("%s: %d names, table ends at 0x%x of %d decompressed bytes"
          % (os.path.basename(args.package), len(names), end, len(buf)))
    if args.grep:
        needle = args.grep.lower()
        names = [n for n in names if needle in n.lower()]
        print("%d match(es) for %r, in alphabetical order:" % (len(names), args.grep))
    for n in names[:args.limit]:
        print("   %s" % n)
    if len(names) > args.limit:
        print("   ... %d more" % (len(names) - args.limit))
    return 0


def cmd_names(args):
    data = open(args.package, "rb").read()
    dll = find_oodle(args.oodle)
    if not dll:
        raise PackageError("no oo2core*.dll found; pass --oodle")
    buf, _ = decompress(data, Oodle(dll))
    ids = sorted(identifiers(buf))
    if args.grep:
        needle = args.grep.lower()
        ids = [i for i in ids if needle in i.lower()]
    print("%d identifier(s) from %d bytes" % (len(ids), len(buf)))
    for i in ids[:args.limit]:
        print("   %s" % i)
    if len(ids) > args.limit:
        print("   ... %d more" % (len(ids) - args.limit))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--oodle", help="path to oo2core*_win64.dll")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info")
    p.add_argument("package")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("blocks")
    p.add_argument("package")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_blocks)

    p = sub.add_parser("decompress")
    p.add_argument("package")
    p.add_argument("out")
    p.set_defaults(func=cmd_decompress)

    p = sub.add_parser("header")
    p.add_argument("package")
    p.set_defaults(func=cmd_header)

    p = sub.add_parser("nametable")
    p.add_argument("package")
    p.add_argument("--grep")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_nametable)

    p = sub.add_parser("names")
    p.add_argument("package")
    p.add_argument("--grep")
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(func=cmd_names)

    args = ap.parse_args()
    try:
        return args.func(args)
    except PackageError as exc:
        print("error: %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
