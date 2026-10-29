# Command suite: every kind of command, in the order that keeps a failure attributable

The question this answers is *"does this build let a second player do the thing"*, for every
kind of thing, without finding out one restart at a time.

A failed experiment on this game costs a reload and sometimes a save. So the suite is built
around one rule: **a command is only worth running when the thing it depends on has already
been proven, in this process, on this build.** That rule is what turns 156 commands into 23
ordered phases instead of 156 independent attempts.

| Part | Where |
|---|---|
| Catalogue (156 command mechanisms, measured presence per game) | [`tools/ue3live/commands.json`](../tools/ue3live/commands.json) |
| Generator, verifier, live resolver, plan builder | [`tools/ue3live/ue3_command_suite.py`](../tools/ue3live/ue3_command_suite.py) |
| **Runner** — executes a plan through the in-game bridge | [`tools/ue3live/ue3_run_plan.py`](../tools/ue3live/ue3_run_plan.py) |
| **Game-side adapter contract** — the five methods a mod implements | [COMMAND-SUITE-ADAPTER.md](COMMAND-SUITE-ADAPTER.md) |
| Tests (30 + 24 cases, no game required) | [`test_command_suite.py`](../tools/ue3live/test_command_suite.py), [`test_run_plan.py`](../tools/ue3live/test_run_plan.py) |

```
python -I tools/ue3live/ue3_command_suite.py list
python -I tools/ue3live/ue3_command_suite.py phases --verbose
python -I tools/ue3live/ue3_command_suite.py verify
python -I tools/ue3live/run_tests.py            # ALL OK: 94 cases across five suites
```

---

## Why the unit is a mechanism, not a test

`SetLocation` and `ClientSetLocation` do the same visible thing and are separate entries,
because one exists in all three games and the other exists only in LE3. That difference is a
finding, and folding them into "teleport" would erase it.

The same for `CanFireWeapon` / `CanFire` / `WeaponFire` / `StartFiring` / `HandleWeaponFiring`
— five spellings of firing across three builds. A port that maps one of them leaves the rest
dead, and the symptom is "the client can't shoot", which tells you nothing about which name
was wrong.

Eighteen categories, twenty-of-everything in the risky layers:

| Category | What it covers | Why it is its own category |
|---|---|---|
| `console` | engine exec verbs | the only channel present in all three builds with no injected code |
| `kismet` | Sequence objects, activation, map events | where "the door opened for player 1" comes from |
| `pawn` | spawn, possess, create-player | the second body itself |
| `transform` | location, rotation, base, collision, scale | cheap to verify, and the commonest source of divergence |
| `state` | state machine entry and exit | divergence here is silent and permanent |
| `input` | input object, bindings, gates, input trap | whether input reaches the pawn, and whose |
| `combat` | firing, powers, damage, busy state | the player-visible half, and the per-pawn-gated half |
| `conversation` | dialogue ownership | where player 2 has to become a spectator |
| `camera` | view target, camera mode | two clients, one set of `SetViewTarget` calls |
| `timer` | timer set/clear/query | a timer is keyed by name *and* object, so it is not global |
| `save` | save, load, profile | shared across every copy on the machine |
| `hud` | HUD presence and ownership | the cheapest per-client visible proof |
| `replication` | roles, RPC-shaped calls | LE3 only, and the reason LE3 goes first |
| `message` | text to a player | the first thing that breaks with two of them |
| `travel` | level transitions | every one is a place a client gets dropped |
| `query` | identity and context lookups | must all agree on both clients before anything else means anything |
| `audio` | sound and music | same trigger path as everything else, different listener count |
| `anim` | animation and morph | scripted animation that assumes it drives *the* player |
| `pause` | pause and `bPlayersOnly` | the mechanism that already freezes both, so it looks correct |
| `squad` | party membership | the client *is* a squadmate, so this is what it fights the AI over |

---

## Presence is measured, and the measurement can fail

Presence comes from the decompressed identifier tables in `local/MELE/maps/ME{1,2,3}.json`, not
from anyone's memory of the engine. `catalogue` refuses to write when the hand-written
taxonomy disagrees with the data, and `verify` recomputes the whole table and reports drift.

Result on the current data:

| | Commands present |
|---|---|
| LE1 | 97 |
| LE2 | 100 |
| LE3 | **143** |
| all three | 75 |

Six distinct presence patterns, 47 commands present in exactly one build — so the per-game
claim is doing real work rather than being `TTT` everywhere.

**`verify` was tested by breaking it.** Two of the twenty-four cases delete an identifier from a
copy of the map files and require a non-zero exit that *names the affected command and game*.
Writing those two cases found two real tool bugs:

1. Stripping `Possess` from the ME3 map did not register, because the real name table holds
   both `Possess` **and** `possess`. Removing one spelling leaves the command present, and a
   one-case-variant check would have been a check that cannot fail.
2. The "removed identifier" case for the second direction originally removed `Ghost` from
   ME2 — where `Ghost` is *already absent*. The test passed while testing nothing.

Both are the same class of mistake as rule 25 in [NOTES.md](NOTES.md): a check whose result
does not depend on the thing it claims to check.

Six further cases test the **live resolver's selection logic**, which is the decision that
names which `UFunction` a plan dispatches. It is a single pure function (`resolve_target`)
precisely so it can be tested directly rather than through a copy of itself — a test that
reimplements the algorithm only proves the two copies agree today. The cases pin:

| Case | Requires |
|---|---|
| targeted owner vs first hit | `Actor` wins over `SceneManager` even though `SceneManager` comes first |
| superclass match | `BioPlayerController` is accepted when the target says `Controller` |
| unrelated owner | rejected, and the reason names the owners it expected |
| ambiguous name, no targets | still resolves, but flagged **and** the other owner is named |
| no candidates | `None`/`None` — the caller supplies the "absent" wording, not the resolver |
| unambiguous candidate | resolves with no warning (so the warning means something) |

---

## Evidence level, stated plainly

A `present: yes` means **the identifier exists in that build's decompressed script data.**

It does **not** mean:

- the function is reachable at the moment you care about;
- it is owned by the class you intend (UE3 names are not unique — see below);
- calling it is safe, or that its parameters are what you think.

That is what `probe` is for. It resolves every catalogue entry against a **running** game,
read-only, and reports the owning class, the resolved `UFunction` pointer and the measured
parameter block for each. A name that resolves to three owners is reported with all three
rather than silently taking the first, because taking the first is how a port calls the wrong
`SetLocation` and gets a no-op that looks like a gate.

Neither the offline catalogue nor the live probe can tell you a call *worked*. Only the plan,
executed on the game's main thread, can.

---

## The phases, and why this order

Twenty-three phases, four of them opt-in. Ordered by one principle: **everything reversible is
proven reversible before anything irreversible runs, and every phase states what it would
mean for it to be wrong.** The ordering is enforced against *required* phases only, because an
optional phase cannot corrupt a run that never opts into it — and a test asserts exactly that.

| Phase | Risk | Does | Abort if |
|---|---|---|---|
| `P00-baseline` | read | player chain, physics, location, gates | any read fails — an offsets problem, not a co-op problem |
| `P01-query` | read | every query command, both clients | map name or player index differs |
| `P02-console` | low | a verb that must visibly work, then `Say` markers | the control verb changes nothing on screen |
| `P02b-console-state` | low | god/fly/walk/slomo/freezeframe/ghost, each ON then OFF | physics or time dilation does not return to P00 |
| `P02c-console-destructive` | **high, optional** | killpawns, allweapons, suicide, playersonly, team | the world is left with `bPlayersOnly` set |
| `P03-timer` | low | SetTimer → IsTimerActive → ClearTimer → IsTimerActive | the two reads disagree on one client |
| `P04-visual` | low | hidden/scale/sound/anim/HUD/message round-trips | `bHidden` did not change |
| `P05-state` | medium | three `GetStateName` values around a push/pop | `GetStateName` absent (LE1) — skip the assertion, do not guess |
| `P06-transform` | medium | set location/rotation to their **current** values | location changed — the call is not idempotent |
| `P07-camera` | medium | set view target to the current target, mode to current | the view changes |
| `P08-placement` | medium | collision → physics 1 → `ClientSetLocation`, restore | the pawn is at physics 7 (scripted) |
| `P09-input` | medium | `SetCinematicMode` on/off around a read | `bCinematicMode` does not follow the call |
| `P10-combat` | medium | fire, powers, hotkey, busy state, one 0-damage hit | no weapon equipped |
| `P11-kismet` | medium | count Sequence objects both clients, `CauseEvent`, `TriggerEvent` | sequence counts differ |
| `P12-conversation` | medium | cinematic + `bPlayersOnly` as a stand-in for a scene | `bPlayersOnly` already 1 at baseline |
| `P13-possession` | **high** | UnPossess → Possess the squadmate → re-read the chain → back | no second pawn, or UnPossess leaves no pawn |
| `P13b-engine-player-path` | **high, optional** | `PostLogin` / `RemovePlayer`, LE3 only | the process faults — do not retry on another build |
| `P14-replication` | medium | roles and `bNetOwner` both pawns; LE3 `ReplicatedEvent` | never — informational |
| `P15-squad` | medium | remove a member, re-add the same, compare the list | the list is not restored exactly |
| `P16-pause` | medium | `SetPause` on/off, then `PauseGame` | either client stays paused |
| `P17-travel` | **high, optional** | one transition and back; `Login`/`Logout` may only be used here | either client is gone after the return |
| `P18-save` | **high, optional** | nothing unless deliberately opted in | any doubt — the profile is shared with the real game |
| `P19-cleanup` | low | restore every P00 value, re-run P00, diff | any P00 value differs — the run is void |

Two design choices worth naming:

**`P06` sets values to themselves.** It is the only way to test that a transform call reaches
its object without also moving a pawn, so a later placement failure is about the value and not
about the call.

**`P12` never starts a real conversation.** A conversation needs story context, and burning a
save to prove a mechanism is the trade this suite exists to avoid. `bPlayersOnly` plus the
cinematic bit is the same state a scene produces, and it is reversible.

### The four optional phases

`P02c`, `P13b`, `P17`, `P18` are off by default, and a test enforces it by asserting that no
hazardous command (`SaveGame`, `SaveProfile`, `LoadGame`, every travel verb, `Login`,
`Logout`) appears in a required phase.

Why they are separate:

- **Saves and the profile are shared.** Every copy on the machine resolves the same
  `Documents\BioWare\Mass Effect Legendary Edition\Save` path — verified, with no config key
  able to redirect it. A routine run that saves can overwrite real progress.
- **Travel drops both clients.** That is the *point* of the phase and the reason it cannot be
  a step inside another one.
- **`P13b` calls `PostLogin` by hand**, which is the one route left that has never been
  proven, and LE1/LE2 crash inside the equivalent path *after* returning success.

---

## What the plan is, and what Python refuses to do

`plan` turns a probe result into ordered `ready` / `skipped` / `unresolved` entries, each
carrying the resolved `UFunction`, its owner and the measured parameter block. The **runner**
(`ue3_run_plan.py`) then executes it through the in-game bridge.

Python still does not make the call, and that part matters: `ProcessEvent` must run on the
game's main thread, and its vtable slot differs per game:

| Game | `ProcessEvent` slot | Evidence |
|---|---|---|
| LE1 | `+0x238` | body `mov r14, r8` — ARG3 is the parms block |
| LE2 | `+0x228` | byte-identical prologue to LE1's |
| LE3 | `+0x230` | a wrapper that reorders arguments |

And LE1/LE2 crash inside `CreatePlayer` **from the game's own thread, after a correct call
returned**. A dispatcher in a separate process cannot attribute a fault like that, and one in
Python cannot legally make the call at all. So the split is: **Python decides, the bridge calls,
both write JSON** — and a run is replayable from the result file alone.

### Commands are aimed by ROLE, not by address

A plan says `player.pawn`, not `0x1AEE23EECB0`. Addresses are per-launch — the project's own
offsets file records `function_pointers_are_per_launch: true` — so a baked-in address is stale
the next time the game starts, and **a stale address is the worst possible failure**: it is a
valid-looking pointer to arbitrary memory. The game-side adapter resolves the role against the
live process at call time and reports what each role meant in `ue3.info`, so the result file can
say what it actually touched.

| Role | Means | Commands |
|---|---|---|
| `player.controller` | the local player's PlayerController | 54 |
| `player.pawn` | the local player's pawn | 53 |
| `gameinfo` | the active GameInfo | 13 |
| `none` | class-level, or a Kismet action found by name | 10 |
| `sequence` | the level's Kismet Sequence | 9 |
| `weapon` / `hud` / `worldinfo` / `squad.member` | the rest | 13 |
| `level` / `viewport` | the actor list and the GameViewportClient | 4 |

146 of 156 are role-addressed. The 10 that are not are justified individually: eight are
`SeqAct_*` actions the game side must find by name, `CreatePlayer` is blocked anyway, and
`LocalPlayerController` names a class object rather than an instance.

Two roles were wrong until the end-to-end test caught them: `IsLocallyControlled` was aimed at
nothing (it must be asked **of a controller** — the most useful query a co-op client has), and
`RemovePlayer`/`LayoutPlayers` were aimed at a player when they are `GameViewportClient`
methods. Both now have tests that fail when regressed, verified by breaking them deliberately.

### Three things a plan will not do

1. **Dispatch a blocked command.** `CreatePlayer` is in the catalogue with its three LE1/LE2
   crash signatures and `risk: blocked`; a test asserts it can never be `ready`.
2. **Drop an unresolved command.** It appears as `unresolved` with the reason, because a plan
   that silently omits a command reads as "passed".
3. **Hide its own coverage gap.** Any command present in the build but used by no phase is
   listed under `uncovered`. It is 0 today — and it was 17 before three phases were added,
   which is exactly the sort of gap a summary would have concealed.

---

## Per-game findings this catalogue already carries

These are measured, and each one changes the design for that game:

| Finding | Consequence |
|---|---|
| LE3 has **198** `Client*`/`Server*` names; LE1 has 6 and LE2 has 8 | LE3 is the only build with a usable replication layer, so it goes first |
| `CreatePlayer` / `RemovePlayer` / `LayoutPlayers` exist **only in LE3** | the split-screen route is LE3-only; LE1/LE2 need possession of an existing body |
| `SetCollision` is LE1/LE2, `SetCollisionType` is LE3 | a port that calls one on the wrong game gets a silent no-op |
| `ClearPause` is LE3-only, `PauseGame` is LE1/LE3 | pausing has three different shapes across three builds |
| `IsLocallyControlled` absent in **LE2**; `GetALocalPlayerController` absent in LE2 | LE2 is the odd one out in the single most useful query group |
| `SeqAct_Delay` present in LE1/LE2, **absent in LE3** | a trigger delay built on the stock delay node cannot be ported to LE3 |
| `CauseEvent` **and** `TriggerEvent` present in all three | one named-event mechanism covers all three games, which is the safest trigger-delay hook |
| `ActivateOutputLink` is LE3-only; `Activate` is LE2/LE3 | per-player delay of one Kismet branch is expressible in LE3, differently in LE2, not at all in LE1 |
| `BioSeqAct_SetPlayerControlling`, `BioSeqEvt_PlayerInput`, `BioSeqAct_SetInputTrap` are **LE1-only** | LE1's own control-change and input-observation hooks — the most promising LE1-specific levers found so far |
| `RemoteRole` absent in LE1 | LE1 cannot express asymmetric authority at all |
| 24 delegate names in LE1, 57 in LE2, **105** in LE3 | how much event plumbing survived per build |

The `GetALocalPlayerController` finding is worth stating on its own: the function name
promises "**a** local player controller", and in a two-player build that ambiguity is the
entire problem. LE1 and LE3 have it, LE2 does not.

---

## What this does not solve

- **Attribution at run time.** The export/import tables now parse (see
  [CONTAINER-FORMAT.md](CONTAINER-FORMAT.md)), so offline work gives class, superclass and outer -
  it just cannot say which *instance* is live in the current level. `probe` answers that, and needs
  a running game.
- **Enum members.** `EWhoTriggers` values are not recoverable from identifiers alone; the
  trigger-delay design in [TRIGGER-DELAY.md](TRIGGER-DELAY.md) names the property but not its
  values.
- **Whether a call works.** The plan carries the assertion, the runner enforces it, and only an
  executed run produces a result. No phase in this document has been run yet — the catalogue,
  verifier, planner, runner and their 94 tests have, and those need no game.
- **The game side.** The four `ue3.*` bridge methods the mod must implement are specified in
  [COMMAND-SUITE-ADAPTER.md](COMMAND-SUITE-ADAPTER.md) and **not written**. Everything above the
  game process is done and tested; nothing inside it is.
- **An offsets file for two of the three games.** `probe` reads a per-game offsets JSON, and
  only LE1 has a complete, verifier-passed one. LE3 and LE2 have their `objects_rva`,
  `name_pools_rva` and `engine_global_rva` recorded in the
  [ue3live README](../tools/ue3live/README.md), but no `offsets_LE3.json` exists — so
  probing **LE3, the first target game, needs `ue3_discover_external.py` and
  `ue3_verify_offsets.py` run against a live LE3 first.** Writing that file by hand from the
  README's RVAs is the worst option available: it would look authoritative, carry a
  `"verified": true` nobody earned, and fail mid-dispatch. Discover, verify, then write.
- **Two clients.** Every phase describes a comparison between two clients, and the harness
  that runs both is the [bridge](../tools/bridge/README.md) plus
  [gamepool](../tools/gamepool/). The suite is the content; the plumbing to drive two
  copies at once is the next piece.

## Status

| Piece | State |
|---|---|
| Catalogue (156 mechanisms, 20 categories, 5 risk levels, 12 target roles) | generated and verified, `ALL OK` |
| `verify` drift detection | proven by two deliberate corruptions |
| Phase definitions (23, four optional) | defined, ordered, self-checked |
| Coverage of present commands by phases | 0 uncovered |
| Target roles | 146 of 156 role-addressed, the other 10 justified; role tests proven by breaking them |
| **Runner** (`ue3_run_plan.py`) | **written and tested**, 24 cases against a real bridge server, including a generated-plan end-to-end run |
| **Game-side adapter** (`ue3.*` methods) | **not written** — specification in [COMMAND-SUITE-ADAPTER.md](COMMAND-SUITE-ADAPTER.md) |
| Tests | 94 cases across 5 suites, all pass, no game needed |
| Live `probe` | written, needs a running game — **not yet run** |
| An actual phase executed against a game | **not yet** |
