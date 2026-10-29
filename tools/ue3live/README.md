# ue3live

Read a **running** Unreal Engine 3 game's object graph, with no injection and no writes.

Every other UE3 tool in this hub works on files. This one works on a live process, which is
what you need the moment scripts, offsets or splitters have to be found *in the build that is
actually running*. It exists because the alternative — putting a probe DLL inside the game to
answer "what is at this offset" — is how games get crashed: a wrong read costs nothing, a
wrong call or write is unrecoverable, and rebuilding a save state can cost minutes of
unskippable cutscene.

`OpenProcess` asks for `VM_READ | QUERY_INFORMATION` only, so these tools *cannot* write,
allocate in the target, or start a remote thread even by accident.

## What it solves

UE3's `GObjects` and name pool are plain data, so they can be found structurally, with no
symbols and no prior offsets:

1. A `TArray<UObject*>` is `{data, count, max}`. Scan the image's writable sections for those
   three fields with a plausible `count`.
2. **Require every object's `Class` pointer to be a member of the candidate array.** Only the
   real object array satisfies this — a class is an object. It rejected every false positive on
   all three games tested (`class_pointers_outside_array = 0`).
3. Require an object that is its own class (a meta-class such as `Class`), then use its `Name`
   index to locate the pool: search for a pool slot where
   `pool + (index & 0x1FFFFFFF) + 12` decodes to that name.
4. Find the engine global by requiring an image slot to dereference to the **live** engine
   object — not merely to contain the pointer.

Measured on Mass Effect Legendary Edition, one process each, no injection:

| | LE1 | LE2 | LE3 |
|---|---|---|---|
| `objects_rva` | `0x1770670` | `0x173CC48` | `0x1887E40` |
| `name_pools_rva` | `0x169A080` | `0x16609F8` | `0x17B33D0` |
| `engine_global_rva` | `0x17813F0` | `0x16C8E00` | `0x18B31D0` |

## Tools

| Tool | What it does |
|---|---|
| `ue3_proc.py` | Shared read-only primitives: `Reader` (read/ptr/u32, chunked scans), `module_base`, `module_info`. Import this rather than re-implementing `OpenProcess`. |
| `ue3_reader.py` | `Game` object: name decoding with a **validating** decoder, class/super lookup, property iteration. Rejects a name index whose pool does not exist instead of reading adjacent memory and returning a garbage name that then passes naive comparisons. |
| `ue3_props.py` | `class_properties`: a class's fields **including inherited ones**, by walking `SuperStruct`. Reading only `UClass.Children` silently hides inherited fields — it reported 6 fields for a class that has 368, and "no `Controller` on `BioPawn`" when `Controller` is declared on `Pawn`. |
| `ue3_discover_external.py` | Finds `GObjects`, the name pool and the engine global by structure (the four steps above). Writes a JSON offsets file. |
| `ue3_verify_offsets.py` | Checks a discovered offsets file against the live game: class membership, name decoding, engine dereference. A discovery result is a hypothesis until this passes. |
| `ue3_find_engine_global.py` | Verifies an engine-global candidate **by dereferencing it**, the only way to tell it from an image slot that merely happens to contain the pointer. |
| `ue3_playerchain.py` | Resolves `Engine.GamePlayers` → `LocalPlayer.Actor` → controller → pawn for the running game. Enumerates *every* live `*Engine` object, because the array is not always on the one the engine global points at. |
| `ue3_check_chain.py` | Re-derives the whole player chain from an offsets file and asserts each link, printing PASS/FAIL. Machine-checks a documented table instead of trusting it. |
| `ue3_level_pawns.py` | Inventories the actors of the level the player is in, naming pawns by their controller and behaviour. |
| `ue3_conversation.py` | Read-only conversation/scene detector: reports `bPlayersOnly`, the controller's cinematic bit(s), conversation counts, the active conversation object and the pawn's `Physics`, then a conservative verdict (`playing` / `scene` / `conflicting` / `incomplete`). An unreadable field is never treated as "no scene". `--watch` samples over time. |
| `ue3_command_suite.py` | The command catalogue and the ordered test area. 156 command **mechanisms** in 20 categories, presence measured per game, plus `probe` (resolve every command in a running game, read-only), `plan` (turn a probe into ordered bridge requests) and `phases` (the 23-phase test area). See [COMMAND-SUITE.md](../../docs/COMMAND-SUITE.md). |
| `ue3_run_plan.py` | The **runner**: executes a plan through the in-game bridge on the game's main thread. Probe-only mode validates every role, function and parms block without calling anything. Refuses a malformed plan in preflight, gates destructive phases behind `--yes-destructive`, and records a refusal as a result rather than losing the run. Also executes field phases (`writes`) with a mandatory read-back. |
| `ue3_fields.py` | The **write allow-list**. A caller names a *field*, never an address, so an arbitrary write cannot be expressed. Four writable fields, each with a measured per-game offset and its provenance; a field with no measured offset for that build refuses rather than borrowing another build's. Enums are checked by member, not width. |
| `commands.json` | The generated catalogue. Do not hand-edit: `verify` recomputes presence from `local/MELE/maps/*.json` and reports drift, and `catalogue` refuses to write when the taxonomy disagrees with the data. |
| `run_tests.py` | Runs every game-free suite here and prints `ALL OK`. Wired into `tools/test_all.py`. |
| `test_conversation_verdict.py` | Unit tests for that verdict logic, runnable without a game (10 cases). |
| `test_command_suite.py` | Unit tests for the catalogue, the drift check, the target roles, the phase ordering and the live resolver's selection logic (30 cases). Several break the thing they test on purpose: a check that cannot fail is worse than none. |
| `test_run_plan.py` | Tests the runner against the real `bridge_client` and `fake_game` server (29 cases), including one that builds a real plan with `build_plan` and executes it end to end — the test that catches schema drift between the two modules. |
| `test_fields.py` | Tests the write path (19 cases), mostly **refusals**: cross-game offset borrowing, unverified writes, undefined enum members, read-only fields, and a growing write surface. |
| `mele_paths.py` | Where Mass Effect's data lives. `local/` is shared with other games' work in this hub, so MELE files are under `local/MELE/`; this resolves that in one place, with `$MELE_DATA_DIR` override, the moved layout winning, and a fallback to the pre-move layout so a reorganisation cannot break a tool. |
| `test_mele_paths.py` | Tests the resolver (6 cases), including the offset-file spelling fallback and that this repo really resolves to `local/MELE`. |

## Traps these tools encode, learned the hard way

- **Object class identity by name is unreliable.** A controller can genuinely *be* a
  `BioPlayerController` while `obj_class(pc) == "BioPlayerController"` is false, because that
  class object's name does not decode. Match by class pointer or `SuperStruct` lineage.
- **A global that "is not there" may be on another object.** One game has two live `UEngine`
  subclasses; `GamePlayers` was on `GameEngine` while the engine global pointed at `SFXEngine`.
  Scanning the wrong one looks exactly like "not in game yet". Check a subsystem object's
  `Outer` when a global seems absent.
- **`Default__X` is an archetype, not the live object.** Accepting the first object whose name
  ends in `Engine` found `Default__Engine` and produced "no engine global at all".
- **The first slot holding a pointer is not the global.** Two image slots contained the engine
  pointer; one dereferenced to a `StaticMesh`. Verify by dereference.
- **`objects_count` moves** as the game loads (observed 140k → 165k in one session). Re-read
  the header; a saved count is a note, not a constant.
- **Counters that look like names are not names.** One game masks pointers into name indices,
  so a naive `[obj+0x48] & 0x3FF` yields numbers. Validate that a decode is printable and
  NUL-terminated before believing it.

## Limits, stated plainly

- **Windows only.** Uses `ReadProcessMemory` / `VirtualQueryEx` / a toolhelp snapshot.
- x64 layout only (`UObject` class at `+0x50`, name at `+0x48`, outer at `+0x40`,
  `SuperStruct` at `+0x60`, `Children` at `+0x70`, property offset at `+0x94`, size at
  `+0x74`). UE3 x86 or UE4 layouts differ and are not handled.
- The scan reads the main module's writable regions; a game whose `GObjects` lives outside its
  image would need the region list widened.
- It reads game memory. Only acceptable on a machine and a copy you own, against single-player
  or your own private session — a research tool, not a cheat.

## Tested against

Mass Effect Legendary Edition, all three executables, verified by SHA-256 before use:

| Game | exe SHA-256 (prefix) |
|---|---|
| LE1 | `13be26ecc292eb62b9f3cbee7…` |
| LE2 | `71976f300ea7159ca22574d29…` |
| LE3 | `72047e65a819878397c25650cc…` |

Usage, for LE1 as an example:

```
python -I tools/ue3live/ue3_discover_external.py --pid <pid> --exe MassEffect1.exe \
       --out offsets_LE1.json
python -I tools/ue3live/ue3_verify_offsets.py --pid <pid> --exe MassEffect1.exe \
       --offsets offsets_LE1.json
python -I tools/ue3live/ue3_check_chain.py     --pid <pid> --exe MassEffect1.exe \
       --offsets offsets_LE1.json
```

Working notes and the offsets these produced for each game:
[docs/](../../docs/INDEX.md).

## Tests

```
python -I tools/ue3live/run_tests.py        # ALL OK: 94 cases across five suites
```

No game is needed and none is touched.

**Two command-suite cases deliberately corrupt a copy of the map files** to prove the drift
check can fail. Writing them found two bugs in the check itself, both of the same kind: removing
`Possess` while the real name table also holds `possess`, and removing an identifier that was
already absent. A verification that cannot report failure is the most expensive kind of test to
have.

**The role tests were proven the same way.** Aiming `Possess` at a pawn instead of a controller,
aiming `RemovePlayer` at a player instead of the viewport, and aiming `IsLocallyControlled` at
nothing all produce a failing test — checked by breaking each one rather than assumed.

**The runner is tested against a real server.** `test_run_plan.py` starts the actual
`fake_game` bridge server and drives the actual `bridge_client` through it, so a marshalling or
protocol bug shows up in the suite instead of in a crashed game. One case builds a plan with the
real `build_plan`, executes it, and requires every ready command to reach the game with a role
the adapter understands — which is what catches schema drift between the generator and the
runner. It found three real role bugs on its first run.

**The write path is tested mostly by refusing it.** `test_fields.py` is 19 cases and most of them
are about what must NOT happen: writing LE2's viewport field (unmeasured offsets are refused
rather than borrowed from LE1), a write whose read-back disagrees, a write with no read-back at
all, an enum member the engine does not define, a read-only field, and a write surface that has
grown past what was reviewed. Writing them caught a real bug — `DesiredSplitscreenType` is one
byte, so a width-only check accepted `200`, which leaves the render path undefined with no error.
Enums are now checked by member.
