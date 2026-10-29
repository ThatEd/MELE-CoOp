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
2. **Resolve `AddLocalPlayer` on the live object graph.** `ue3_script_map.py` gives the owning class
   and the parameter block. That is the input `CreatePlayer` could not supply and the reason its
   crash was unattributable.
3. **Compare ME2's `Engine.pcc` against ME3's.** Both have the function; ME3's registration path
   works. The difference between them is a concrete disassembly target, and it is the one comparison
   that has not been made.
4. **Only then dispatch**, through the bridge on the main thread, with a restore point.

## What the import table added

`tools/melepkg/mele_tables.py` parses the import table, which says **which package an object comes
from and what kind of object it is** — the thing an identifier list cannot say. Verified per game:

| | imports | layout | dominant source |
|---|---|---|---|
| ME1 `SFXGame.pcc` | 1,426 | `(cls 1, outer 3, obj 4, src 6)` | `Core` 1,292 |
| ME2 `SFXGame.pcc` | 1,909 | same as ME1 | `Core` 1,764 |
| ME3 `SFXGame.pcc` | 4,189 | **rotated**: `(cls 5, outer 0, obj 1, src 3)` | `Core` 4,021 |

ME3 stores the class and object columns the other way round. Reading ME3 with ME1's order produces
`object=PlayerController, class=Class`, which is backwards and internally consistent enough to look
correct — so the layout is declared per game and the acceptance test checks shape, not ranges.

**The export table is still not parsed.** Entries are 68 bytes and the region closes exactly on the
header's `depends_offset`, but the field order inside an entry is unestablished. That is the next
piece of work and it is what would let this page name *which class declares* `AddLocalPlayer` in
each build, rather than just that the name exists.

## Honest status

| Claim | Evidence |
|---|---|
| All three builds contain `AddLocalPlayer` and the split-screen surface | **proven** — identifier tables from each build's `Engine.pcc` |
| The earlier "there is no `AddLocalPlayer`" was measured on `SFXGame.pcc` only | **proven** — that package's table lacks it |
| `CreatePlayer` crashes in LE1 and LE2 | **proven** — three dispatches, stack traces in NOTES.md |
| `AddLocalPlayer` would work in ME2 | **unknown** — present, never dispatched |
| Which class declares it | **unknown** — needs the export table |
| Two panes need a second `LocalPlayer` | **derived**, not proven |
