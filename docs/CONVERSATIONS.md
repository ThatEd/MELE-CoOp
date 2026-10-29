# Conversation handling for a second player (spectator mode)

What has to be understood and built so that conversations behave correctly in co-op: Shepard
keeps the conversation, and player 2 becomes a **spectator** — camera and input suspended,
watching, and then handed back cleanly when it ends.

Scope note from the owner: reverse engineer the function first, then attempt the scene. This
page is the function-level map, with an explicit split between what is established, what is
derived from UE3 structure, and what is unknown. The unknown columns are the RE work.

## Why this is a hard requirement, not a nicety

A co-op mod that ignores conversations breaks in three ways, all observed or documented:

1. **The engine pauses the world for the conversation owner.** LE3's world tick can enter
   `PlayersOnly` for a modal conversation. A second player's pawn then stops being simulated
   while their camera keeps rendering — they are frozen, not watching.
2. **Release and reacquisition is not clean.** After control is released for a scene, a
   possessed pawn was observed to **lose world and actor collision permanently** and then
   fall, and to **jump ~54,000 units toward the world origin**. `[documented]` So the end of a
   conversation is the dangerous moment, not the start.
3. **Input must not leak into the conversation.** The conversation camera and choices are
   driven through the same controller whose pawn the guest is holding. Input routed to player 2
   during a scene will fight the dialogue wheel.

## The engine primitives that matter

These are the state fields and calls any implementation has to read or set. Offsets are LE1
where measured, because that is the game most likely to be tested first; LE2/LE3 equivalents
must be re-measured, and the project rule applies — porting an offset is a hypothesis.

| Primitive | What it tells us | Status |
|---|---|---|
| `WorldInfo.bPlayersOnly` | the world is simulating only "players", i.e. a modal scene owns the frame | `[documented]` in LE3; field exists in all three |
| `PlayerController` cinematic bit | this controller is in a cinematic | `[documented]` in LE3 |
| active / activating conversation count | a conversation is actually running | `[documented]` as a paired set; three separate reads, of which an unreadable one is rejected rather than treated as zero |
| `m_bPausedByFocusLoss` | the focus path paused the game | `[documented]` in LE3 |
| `PauseGame(true/false)` | what the engine calls | `[documented]` |
| conversation controller + playing line `Matinee` | the specific scene objects | `[documented]` observed in mode 0 |
| `BioPawn.Physics` | 7 = `Interpolating` = script owns this pawn | `[proven]` all three (`+0x27C` / `+0x284` / `+0x268`) |
| `Pawn.Controller` ↔ `Controller.Pawn` | reciprocal ownership, the check that a release worked | `[proven]` |

The important structural insight: **`bPlayersOnly` is world-wide, and a conversation count is
per-controller.** A spectator implementation needs both readings, because either alone gives a
false answer — a world can be `PlayersOnly` for a reason that is not a conversation, and a
controller can report a conversation while the world carries on.

## The three states a second player can be in

This framing is the design; everything else follows from it.

| State | Trigger | What player 2 should get |
|---|---|---|
| **Playing** | normal gameplay | full pawn control: movement, camera, abilities |
| **Spectator** | a conversation is active for player 1 | camera only, positioned to watch; **no input to the pawn**; input captured and discarded (or used for a "skip/ready" signal later) |
| **Suspended** | scene with no useful camera (loading, mission transition, full-screen matinee) | fixed or follow camera, input ignored, pawn hidden or frozen in place |

**Fail point V1 — the transition into Spectator is not atomic.** If input is released before
the camera is attached, player 2's pawn keeps the last input velocity and drifts. If the camera
is attached first, the engine's own `PlayersOnly` freeze lands mid-frame and the camera target
is already stale. `[derived]`

**Fail point V2 — the transition out of Spectator is where possession breaks.** `[documented]`
The observed loss of collision and the position jump both happened on *return*. Any
implementation must re-verify the reciprocal chain and restore collision flags explicitly.

**Fail point V3 — a spectator camera must not fight the conversation camera.** `[derived]` The
scene drives the view for the conversation. Player 2's camera must be independent of it, or it
will be overridden every shot.

## Two candidate mechanisms

### Mechanism 1 — freeze and watch (least invasive)

On conversation start: `UnPossess` the squadmate from player 2, set their pawn's physics to a
non-simulating state, attach a camera to the conversation's focus, and stop routing input. On
end: re-`Possess`, restore collision, restore physics to 1, verify.

- **Why it fits:** it uses only calls this project has already executed successfully
  (`Possess`/`UnPossess` parms measured in all three games; the collision repair is documented).
- **Cost:** the release/reacquire path is precisely the one that broke collision and position.
  So this mechanism needs the repair from day one, not later.
- **Unknown:** whether `UnPossess` alone stops the pawn being pulled into the scene.

### Mechanism 2 — never release, just mute (less invasive on state)

Keep possession, keep the camera, but zero player 2's input contribution for the duration, and
force the pawn's physics to 7 (`Interpolating`) so the script can drive it if it wants.

- **Why it fits:** it avoids the unpossess/repossess cycle that demonstrably breaks things.
- **Cost:** physics 7 is the state that made player 2 unable to move in ME3 — acceptable for a
  spectator, but it means the *engine* may now drive their pawn, and on exit the pawn can be
  somewhere unexpected.
- **Unknown:** whether the scene will try to move a pawn it does not own, and what that does to
  the camera.

Both are testable read-only up to the moment of mutation: the conversation state fields are
readable, so the detection half can be built and validated before either mechanism is tried.

## What to read, in order, to detect a conversation

The read-only detector is the first deliverable because it costs nothing and blocks nothing:

1. `PlayerController` cinematic bit — cheapest, per player.
2. active + activating conversation count — must be read as a set; a single unreadable field
   invalidates the reading rather than counting as zero.
3. `WorldInfo.bPlayersOnly` — world-wide, and the thing that actually freezes a pawn.
4. the pawn's `Physics` — 7 confirms the script is driving it.

A detector that requires all four to agree is stricter than the engine's own notion, which is
correct: this project's documented rule is that missing or unreadable information must never be
treated as "no conversation".

## The test procedure this enables

Conversations cannot be tested without a test environment, so the order is:

1. Provision isolated copies (done: [TEST-ENVIRONMENT.md](./TEST-ENVIRONMENT.md)).
2. Reach a known conversation in a known save, on both copies.
3. Run the **read-only detector** and confirm it reports Spectator for player 2 and Playing for
   player 1, with all four fields agreeing.
4. Only then try a mechanism, and only one at a time.
5. After each attempt, assert the reciprocal chain and `Physics` before doing anything else.

Step 3 is the gate. If the detector cannot tell "conversation" from "not conversation" reliably,
no mechanism can be evaluated, because there is no way to know whether a change in behaviour
was caused by the mechanism or by the scene.

## Unknowns that block a real implementation

Stated plainly so the RE work has targets:

| Unknown | Why it blocks |
|---|---|
| Which field actually freezes a *non-possessed* pawn during `PlayersOnly` | decides whether releasing or muting is correct |
| Whether `UnPossess` during a scene is safe in LE1/LE2 | `[unknown]`; in LE3 the observed damage was on return |
| Where the conversation camera's target comes from | a spectator camera must not duplicate or fight it |
| Whether a conversation can address a pawn that is not the player's | decides if player 2 can be *in* a scene at all rather than only watching |
| How dialogue choices are bound per controller | if choices are per-`LocalPlayer`, a spectator cannot be given them at all — which is fine for spectator mode, and a hard wall for "player 2 picks their own lines" |
| Whether LE1/LE2 pause on focus loss the way LE3 does | a two-window test will otherwise appear to freeze randomly |

## Recommended next actions, in order

1. **Build the read-only conversation detector** against a live LE1, using the four fields
   above. It needs no mutation and answers whether detection is even possible. This is the
   single highest-value item.
2. **Measure the LE1/LE2 equivalents** of the LE3 conversation fields by property name — never
   by ported offset.
3. **Establish a reliable conversation to test against**: which save, which level, how long it
   runs, and whether it is mode 0 or mode 7. LE3's notes are explicit that a mode-0 landing
   conversation does not validate mode 7.
4. Then choose Mechanism 1 or 2 based on what the detector reports about who owns the pawn.

Do not start with the mechanism. The detector is cheap, safe, and tells you which mechanism
the engine will tolerate.
