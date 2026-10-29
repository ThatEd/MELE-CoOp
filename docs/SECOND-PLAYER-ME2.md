# The second player in ME2 — what the engine packages say

This page answers one question: **how does a second player get created in ME2?** It is written from
identifier evidence across *all* of each build's packages, not from one, and it corrects an earlier
conclusion that was drawn from a single package.

## The correction first, because it changes the plan

An earlier finding in [CO-OP-FAIL-POINTS.md](CO-OP-FAIL-POINTS.md) says:

> **Fail point A1 — there is no `AddLocalPlayer`.** Scanning LE1's full object graph finds no
> `AddLocalPlayer`, no `JoinSplitscreen`, no `CreateLocalPlayer`.

That is true of `SFXGame.pcc` and **false of the build**. The engine surface lives in `Engine.pcc`:

| Identifier | ME1 `Engine.pcc` | ME2 `Engine.pcc` | ME3 `Engine.pcc` |
|---|---|---|---|
| `AddLocalPlayer` | **yes** | **yes** | **yes** |
| `CreatePlayer` | yes | yes | yes |
| `RemovePlayer` | yes | yes | yes |
| `LayoutPlayers` | yes | yes | yes |
| `SplitscreenInfo` | yes | yes | yes |
| `DesiredSplitscreenType` | yes | yes | yes |
| `ActiveSplitscreenType` | yes | yes | yes |
| `PerPlayerSplitscreenData` | yes | yes | yes |
| `InitInputSystem` | yes | yes | yes |
| `SpawnPlayerController` | yes | yes | yes |
| `LocalPlayerControllers` | yes | yes | yes |

All three builds ship the entire split-screen and local-player surface. None of it is in
`SFXGame.pcc`, which is why scanning that package alone produced "it does not exist".

**Why this happened, so it does not happen again.** The identifier maps under `local/MELE/maps/`
were generated from one package per game (`SFXGame.pcc`). That answers "does the game's own script
package reference this", which is a narrower question than "does the build contain this". For
engine machinery the answer lives in the `Engine` package. Rule 31 applies with force: a value
measured in one artefact is not a value of the system.

## What this does *not* change

The crash evidence stands. `GameViewportClient.CreatePlayer` was **dispatched** in LE2 and LE1 and
crashed there three separate ways, with every input verified first. Having the function is not the
same as the call succeeding:

| Attempt | Game | `bSpawnActor` | Result |
|---|---|---|---|
| 1 | LE2 | 1 | crash, `ucrtbase!aligned_realloc`, 7 engine frames past `ProcessEvent` |
| 2 | LE1 | 1 | crash `+0x99611a`, engine's own thread, **after** the call returned |
| 3 | LE1 | 0 | crash, `ucrtbase!aligned_realloc`, synchronous inside `CreatePlayer` |

So the mechanism is still unproven. What changes is where to look next: not "the call is missing",
but "the call runs and the engine's own registration path faults". Those need different
investigations.

## The ME2 route, in order

`AddLocalPlayer` is present and is a **better candidate than `CreatePlayer`** because it is the
function `CreatePlayer` itself calls to register the player. Reaching past a failing wrapper to the
step it delegates to is the same idea that worked for `ProcessEvent`: the wrapper differed per game,
the inner body did not.

1. **Read, do not dispatch.** `ue3_level_pawns.py` and `ue3_check_chain.py` establish the live state:
   `LocalPlayer.Actor`, `Controller.Pawn`, and whether `Engine.GamePlayers` count is 1.
2. **`AddLocalPlayer` is `GameViewportClient.AddLocalPlayer`** — settled offline from `Engine.pcc`'s
   export table on all three builds, no game needed. The live step is only to confirm the viewport
   object exists and to read its `AddLocalPlayer` params, not to discover the owner.
3. **Compare ME2's `Engine.pcc` against ME3's.** Both have the function; ME3's registration path
   works. The difference between them is a concrete disassembly target, and it is the one comparison
   that has not been made.
4. **Only then dispatch**, through the bridge on the main thread, with a restore point.

## What the import table added

`tools/melepkg/mele_tables.py` parses the import table, which says **which package an object comes
from and what kind of object it is** — the thing an identifier list cannot say:

| | imports | layout (`type`/`obj`/`outer`/`src`) | type-column share | dominant source |
|---|---|---|---|---|
| ME1 `SFXGame.pcc` | 1,426 | `1 / 4 / 3 / 6` | 88% | `Core` 1,292 |
| ME2 `SFXGame.pcc` | 1,909 | `1 / 4 / 3 / 6` | 91% | `Core` 1,764 |
| ME3 `SFXGame.pcc` | 4,189 | **`5 / 1 / 0 / 3`** | 94% | `Core` 4,021 |

**Correction:** the layout row above is wrong. There is ONE layout on all three builds, the standard
UE3 `FObjectImport` `ClassPackage(0) ClassName(2) Outer(4) ObjectName(5)`. The apparent ME3 rotation
was an artefact of reading the table 142 bytes past its real start — every entry straddled two real
entries. See CONTAINER-FORMAT.md and rule 34. The share and source columns happened to survive the
shift because they were measured on different columns; the *layout* claim did not.

That gives a directly useful answer for all three builds — the kind of thing, and where from:

| object | type | source | ME1 | ME2 | ME3 |
|---|---|---|---|---|---|
| `PlayerController` | `Class` | `Core` | yes | yes | yes |
| `PlayerInput` | `Class` | `Core` | yes | yes | yes |
| `LocalPlayer` | `Class` | `Core` | yes | yes | yes |
| `Pawn` | `Class` | `Core` | yes | yes | yes |
| `LocalPlayerControllers` | `Function` | `Core` | — | yes | yes |
| `SpawnPlayerController` | `Function` | `Core` | — | — | yes |

The `—` marks mean **absent from that build's `SFXGame.pcc` imports**, not absent from the build.
That distinction matters and the export table now settles it: all six, plus `AddLocalPlayer`,
`CreatePlayer`, `RemovePlayer`, `LayoutPlayers` and `InitInputSystem`, are **exports of
`Engine.pcc` on all three builds**, and `AddLocalPlayer` is a `Core.Function` whose outer is
`GameViewportClient` everywhere. So ME1 does have `LocalPlayerControllers` and ME2 does have
`SpawnPlayerController` — SFXGame simply does not import what Engine defines.

**The export table is now parsed**, so class attribution is available offline. Rule 33's retraction
turned out to be one symptom of a coordinate-space error: header offsets index the serialized
*image*, and the decompressed buffer starts at `image[name_offset]` (142 bytes on ME3, 133 on
ME1/ME2). Reading the table 142 bytes late made every entry straddle two real entries, which is
what produced the "rotated import layout", the "68.005 export stride" and the filler name column.
See rule 34 in NOTES.md.

`tools/melepkg/mele_tables.py` now resolves, on all three builds, with four independent closures
passing:

    python -I tools/melepkg/mele_tables.py find    <SFXGame.pcc> SFXPlayerController
    python -I tools/melepkg/mele_tables.py classes <SFXGame.pcc>

The answer `AddLocalPlayer` needed, measured per build with `mele_tables.py chain`:

    ME2  SFXPlayerController -> BioPlayerController -> BioBasePlayerController -> Core.PlayerController
    ME3  SFXPlayerController -> BioPlayerController -> Core.PlayerController
    ME1  (SFXGame.pcc defines no SFXPlayerController)

    ME2  SFXPawn_Player -> SFXPawn -> BioPawn -> Core.Pawn
    ME3  SFXPawn_Player -> SFXPawn_PlayerParty -> SFXPawn -> BioPawn -> Core.Pawn
    ME1  SFXPawn_Player -> BioPawn -> Core.Pawn

Note the builds do **not** agree on the middle of the chain: ME2 inserts `BioBasePlayerController`
and ME3 does not, and ME1 has no `SFXPawn_PlayerParty`. That is rule 31 applied to class hierarchy -
a chain measured on one build is not the chain on another, so anything that walks or pattern-matches
it must be per-build.

So the class the client's controller must be is a `SFXPlayerController` subclass, whose chain reaches
`Core.PlayerController` through `BioPlayerController` on every build that has one; and the pawn a
second player would drive is an `SFXPawn_Player` subclass reaching `Core.Pawn`. Neither is a guess.

## Honest status

| Claim | Evidence |
|---|---|
| All three builds contain `AddLocalPlayer` and the split-screen surface | **proven** — identifier tables from each build's `Engine.pcc` |
| The earlier "there is no `AddLocalPlayer`" was measured on `SFXGame.pcc` only | **proven** — that package's table lacks it |
| `CreatePlayer` crashes in LE1 and LE2 | **proven** — three dispatches, stack traces in NOTES.md |
| Import and export tables parse on all three builds | **proven** — four closures, **12,704/12,704 packages** (100% of the three installs) |
| `SFXPlayerController : BioPlayerController : [BioBasePlayerController on ME2 only] : Core.PlayerController` | **proven** — export table chain, both builds; the builds differ in the middle |
| `AddLocalPlayer` would work in ME2 | **unknown** — present, never dispatched |
| `AddLocalPlayer` is `GameViewportClient.AddLocalPlayer` on all three builds | **proven** — `Engine.pcc` export table, class `Core.Function`, outer `GameViewportClient` |
| Two panes need a second `LocalPlayer` | **derived**, not proven |
