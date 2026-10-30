# Split-screen for LE1 and LE2: what the reverse engineering says, and what to do

This page answers one question — *what is the plan for ME2 and ME1 split screen?* — and the
answer is not the one the project assumed a week ago. It is written from measured evidence, and
two of its sections are **corrections to earlier documents** rather than additions.

Short version:

1. **Two panes in one process is not reachable in LE1/LE2**, and the reason is not an offset.
2. **What split screen was for is already built** — two isolated copies per machine.
3. **The route that works is ME3SquadCoop's**: a possessed squadmate with a custom camera, never
   involving a second `LocalPlayer`. Not split screen at all.
4. **ME2 goes first**, then ME1, for reasons that are measured, not preferred.

---

## 1. Why two panes cannot be the plan

Three fail points. They were expensive to find and cheap to state.

| | Finding | Label |
|---|---|---|
| **A1** | No `AddLocalPlayer`, no `JoinSplitscreen`, no `CreateLocalPlayer` anywhere in LE1's object graph. `GameViewportClient.CreatePlayer` is the *only* scripted entry. | proven |
| **A2** | `Actor.Spawn(BioPlayerController)` **succeeds**, then the engine faults at `call qword ptr [rax+0x6D0]` with `rax = 0`. `+0x6D0` is the new controller's `PlayerInput`. A controller with no `LocalPlayer` has no input object. | proven |
| **A3** | Each `LocalPlayer` owns its own `ViewState`, and `SplitscreenInfo` / `PerPlayerSplitscreenData` is indexed **by local player**. One `LocalPlayer` means one view. | **derived**; the struct layout now confirms it is an array of panes (`SplitscreenData.PlayerData: TArray<PerPlayerSplitscreenData>`), but the index binding is unproven |

A3 is the one that matters for split screen, and it is *derived*, not proven — deliberately. It
says "splitting one player gives you one view", and the experiment that would prove it requires
a second `LocalPlayer`, which is the thing that crashes. **So the plan does not rest on A3.** It
rests on A1 and A2, which are proven, plus the crash table below.

*(**A1 is superseded** — see [SECOND-PLAYER-ME2.md](SECOND-PLAYER-ME2.md). `AddLocalPlayer` is
present in `Engine.pcc` on all three builds and is a `GameViewportClient` method; "no
`AddLocalPlayer` anywhere" came from scanning only `SFXGame.pcc`. A2 and A3 stand.)*

### A3's structure, now read offline

`PerPlayerSplitscreenData` and `SplitscreenData` are direct children of `GameViewportClient`, and a
`UScriptStruct` has no native base class, so its field offsets are exact:

    PerPlayerSplitscreenData  { SizeX: float @0x00, SizeY @0x04, OriginX @0x08, OriginY @0x0C }
    SplitscreenData           { PlayerData: TArray<PerPlayerSplitscreenData> }

    python -I tools/melepkg/mele_tables.py fields <Engine.pcc> PerPlayerSplitscreenData

Identical on ME1, ME2 and ME3. `SplitscreenData.PlayerData` being an **array** is what makes A3's
"indexed by local player" reading the natural one — an array of pane rectangles, one per player.
That raises A3 from *derived* to *structurally supported* but does not prove it: the index binding
still needs a live read.

### The crash table, which is what actually settles it

| Attempt | Game | `bSpawnActor` | Result |
|---|---|---|---|
| 1 | LE2 | 1 | crash, `ucrtbase!aligned_realloc`, 7 engine frames past `ProcessEvent` |
| 2 | LE1 | 1 | crash `+0x99611a` (`mov r10,[rax+0x90]`, `rax=0`), **engine's own thread, after the call returned** |
| 3 | LE1 | **0** | crash, `ucrtbase!aligned_realloc`, synchronous inside `CreatePlayer` |

`bSpawnActor` changes nothing, so the fault is in **creating and registering the `LocalPlayer` at
all** — the login path — not in giving it a pawn or a view. Three attempts, two games, every
input verified first each time (parms `0x2C` field by field, `ProcessEvent` slot confirmed by
disassembling `mov r14, r8`, `UFunction` resolved by name, level gate passed, and a check-only
build reporting success). **LE3 is the only build that tolerates the call.**

That is the wall, and it is the same wall from three directions.

### A correction to this project's own advice

An earlier recommendation was "add a second *view* rather than a second player". **Do not follow
that blindly** — it is the A3 trap: a view is owned by a `LocalPlayer`, so you cannot get one
without the other, and getting the other crashes. The recommendation is retracted here; the
notes already record the retraction, and this page exists so it is not re-discovered.

---

## Only LE3 has a split-screen camera mode

A census of every class in the install turns up exactly **one** split-screen class, and it is in one
build:

    ME1  0
    ME2  0
    ME3  1   SFXCameraMode_SplitScreenCombat : SFXCameraMode_Combat   (SFXGame.pcc)

That does not mean LE1/LE2 cannot do split screen - the *layout* machinery is engine-side and present
everywhere (`ESplitScreenType`, `LayoutPlayers`, `PerPlayerSplitscreenData`, all confirmed on all
three builds). What it means is that LE3 shipped a **camera mode** built for it and LE1/LE2 did not,
so a LE2 split-screen attempt has to work with the engine's generic layout and the game's ordinary
combat camera, where LE3 has a mode written for two panes.

This is the concrete reason the plan orders ME2 first and treats it as the harder case: it is missing
a piece LE3 has, not merely missing the proof.

## The split-screen enum, decoded

`ue3_fields.py` used to say the `ESplitScreenType` member list was "not recoverable offline". It is -
a `UEnum`'s members are in its serial data - and the list is **identical on ME1, ME2 and ME3**:

| value | member |
|---|---|
| 0 | `eSST_NONE` |
| 1 | `eSST_2P_HORIZONTAL` |
| 2 | `eSST_2P_VERTICAL` |
| 3 | `eSST_3P_FAVOR_TOP` |
| 4 | `eSST_3P_FAVOR_BOTTOM` |
| 5 | `eSST_4P` |
| 6 | `eSST_MAX` |

Two consequences. **Value 1 is HORIZONTAL**, so the one step this project has actually observed
working - `DesiredSplitscreenType = 1` - was producing a horizontal split, not the vertical one an
earlier comment claimed. And `eSST_MAX` (6) is a bound rather than a layout, so the write
allow-list still refuses it even though the engine defines it.

`test_fields.py` now reads the enum from the package and compares it to the allow-list, so the two
cannot drift; the case prints the member list when they disagree.
## 2. What split screen was for is already built

Split screen's stated purpose was a cheap local harness for two players. **Two isolated copies
on one machine does that job and is strictly better for the next step**, because it also models
the network boundary that co-op will eventually have.

Already provisioned:

| | |
|---|---|
| Configs | `tools/gamepool/examples/masseffect-le-{me1,me2,me3}.json` |
| ME1 instance | **built** — 36,631 hard links, its own `GamerSettings.ini` (1280x720) while the original stays 3440x1440 untouched |
| Verification | pinned exe SHA-256 matched; originals byte-identical afterwards |

So the honest position is: **splitscreen's benefit is real but it is already realised by a
different mechanism**, and its remaining unique value — two views *in one process* — is exactly
the part LE1/LE2 refuse to give.

---

## 3. The route that works, and it is not split screen

ME3SquadCoop's shipped co-op **does not add a player**. It possesses a squadmate and attaches a
**custom shoulder camera** to it. That design:

- needs no `CreatePlayer`, so it cannot hit crashes 10–13;
- touches no engine-managed array, so rule 3 is respected;
- gives a real second body with real input.

This is **Route 3** in [ADDING-MISSING-SYSTEMS.md](ADDING-MISSING-SYSTEMS.md), and the evidence
has promoted it from "cheapest fallback" to "the route".

Its weakness is real and must not be hidden: the engine's own code assumes "the player" is the
`LocalPlayer`'s controller, so anything keyed on that — HUD, the power wheel, conversation
camera — follows Shepard, not P2. That is a work item, not a blocker.

### The crux, stated precisely

**Possession needs a `PlayerController` to possess with, and there is only one — it is driving
Shepard.** That is the whole problem, and it is a design constraint rather than a bug.

So Route 3 needs one of two things:

- **(a)** a controller that lives with input but no `LocalPlayer` — which is exactly what
  `p2v2_input.dll` was built to test, and **it has never been run**; or
- **(b)** acceptance that P2's body is driven through a direct input path that never becomes a
  `PlayerController` at all, and everything engine-side that asks for one is handled by Shepard's.

(a) is one injection away from an answer. That is why it is step 1.

---

## 4. Order: ME2 first, then ME1

Not a preference. Measured reasons:

| | ME2 | ME1 |
|---|---|---|
| Offsets file | discovered (`objects_rva`, `name_pools_rva`, `engine_global_rva`), **verified** | complete, 62 keys, `ue3_check_chain` 8/8 PASS |
| Player chain | `LocalPlayer.Actor +0x68`, `Controller.Pawn +0x294`, `Controller.Player +0x698` | `+0x68`, `+0x3DC`, `+0x6A8` |
| `ProcessEvent` slot | `+0x228` (body read: `mov r14, r8`) | `+0x238` (same prologue) |
| Trap | `+0x230` is a **wrapper with a different signature** — calling it LE3's way is what crashed LE2 the first time | **three live `UEngine` subclasses**: `GamePlayers` is on `GameEngine`, not the `SFXEngine` the engine global dereferences to |
| `CreatePlayer` | crashes | crashes 3 ways |
| Control hook | none found | **`BioSeqAct_SetPlayerControlling`**, `BioSeqEvt_PlayerInput`, `BioSeqAct_SetInputTrap` — all LE1-only |

**ME2 first** because it is closer to LE3 (which has working co-op), so a ME2 failure is
informative rather than ambiguous, and its `ProcessEvent` slot is already proven. **ME1 second**
because it has the two strongest *unique* leads — its own "change which pawn the player controls"
Kismet action and its own input trap — and those are level-data, so they need no injected code at
all. Trying those earlier would mean learning LE1's three-engine trap at the same time as
learning the mechanism; doing ME2 first separates the two problems.

---

## 5. The steps

Each is independently testable, and each has a read-only check for whether it worked.

| # | Step | Cost | Read-only check |
|---|---|---|---|
| 1 | **Run `p2v2_input.dll` on ME2.** Spawns a `PlayerInput`, stores it at `PlayerController+0x6F8`, calls `InitInputSystem`. **Built, never run.** | one injection; restore point exists | `S04-player-input-slot` reads the pointer; `gameinfo.num_players` must not drop |
| 2 | **ME2 read-only baseline** — `ue3_check_chain.py`, then `P00`/`P01` of the command suite | free | PASS/FAIL per link |
| 3 | **Measure ME2's viewport offsets.** `SplitscreenInfo` / `Desired` / `Active` are only measured on LE1. | one live session, read-only | `ue3_fields.py check --game ME2` goes from 3 unmeasured to 0 |
| 4 | **Request a split (ME1, and ME2 once measured).** `S01-request-split` writes `DesiredSplitscreenType=1` and verifies the read-back. | one write | `Active` read after; expect 1 with one view — A3 made measurable |
| 5 | **Camera.** A second view via `DynamicCameraActor` is already documented as working; no camera needs inventing. | moderate | the second view exists on screen |
| 6 | **ME1: try the two LE1-only Kismet hooks.** `BioSeqAct_SetPlayerControlling` and `BioSeqAct_SetInputTrap` are level data, so no injected code is required. | low — no injection | `controller.pawn` before/after |
| 7 | **Splitscreen, only if a `LocalPlayer` source appears.** Revisit section 1; do not restart from it. | unknown | — |

Step 1 is first because it decides the shape of everything after it: if a controller can live
with input and no `LocalPlayer`, Route 3 is unblocked and the whole remaining problem is camera
plus UI. If it cannot, the fault names the next missing companion object, which is still progress
and still cheaper than another `CreatePlayer` attempt.

---

## 6. What was built to support this

The split-screen plan is mostly **field writes**, not function calls, so the command suite gained
a write path — and the write path is deliberately hard to misuse.

| Piece | What it does |
|---|---|
| [`tools/ue3live/ue3_fields.py`](../tools/ue3live/ue3_fields.py) | A **fixed allow-list** of named fields. A caller names a *field*, never an address, so arbitrary writes cannot be expressed. |
| `S00`–`S04` field phases | The split-screen plan as verified reads and writes: baseline, request split, restore, player cap, `PlayerInput` slot. |

Four rules, all enforced by tests:

1. **No measured offset, no write.** LE2's viewport offsets are unmeasured, so
   `S01-request-split` **cannot run on ME2** — it refuses and says to measure them. Borrowing
   LE1's `0x1C0` would be a guess, and `PlayerController.Pawn` alone is `+0x3DC` / `+0x294` /
   `+0x3BC` across the three builds.
2. **Read-back is mandatory.** A write whose read-back disagrees is a failure. A write with *no*
   read-back is also a failure, because "applied" and "went somewhere harmless" are otherwise the
   same answer.
3. **Enums are checked by member, not width.** `DesiredSplitscreenType` is one byte, so a width
   check once accepted `200` — which puts the render path in an undefined state with no error.
   It is now kind `enum8` with an explicit member set `{0, 1}`.
4. **The surface is small and capped.** Four writable fields; a test fails if it grows past six,
   because a safe primitive becomes an unsafe one by accretion.

The write surface as it stands:

| Field | Kind | ME1 | ME2 | Writable |
|---|---|---|---|---|
| `viewport.desired_splitscreen_type` | enum8 `{0,1}` | `0x1C0` | **unmeasured** | yes |
| `viewport.active_splitscreen_type` | enum8 | `0x1C1` | **unmeasured** | read only |
| `viewport.splitscreen_info` | ptr | `0x118` | **unmeasured** | read only |
| `gameinfo.max_players` | int | `0x440` | `0x448` | yes |
| `gameinfo.max_players_allowed` | int | `0x444` | `0x44C` | yes |
| `gameinfo.num_players` | int | `0x448` | `0x450` | read only |
| `controller.player_input` | ptr | `0x708` | `0x6F8` | yes |
| `controller.pawn` | ptr | `0x3DC` | `0x294` | read only |

```
python -I tools/ue3live/ue3_fields.py check --game ME2
python -I tools/ue3live/ue3_command_suite.py phases --fields --game ME1
```

---

## 7. Two corrections to earlier documents

Both are findings from this work, and both reduce planned work rather than adding to it.

### 7a. The `MaxPlayers` cap raise is probably unnecessary

`ADDING-MISSING-SYSTEMS.md` and the notes say LE1/LE2 need `MaxPlayers`/`MaxPlayersAllowed`
raised to 2 before `CreatePlayer`, and that the cap is what blocks a second controller. The
measured values say otherwise:

| | `MaxPlayers` | `MaxPlayersAllowed` | `NumPlayers` |
|---|---|---|---|
| LE1 `BioSPGame` | **16** | **32** | 1 |
| LE2 `BioSPGame` | **16** | **32** | 1 |

The `1 / 1` reading in the notes was **LE3's `SFXGameInfoSP`**, not LE2's `BioSPGame`. So for
LE1/LE2 the cap is not the obstacle, and a planned mitigation step can be dropped. That is risk
removed, and it is only visible because the two builds' game-info values were measured
separately instead of inferred from each other.

`S03-player-cap` now reads these three fields and writes nothing, so the claim is re-checked on
every run rather than trusted.

### 7b. A3 is derived, not proven — and the plan does not depend on it

Stated as a correction because a derived claim presented as fact is how an earlier retraction
happened. A3 ("one `LocalPlayer` means one view") cannot be tested without the thing that
crashes, so it stays `[derived]` and the plan rests on A1/A2 plus the crash table instead.

---

## 8. Status, honestly

| Piece | State |
|---|---|
| Field allow-list + verified write path | **written, 19 tests, all pass** |
| Split-screen field phases `S00`–`S04` | **defined**; fully runnable on ME1, 3 of 5 blocked on ME2 by unmeasured offsets |
| Runner write path (with mandatory read-back) | **written, tested against a real bridge server** including the "game lies about the read-back" case |
| `ue3.write` in the game-side adapter | **not written** — spec in [COMMAND-SUITE-ADAPTER.md](COMMAND-SUITE-ADAPTER.md) |
| ME2 viewport offsets | **not measured** — step 3 |
| `p2v2_input.dll` | **built, never run** — step 1 |
| Any step actually executed against a game | **no** |

The honest summary: **everything that can be built without a running game is built and tested.
The plan's first three steps all need the owner to launch a game**, and step 1 needs ME2
specifically.

---

## 9. What would change this plan

- **A `LocalPlayer` source appearing for LE1/LE2.** If `p2v2_input.dll` shows a controller can
  live without one, section 3 route (a) becomes viable, and split screen becomes a camera
  problem worth re-attempting.
- **`DesiredSplitscreenType=1` producing a genuine second view.** That would falsify A3 and
  reopen two panes. `S01-request-split` is written to detect exactly this, which is why it exists
  even though the expectation is that it will show one view.
- **A second `PlayerInput` path.** If `InitInputSystem` can be driven for a controller the engine
  never registered, A2 stops being a wall.

None of these are hoped for. They are the specific observations that would justify revisiting the
conclusion, written down so the conclusion can be overturned by evidence rather than by fatigue.
