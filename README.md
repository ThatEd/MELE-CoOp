# MELE Co-Op Mod

Campaign co-op for **Mass Effect Legendary Edition** — the host keeps Shepard, the client takes a
squadmate. Scope order is **LE3 first, then LE2, then LE1**.

Self-contained and published: clone it and run the tests, no game required. `local/` is git-ignored
and holds nothing that belongs in a repository ? the identifier tables and per-build offsets are
generated from your own copy of the game (see [Getting the data](#getting-the-data)).

## Where it stands

| | |
|---|---|
| **LE3** | A second player works. Input reaches P2's controller and P2 has its own view. `CreatePlayer` creates a real second `SFXLocalPlayer`. What has *not* run end to end is a whole level with P2 controlling the mission's own squadmate. |
| **LE2** | Offsets and player chain verified read-only. `CreatePlayer` crashes inside its own allocator — **do not call it**. The route that can work is possession of an existing pawn. |
| **LE1** | Same wall, three separate crashes. Has two levers the other two lack: `BioSeqAct_SetPlayerControlling` and `BioSeqAct_SetInputTrap`, both level-data, so they need no injected code. |

**No phase of the test suite has been executed against a game.** What is verified is the tooling,
the container format, the offsets, the crash analysis and the design. Read
[docs/README.md](docs/README.md) first — it links the per-topic pages and labels every claim
`proven` / `documented` / `derived` / `unknown`.

## The single most important correction

**`GameViewportClient.CreatePlayer` cannot add a player in LE1 or LE2.** Three attempts, two games,
every input verified first, `bSpawnActor` both ways — all crash, and the fault is in registering the
`LocalPlayer` at all, not in giving it a pawn. Only LE3 tolerates the call.

So "add a second *view* instead" does **not** work either: each `LocalPlayer` owns its own
`ViewState`, and `SplitscreenInfo` is indexed *by local player*. See
[docs/SPLITSCREEN-PLAN.md](docs/SPLITSCREEN-PLAN.md).

The route that does work is the one ME3SquadCoop ships: **possess a squadmate and attach a custom
camera** — never involving a second `LocalPlayer`.

## Layout

```
MELE Co-Op Mod/                 (this repository)
  README.md                     this file
  docs/                         every write-up, one page per topic
  tools/
    ue3live/                    read-only live inspection + the command suite + the runner
    melepkg/                    verified .pcc container reader (offline)
    bridge/                     in-process RPC server + Python client (the executor seam)
    saverestore/                cheap save restore points
    gamepool/                   run N isolated copies for tests
```

## Quick start

Nothing here needs a game running, except `probe` and anything that dispatches.

```bash
python -I tools/ue3live/run_tests.py          # ALL OK: 97 cases, no game needed
python -I tools/ue3live/ue3_command_suite.py list
python -I tools/ue3live/ue3_command_suite.py phases --verbose
python -I tools/ue3live/ue3_command_suite.py phases --fields --game ME1
python -I tools/ue3live/ue3_fields.py list
```

With a game open:

```bash
python -I tools/ue3live/ue3_check_chain.py --pid <pid> --exe MassEffect1.exe \
    --offsets local/MELE/offsets/offsets_LE1.json
python -I tools/ue3live/ue3_discover_external.py --pid <pid> --exe MassEffect3.exe \
    --out local/MELE/offsets/offsets_LE3.json
```

## What is here, and what each piece is for

### `tools/ue3live` — live inspection and the command suite

Everything opens the process `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION` only. No writes, no
allocation in the target, no remote thread — a wrong read costs nothing, a wrong write crashes the
game.

| File | Purpose |
|---|---|
| `ue3_proc.py`, `ue3_reader.py`, `ue3_props.py` | Read-only process primitives; a **validating** name decoder that refuses an index whose pool does not exist instead of reading adjacent memory; a property walker that follows `SuperStruct` so inherited fields are visible. |
| `ue3_discover_external.py`, `ue3_verify_offsets.py`, `ue3_find_engine_global.py` | Find `GObjects`, the name pool and the engine global **by structure** — no symbols, no SDK. A discovery result is a hypothesis until the verifier passes. |
| `ue3_playerchain.py`, `ue3_check_chain.py`, `ue3_level_pawns.py` | Resolve and assert the player chain (PASS/FAIL per link), and inventory the level's actors. |
| `ue3_conversation.py` | Conversation/scene detector. Four independent signals and a conservative verdict; **an unreadable field is never treated as "no scene"**. |
| `ue3_script_map.py` | Exact class/function/property/state attribution from a running game, because offline identifier tables give names but not ownership. |
| `ue3_command_suite.py` | **156 command mechanisms** in 20 categories, presence measured per game, arranged into 23 ordered phases. Also the live `probe` and the plan builder. |
| `ue3_run_plan.py` | Executes a plan through the bridge on the game's main thread. Probe-only mode validates everything without calling. Refuses a malformed plan before sending anything. |
| `ue3_fields.py` | The **write allow-list**: a caller names a *field*, never an address, so an arbitrary write cannot be expressed. Four writable fields, each with a measured per-game offset. |
| `mele_paths.py` | Finds `local/MELE` by searching upward, so the tools work in the hub and in this folder. |

### `tools/melepkg` — the `.pcc` container, offline

Mass Effect's packages are fully-compressed UE3 chunks using Oodle. This decodes them without the
game:

```
chunk := u32 magic 0x9E2A83C1 | u32 max_block 0x40000 | u32 total_csize
       | u32 total_usize      | block[N] {u32 csize, u32 usize} | data
N = (chunk_span - 16 - total_csize) / 8
```

Verified on **19/19 ME1, 31/31 ME2, 45/45 ME3** chunks, all invariants. Identifiers are ASCII in
ME1/ME2 and **UTF-16LE in ME3** — checking one encoding gives a confident zero.

`mele_script_map.py` uses it to inventory control gates and map-event classes per build.

### `tools/bridge` — the executor seam

A small RPC server a mod embeds, plus a Python client with a timeout on every call. The command
suite decides *what* to call; this runs it **on the game's main thread**, which is the only thread
where `UFunction` invocation is legal. `ProcessEvent`'s vtable slot differs per game (LE1 `+0x238`,
LE2 `+0x228`, LE3 `+0x230` as a wrapper) — read the body, never port the slot.

[`docs/COMMAND-SUITE-ADAPTER.md`](docs/COMMAND-SUITE-ADAPTER.md) specifies the five `ue3.*` methods
the game side must implement. **That side is not written** — it is the next piece of work.

### `tools/saverestore`, `tools/gamepool`

A failed experiment should cost seconds, not twenty minutes of unskippable cutscene. `saverestore`
snapshots the save directories with a SHA-256 manifest and restores by *quarantining* rather than
overwriting; it resolves the real Documents folder from the registry, which matters because on this
machine it is on `F:` and searching the user profile finds nothing.

`gamepool` runs several isolated hardlinked copies of a game from one JSON config — the cheap
harness split screen was meant to provide. `examples/masseffect-le-{me1,me2,me3}.json` are validated
per-game configs.

## Things that will bite you

| Trap | What actually happens |
|---|---|
| `CreatePlayer` on LE1/LE2 | three distinct crashes; refuse it by name |
| Porting an offset between builds | `PlayerController.Pawn` is `+0x3DC` / `+0x294` / `+0x3BC` |
| Reading identifiers as ASCII only | ME3 stores them **UTF-16LE**; you get a clean zero and conclude "absent" |
| A property walker without `SuperStruct` | reports 6 fields for a class that has 368 |
| One `UEngine` object | LE1 has three; `GamePlayers` is on `GameEngine`, not the `SFXEngine` the global points at |
| A name is a function | `Possess` and `possess` both exist; `CanFire` / `CanFireWeapon` / `WeaponFire` are three spellings |
| Believing a call that returned | ME1's crash 11 is in the engine's own thread *after* a correct call returned |
| A check that cannot fail | two of this project's drift tests passed while testing nothing |

`docs/NOTES.md` has all 31 rules, each with the incident that produced it.

## Getting the data

The repository ships code and findings, not game data. Two kinds of data are needed for the
game-specific tools, and both are generated from **your own copy of the game**:

| Data | Needed by | Why not committed |
|---|---|---|
| Identifier tables (`local/MELE/maps/*.json`) | `ue3_command_suite.py verify`, the drift tests | derived from the game's decompressed packages |
| Offsets (`local/MELE/offsets/offsets_LE*.json`) | `probe`, the runner, the field allow-list | per-build and **per-launch** artefacts for one exe hash |

Everything that does not need them works from a clean clone — the tooling, the runner, the write
allow-list, the container reader and all 97 tests.

```bash
# 1. Identifier tables. Needs an install (for the packages and Oodle), not a running game.
#    The install is found via $MELE_GAME_DIR, then the Steam registry, then a drive probe.
python -I tools/melepkg/mele_script_map.py \
    --package "<game dir>/Game/ME1/BioGame/CookedPCConsole/SFXGame.pcc" \
    --out local/MELE/maps/ME1.json

# 2. Verify the container model against your build before trusting anything derived from it.
python -I tools/melepkg/test_container_format.py

# 3. Offsets. Needs a RUNNING game; this only reads.
python -I tools/ue3live/ue3_discover_external.py --pid <pid> --exe MassEffect1.exe \
    --out local/MELE/offsets/offsets_LE1.json
python -I tools/ue3live/ue3_verify_offsets.py --pid <pid> --exe MassEffect1.exe \
    --offsets local/MELE/offsets/offsets_LE1.json
```

Already have a data set elsewhere? Point at it:

```bash
MELE_DATA_DIR=/path/to/local/MELE python -I run_all_tests.py      # sh
$env:MELE_DATA_DIR = "D:\mele-data"                               # PowerShell
```

Skipped test cases tell you which file is missing and which command regenerates it, so a clean clone
is green-with-visible-gaps rather than red for a reason unrelated to the code.

Nothing here contains game files, assets, saves or decompiled binaries. Findings are described, not
shipped. Build on and patch existing decompilations freely; follow their licences.

Maintainers preparing a release: [PUBLISHING.md](PUBLISHING.md) is the checklist.


## Credit and licence

Built on the work of [ME3SquadCoop](https://github.com/MatrimAl/ME3SquadCoop) (MIT), whose shipped
co-op is the route this project follows, and
[LEMP](https://www.nexusmods.com/masseffectlegendaryedition/mods/3463) /
[lemp-asi](https://github.com/PayDay42/lemp-asi), a proven UE3 networking reference.

MIT — see `LICENSE`.
