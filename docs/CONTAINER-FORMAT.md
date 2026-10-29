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

## The packfile header layout (decoded)

The bytes before the first chunk are a **packfile header**, and the package's table offsets index
the serialized *image* — which is **header + decompressed body**, not the file and not the buffer on
its own. That distinction is the whole of [the coordinate-space error](#the-coordinate-space-error):
a header offset `o` indexes the decompressed buffer at `o - name_offset`.

Fields, located by requiring the seven-word group in this order to be coherent (`validate_header`),
rather than at fixed offsets:

| Field | ME1 | ME2 | ME3 | Meaning |
|---|---|---|---|---|
| magic | `0x9E2A83C1` | same | same | |
| version / licensee | 684 (0x2AC) / 171 | same | same | |
| folder | `"None"` (FString) | same | same | 9 bytes longer on ME3, shifting every later field |
| `name_count` | 23,258 | 27,293 | 37,669 | equals the parsed name-table length exactly |
| `name_offset` | **133** | **133** | **142** | the image/buffer displacement |
| `import_count`, `import_offset` | 1,426 | 1,909 | 4,189 | |
| `export_count`, `export_offset` | 35,385 | 39,961 | 54,768 | |
| `depends_offset` | | | | the export walk lands exactly here |

`NameCount` matching the independently parsed name table is what pins the layout — that agreement
cannot be coincidence, and it is now checked as part of a seven-field predicate because on its own
it is not enough: `BioD_ProEar_120WalkC_LOC_DEU.pcc` contains a byte run equal to its `NameCount`
whose neighbours give `name_offset = 2,189,950,989`.

Numbers above are for each build's `SFXGame.pcc`. Two things vary and must never be hardcoded:

* **The field offsets shift per build.** ME3's folder FString is 9 bytes longer, moving every later
  field. `parse_header_fields()` finds the group by its content, not by offset.
* **The header length varies per package, not per game.** `SFXGame.pcc` is 437 / 629 / 862 bytes on
  ME1 / ME2 / ME3, but `Engine.pcc` is **341 / 229 / 238** on the same three builds — smaller, and
  not in the same order as the game list. The header length is `first_chunk_offset`, so derive it.

### The coordinate-space error

```
image  = header + decompressed body        (the serialised package)
buffer = decompressed body                 (what the decompressor hands you)
buffer[i] == image[name_offset + i]
```

Every table offset must therefore be reduced by `name_offset` before indexing the buffer. Reading
them undisplaced puts each entry 133/142 bytes past its real start, so every entry straddles two
real entries — which is what produced the "rotated ME3 import layout", the "68.005 export stride",
the filler export-name column, and the belief that class attribution was unavailable.

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
| Whether function **bytecode** survives in these packages | **Solved**: it survives. Extent is `serial[32 : 32 + u32(serial+28)]` plus a 15-byte trailer on ME1/ME2 (6 on ME3), closing on every function in all three builds. The *opcode table* is still unproven - see `tools/melepkg/ue3_bytecode.py`, which deliberately ships no disassembler |
| Enum member values (e.g. `EWhoTriggers`) | **Solved**: a `UEnum`'s serial data is `UObject` fields then `TArray<FName>`, with the count at `+20` and 8-byte (index + number) names after it. Closes on 255/255 ME3 enums. `enum` and `enums` commands in `mele_tables.py` |
| The `depends_offset` section | unexamined; the export walk lands exactly on it, so what follows is a separate structure |

Resolved since this page was first written: the field offsets (they differ per game), the chunk
index (4-tuples, 95/95 verified), the name table (parseable, wide in ME3 and mixed in at least one
ME2 map), **the import and export tables** (see below), **function signatures in declaration
order**, and **that bytecode is present**.

## The import and export tables (solved 2026-10-29)

The header's offsets index the serialized **image**, and the decompressed buffer begins at
`image[name_offset]` because the name table is the first thing after the summary. Every offset must
therefore be reduced by `name_offset` - 142 on ME3, 133 on ME1/ME2, and it is a header FIELD
describing the summary's variable part, not a constant.

Missing this displaced every table read by 142/133 bytes and produced four false conclusions that
lived in this project for a session: a "rotated import layout" on ME3, an "export stride of
68.005", export name fields at `+15`/`+35`/`+26`, and "no offline class attribution".

Once displaced correctly:

* **Imports** are the standard UE3 `FObjectImport`, 28 bytes, ONE layout on all three builds:
  `ClassPackage FName (0,1) | ClassName FName (2,3) | OuterIndex (4) | ObjectName FName (5,6)`.
* **Exports** are 68 bytes: `ClassIndex(0) SuperIndex(4) OuterIndex(8) ObjectName FName(12,16)
  ArchetypeIndex(20) ObjectFlags(24, u64) SerialSize(32) SerialOffset(36) ExportFlags(40)
  NetObjectCount(44)`, followed by a `4 * NetObjectCount` TArray tail.
* **Package indices** resolve UE3-style: `0` null, `+n` is `export[n-1]`, `-n` is `import[-n-1]`.

Four independent closures now pass on every package, and `build()` refuses rather than returning a
table that fails any of them:

| check | pins |
|---|---|
| `table_adjacency` | `import_offset + 28 * import_count == export_offset` |
| `export_walk` | walking `export_count` records lands exactly on `depends_offset` |
| `serial_contiguity` | `serial_offset[k] + serial_size[k] == serial_offset[k+1]`, last one ends at the image length |
| `acceptance` | class chains terminate in a class; every `Default__X` resolves to class `X` |

Verified on **all 12,704 packages** in the three installs (100%), excluding nothing — the ten
133-byte header-only stubs and 380 uncompressed ME1 packages are handled as the distinct shapes they
are, not skipped. That is 896,562 export records in the sample that was measured for record count;
the full sweep is the population.

    python -I tools/melepkg/mele_tables.py summary <pkg>
    python -I tools/melepkg/mele_tables.py find    <pkg> <name>
    python -I tools/melepkg/mele_tables.py classes <pkg>
    python -I tools/melepkg/mele_tables.py chain   <pkg> <class>
    python -I tools/melepkg/mele_tables.py signature <pkg> <function>
    python -I tools/melepkg/mele_tables.py enum    <pkg> <EWhoTriggers>
    python -I tools/melepkg/mele_tables.py enums   <pkg>
    python -I tools/melepkg/mele_tables.py objects <pkg> [name]
    python -I tools/melepkg/ue3_bytecode.py survey <pkg>
    python -I tools/melepkg/ue3_bytecode.py strings <pkg> <function>

## Enum members

A `UEnum`'s serial data is the `UObject` fields then `TArray<FName> Names`:

    +0   NetIndex, +4 constant, +8 two zero dwords, +16 StateIndex
    +20  Names.Num()
    +24  names, each a 4-byte index plus a 4-byte number (8 bytes)
    end  24 + 8 * Num, which equals serial_size

Closes on **255 of 255** enums in ME3's `Engine.pcc`, which is what rules out the alternative
(count at `+16` with 4-byte names) - that hypothesis closes on zero. `EWhoTriggers` comes out as
`WT_PlayerOnly, WT_PlayerAndSquad, WT_Everyone, WT_TagList, WT_MAX` on ME1/ME2 and, on ME3,
`WT_PlayerOnly, WT_PlayerOnlyLocal, WT_PlayerAndSquad, ...` - **the same number means a different
policy per build.**

## Function signatures, offline

A function's parameters are its child exports, but exports are alphabetically ordered, so the
export list is NOT declaration order. The real order is the `UProperty.Next` chain at serial `+16`:

    python -I tools/melepkg/mele_tables.py signature <Engine.pcc> CreatePlayer

    CreatePlayer(ControllerId: Int, OutError: Str, bSpawnActor: Bool,
                 NewPlayer: Object, InsertIndex: Int) -> Object      [parms 0x2c]
        +0x00 ControllerId   +0x04 OutError     +0x14 bSpawnActor
        +0x18 ReturnValue    +0x20 NewPlayer    +0x28 InsertIndex

This reproduces the layout measured from live memory in a running game, field for field - the
strongest cross-check in this project, because the engine accepted that layout. Two rules make the
difference: a 16-byte `FString` is 4-aligned in this ABI, and the struct total is 4-aligned, not 8.

## Bytecode

Present. Extent is `serial[32 : 32 + u32(serial+28)]` plus a fixed trailer of 15 bytes on ME1/ME2
and 6 on ME3; `32 + length + tail == serial_size` closes on every `Core.Function` in all three
builds (5416/5416, 5350/5350, 5279/5279).

**A correction worth keeping:** the first version of this said the bytecode starts at 47/47/38,
because `serial_size - u32(+28)` is a constant per build. That constant is `32 + tail`, so the
extraction started inside the trailer and the closure check agreed with it. What caught it was
asking whether the region *contains the instructions* - `CreatePlayer` must begin with
`e7 a8 ... "Creating n..."` and the text was 15 bytes late. Closure tests self-consistency, not
truth.

The **opcode table is not solved**. A walk that "closes" is meaningless here: a partition into
instructions of any length 1..24 succeeds on 5279 of 5279 functions, so the closure test eliminates
almost no hypotheses. `ue3_bytecode.py` therefore extracts bytecode and string constants and stops.
String constants alone already answer real questions - `RemovePlayer` refuses on a role check
("player does not have appropriate role") in all three builds.
