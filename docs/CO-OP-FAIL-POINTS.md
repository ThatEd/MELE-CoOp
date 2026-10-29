# Mass Effect Legendary Edition — co-op fail points on paper

A map of every game subsystem co-op has to touch, what this project has *proven* about it
in LE1/LE2/LE3, where it will fail, and how to detect that failure cheaply before paying
for it with a crash.

Written for the next agent so that no fail point has to be rediscovered by experiment.

## How to read the evidence labels

This document is deliberately pedantic about what is known, because a plausible guess
presented as a fact has already cost several crashes in this project.

| Label | Meaning |
|---|---|
| **[proven]** | Measured or crashed by this project, with a stack, dump or live read to show for it |
| **[documented]** | Established by ME3SquadCoop's protected two-process tests, per its own docs |
| **[derived]** | Follows from UE3's structure and the engine state we can see; not yet exercised |
| **[unknown]** | Not established. Do not build on it |

Offsets are for the exact builds pinned in [NOTES.md](./NOTES.md). Function addresses are
**per launch** — resolve by name at run time, never hardcode.

## The single most important correction this document makes

An earlier version of the game write-up proposed `GameViewportClient.CreatePlayer` as the
route to a second player, and treated it as the main target. That is wrong for two reasons,
both now clear:

1. It crashes LE1 and LE2 in three distinct ways (below), so it is not a route at all.
2. **LE3SquadCoop's working co-op does not use it either.** Its verified path is a native
   multiplayer `listen` on the host and `open <peer>` on the guest, followed by possession
   of a squadmate. `[documented: RUNTIME-INTEGRATION.md]` The second player comes from the
   engine's *network* login path, which is already written and already exercised by the
   game's own multiplayer code — not from a single-player addition.

That reframes everything: the hard part in LE1/LE2 is not adding a player, it is that
**there is no multiplayer login path to borrow**, because LE1 and LE2 have no multiplayer at
all in the Legendary Edition.

---

## A. Second local player

| Game | Status |
|---|---|
| LE3 | `CreatePlayer` works — a real second `SFXPlayerController` appears. `[proven]` |
| LE2 | `CreatePlayer` crashes inside its own allocation path, 7 engine frames past `ProcessEvent`, `ucrtbase!aligned_realloc`. `[proven]` |
| LE1 | `CreatePlayer(bSpawnActor=1)` crashes in the engine's own thread *after* the call returns (`+0x99611a`, `mov r10,[rax+0x90]`, `rax=0`). `CreatePlayer(bSpawnActor=0)` also crashes, in `ucrtbase!aligned_realloc`, synchronously inside the call. `[proven]` |

**Fail point A1 — there is no `AddLocalPlayer` *in `SFXGame.pcc`*.** Scanning LE1's script package
finds no `AddLocalPlayer`, no `JoinSplitscreen`, no `CreateLocalPlayer`. `[proven for that package]`

**CORRECTION 2026-10-29: that is true of `SFXGame.pcc` and false of the build.** All three games
ship the whole local-player and split-screen surface in `Engine.pcc`:

| Identifier | ME1 `Engine.pcc` | ME2 `Engine.pcc` | ME3 `Engine.pcc` |
|---|---|---|---|
| `AddLocalPlayer` | yes | yes | yes |
| `CreatePlayer`, `RemovePlayer`, `LayoutPlayers` | yes | yes | yes |
| `SplitscreenInfo`, `DesiredSplitscreenType`, `ActiveSplitscreenType`, `PerPlayerSplitscreenData` | yes | yes | yes |
| `InitInputSystem`, `SpawnPlayerController`, `LocalPlayerControllers` | yes | yes | yes |

Measured from each build's `Engine.pcc` identifier table. The earlier conclusion came from scanning
one package per game, which answers a narrower question than "does the build contain this". See
[SECOND-PLAYER-ME2.md](SECOND-PLAYER-ME2.md).

**What still stands:** dispatching `CreatePlayer` crashed LE1 and LE2 three ways with every input
verified, so having the function is not the same as the call succeeding. What changes is the next
thing to try — `AddLocalPlayer` is the step `CreatePlayer` itself delegates to, and it has never
been dispatched.

**Fail point A2 — a standalone controller is not viable.** `Actor.Spawn(BioPlayerController)`
with the measured `0x50` parms layout **succeeds** and then the engine faults on a later
frame at `call qword ptr [rax+0x6D0]` with `rax = 0`, where `+0x6D0` is the new controller's
`PlayerInput`. `[proven]` A controller without a `LocalPlayer` has no input object, and the
campaign's login path is what normally creates one.

**Fail point A3 — the view cannot be split instead.** Each `LocalPlayer` owns its own
`ViewState`/camera, and `SplitscreenInfo`/`PerPlayerSplitscreenData` is indexed *by local
player*. `[derived]` One `LocalPlayer` means one view, so "add a view, not a player" is not
available either. **Still derived, not proven** — the experiment that would settle it needs a second
`LocalPlayer`, and the only scripted route to one (`CreatePlayer`) crashes. `AddLocalPlayer` being
present in `Engine.pcc` is the untried route.

**How to detect A1–A3 cheaply, in LE1/LE2:** do not dispatch. Read, per game:

- `Engine` → `GamePlayers` count (LE1: on `GameEngine`, `+0x498`)
- `LocalPlayer+0x68` → controller; `Controller+0x3DC` (LE1) / `+0x294` (LE2) → pawn
- the controller's `PlayerInput` property (LE1: `+0x6D8`… resolve by name, never assume)

`tools/ue3live/ue3_check_chain.py` already does the first three and prints PASS/FAIL. If
`GamePlayers` still reads 1 after a candidate mechanism, nothing happened; if it reads 2 but
`PlayerInput` is null, you are at A2 and the next crash is already written down.

**What is genuinely unknown:** whether manually appending a `LocalPlayer` +
`BioPlayerController` pair to `GamePlayers` and initialising input by hand would work. It
requires writing engine structures (`GamePlayers` array) that the engine manages, which rule
3 warns against. `[unknown]`

---

## B. Possession — the mechanism that actually works

| Fact | Value |
|---|---|
| `Controller.Possess` parms | LE1 `0x0C`: `inPawn@0x00`, `bVehicleTransition@0x08`. `[proven]` |
| `BioPlayerController.Possess` parms | LE1 `0x14` (adds a trailing object). `[proven]` |
| `BioAiController.UnPossess` parms | `0x00`. `[proven]` |
| Verified chain, LE1 | `LocalPlayer+0x68` → `BioPlayerController`; `.Player +0x6A8` → same `LocalPlayer`; `.Pawn +0x3DC` → `BioPawn`; `Pawn.Controller +0x400` → same controller. `[proven]` |

**Fail point B1 — possession needs a controller that is not already Shepard's.** With one
`LocalPlayer`, the only `PlayerController` is busy. This is the crux of the whole problem,
and it is a design constraint, not a bug.

**Fail point B2 — possession alone does not give working abilities.** `[documented]` The power
route needs the power-wheel handler's controller, valid quickslot definitions **and
initialised cached power icons**; the mod's own notes say possessing Ashley alone does not
establish those prerequisites. LE3's documented static route is:

    UseAbility → doHotKey → inherited UsePower → GameModeDefault.UsePower
              → SquadOrderUsePower → ApplyTacticalOrders

**Fail point B3 — release/reacquisition is not clean.** `[documented]` When control was
released, the possessed pawn lost **world and actor collision**, and those settings stayed
disabled when control returned; the pawn then fell. Repairing it needs
`Actor.SetCollisionType` with `COLLIDE_BlockAllButWeapons` (6) *plus* a separate
`bCollideWorld` bit restored by compare-exchange, because the engine method does not restore
that bit.

**Fail point B4 — position continuity across a scene is not preserved.** `[documented]` After
a synthetic scene release, a pawn **jumped ~54,107 units toward the world origin**, and after
reacquisition the host's pawn jumped ~54,025 units, lost its world base and fell.

**Detection:** before possessing, assert (a) the target pawn's controller is a
`BioAiController` and not a `PlayerController`; (b) `Physics == 1` (Walking); (c) the
engine's own identification of the character, not a name guess. LE1's pawn identity has two
tiers `[proven]`:

- reliable when present: a party pawn (`m_PawnAttributes` class is `BioAttributesPawnParty`,
  `Squad == Player`) whose owned-object subtree references the character name — this is how
  Kaidan was identified (`refs=3` vs `0` for the other squadmate);
- **absent in some levels:** both party pawns read generic `ActorGameName = 'BioPawn'` with
  zero character references. In that state identification is impossible and a plugin must
  refuse rather than possess a stranger.

---

## C. Camera

- LE3's working design is a **custom shoulder camera attached to the possessed pawn**, not a
  UE3 splitscreen pane. `[documented]`
- LE1 has the splitscreen surface, measured live: `SplitscreenInfo +0x118`,
  `DesiredSplitscreenType +0x1C0`, `ActiveSplitscreenType +0x1C1`; both types read `0`.
  `[proven]`

**Fail point C1 — see A3.** Splitscreen cannot produce a second view without a second
`LocalPlayer`, so camera work is downstream of A, not an alternative to it.

**Fail point C2 — the camera must not fight the engine's own view.** `[derived]` The
campaign constantly drives the player's view during conversations and cutscenes
(`BioWorldInfo.Tick`, `bPlayersOnly`). A custom camera attached to a squadmate will be
overridden whenever the script takes the view back.

---

## D. Input

**Fail point D1 — one `PlayerInput` per controller, and a spawned controller has none.**
`[proven]` (crash 13). This is the concrete, dump-verified reason a standalone controller
cannot work.

**Fail point D2 — key delivery is unverified even in LE3.** `[documented]` "Ordinary key
delivery, reliable walking and combat remain unverified. The read-only input capture is
asynchronous, not a true post-Tick observation."

**Fail point D3 — two windows, one keyboard.** `[derived]` Both game instances receive the
same physical input unless it is filtered per process. LE3's guest path uses a real network
session so it does not face this; a two-instance local test does.

**Detection:** read the controller's `PlayerInput` pointer and check it is a live object
before any mechanism that assumes input. If it is null, stop — do not wait for the engine to
dereference it, which is what crash 13 did.

---

## E. Story, cutscenes and conversations — the biggest under-estimated area

**Fail point E1 — an unfocused window pauses the world.** `[documented]` LE3 has a custom
focus check in `BioWorldInfo.Tick`: when the world is not in `PlayersOnly`, the window lacks
focus and the game mode can pause, it sets `m_bPausedByFocusLoss` and calls
`PauseGame(true)`. A protected two-process test captured **39 consecutive observations over
10 seconds** with both pause flags true because the host window was outside foreground.

This is the single most likely reason a two-instance local co-op test "does not work" while
looking like a sync bug. Any two-window testing must neutralise it first.

**Fail point E2 — the script owns the characters you want to possess.** `[derived]` Cutscenes
and conversations drive squadmates directly. Possessing one fights the matinee, which is
exactly what `Physics == 7 (Interpolating)` reports.

**Fail point E3 — dialogue modes behave differently.** `[documented]` Mode-0 dialogue and
mode-7 dialogue are not equivalent; a mode-0 landing exchange returned control without the
focus-owned pause, which "does not validate full mode-7 dialogue or safe position continuity
across every scene".

**Fail point E4 — synthetic scene release corrupts position.** See B4.

**Detection:** before mutating, read the level identity and the pause/`PlayersOnly` state.
Rule 7 of the project's own notes is the lesson: gate on level identity, not on incidental
state like "a pawn exists", because between levels there is no pawn and that is precisely
when a mutation is unsafe. LE1's menu level vs mission sizes were measured (`EntryMenu`
≈107,530 objects vs a mission ≈169,000) — but object count alone is not a gate, because it
also moves as streaming loads.

---

## F. Physics

**Fail point F1 — scripted pawns are at physics 7 and ignore movement by design.**
`[proven]` ME3's Anderson: `Physics == 7`. LE1's own player pawn read `Physics == 7` with
`Location == 0,0,0` during a cutscene, and `Physics == 1` with a real location in gameplay.
`Pawn.Physics` offsets: LE1 `+0x27C`, LE2 `+0x284`, LE3 `+0x268`.

This is a *state* check, not a bug. Any body handed to player 2 must be at physics 1.

---

## G. Collision

**Fail point G1 — release disables collision permanently.** See B3. `[documented]`

**Fail point G2 — partial restoration parses as success.** `[documented]` "A measured return
can retain world collision while actor collision is absent… The blocking bit may be absent or
retained: both exact states were observed with reciprocal ownership." So a naive
"collision is back" check is not enough; the healthy shape must be compared against the
class/template defaults.

**Detection:** capture the healthy collision shape (cylinder/template identity, blocking
mask, `bCollideWorld`) *before* releasing control, and compare after. Two disabled snapshots
must match before a repair is authorised.

---

## H. Save, level load and restore points

- Saves live under the **real** Documents folder — here `<documents>\BioWare\
  Mass Effect Legendary Edition\Save\{ME1,ME2,ME3}\<slot>\*.pcsav`. `[proven]`
  `%USERPROFILE%\Documents` is the wrong path and silently reads as "no saves".
- `tools/saverestore` snapshots and restores them, verified by corrupting one file and
  deleting another then restoring. `[proven]`

**Fail point H1 — both instances must be in the same level.** `[derived]` Nothing replicates
level identity for free; if the host loads the mission and the guest is still in a menu, any
shared state is meaningless.

**Fail point H2 — recovering from a crash is expensive, and that shapes what you dare try.**
LE2's intro is ~20 minutes of unskippable cutscene. Four crashes in this project each cost a
reload. This is why H exists before anything else.

---

## I. Netcode

- LE3 has working UE3 multiplayer underneath (LEMP restores the horde mode; ME3SquadCoop uses
  native `listen`/`open`). `[documented]`
- **LE1 and LE2 Legendary Edition have no multiplayer at all.** `[derived]` No listen path,
  no net driver in the campaign — which is precisely why there is no login machinery to
  borrow (section A) and why `PlayerInput` has no natural owner.

**Fail point I1 — `PackageMap`/`ActorChannel` decoding is fragile.** `[documented]` A
retained real LE3 pair saw `Pawn.InvManager` become **null** during `ActorChannel.ReceivedBunch`
while pawn and weapon identity stayed unchanged; the observation could not distinguish
property decoding from engine callbacks clearing it.

**Fail point I2 — the host must own the session.** `[documented]` ME3SquadCoop refuses to
proceed without a native `-MULTIHOME=` argument, because "setting an environment variable or
`FURL.Host` alone cannot select the local interface". Socket-level details matter more than
they look.

---

## J. Combat, health and inventory

**Fail point J1 — there is no authority concept in a single-player campaign.** `[derived]`
Damage, health and inventory are computed locally with no arbitration, so two instances will
disagree the moment both simulate.

**Fail point J2 — a damage observation is not a damage verdict.** `[documented]` Even in LE3
the observer records `damage_verified` and `hostility_verified` as **false**; a health
decrease within one dispatch "does not exclude synchronous scripted/delegate effects, prove a
hostile target, establish client replication, or demonstrate sustained combat".

**Fail point J3 — read health from the damage module, not legacy fields.** `[documented]`
Health must come from the target's reciprocal damage module's `CurrentHealth`; shields from
the reciprocal inventory chain's `CurrentShields`. Legacy pawn health and quantised
replication fields are explicitly not used.

---

## K. Powers and abilities

Covered under B2. The short version: the static route exists and is documented with
function-entry RVAs for LE3's build, and the prerequisite that breaks naive possession is
**initialised cached power icons plus valid quickslot definitions on the power-wheel
handler**. `[documented]`

---

## L. The one thing that could be built today

The doc above is mostly about *not* building things. The one experiment that is worth its
cost is a **read-only probe** answering, for LE1 and LE2:

1. Does `Engine.GamePlayers` ever exceed 1 by any means the engine itself provides?
2. Is there any `PlayerController`-like object that is not Shepard's controller?
3. What is `PlayerInput` on each of them?

All three are answered by `tools/ue3live` with no injection and no writes. If (2) is "no" in
every level tested, then section A's conclusion is confirmed empirically rather than by
inference, and the only remaining route for LE1/LE2 is the hand-built pair — which needs a
deliberate decision about writing engine structures.

---

## Summary table

| Area | LE3 | LE2 | LE1 | Highest-value next check |
|---|---|---|---|---|
| Second local player | works via `CreatePlayer`; real co-op uses network login | crashes (allocator) | crashes 3 ways | read-only: is there ever a second controller? |
| Standalone controller | n/a | untested | crash: null `PlayerInput` | — (settled) |
| Possession | works, prerequisites documented | chain measured | chain measured, reciprocal | possession on a *second* controller |
| Camera | custom shoulder cam | n/a | splitscreen surface present, unused | — (blocked on A) |
| Input | unverified in-game | n/a | `PlayerInput` null kills a spawned controller | — (settled) |
| Story/focus | auto-pause on focus loss | likely same | likely same | neutralise focus pause before any 2-window test |
| Physics 7 | blocks movement | same | same | already a gate in the plugin |
| Collision | lost on release, repair documented | untested | untested | capture healthy shape first |
| Netcode | exists, fragile | none | none | — (structural) |
| Combat | no authority, verdicts false | none | none | — (structural) |
| Saves/restore | snapshots work | works | works | take a snapshot before every attempt |
