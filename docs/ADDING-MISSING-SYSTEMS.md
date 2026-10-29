# Adding what the engine is missing

The owner's direction: LE1/LE2 lack subsystems LE3 has, and we are allowed to **add** them —
"this can be a whole mod". Splitscreen is wanted as the cheap harness that gets the logic
working before network play.

This document answers one question: **what exactly is missing in each game, and in what
order should it be built so that each step is testable on its own?**

It deliberately does not re-list the crash history; that is in
[CO-OP-FAIL-POINTS.md](./CO-OP-FAIL-POINTS.md). Here we only care about capability gaps and
the order to close them.

## Why splitscreen first is the right call

Not because it is easier, but because of what it removes:

| Problem | Splitscreen | Network |
|---|---|---|
| Two windows fighting for keyboard | one window, two inputs | two machines, two keyboards |
| Desync | impossible — one simulation | everything desyncs |
| Level identity between instances | one instance, one level | two instances must agree |
| Focus-pause on losing foreground | never loses focus | always loses focus (the documented 10-second pause) |
| Time to first "does P2 move" answer | seconds | needs transport, handshake, join |
| Restore cost after a crash | one reload | one reload per instance |

So splitscreen is a **logic harness**: it proves input routing, possession, camera and pawn
ownership *without* any of the hard parts. Everything except window-focus and level identity
transfers to network later; those two are added last.

The one thing splitscreen cannot test is latency and authority. That is fine — those are a
separate problem, and it is better to meet them with a working simulation than to debug both
at once.

## The capability gap, subsystem by subsystem

What LE3 has, what LE1/LE2 have, and whether the missing part can be built.

| Capability | LE3 | LE1 | LE2 | Can we add it? |
|---|---|---|---|---|
| A second `LocalPlayer` | via `CreatePlayer` (works) and via network login | absent; `CreatePlayer` crashes 3 ways | absent; `CreatePlayer` crashes | **Unknown, and the crux.** Two candidate routes below |
| A `PlayerController` to possess with | comes from login | only Shepard's exists | only Shepard's | **Yes, if a controller can exist without a `LocalPlayer`** - blocked by null `PlayerInput` |
| `PlayerInput` for a controller | created by login | absent on a spawned controller | same | **Yes** - spawn it and install it (phase 3, built) |
| Input routing to a second player | network RPCs | none | none | **Yes** - read the device per frame and apply to P2's controller |
| A second view | custom shoulder camera on a possessed pawn | splitscreen surface exists but needs a 2nd local player | same | **Yes, via the shoulder-camera route** - not via splitscreen |
| Possession | works | works (chain verified, reciprocal) | works (chain verified) | **Already there** |
| Retention of possession across a scene | documented problems (position jump, collision loss) | untested | untested | **Yes, with the documented repair** (`SetCollisionType(6)` + `bCollideWorld`) |
| Pause on focus loss | present, documented | likely present | likely present | **Yes** - patch the focus getter for co-op worlds only |
| Network transport | exists (UE3 multiplayer, LEMP proves it) | **none** | **none** | **Yes, but it is a big build** - there is no net driver to reuse |
| Replication / authority | exists | **none** | **none** | **Yes, but biggest piece** |

The two rows in bold are the whole project. Everything else is either already working or has a
known repair.

## The crux, stated precisely

To possess a squadmate you need a `PlayerController`. In LE1/LE2 there is exactly one, and it
is driving Shepard. So the question is not "how do I get a second player" but **"how do I get a
second `PlayerController` that has input and a view?"**

Three candidate routes, cheapest first:

### Route 1 — spawn a controller and give it input (in progress)

`Actor.Spawn(BioPlayerController)` works. It faults later because `PlayerInput` is null
(**proven**, crash 13). Phase 3 (`p2v2_input.dll`) creates a `PlayerInput` with `Actor.Spawn`,
stores it at the controller's measured `PlayerInput` offset, and calls `InitInputSystem`.

- **Cost:** one injection, cheap to recover from now that saves can be restored.
- **Answers:** does a controller survive with input but no `LocalPlayer`?
- **If yes:** possession is unblocked and splitscreen becomes a camera + input problem.
- **If no:** the next fault names the next missing companion object, which is still progress.

### Route 2 — a second `LocalPlayer`, built by hand

Append a `LocalPlayer` + `BioPlayerController` pair to `Engine.GamePlayers` ourselves, since
`CreatePlayer` cannot. UE3 needs: the `LocalPlayer` object, its `ViewportClient` set, a
controller with `Player` pointing back at it, and `Engine.GameViewportClient` aware of it.

- **Cost:** high. It writes an engine-managed array, which the project's own rule 3 warns
  against ("never splice a UE3 object array"), and it must be done consistently or the engine
  walks a broken array.
- **Why it may be necessary:** it is what the engine's own login path does, and it is the only
  way to get a genuine second *player* rather than a controller with faked input.
- **Order:** only after Route 1 tells us whether a controller can live without a `LocalPlayer`.

### Route 3 — do not create a player at all

Give P2 a pawn (a possessed squadmate), a camera, and a direct input path, and never involve
`LocalPlayer`. This is closest to what ME3SquadCoop actually ships: its client is not a
splitscreen player, it is a possessed pawn with a shoulder camera.

- **Cost:** moderate, and no engine arrays are touched.
- **Weakness:** the engine's own code assumes "the player" is the `LocalPlayer`'s controller.
  Anything keyed on that (HUD, powers, conversation camera) will follow Shepard, not P2.
- **Strength:** it cannot hit the crash classes seen so far, because none of them are reached.

**Recommendation:** run Route 1's phase-3 build first (one injection, already compiled), and
use its outcome to choose between Routes 2 and 3. Do not start Route 2 speculatively: it is
the riskiest and it is only justified if Route 1 shows a controller absolutely requires a
`LocalPlayer`.

## Build order

Each step is independently testable, and each has a read-only way to check whether it worked
before anything is mutated.

1. **Save restore points** — done (`tools/saverestore`). Prerequisite for everything.
2. **A controller that survives** — phase 3. Read-only check: `PlayerInput` non-null and the
   process still alive after a few frames.
3. **Possess with it** — phase 2 logic against the new controller. Read-only check: both ends
   of the chain reciprocal (`Controller.Pawn == kaidan` and `Pawn.Controller == controller`).
4. **A second view** — a shoulder camera attached to the possessed pawn. Check: the camera's
   target is P2's pawn, and it is not overridden outside conversations.
5. **Input to P2** — read device state and apply movement/look to P2's controller. Check: P2's
   pawn position changes while Shepard's does not.
6. **Retention across a scene** — capture the healthy collision shape first, restore
   `SetCollisionType(6)` plus `bCollideWorld` after release; verify position continuity.
7. **Focus-pause** — patch the focus getter for co-op worlds only.
8. **Then, and only then, network** — the same steps again with a transport, authority and
   level-identity agreement. Expect the collision/position repairs from step 6 to be needed
   again, since they interact with who owns the pawn.

## What "missing" actually means technically

Worth being concrete, because "add networking" hides a lot:

- **Transport:** LE1/LE2 have no net driver in the campaign. Either write one, or piggyback a
  socket and drive UE3's `ActorChannel`/`PackageMap` yourself. The latter is where LE3's
  documented fragility lives (`InvManager` going null during `ReceivedBunch`).
- **Authority:** single-player code has no notion of a server. Every damage/health/inventory
  computation runs locally and will disagree. Adding authority means deciding, per system, who
  is right - which is a design job before it is a code job.
- **Level identity:** both sides must be in the same level before any shared state means
  anything. Nothing provides this for free.
- **Join-in-progress:** the engine's own "receive world state" path is the one that carries the
  `PackageMap` problems. A "both start together from the same save" model sidesteps it and is
  the sane first target.

## What could not be reverse-engineered offline — and now can

*(This section said "the container is not parsed; every codec failed". That was the state before
2026-10-29. The codec is **Oodle 8 from the game's own `oo2core_8_win64.dll`**, the container is
solved, and the import and export tables parse. See
[CONTAINER-FORMAT.md](./CONTAINER-FORMAT.md).)*

What offline analysis gives now, with no process to attach to:

| Question | Tool |
|---|---|
| Does this build contain X? | identifier table (`melepkg names --grep X`) |
| What kind of object is X, and which package is it from? | import table (`mele_tables imports`) |
| Which class declares X, and what is its superclass and owner? | export table (`mele_tables find` / `objects`) |
| What is the full inheritance chain of a class? | export table + `super_index` |

What still needs a running game: which *instance* of a class is live in the current level, and
whether a call actually did anything. Those are `probe`'s job.

Consequence for planning: the container is no longer a parallel task, it is done. The remaining
risk is dispatch, not reading.

## Honest risk list

- Route 1 may fail with another missing companion object, possibly several in a row.
- Route 2 touches an engine-managed array and could corrupt the game in ways a restore point
  does not fix (saves would still be fine; the process would not).
- Route 3 produces a second *body* that the engine does not consider a player, so HUD, powers
  and conversations may follow Shepard regardless.
- The network stage is larger than everything above it combined, and LE1/LE2 give it no
  starting point.
