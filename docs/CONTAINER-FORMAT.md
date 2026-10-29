# ME:LE package container — verified format

Every field below is justified by byte evidence and validated arithmetically against real
packages from all three games. Nothing here is inferred from a plausible-looking API return or
from a single successful call.

Read this with `tools/melepkg/melepkg.py`, which implements it.

## Status

| | |
|---|---|
| Verified on | `SFXGame.pcc` from ME1, ME2 and ME3 |
| Chunks validated | **19 / 31 / 45** (all, in each game) |
| Blocks decompressed | 75 / 198 / 277 |
| Failures | **0** |
| Output | ME1 18,123,695 B (3.01x), ME2 48,304,514 B (2.31x), ME3 65,619,461 B (3.51x) |

## The format

```
package := header  chunk*
header  := u32 magic (0x9E2A83C1) | u16 version | u16 licensee | ... | name table ...
           (opaque to this reader; the first chunk's offset is found by signature, not by
            parsing the header — see "why the header is not parsed")

chunk   := u32 magic           0x9E2A83C1
           u32 max_block       0x40000 (262144) in every chunk observed
           u32 total_csize     sum of all block compressed sizes in this chunk
           u32 total_usize     sum of all block uncompressed sizes in this chunk
           block[N]            { u32 csize, u32 usize }
           data                the blocks, concatenated, in table order

N is not stored:  N = (chunk_span - 16 - total_csize) / 8
```

`chunk_span` = offset of the next chunk's signature minus this chunk's offset (for the last
chunk, minus end of file).

### Why `N` is derived rather than read

By definition the chunk's data occupies exactly `total_csize` bytes. The chunk also has a 16-byte
header and a table of 8-byte entries. So:

    chunk_span = 16 + 8*N + total_csize

which gives `N` directly. **The evidence that this is right, not merely consistent:** the
remainder `chunk_span - 16 - total_csize` is divisible by 8 for *every* chunk in all three games,
and the resulting table then sums to exactly `total_csize` and `total_usize`. A wrong model would
have to satisfy both conditions at once, on 95 chunks, by accident.

### Why the chunk stream was solved before the header

The bytes before the first chunk are **not** a stock UE3 header:

* at `+0x08` is `total_usize`-like data and at `+0x0C` a `5`, and `+0x10` begins the name table
  (`"None"` as ASCII) — a stock-parsing reader lands mid-structure and produces garbage;
* **the header is itself compressed**: entropy over `0x1D5..0x1000` is **7.926** and over
  `0x1000..0x10000` it is **7.995**, while `0x00..0x1B5` is only 5.538. So the readable part
  (the name table) and the packed part are interleaved, and the chunk block carrying the
  compressed header lives somewhere among the chunks.

At the time the container was solved, this meant the header could not be parsed and the reader
recovered the decompressed chunk stream rather than a package. **That has since been resolved** -
see "The packfile header layout" below, which pins the field offsets and the chunk index.

### The first chunk offset varies by game

| Game | first chunk | header bytes before it |
|---|---|---|
| ME1 | `0x1B5` | 437 |
| ME2 | `0x275` | 629 |
| ME3 | `0x35E` | 862 |

So it must be found, never hardcoded. Finding it by signature alone is not enough either, because
`0x9E2A83C1` occurs inside compressed data by chance — ME1 has 20 hits for 19 chunks. A candidate
is accepted **only if its table arithmetic closes exactly**, which both rejects false positives
and prevents a false positive from shifting every later boundary.

### The last chunk is the edge case worth testing

ME1's last chunk starts at `0x5BD560`, spans to EOF, and declares `total_csize=210`,
`total_usize=673`, giving a **one-entry** table. A parser that assumes a minimum table size, or
that reads `N` from a field, gets this one wrong.

## Codec

Blocks are Oodle. `oo2core_8_win64.dll` ships in `Binaries\Win64` (also in ME2 and ME3), and the
14-argument `OodleLZ_Decompress` is called on the game's own copy. `fuzzSafe` 0 and 1 both work.

**Do not use `OodleLZ_GetChunkCompressor` to identify the codec.** It returns `consumed=1` for
random bytes, so its answer is meaningless. This is recorded because it produced a wrong
conclusion earlier ("the codec is LZNA") that had to be retracted.

## The minimal reproducible test

One block, from the file, to prove the reading before trusting the whole package:

```
offset 0x225   csize 68,743   usize 262,144
OodleLZ_Decompress(...) -> 262,144 bytes, first 8 = 0e0000002a2a2a44
```

That is the whole container question in one call: given the correct boundary, the decompressor
returns exactly the declared size on the first attempt. If a future build differs, this is the
test that isolates it.

## Encodings differ per game — this bit me twice

Identifier strings are **ASCII in ME1 and ME2, UTF-16LE in ME3**. ME3's decompressed buffer
yields zero `CanFireWeapon` as ASCII but finds it, plus `IsLocallyControlled`, `Possess`,
`UseAbility` and `StartFire`, as wide strings. The reader now collects both, because which one a
package uses is a property of the build.

## What this unlocks

With the container readable, the **script layer** is available offline for the first time:
identifier extraction per package, and later bytecode where the package keeps it. For the co-op
work that means answering questions without launching a game:

* which control-gate names exist in each build (`CanFireWeapon`, `IsLocallyControlled`,
  `bPlayersOnly`, `PauseGame`, `PlayerInput`) — confirmed present in all three;
* which `SeqAct_*` / `BioSeqAct_*` classes a level uses, i.e. the **map events**;
* diffing LE1 vs LE3 identifier sets, which is the "only some naming calls need changing" list.

## The packfile header layout (partially decoded)

The bytes before the first chunk are a **packfile header**, and the package's table offsets index
*this header*, not the file and not the decompressed body. That resolves the long-standing confusion:

| Field | ME1 | ME2 | ME3 | Meaning |
|---|---|---|---|---|
| `+0x00` | `0x9E2A83C1` | same | same | magic |
| `+0x04` | ver 684, lic 171 | same | same | version / licensee |
| `+0x0C` | `"None"` (FString) | same | same | folder name |
| header size | 437 | 629 | 862 | bytes before the first chunk |
| NameCount at | `+0x19` | `+0x19` | `+0x22` | equals the parsed name-table length exactly |
| NameOffset at | `+0x1D` = **133** | `+0x1D` = **133** | `+0x26` = **142** | offset **into this header** |
| ExportCount | 35,385 | 39,961 | 54,768 | |
| ImportCount | 1,426 | 1,909 | 4,189 | |

`NameCount` matching the independently parsed name table (23,258 / 27,293 / 37,669) is what pins
the layout — that agreement cannot be coincidence. ME3's header is shifted by 9 bytes because its
folder FString is longer, so **the field offsets must be derived per package, not hardcoded**.

### The chunk index — fully decoded

At the header's chunk-count field there is a count (19 / 31 / 45, equal to the chunk count) followed
by that many **4-tuples**, and every one of them matches:

    entry = (uncompressedOffset, uncompressedSize, fileOffset, span)
    span  = total_csize + 16 + 8*blocks      (the whole chunk, header and block table included)

**Verified 95/95 across ME1 (19), ME2 (31) and ME3 (45).** That is the last piece needed to map
between decompressed offsets and file offsets.

Two things worth recording, because both are how this looked wrong for a while:

* **The count sits immediately before the entries**, so the entries start at `count_at + 4`, not at
  the value in the neighbouring field. Reading from the wrong base gives a table that matches
  exactly one entry and looks like a different structure.
* **`uncompressedOffset` of entry 0 includes the packfile header size** (437 / 629 / 862). So entry
  0 reads `(437, 3111287, 437, 359894)`: its offset field is the header length, and every later entry
  is the plain cumulative uncompressed offset. Once entry 0 is understood, all of them line up.

## Still unknown

| Unknown | Why it matters |
|---|---|
| The **import and export tables** are not parsed | `ExportOffset`/`ImportOffset` are known fields, but their entry format is not decoded. This is what would give class -> function -> property attribution, and enum members such as `EWhoTriggers` values |
| Whether function **bytecode** survives in these packages | decides whether script *logic* can be read, not just names |
| What `ExportCount` 35,385 / 39,961 / 54,768 actually enumerates | large enough to be every object; not confirmed |
| The `depends_offset` section | unexamined |

Resolved since this page was first written: the field offsets (they differ per game), the chunk
index (4-tuples, 95/95 verified), and the name table (parseable, and UTF-16LE in ME3).
