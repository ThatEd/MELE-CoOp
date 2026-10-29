# Delaying map events until player 2 passes

The problem, stated the way it actually happens in play: Shepard walks into a trigger, something
fires — a door opens, a cutscene starts, enemies spawn, the story advances. In co-op, **player 2
is elsewhere**, so firing the event the moment Shepard touches it means P2 misses it, gets left
behind a door, or is teleported into a scene they were not in.

So the mod needs a rule the engine does not have: **hold the event until both players are present
(or until P2 arrives), then release it.**

This page is the engine-side design: what actually fires when a player walks somewhere, what
state says "a player is inside", and where a hold can safely be inserted.

## What fires when a player walks somewhere

Three separate layers, which is why this is not a single hook.

### 1. The volume actor — the physical trigger

| Name | ME1 | ME2 | ME3 | What it is |
|---|---|---|---|---|
| `BioTriggerVolume` | yes | yes | yes | BioWare's trigger volume; the usual level trigger |
| `TriggerVolume` | yes | yes | yes | generic UE3 volume |
| `Trigger` | yes | yes | yes | generic UE3 trigger actor |
| `BioTrigger` | yes | yes | yes | BioWare base |
| `BioTriggerStream` | yes | yes | yes | volume that drives level streaming |
| `BioSaveTriggerVolume` | yes | - | - | ME1: triggers a save |

Detection on these is the classic UE3 trio: **`Touch`, `UnTouch`, `Touched`** — all three present
in all three games — plus per-game spellings (`Touching` ME1, `TouchingActor` ME2,
`TouchingCylinder`/`TouchedActor` ME3).

**`EWhoTriggers` exists in all three games, and its members are now decoded.**

    ME1, ME2:  0 WT_PlayerOnly   1 WT_PlayerAndSquad   2 WT_Everyone   3 WT_TagList   4 WT_MAX
    ME3:       0 WT_PlayerOnly   1 WT_PlayerOnlyLocal  2 WT_PlayerAndSquad
               3 WT_Everyone     4 WT_TagList          5 WT_MAX

Read offline with `python -I tools/melepkg/mele_tables.py enum <Engine.pcc> EWhoTriggers`.

**The answer to the question this document was asking: the engine already has both concepts.** There
is no need to invent a "both players" trigger - `WT_PlayerAndSquad` already means more than one
player-side actor, and `WT_PlayerOnly` means exactly the local player.

**And ME3 is not ME2 plus one.** ME3 inserts `WT_PlayerOnlyLocal` at index 1, which shifts every
later member:

| intent | ME1 / ME2 | ME3 |
|---|---|---|
| local player only | 0 | 0 |
| local player only (new, explicit) | — | 1 |
| player and squad | 1 | **2** |
| everyone | 2 | **3** |
| tag list | 3 | **4** |

So writing ME2's `WT_Everyone` (2) into a ME3 trigger selects `WT_PlayerAndSquad`, and ME2's
`WT_PlayerAndSquad` (1) selects `WT_PlayerOnlyLocal`. A delay that "looks right" on LE2 and is
copied to LE3 changes who can set it off. This is rule 31 with a byte-level consequence.

### 2. The Kismet action — what the event does

| Name | ME1 | ME2 | ME3 | Meaning |
|---|---|---|---|---|
| `BioSeqEvt_OnPlayerActivate` | yes | yes | yes | **the player activated/interacted with something** |
| `SeqAct_ActivateRemoteEvent` | yes | yes | yes | remotes an event to another sequence |
| `BioSeqEvt_SeePlayer` / `NoLongerSeePlayer` | ME1 | ME2 (`NoLonger` only) | - | perception-based, not proximity |
| `BioSeqEvt_ProximityAlarm` | ME1 only | - | - | a real proximity event |
| `BioSeqAct_CheckIfInVolume` | ME1 only | - | - | condition: is something inside a volume |
| `BioSeqAct_DoActionInVolume` / `DoActionInRadius` | ME1 + ME2 | | - | applies an action to what is inside |
| `BioSeqAct_GetPawnsInRadius` | ME1 only | - | - | enumerate pawns in a radius |
| `BioSeqAct_NonCombatArea` | yes | yes | yes | declared non-combat region |

### 3. Level readiness — whether the event should be considered at all

`BioSeqAct_SetStreamingState`, `BioSeqEvt_ForegroundStreamingDone`,
`BioSeqEvt_BackgroundStreamingDone` (all three), and ME3's `SFXSeqEvt_LevelIsLive`. A trigger that
fires while a level is still streaming is a different problem from one firing too early for P2.

## The state that says "a player is inside"

Two candidate readings, and the mod needs both because either alone lies:

1. **Per-volume `Touching` / `TouchingActor`** — the volume knows what is touching it. Cheap, but
   per-game spelling and it is an engine-side list a mod would have to read.
2. **Per-pawn position vs volume bounds** — geometry, no engine hook. `Pawn.Location` is a measured
   field in all three games (LE1 `+0x108`, LE2 `+0x118`... confirm per build), and the volume's
   extent is readable. This is the robust option and needs no engine cooperation at all.

**Recommendation: use geometry for the hold decision.** It works identically in all three games,
it cannot be broken by a renamed property, and it needs no hook into `Touch`. The engine's own
volume state is then only a cross-check.

## Where to insert the hold

Four candidate points, ranked by how much they break if wrong.

### A. Gate the *action*, not the trigger (safest)

Let the trigger fire as normal, and hold the **consequence** — the door, the spawn, the sequence
activation — until P2 arrives. This is a whitelist of specific actions to defer.

- **Why safest:** the engine's own trigger bookkeeping stays intact. Nothing about `Touch` is
  subverted, so there is no risk of a volume that never re-fires.
- **Cost:** needs a list of which actions matter. `SeqAct_ActivateRemoteEvent` is the common
  indirection, so deferring *that* covers most cases with one hook.

### B. Re-arm the trigger (moderate)

Let it fire, and if P2 has not arrived, reset the volume's "already triggered" state so it fires
again when P2 walks in. `Touch`/`UnTouch` exist everywhere; the reset is per-class.

- **Risk:** a volume that fires once by design (a story beat) firing twice is worse than it firing
  early. This needs the action to be idempotent, or the re-arm has to be undone after one use.

### C. Hold the volume from reporting a touch (risky)

Intercept `Touch` and report false until both players are inside.

- **Risk:** `Touch` also drives collision/overlap bookkeeping in some classes. Reporting a false
  touch state can leave an actor permanently "inside" a volume, which is the kind of bug that only
  shows up twenty minutes later.

### D. Delay the player's own movement (do not do this)

Blocking P2 at the door until Shepard's event completes inverts the problem: it makes P2 a
follower, and it fails completely when P2 is *ahead* of Shepard.

## The rule the mod actually needs

Not "wait for both" — that deadlocks when an event is meant to be passed by only one player. What
is needed is a **co-presence policy per event**:

| Policy | When | Behaviour |
|---|---|---|
| `both_required` | doors that lock behind you, teleports, cutscenes | hold until both inside, then release once |
| `any_player` | ambient barks, one-off triggers, scenery | fire on first player, no hold |
| `leader_only` | story progression that must follow Shepard | fire for Shepard only; never for P2 |
| `p2_must_pass` | a trigger whose whole purpose is that both walk through | hold, and if P2 never arrives, release on a timeout rather than deadlocking |

The policy has to be **data, not code**: a table of trigger identity → policy, with a sane default
(`any_player`) so an unclassified trigger behaves as it does today rather than blocking.

## Measured: the pieces that exist for this

Established offline from the packages (not inferred):

**The trigger actors all exist in all three games:** `BioTriggerVolume`, `TriggerVolume`,
`Trigger`, `BioTrigger`, `BioTriggerStream`, `BioTriggerActorFactory`, and the generic UE3
detection trio `Touch` / `UnTouch` / `Touched`.

**The two most relevant identifiers found:**

| Identifier | Games | Why it matters |
|---|---|---|
| `EWhoTriggers` | all three | the engine's own enum for **who is allowed to trigger** something. **Decoded**: `WT_PlayerOnly`, `WT_PlayerOnlyLocal` (ME3 only), `WT_PlayerAndSquad`, `WT_Everyone`, `WT_TagList`. The engine already has the policy this feature needs, but the numbering differs on ME3 |
| `m_bInstigatorIsPlayer` | ME1 (verified) | a boolean on the instigator: **this tells you whether the thing that touched the trigger was a player**. This is the natural discriminator for "was it my player or not" |

`m_bInstigatorIsPlayer` is the single most useful name for this feature. It means the engine already
distinguishes player-instigated touches, so a hold rule can be built on an existing distinction
rather than a geometric guess.

**Also present in all three:** `BioSeqEvt_OnPlayerActivate` (the player interacted),
`SeqAct_ActivateRemoteEvent` (the indirection most actions go through), `SeqAct_Interp` (matinee),
`BioSeqAct_NonCombatArea`.

**ME1-only:** `BioSeqEvt_ProximityAlarm`, `BioSeqAct_CheckIfInVolume`,
`BioSeqAct_DoActionInVolume`, `BioSeqAct_DoActionInRadius`, `BioSeqAct_GetPawnsInRadius`,
`BioSeqEvt_SeePlayer` / `NoLongerSeePlayer`, `BioSaveTriggerVolume`.

**ME2/ME3-only:** the `Playpen` volume family (`BioSeqAct_AddPlaypenVolume`,
`OnEnteredPlaypen`, `BioSeqCond_HasPlaypenVolume`) — a containment concept ME1 lacks.

### The name table is fully parseable, which changes what is knowable

The decompressed stream **begins with the name table**: a sequence of `int32 length, chars, NUL`
entries. ME1's `SFXGame.pcc` yields **23,258 names in order**, ending at `0x7FC3B`. So:

* exact name lists are available offline, not just substring hits;
* an enum's name is present, but **its members are not adjacent** — the table is sorted
  alphabetically (`m_bInstigatorIsPlayer` sits between `m_bInstancePoolEnabled` and
  `m_bInTakeoff`), so adjacency proves nothing about which properties or enum values belong
  together. Getting the members needs the export/import tables, which are the next parsing step.

### A false positive worth recording

I briefly concluded that the package header's readable tail appeared inside the decompressed
stream. It did not: the header is 437 bytes (`0x1B5`), so `header[0x1D5:]` is an **empty slice**,
and `bytes.find(b"")` returns `0`. The "match at offset 0" was the empty-string search succeeding.
Recorded because it is exactly the shape of error this project keeps having to retract - a lookup
that returns a plausible position for a query that cannot fail.

## The hard part, stated plainly

**Identifying a trigger stably.** A trigger has a name (`Tag`) and a position, and both are easy to
read, but a policy table keyed on names breaks when a level is patched. Keying on level + tag +
approximate position is more robust but noisier. This is a genuine design decision, not a detail,
and it is the thing to prototype first.

## What to measure next, in order

1. ~~**`EWhoTriggers` values.**~~ **Done** - see above. `WT_PlayerOnly`, `WT_PlayerOnlyLocal`
   (ME3 only), `WT_PlayerAndSquad`, `WT_Everyone`, `WT_TagList`, `WT_MAX`, with a per-build
   numbering shift. The engine already has every policy this feature needs.
2. **Which class a level actually instantiates for a given trigger.** `BioTriggerVolume` vs
   `TriggerVolume` vs `BioTrigger` are all present; which one a level uses decides which `Touch`
   implementation matters. Needs the export table, or a live read of level actors.
3. **Whether `BioSeqAct_NonCombatArea` and volume triggers interact** — one is a region, the other
   is a trigger, and both could be present on the same walk.
4. **One concrete trigger end to end**, in one level, in one game: find it, hold it, prove P2's
   arrival releases it. That is the whole feature in miniature and it is worth more than a wider
   survey.

## Why this is the right next feature

It is the first requirement that is genuinely *co-op specific* rather than "make single-player code
work twice". The engine has no notion of two players walking through a level together, so this
cannot be solved by porting anything — it has to be designed. That makes it the honest test of
whether the mod is a mod or a pile of hooks.
