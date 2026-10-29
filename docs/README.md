# Mass Effect Legendary Edition (2021)

| | |
|---|---|
| Engine | Unreal Engine 3 |
| Versions tested | Steam appid `1328670`, buildid `10352613`; ME3 exe `2.0.0.48602`, SHA-256 `72047e65a819878397c25650ccd9c1dc646db6b2517f88a69acacdc6215b9ec7` |
| Projects | [ME3 Squad Co-op](https://github.com/MatrimAl/ME3SquadCoop) (research prototype), [LEMP](https://www.nexusmods.com/masseffectlegendaryedition/mods/3463) (wave mode, not campaign), [Nexus 723](https://www.nexusmods.com/masseffect3/mods/723) (abandoned 2019 POC, original ME3 only) |
| QA level | `none` for campaign co-op. ME3SquadCoop's own synthetic suite and native harness pass locally after building; no in-game run has been made here. |
| Last updated | 2026-10-28 |

## Where things stand

**Player 2 works as a player.** In a live game the owner confirmed that input reaches player 2's
controller and the view is player 2's own — including looking around. What did not work was *movement*,
and the cause is identified rather than mysterious: the possessed Anderson was a scripted NPC at
physics 7, which ignores movement by design. There is no input problem to solve.

So what remains is one narrower thing: **give player 2 a walking body** (physics 1) of the right
character. A `SFXPawn_PlayerVanguard` stand-in does that today; the stand-in-then-swap design swaps it
for the mission's real Anderson when Shepard reaches him.

Not yet proven end to end: a full level played with player 2 controlling a squadmate. `CreatePlayer`
also faults when called between levels (root cause found, gate implemented and verified).

The scope goal here: host keeps Shepard, the client takes the level's assigned squadmate, campaign
played in order — **LE3 first, then LE2, then LE1**.

**All three games are now mapped, read-only, from outside the process.** LE3's stand-in-then-swap
design runs; LE2 and LE1 have verified offset files, verified player object chains, verified
`CreatePlayer` parameter layouts and a verified `ProcessEvent` entry point each.

**But adding a player is the wrong goal in LE1/LE2.** Three mechanisms have failed there, all with
their inputs verified first, and a single `LocalPlayer` has a single view so splitscreen is not a
workaround either. See the section below and [CO-OP-FAIL-POINTS.md](./CO-OP-FAIL-POINTS.md).

The cheapest-experiment problem is fixed: saves were never missing, they were under a Documents folder
on another drive, and `tools/saverestore` now snapshots and restores them, so a crashed attempt costs
seconds instead of a cutscene.

## The route that actually works (and why LE1/LE2 are harder)

Worth stating before the detail, because it reframes the whole problem and an earlier version of
this write-up had it wrong:

**ME3SquadCoop's verified co-op does not add a second player at all.** Its host performs a native
in-place `listen` and its guest performs `open <peer>`; the second player comes from the engine's own
**multiplayer login path**, and only then does the guest possess a squadmate. `CreatePlayer` is a
single-player addition that happens to work in LE3 and crashes in LE1/LE2 — it was never the route
that matters.

That is why LE1 and LE2 are the hard ones: **their Legendary Edition builds have no multiplayer at
all**, so there is no login machinery to borrow. The obstacle is not a missing offset, it is a whole
engine subsystem that does not exist in those builds.

A full subsystem-by-subsystem map of every fail point — with evidence labels distinguishing what this
project proved from what was documented elsewhere, derived, or is still unknown — is in
[CO-OP-FAIL-POINTS.md](./CO-OP-FAIL-POINTS.md).

The plan for **adding the subsystems LE1/LE2 are missing**, with splitscreen as the cheap logic
harness before network play, is in [ADDING-MISSING-SYSTEMS.md](./ADDING-MISSING-SYSTEMS.md).

Two supporting pages:

- [CONVERSATIONS.md](./CONVERSATIONS.md) - the function-level map for making player 2 a **spectator**
  during conversations: which engine state fields decide it, two candidate mechanisms, and what is
  still unknown.
- [TEST-ENVIRONMENT.md](./TEST-ENVIRONMENT.md) - running two isolated copies of each of the three
  games, with the per-game configs, what was verified, and the one thing isolation cannot fix
  (shared saves).
- [CONTROL-GATES.md](./CONTROL-GATES.md) - where the player cannot shoot: the named firing chain
  (`IsLocallyControlled` -> `HandleWeaponFiring` -> `CanFireWeapon` -> `StartFire`), every other
  gate, and how to read them live. Includes a measured table of which gate names exist in which
  build.
- [MAP-EVENTS.md](./MAP-EVENTS.md) - the Kismet level-event classes per game: 322 / 306 / 225
  names, **86 present in all three**, and what the ME1-only and ME3-only sets mean for porting.
- [TRIGGER-DELAY.md](./TRIGGER-DELAY.md) - delaying walk-in events until player 2 passes: the
  trigger actors and their `Touch`/`UnTouch` detection, `EWhoTriggers` and `m_bInstigatorIsPlayer`,
  four insertion points ranked by risk, and the per-event policy a co-op mod actually needs.
- [CONTAINER-FORMAT.md](./CONTAINER-FORMAT.md) - the **verified** `.pcc` container format, so the
  script layer can be read offline instead of only from a running game. Now includes the **import
  and export tables**: object name, class, superclass and outer for every object in a package, so
  class attribution and inheritance chains are available with no game running. The four closures
  that prove it, and the coordinate-space error that hid them for a session, are in there.
- [COMMAND-SUITE.md](./COMMAND-SUITE.md) - the **test area**: 156 command mechanisms in 20
  categories with presence measured per game, arranged into 23 ordered phases, each stating what
  it proves and what would make it wrong. Also the per-game capability differences it measured
  (`IsLocallyControlled` absent in LE2, `SetCollision` vs `SetCollisionType`, LE1's
  `BioSeqAct_SetPlayerControlling` control hook, why `CreatePlayer` is LE3-only).
- [COMMAND-SUITE-ADAPTER.md](./COMMAND-SUITE-ADAPTER.md) - the four in-game bridge methods a mod
  must implement for the suite to actually call anything, with the validation order that turns a
  silent wrong answer into a loud one.
- [SPLITSCREEN-PLAN.md](./SPLITSCREEN-PLAN.md) - why two panes cannot be the plan for LE1/LE2, the
  measured per-game field table, the ordered steps (ME2 first, then ME1), and **two corrections to
  earlier documents**: the `MaxPlayers` cap raise is probably unnecessary, and "add a view instead
  of a player" is retracted.

## Existing work

| Project | Game | Last activity | What it really does |
|---|---|---|---|
| [ME3SquadCoop](https://github.com/MatrimAl/ME3SquadCoop) | LE3 | 2026-10-25 (`fix/guest-equipment-readiness`) | Guest squadmate possession, shoulder camera, session handoff/disconnect restore. Combat replication and equipment readiness still open. MIT. |
| [LEMP](https://www.nexusmods.com/masseffectlegendaryedition/mods/3463) + [lemp-asi](https://github.com/PayDay42/lemp-asi) | LE3 | v0.6.0 | Restores ME3's **horde wave mode**; own master server, P2P, in-memory patches. Not a campaign. Useful as a proven UE3 networking reference. |
| [Nexus 723](https://www.nexusmods.com/masseffect3/mods/723) | 2012 ME3 | 2019-04 | Campaign levels rebuilt as MP maps (`DLC_MOD_MPMapPack2`) + a `TESTPATCH` lobby tweak. Abandoned by the author in the mod's own words. Requires `DLC_TestPatch`, absent from LE. |

LE1 and LE2 have nothing. Local co-op groundwork for them is in [NOTES.md](./NOTES.md): the capability
surface is identical across all three games, a runtime offset discoverer has already produced
verified LE2 offsets, and LE2's full player object chain is now measured (see below).

Both reference copies are pulled into `local/MELE/refs/` (git-ignored), along with LEMP's sources. Details
and hashes in [NOTES.md](./NOTES.md).

## How the game works (for co-op)

UE3 throughout, so the netcode this needs is UE3's own: `Actor` replication, `Role`/`RemoteRole`,
`PlayerController`, RPCs through `ProcessEvent`. LEMP proves LE3's networking still functions under
the LE build; ME3SquadCoop builds on that rather than writing a transport.

Scripting lives in cooked `.pcc` packages (`BioGame\CookedPCConsole\SFXGame.pcc` is the big one,
plus per-DLC packages). Saves live under the **real** Documents folder, which is often not on `C:` —
here it is `<documents>`:

    <Documents>\BioWare\Mass Effect Legendary Edition\Save\{ME1,ME2,ME3}\<slot>\*.pcsav

Resolve that folder from the registry (`HKCU\...\Explorer\User Shell Folders\Personal`), not from
`%USERPROFILE%\Documents`. Searching the profile directory finds no saves at all and reads as "this
game has no saves", which is a wrong conclusion that cost real time. `tools/saverestore` in this repo
does the resolution, and takes snapshots so a crashed experiment costs seconds instead of a cutscene.

The exe is a complication for static work: `.text`/`.data` are not readable as code on disk (see
NOTES). `.pdata` and `.rdata` are, which is enough to verify RVA layout. **Every hook has to be
validated at runtime against the loaded image**, which is what ME3SquadCoop's `*_pin[]` signature
checks already do.

### The player object chain in all three games (measured live, read-only)

The two offsets that matter most are **identical across LE1, LE2 and LE3**. Everything below
them differs and must be measured per game:

| Field | LE1 | LE2 | LE3 |
|---|---|---|---|
| `Engine.GamePlayers` | `+0x498` | `+0x498` | `+0x498` |
| `LocalPlayer.Actor` -> controller | `+0x68` | `+0x68` | `+0x68` |
| `PlayerController.Pawn` | `+0x3DC` | `+0x294` | `+0x3BC` |
| `PlayerController.Player` | `+0x6A8` | `+0x698` | - |
| `PlayerController.PlayerInput` | `+0x708` | `+0x6F8` | - |
| `PlayerController.WorldInfo` | `+0x1AC` | `+0x1B0` | - |
| `Pawn.Controller` -> controller | `+0x400` | `+0x3D8` | `+0x374` |
| `Pawn.Physics` | `+0x27C` | `+0x284` | `+0x268` |
| `Engine.GameViewport` | `+0x7C8` | `+0x7A0` | `+0x7A0` |
| `ULevel.Actors` | not measured | `+0x60` | `+0x60` |

`tools/ue3_check_chain.py` re-derives the chain from an offsets file and asserts each link,
so this table is machine-checked rather than hand-copied. LE1 currently passes 8 of 8.

### `ProcessEvent` is at a different vtable slot in every game

The signature is the same everywhere - `(self, fn, parms)`, three arguments, ARG3 is the
parms block - but **the slot is not portable and must be read from the body**:

| Game | slot | handler RVA | how it was confirmed |
|---|---|---|---|
| LE1 | `+0x238` | `0x163A80` | prologue `mov r14, r8` (ARG3 = parms), then `call` the executor |
| LE2 | `+0x228` | `0x10CAA0` | same prologue; feeds the executor at `+0x228` |
| LE3 | `+0x230` | wrapper | what ME3SquadCoop calls, with a 4th NULL argument |

Slot numbers are almost useless as evidence: in each game several neighbouring slots hold
*identical* values across all classes, because inherited `UObject` virtuals are shared.
LE1's `+0x228` is not even `ProcessEvent` - it is a return-bool function. Disassemble the
target and check which register carries the parms.

### `CreatePlayer` is ABI-identical in all three games

`ControllerId Int@0x00`, `OutError Str@0x04`, `bSpawnActor Bool@0x14`,
`ReturnValue Object@0x18`, `NewPlayer Object@0x20`, `InsertIndex Int@0x28` - total
**`0x2C`**, read from each game's own live `UFunction`. So the parameter struct is the one
thing that does port, and a size assertion on it is a cheap guard.

### `CreatePlayer` is an LE3-only capability — proven by three crashes

This is the most consequential result of the whole effort, and it invalidates the obvious
next step, so it is stated plainly.

| Game | `CreatePlayer` outcome | Where it dies |
|---|---|---|
| LE3 | **works** | — a second `SFXPlayerController` is created and used |
| LE2 | crash | *inside* `CreatePlayer`, 7 engine frames deep, `ucrtbase!aligned_realloc` |
| LE1 | crash | in the engine **after** `CreatePlayer` returns, null `+0x90` deref at `+0x99611a` |

In both failures every input was verified correct first: parms struct `0x2C` with each
field at its measured offset, `ProcessEvent` at the measured slot with its body
disassembled, the `UFunction` resolved by name in that game, the level gate passed, and a
check-only build that cannot dispatch anything reporting success. The calls still kill the
game. Two independent games, two different fault sites, one common cause: **the
single-player campaign in LE1/LE2 does not have the player-login/level machinery that
`GameViewportClient.CreatePlayer` assumes.**

The LE1 stack is the instructive one — it contains **no frame of ours**. The thread that
faulted was started by the game's activation stub, so our call had already returned and the
engine faulted while processing what it had created. That is not a marshalling bug; the
mechanism itself is wrong for these builds.

**So: the fix is not a better offset, a better gate, or a different thread.** A fourth test
settled this: `CreatePlayer(..., bSpawnActor=0)`, which registers the `LocalPlayer` without
spawning a pawn, crashed in the same `ucrtbase!aligned_realloc` as LE2, synchronously inside
`CreatePlayer` (our frame on the stack at `le1kaidan_dispatch4+0x1581`, then `ProcessEvent` at
`+0x163B13`, then the executor). **`bSpawnActor` makes no difference, so the fault is in
creating and registering a `LocalPlayer` at all** - the login path, not the pawn.

What that rules out, including a correction to an earlier version of this section:

- **A second *view* without a second player does not work.** In UE3 each `LocalPlayer` owns its
  own `ViewState`/camera, and `SplitscreenInfo`/`PerPlayerSplitscreenData` is indexed *by local
  player*. With one `LocalPlayer` there is one view to split, so splitscreen cannot conjure a
  second view. That was my first recommendation and the evidence does not support it.
- **Possession needs a `PlayerController` to possess with** - and with one `LocalPlayer`, that
  controller is already driving Shepard. That is the real obstacle, and it is a design problem,
  not an offset problem.

A third route was built and also failed, in a way that is now precisely understood. Instead of
`CreatePlayer`, the controller was spawned directly with `Actor.Spawn(BioPlayerController)` and the
parms layout was measured (`0x50`, every field verified against the live UFunction). `Spawn` **succeeded** —
it returned a live controller — and then the engine faulted on a later frame at
`call qword ptr [rax+0x6D0]` with `rax = 0`, i.e. a method called through the new controller's null
**`PlayerInput`**. That is the documented UE3 limitation this repo's ME3 notes already describe: a
standalone controller has no `PlayerInput` and no `LocalPlayer`, because the campaign's login path is
what normally sets both up. It is why ME3SquadCoop patches `ULocalPlayer::SpawnPlayActor`.

Two consequences worth stating plainly:

- **A standalone controller cannot simply be spawned.** It must also be given input and a
  `LocalPlayer`, which is the login machinery that `CreatePlayer` was failing in.
- **LE1's `.text` is entropy 8.00 on disk**, so none of this can be reverse-engineered offline. Any
  patch has to be developed with the game running.

The route consistent with the evidence is the one already proven in LE3: **ME3SquadCoop's client
is not a UE3 splitscreen player at all.** It possesses a squadmate and gives it a shoulder
camera, a custom view attached to a pawn. So LE1/LE2 need either a `LocalPlayer` +
`BioPlayerController` pair constructed directly and appended to `GamePlayers` by hand (most
control, most risk), or a camera-plus-input implementation that never involves a second
`LocalPlayer` in the first place.


### Kaidan: identified from the game's own data

For the "client controls Kaidan" test the character was located without hardcoding any
pointer and without trusting a wiki. Name matching failed first (no object is named Kaidan;
`BioPawn.Tag` decodes to pointer fragments; LE1 mesh name indices are not low-12-bits). What
worked:

1. **`BioPawnBehavior.ActorGameName`** (FString) is the character name.
2. **`BioPawnBehavior.m_PawnAttributes` class == `BioAttributesPawnParty`** plus
   **`BioPawnBehavior.Squad` == `Player`** selects exactly the two squadmates — generic NPCs
   use plain `BioAttributesPawn`.
3. To tell the two squadmates apart, walk each pawn's owned-object subtree (Outer chain,
   depth ≤ 3) and count references to a character name: one pawn had 10 "jenkins"
   references, the other 2 "kaidan" references (its `BioAnimNodeBlendBy*` nodes pointing at
   `Engine\Localization\INT\kaidan_ss.INT`).

**Kaidan's pawn was at `Physics = 1` (Walking), ~100 units from Shepard, squad `Player`** —
exactly the kind of body possession needs, unlike ME3's scripted Anderson at physics 7.

`PHYS_Interpolating` (7) means the pawn is driven by a matinee, so it ignores movement
input **by design**. That is precisely why player 2 could look around but not walk when
possessing ME3's scripted Anderson, and the same value was observed on LE1's player pawn
during a cutscene. Any body handed to player 2 must be at **physics 1 (Walking)**.

In LE2 and LE1 the chain is **reciprocal**, which is how it was proven rather than assumed:
`LocalPlayer+0x68` reaches the controller, `Controller.Player` reaches that same `LocalPlayer`,
`Controller.Pawn` reaches the pawn, and `Pawn.Controller` reaches the same controller again.

In LE1 the player is driven by a `BioPlayerController` standing in a `BioPawn` with a
`BioPlayerSquad` - exactly the same shape as LE2, so the "client takes a squadmate" design
ports across both.

LE2 needs no cap raise: unlike LE3, `MaxPlayers` is already 16 and `MaxPlayersAllowed` 32.
LE1's equivalents are not yet measured.

### Traps that cost real time here, recorded so they don't recur

- **LE1 has two live `UEngine` subclasses.** The engine global dereferences to `SFXEngine`, but
  `GamePlayers`/`GameViewport`/`LocalPlayerClass` are declared on **`GameEngine`**. Scanning the
  wrong one finds no local players and looks like "not in game yet" - it was wrong twice over,
  because the player was in game with a live `BioPlayerController` and `BioPawn` attached. Check
  a subsystem object's `Outer` when a global seems absent.
- **Object class identity by name is unreliable in LE2.** The player's controller *is* a
  `BioPlayerController`, but its class object's name does not decode, so `obj_class(pc) ==
  "BioPlayerController"` is **false**. Roughly 31k of 132k objects have undecodable class names.
  Match by class pointer or `SuperStruct` lineage instead.
- **A property walker that ignores `SuperStruct` cannot prove a field is absent.** Reading only
  `UClass.Children` reported 6 fields for `BioSPGame` (it has 368) and no `Controller` on
  `BioPawn` (declared on `Pawn`). That produced a confident, wrong conclusion that LE2 has no
  player controller.

### Offsets are discovered externally now, with no DLL in the game

`tools/ue3_discover_external.py` finds GObjects, the name pool and the engine global purely
through `ReadProcessMemory`, by structure rather than by address:

1. Scan the image's writable sections for a `TArray<UObject*>` header `{data, count, max}` with a
   plausible count.
2. **Require every object's `Class` pointer to be a member of the candidate array** - a property
   only the real object array has. It rejected every false positive
   (`class_pointers_outside_array = 0`).
3. Use the self-classed meta-class's `Name` index to locate the pool, then find the engine global
   by requiring the slot to dereference to the **live** `SFXEngine`.

This matters because mapping a new game then needs no injection at all - and a wrong guess inside
a running game is what crashes it. `tools/ue3_verify_offsets.py` and
`tools/ue3_find_engine_global.py` check the result instead of trusting it.

## What we changed

Nothing shipped, but the experiment sequence now runs end to end: in a clean session, one injection each,
a second local player is created, given a walking stand-in spawned at the map's own `BioStartLocation`,
the stand-in is retired with hide+move, and the mission's real Anderson is handed over with a verified
back-reference. **No crash** — the first run since the level gate and the repeat-spawn guard were added.

Everything lives in `tools/splitplayer/` of the mod checkout (git-ignored here):

- **Recorded the exact build**, and proved the pinned hash mismatch is not a blocker: 50 of the repo's 108
  hardcoded RVAs land exactly on `.pdata` function starts, against ~0.5 by chance, while ME1/ME2 controls
  score 5.
- **Built an ASI loader** (`tools/binkproxy/`) — a stock install has none, so nothing could be injected.
  76 generated assembly stubs re-export the stock library; export tables match exactly and all 76 resolve.
  Verified loading in the real game.
- **Live injection** (`tools/inject_plugin.py`), so experiments need no restart. Verified.
- **Second local player** via `GameViewportClient.CreatePlayer` after raising the game-info cap to 2.
- **A walking body for player 2** via `SFXGame.SpawnDefaultPawnFor` with Shepard's class, placed with
  `SetCollision → SetPhysics(1) → Pawn.ClientSetLocation` in that order (`StartLocation` is ignored).
- **Two-pane split** by setting `DesiredSplitscreenType = 1`; the engine sets `Active` itself. It resets
  during possession, so it must be re-asserted after a swap.
- **Per-level character table** (`p2-levels.json`), read live, no rebuild; reports `unavailable` rather
  than substituting a character it cannot deliver.
- **A level gate** (`levelguard.h`) that refuses to mutate between levels — this removed the
  `CreatePlayer` crash class entirely.
- **Full actor inventory** tooling (`actors.c`) — found `ULevel.Actors` at `+0x60` by scanning.
- **Runtime offset discovery** (`tools/discover.c`) for porting to LE1/LE2. It finds name pools, GObjects
  and the engine instance by shape in the loaded image and **verifies** them by resolving objects that must
  exist, refusing to write a file if verification fails. LE2 is done and verified (`offsets_LE2.json`:
  name pools `0x16609f8`, GObjects `0x173cc48`, 152,271 objects, engine `SFXEngine`). LE2 was left
  completely stock — discovery ran by injection only.

## QA

```
python -m unittest discover -s tests -p "test_*.py"    # 257 tests
python tests/native_probe_harness.py                   # native DLL guards
```

Last results 2026-10-28, after building:

    synthetic suite : 270 tests, OK (1 skipped)   [257 original + 13 split-screen evaluator]
    native harness  : PASS memory reader and opt-in gate (no game launched)
                      PASS low-integrity child denied medium-file write

These are synthetic Python tests and a native harness. **They do not launch the game and are not
evidence of campaign co-op.** The new split-screen evaluator is tested against fabricated logs,
including negative cases, so it is known to reject bad input — but no real probe log exists yet.

The repo-wide tool suite also passes: `python -I tools/test_all.py` → `ALL OK` across 14 tools.

## How to play

Not playable. There is no release to install.

## Known issues

- **`GameViewportClient.CreatePlayer` faults when the engine is between levels.** Root cause found
  after seven crashes: it adds to `GamePlayers`, and the new player's login path then runs against a
  world that is not there. Every fault happened with the game at `EntryMenu` (~107,530 objects, no
  pawn); every success happened in `Biop_ProEar` (~169,000 objects, live pawn). The fix is a gate on
  **level identity** (`levelguard.h`: expected player count, a live pawn, a level name that is not a
  menu, and `SFXGameInfoSP` present in `ULevel.Actors`), re-checked immediately before the call.
  Guarding on a pawn alone does not work — between levels there is no pawn, so such a gate never fires.
- **Repeating a spawn is its own hazard.** A second stand-in spawn in a session that already had one
  faulted at the entry instruction of a tiny thunk (`0x1462c5`, `+0x0` of an 18-byte function) — the shape
  of a bad function pointer or null `this`, not a bad parameter, and a different site from every
  `CreatePlayer` crash. `spawn_stand_in` now refuses when slot 1 already owns a pawn or a stand-in is
  still live.
- **The swap hands player 2 a non-walking body.** The mission's real Anderson is physics 7, so after the
  swap player 2 is present for the story but cannot move. That is a design decision, not a bug: either the
  swap exists for story presence, or the stand-in should persist and the story gets its own Anderson.
- **The split layout resets during possession.** `DesiredSplitscreenType` was `0` after the swap and had to
  be re-asserted. Set it after any possession change, not once at setup.
- **One hung process after an otherwise successful run.** After a completed swap with the split
  re-asserted, the process stopped responding (~2 GB resident, no `Application Error` logged). That is a
  hang rather than a fault, and it is unexplained.
- **Input to player 2 works.** Confirmed by the owner in a live game: keyboard/gamepad reaches player 2's
  controller and the view is player 2's own (looking around works). The blocker was never input — it was
  that the possessed body was a **scripted NPC at physics 7**, which ignores movement by design.
- **Player 2 needs a walking body.** `SFXPawn_PlayerVanguard` at physics 1 satisfies this and is proven to
  spawn. The stand-in-then-swap design uses it, retiring the stand-in when Shepard reaches the mission's
  real Anderson.
- **`GameViewportClient.CreatePlayer` faults between levels** (root cause found; `levelguard.h` gate
  implemented and verified to refuse at `EntryMenu`).
- **Stand-in bodies are player pawns**, because `SpawnDefaultPawnFor` is a player-pawn factory and passing
  it a character class faults. A proper archetype spawn is still needed if the stand-in must look like a
  specific squadmate.
- **Per-level characters are limited to what the level has loaded.** `Biop_ProEar` has only Shepard, two
  Andersons (physics 7 scripted; physics 1 far away, no controller) and husks. No squadmate pawns.
  `LoadPackage` on a mission package registers no pawn class, and `DynamicLoadObject` with a guessed path
  can fault, so it must not be used to search.
- ME3SquadCoop's own known-open items (guest firing/ammo, equipment readiness) are unchanged.

## The two-harness plan

Split screen is the **first** harness, not a nice-to-have, because it separates the two failures that
are easy to confuse:

| Harness | Question it answers | What it cannot tell you |
|---|---|---|
| Split screen (one process, two `LocalPlayer`s) | What is P2 allowed to do *at this moment* — possession, powers, camera, HUD, dialogue handoff | Anything about replication |
| Networked client (two machines) | Does that agreement survive latency, loss and two separate worlds | Whether the rules are right in the first place |

So a split-screen bug means the control semantics are wrong and netcode work would be wasted; a
split-screen success with a net failure localises the problem to replication alone.

**But for LE1 and LE2, two panes in one process is not reachable**, and the reason is not an
offset: `GameViewportClient.CreatePlayer` crashes in both builds three different ways, and there
is no other scripted entry (`AddLocalPlayer`, `JoinSplitscreen`, `CreateLocalPlayer` do not
exist). See [SPLITSCREEN-PLAN.md](./SPLITSCREEN-PLAN.md) for the evidence, the two corrections it
makes to earlier documents, and the route that does work. The harness role split screen was meant
to fill is already filled by two isolated copies per machine.

The content of that first harness is now written down as an ordered suite rather than an
improvised sequence: [COMMAND-SUITE.md](./COMMAND-SUITE.md) catalogues **156 command mechanisms**
across 20 categories, with presence measured per game from the decompressed script data, and
arranges them into **23 phases** where each one only runs after the thing it depends on has been
proven in the same process. Four phases are opt-in because they touch state shared with the real
game (saves and the profile live in one Documents folder for every copy) or drop both clients.

Good news for this: the engine *is* built for it, and the first step is done. Read live from the
running game (2026-10-28), `Engine+0x498` is a `GamePlayers` array with **capacity 4**, the viewport is
`SFXGameViewportClient`, and the local player class is `SFXLocalPlayer`. Calling
`GameViewportClient.CreatePlayer` then **created a real second `SFXLocalPlayer`** — confirmed in slot 1
of the live array, with no crash.

It **is** a playable second player, with one gap: input reaches player 2 and its view works (owner
confirmed), and a `SFXPawn_PlayerVanguard` at physics 1 gives it a walking body. What has not run yet is
the full design — a stand-in swapped for the mission's real squadmate — across a whole level.
Details in [NOTES.md](./NOTES.md).

## Next steps

1. **Decide what the swap is for.** After it, player 2 holds the mission's real Anderson at physics 7, so
   it is present for the story but cannot walk. Either that is the intent (swap for presence), or the
   stand-in should persist and the story keeps its own Anderson (swap for play). This is a design choice,
   not a bug.
2. **Diagnose the hang.** A completed run left the process unresponsive (~2 GB, no `Application Error`).
   A thread dump is the next step, not another spawn.
3. Wire `levelguard.h` into the player-creation path as well as the stand-in spawn.
4. Find a proper archetype spawn so a stand-in can look like the level's assigned squadmate instead of a
   player pawn.
5. **Port to LE2 carefully - offsets are hypotheses, not facts.** LE2's player chain is now measured
   live and read-only, and it is closer to LE3 than an earlier note claimed (`LocalPlayer.Actor` **is**
   at `+0x68`). What differs is the dispatch: LE2's `ProcessEvent` is `vtable+0x228` in its plain
   three-argument form, while `+0x230` - the slot ME3's plugin uses - is a **wrapper with a different
   signature** that discards the parms argument. Calling it the ME3 way is what crashed LE2 the first
   time. A P2-spawn attempt then crashed a second time *inside* `CreatePlayer`: the dump shows seven
   engine frames past `ProcessEvent` before faulting in `ucrtbase!aligned_realloc`, so the call was
   reached correctly and failed in the engine's own player-login/allocation path. Threading was my first
   hypothesis and the dump **disproved** it. See [NOTES.md](./NOTES.md) for the stack and analysis.
6. ~~Run `discover.dll` inside LE1~~ **Done, and better: no injection was needed.** All three games are
   now mapped from outside the process (see the offset table above). LE1's `offsets_LE1.json` is written
   and machine-verified. What remains is to have the plugins read their offsets from
   `offsets_<game>.json` instead of hardcoding ME3's.
7. **Do not use `CreatePlayer`, and do not just spawn a controller instead.** Three routes have failed
   across two games with every input verified: `CreatePlayer` with a pawn, `CreatePlayer` without a
   pawn, and `Actor.Spawn(BioPlayerController)`. The last one *succeeded* at spawning and then faulted
   on the next frame through the controller's null `PlayerInput`. So the missing piece is not a call,
   it is the whole login-and-input setup. What to do instead:
   - **A second view is not a workaround.** One `LocalPlayer` owns one `ViewState`, and
     `SplitscreenInfo`/`PerPlayerSplitscreenData` is indexed by local player, so there is nothing to
     split into two views. Splitscreen needs the same second player this path cannot create.
   - **The real obstacle is that possession needs a `PlayerController`,** and the only one is already
     driving Shepard. Solve that: either build a `LocalPlayer` + `BioPlayerController` pair directly
     and append them to `GamePlayers` (bypassing the login path), or give up the second
     `LocalPlayer` entirely and implement input plus a camera the way ME3SquadCoop does — a shoulder
     camera attached to a possessed pawn, which is the design that already works in LE3.
8. **The save/restore blocker is solved — use it.** Saves were never missing, they were under the
   *real* Documents folder (`<documents>` on this machine, not `C:`). `tools/saverestore`
   resolves that from the registry, snapshots with a SHA-256 manifest, and restores by quarantining the
   current state rather than overwriting it. Verified: corrupt one file, delete another, restore — both
   came back byte-correct and the corrupted copy was preserved. An ME1 snapshot named
   `me1-pre-coop-test` exists. **Close the game before restoring**, and take a fresh snapshot before
   each experiment. With this, a crashed attempt costs seconds, which makes the remaining exploratory
   work (hand-built `LocalPlayer` pair, or input plus a shoulder camera) affordable to attempt.
