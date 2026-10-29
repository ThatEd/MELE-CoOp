# ME3 test suite: what is set up, and how to run it

LE3 is the first target game because its co-op already works. This page is the operational
counterpart to [COMMAND-SUITE.md](COMMAND-SUITE.md): what exists for ME3 specifically, what runs
without a game, and what the first live session should do.

Everything here is **offline-capable**. Nothing in steps 1–4 needs the game running.

## 1. Preflight the offsets — the step that saves a launch

```bash
python -I tools/ue3live/ue3_preflight_offsets.py --game LE3
```

Expected today:

```
LE3 offsets: .../local/MELE/offsets/offsets_LE3.json
  claims   : game='LE3' exe='MassEffect3.exe' verified=False
  WARN  this file does NOT claim to be verified: ...
Fit to use. NOT verified - the offsets are present but nobody re-checked them.
```

`local/MELE/offsets/offsets_LE3.json` now exists with the three discovery RVAs
(`objects_rva 0x1887E40`, `name_pools_rva 0x17B33D0`, `engine_global_rva 0x18B31D0`) and the LE3
chain column. **It says `verified: false` on purpose**, because no live LE3 has re-asserted those
values on this machine. The preflight exists so that difference is visible rather than assumed.

It also refuses an offsets file that carries another build's values. `PlayerController.Pawn` is
`+0x3DC` / `+0x294` / `+0x3BC` across the three releases, so a file copied from LE1 and relabelled
LE3 is wrong by `0x20` and nothing else would notice.

## 2. Run everything that needs no game

```bash
python -I run_all_tests.py                 # from the repo root
```

| Suite | Cases | Needs |
|---|---|---|
| `test_conversation_verdict.py` | 10 | nothing |
| `test_command_suite.py` | 30 | identifier maps (present) |
| `test_run_plan.py` | 29 | nothing — drives a real bridge server |
| `test_fields.py` | 19 | nothing |
| `test_mele_paths.py` | 9 | nothing |
| `test_preflight_offsets.py` | 12 | nothing |
| `test_container_format.py` | 1 | game install + Oodle |
| `test_mele_tables.py` | 13 | game install for the live cases; the rest skip |

Verified on this machine: **123 cases pass** (109 in `ue3live`, 14 in `melepkg`), `ALL OK`, with the
container and table suites skipping their install-dependent parts when the game is absent.

## 3. Read the command catalogue for ME3

```bash
python -I tools/ue3live/ue3_command_suite.py list --game ME3
python -I tools/ue3live/ue3_command_suite.py phases --verbose
python -I tools/ue3live/ue3_command_suite.py phases --fields --game ME3
```

ME3 has **143 of the 156 catalogued command mechanisms** — the most of the three, because it is the
only build that kept a replication layer (`IsLocallyControlled`, `ClientSetLocation`,
`ServerMove`, `CreatePlayer`, `LayoutPlayers` all present). That is why LE3 goes first.

**13 are absent from ME3** (`156 - 143`) and they matter, because each is a mechanism that exists in
another build and not this one:

| Absent from ME3 | Present in | Consequence |
|---|---|---|
| `SeqAct_Delay` | LE1, LE2 | a trigger delay built on the stock delay node cannot be ported to LE3 |
| `SetCollision` | LE1, LE2 | replaced by `SetCollisionType`; a port calling `SetCollision` is a silent no-op |
| `StartConversation` / `EndConversation` | LE1, LE2 | LE3's conversation entry is elsewhere |
| `PlayFaceFXAnim` | LE1 | LE3 lip-sync goes through another path |
| `WeaponFire` | LE1 | LE3 uses `StartFire` / `CanFire` / `HandleWeaponFiring` |
| `SetBusyState` | LE1, LE2 | a gate that exists in two builds and not the third, and is invisible unless measured |
| `BioSeqAct_SetPlayerControlling`, `BioSeqEvt_PlayerInput`, `BioSeqAct_SetInputTrap` | LE1 | the three LE1-only control hooks |
| `SpawnActorFromType`, `DebugSpawnActor` | LE1/LE2 | LE3 creates by `Spawn` |
| `StopMusic` | LE1 | |

`CreatePlayer`, `RemovePlayer`, `LayoutPlayers`, `ClientSetLocation`, `ClientSetRotation`,
`ServerMove`, `ClientAckGoodMove`, `ClientAdjustPosition`, `ReplicatedEvent`, `bNetOwner`,
`NetUpdateFrequency` — **40 of the 156 mechanisms exist in ME3 and neither other build.** That is
the whole reason this game goes first: it is the only one with a replication layer to build on.

## 4. Parse the packages offline

```bash
python -I tools/melepkg/mele_tables.py summary "<game dir>/Game/ME3/BioGame/CookedPCConsole/SFXGame.pcc"
python -I tools/melepkg/mele_tables.py find    <...>/SFXGame.pcc PlayerController
python -I tools/melepkg/mele_tables.py imports <...>/SFXGame.pcc --package Engine --limit 20
```

This is new and it is the thing that answers "which package does this object come from and what kind
of object is it". Verified on all three builds; ME3 needs its own column order:

| | imports | layout |
|---|---|---|
| ME1 `SFXGame.pcc` | 1,426 | `(cls 1, outer 3, obj 4, src 6)` |
| ME2 `SFXGame.pcc` | 1,909 | same |
| ME3 `SFXGame.pcc` | 4,189 | **rotated**: `(cls 5, outer 0, obj 1, src 3)` |

## 5. What the first live session should do

**Nothing mutating.** The first LE3 session is read-only and has one job: set `verified: true` on
the offsets file, or find out which value is wrong.

```bash
# 1. validate the discovered RVAs against the running build
python -I tools/ue3live/ue3_verify_offsets.py --pid <pid> --exe MassEffect3.exe \
    --offsets local/MELE/offsets/offsets_LE3.json

# 2. assert the player chain link by link (PASS/FAIL each)
python -I tools/ue3live/ue3_check_chain.py --pid <pid> --exe MassEffect3.exe \
    --offsets local/MELE/offsets/offsets_LE3.json

# 3. resolve every command against the live object graph
python -I tools/ue3live/ue3_command_suite.py probe --pid <pid> --exe MassEffect3.exe \
    --offsets local/MELE/offsets/offsets_LE3.json --out local/MELE/probes/le3_probe.json

# 4. turn that into an ordered plan and dry-run it
python -I tools/ue3live/ue3_command_suite.py plan --probe local/MELE/probes/le3_probe.json \
    --game ME3 --out local/MELE/probes/le3_plan.json
```

Only after `ue3_check_chain` prints PASS on every link should `verified` be flipped to `true` and
any mutation be considered.

**What is still missing before step 4 can dispatch:** the game-side `ue3.*` bridge methods
(`ue3.info`, `ue3.read`, `ue3.write`, `ue3.call`, `ue3.chain`) do not exist in a mod yet. The
specification is [COMMAND-SUITE-ADAPTER.md](COMMAND-SUITE-ADAPTER.md). `probe` and `plan` need none
of them; only execution does.

## 6. The known gaps, stated plainly

| Gap | Consequence |
|---|---|
| `offsets_LE3.json` is `verified: false` | every offset in it is a documented value, not a re-checked one |
| No live LE3 run since those RVAs were recorded | the preflight cannot close that gap; only a game can |
| The export table is unparsed | import attribution works; "which class declares `AddLocalPlayer` in LE3" does not |
| The game-side adapter is unwritten | `probe`/`plan` run; `run_plan` execution cannot |
| No phase has ever executed against a game | the 123 tests are the tooling, not a co-op session |

## What "set up" means here, concretely

- **Ran:** 123 tests across 8 suites, `ALL OK`, on this machine.
- **Written this session:** `ue3_preflight_offsets.py` + 12 tests, `mele_tables.py` + 13 tests,
  `offsets_LE3.json`, `offsets_LE2.json` (complete, not discovery-only).
- **Not done:** no game launched, so nothing is marked verified and no phase has run.
