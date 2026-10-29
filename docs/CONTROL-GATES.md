# Where the player cannot shoot: the control gates

A named map of every point that can stop a player from acting — firing, moving, using powers,
being in the world at all — because "the player cannot shoot here" is a small, finite set of
gates once they are named. Each entry says what the gate is, how it is evidenced, and how to
read it from a live process.

Evidence labels as elsewhere in this folder: **[proven]** by this project, **[documented]** by
ME3SquadCoop's protected tests, **[derived]** from engine structure, **[unknown]**.

## The firing chain, in order

**Read the caveat first: this chain is from LE3 and is not the chain LE1 assembles.** Measurements
later in this document show ME1 has `CanFireWeapon`, `IsLocallyControlled` and `StartFire`, but
**not** `HandleWeaponFiring`, `CanFire`, `bCanMove` or `InCombat`. So this describes LE3's
assembly; for LE1 treat it as the shape to look for, not a list of hook targets. Even so it is the
most useful single piece of information in this document, because it is the actual call path and
it is documented with the checks inside it:

    BioPawn.Tick
      -> IsLocallyControlled                 (gate 1)
      -> HandleWeaponFiring                  (only if pending fire)
          -> CanFireWeapon                    (gate 2)
              -> Pawn.StartFire               (gate 3)

`[documented: NATIVE-FIRE-OBSERVATION.md]` The documented script bodies for these contain **no
player-pawn class cast**, which matters: it means the chain is not restricted to a specific
pawn class, only to the local-control predicate.

**Gate 1 — `IsLocallyControlled` is the wall.** The documented finding is explicit:

> in a network session its controlled player controller requires `Player` to be an
> `Engine.LocalPlayer`. The inspected predicate has no pawn-class or Role restriction.

So on a possessed squadmate, firing stops at the *first* gate if `Controller.Player` is null or
not a `LocalPlayer` — regardless of whether the pawn, weapon and ammo are all fine. That is
exactly the state created by `Actor.Spawn(BioPlayerController)` without a `LocalPlayer`, and it
is why crash 13's null `PlayerInput` was a symptom of the same missing object.

**[derived]** Consequence: any design that gives player 2 a controller *without* a `LocalPlayer`
will fail gate 1. Route 3 in [ADDING-MISSING-SYSTEMS.md](./ADDING-MISSING-SYSTEMS.md) (a
possessed pawn + camera + direct input, no `LocalPlayer`) therefore cannot make player 2 fire
through the normal path. It would need to call the fire path *below* the gate, which means
bypassing the engine's own control check — a deliberate decision, not an incidental one.

**Gate 2 — `CanFireWeapon`** is inside `HandleWeaponFiring`, so it is only reached if gate 1
passed. Its inputs are the weapon and the pawn's state, not the player's.

**Gate 3 — `StartFire`** exists in at least three places and they are not interchangeable:
`Pawn.StartFire`, plus **both** the base and `SFX` inventory-manager `StartFire`
implementations. `[documented]` Hooking "StartFire" without distinguishing these gets the wrong
one; the mod's own observer distinguishes pawn, inventory and weapon receivers.

## The other gates, grouped

| Gate | Where | Effect | Evidence |
|---|---|---|---|
| **`IsLocallyControlled`** | `BioPawn.Tick` | no firing at all | `[documented]` |
| **Pending fire not set** | `HandleWeaponFiring` | the per-frame path never runs | `[documented]`, `bFire` observed |
| **Weapon state not `Active`** | inventory manager | ammo does not consume | `[documented]` |
| **Inventory chain null** | `Pawn.InvManager` | firing refused; documented case of it going null during `ReceivedBunch` | `[documented]` |
| **Power prerequisites** | power-wheel handler | powers do nothing: needs controller, quickslot definitions **and initialised cached icons** | `[documented]` |
| **`bPlayersOnly`** | `WorldInfo` | world simulates only players; a non-player pawn stops being simulated | `[documented]` |
| **Focus pause** | `BioWorldInfo.Tick` | `PauseGame(true)` when the window loses foreground | `[documented]`, 10 s observed |
| **`Physics == 7`** | `BioPawn` | script owns the pawn; movement input ignored by design | `[proven]` |
| **Collision disabled after release** | `Actor` | pawn falls, movement fails afterwards | `[documented]` |
| **Cinematic mode** | `PlayerController` | input suppressed for the cinematic owner | `[documented]` |
| **Conversation active** | controller counts | script drives everything | `[documented]` |

## Measured: which gate names exist in which build

Now answerable **offline**, from the packages themselves, using `tools/melepkg`. Scanned
`SFXGame.pcc` plus eight `BIOA_*.pcc` level packages per game, checking both ASCII and UTF-16LE
identifiers (ME3 stores them wide - checking one encoding reports a false zero).

| Name | ME1 | ME3 | Note |
|---|---|---|---|
| `CanFireWeapon` | **yes** | yes | the firing predicate exists in both |
| `CanFire` | **no** | yes | ME3-only spelling |
| `HandleWeaponFiring` | **no** | yes | ME3-only: ME1 has no such function name |
| `bCanMove` | **no** | yes | ME3-only |
| `InCombat` | **no** | yes | ME3-only |
| `CanUsePower` | **no** | yes | ME3-only |

This is a real, load-bearing result and it changes the porting estimate:

- **The documented firing chain is an ME3 chain.** `BioPawn.Tick -> IsLocallyControlled ->
  HandleWeaponFiring -> CanFireWeapon -> StartFire` describes LE3. ME1 has `CanFireWeapon`,
  `IsLocallyControlled`, `StartFire` and `bFire`, so the *shape* exists, but it is not assembled
  from the same named pieces. **Porting the ME3 hook targets to ME1 is not a rename.**
- **Some names are present in both**, and those are the portable base: `CanFireWeapon`,
  `IsLocallyControlled`, `StartFire`, `UseAbility`, `UsePower`, `doHotKey`, `ActivatePower`,
  `SquadOrderUsePower`, `PlayerInput`, `bCinematicMode`, `bPlayersOnly`, `PauseGame`, `HasFocus`,
  `AddMember`, `RemoveMember`, `SquadMember`, `Henchman`, `bCanBeDamaged`.
- **Scanned as nine packages each**, so a "no" is not just "this one file did not have it".

Caveat that still applies: this is an **identifier inventory**, not attribution. It proves a name
exists somewhere in the build; it does not prove which class declares it, nor that it is
reachable at a given moment. Confirm against live reflection before hooking.

### Method, so it can be re-run

    python -I tools/melepkg/mele_script_map.py --package <SFXGame.pcc> --gates
    python -I tools/melepkg/mele_script_map.py --package <SFXGame.pcc> --out local/MELE/maps/ME1.json
    python -I tools/melepkg/mele_script_map.py --diff local/MELE/maps/ME1.json local/MELE/maps/ME3.json

The `--diff` output *is* the "which naming calls need changing" list, per gate group and for the
map-event classes.

## How to read these gates from a live process

All are plain fields, so none of this needs a mutation. Resolve by name; never port an offset.

| Field | Object | Notes |
|---|---|---|
| `Physics` | `BioPawn` | LE1 `+0x27C`, LE2 `+0x284`, LE3 `+0x268`; 7 = script-owned |
| `bPlayersOnly` | `WorldInfo` | world-wide freeze |
| `Player` | `PlayerController` | LE1 `+0x6A8`; **null here means gate 1 fails** |
| `PlayerInput` | `PlayerController` | LE1 `+0x6D8` region; null = the crash-13 state |
| `Pawn` / `Controller` | controller / pawn | must be reciprocal or nothing is owned |
| `InvManager` | `Pawn` | null = cannot fire |
| cinematic bit(s) | `PlayerController` | several spellings; the tool tries them all |
| conversation counts | `PlayerController` | read as a set; one unreadable field invalidates |

`tools/ue3live/ue3_conversation.py` already reads six of these. The script map
(`tools/ue3live/ue3_script_map.py --category gates`) finds the *functions and properties* that
implement the rest, by name, across every class.

## Map events: what fires when the level changes

The Kismet layer is how the game decides "you may not act here yet", so it is the other half of
this document. The class-name prefixes to look for, and what they mean:

| Prefix | What it is |
|---|---|
| `SeqAct_*` | an action: teleport, lock controls, spawn, set a variable |
| `SeqEvt_*` | an event: player entered a volume, looked at something, finished a conversation |
| `SeqCond_*` | a condition: branches the script on state |
| `SeqVar_*` | a variable: object, int, string references the script reads |
| `BioSeqAct_*` / `BioSeqEvt_*` | BioWare's own extensions — the ones that actually drive ME levels |
| `SFXSeqAct_*` | ME3-era extensions |

`[proven]` An earlier inventory of one ME3 level found `BioSeqAct_EndCurrentConvNode`,
`BioSeqEvt_ConvNode`, `BioSeqAct_PMCheckState`, `BioSeqAct_PMExecuteTransition` and
`BioSeqVar_StoryManagerInt` in live actor lists — i.e. the story/progression machinery is
visible at run time and its names are readable.

**[unknown]** What any individual `BioSeqAct_*` does. Names are suggestive, never conclusive:
`BioSeqAct_PMCheckState` looks like a plot-manager check but that is an inference. Reading the
bytecode would settle it, and that is blocked on the container.

## Measured: map events in a real ME1 level

`BIOA_CRD00.pcc` (27.5 MB decompressed, 77,735 identifiers) carries **58** map-event class names.
That is the script surface for one level, and it is now readable offline.

Counts by prefix: `BioSeqAct_` 23, `SeqAct_` 13, `SeqVar_` 11, `SeqCond_` 4, `BioSeqCond_` 2,
`BioSeqEvt_` 2, `BioSeqVar_` 1, `SFXSeqAct_` 1.

The ones that matter for "why can I not shoot here", with what the name implies:

| Class | Implication | Confidence |
|---|---|---|
| `BioSeqAct_NonCombatArea` | a volume the script declares **non-combat** - the direct answer to "cannot fire here" | name is explicit; behaviour unverified |
| `BioSeqAct_HideAllWeapons` | weapons hidden, so firing is meaningless | `[derived]` from the name |
| `BioSeqCond_InCombat` | the script *branches* on combat state, so combat is a scripted concept per level | `[derived]` |
| `BioSeqAct_StartConversation` / `BioSeqEvt_Conversation` | conversations are **script-driven events**, which is why they suppress control | `[derived]` |
| `BioSeqAct_ModifyPropertyPawn` | the script writes pawn properties directly - this is how a level disables things without a named gate | `[derived]` |
| `BioSeqAct_ToggleSave` / `BioSeqAct_AutoSave` | saving is level-controlled, which matters for a two-copy test | `[derived]` |

**`BioSeqAct_NonCombatArea` and `BioSeqAct_ModifyPropertyPawn` together explain most
"the player cannot act here" cases**: one is a declared region, the other is arbitrary property
surgery on a pawn. A co-op mod cannot rely on finding one named "can shoot" flag, because a level
can disable firing by writing an unrelated property through `ModifyPropertyPawn`.

That is the most useful negative result in this document. It means the practical approach is:

1. read the *state* the script sets (`Physics`, weapon visibility, combat flag), not just one gate;
2. treat `NonCombatArea` as a region to detect, not a flag to flip;
3. for a second player, assert "can this pawn act" from several fields rather than trusting a
   single `CanFire`-shaped name.

To inventory a level:

    python -I tools/melepkg/mele_script_map.py --package <BIOA_*.pcc> --events --limit 60

## Naming: the part that makes a build portable

The owner's target is a verified base where "possibly only some naming calls need changed". That
is achievable and it is a concrete process:

1. Dump `tools/ue3live/ue3_script_map.py` for each game into `local/MELE/maps/LE1.json`,
   `LE2.json`, `LE3.json`. This gives exact `class -> functions (with parms) -> properties`
   with the declaring class for inherited members.
2. Run `--diff` between two maps. The output is three lists: classes only in A, classes only in
   B, and the same for `class.function` pairs.
3. Treat "in both" as the portable base. Treat the differences as the naming work — each one is
   a call site that needs a different name, not a different design.
4. Anything the map lists as **not found** is a genuine absence, because the scan walks the
   whole object array rather than stopping at the first match.

The map records `declared_on` for every property and the full `SuperStruct` chain for every
class, which is what makes this safe: a function found on `BioPawn` may be declared on `Pawn`,
and a port that assumes the derived class will miss it.

## What to do with this, in order

1. **Run the script map on all three games** (needs each game running once). Until then this
   document is a map of *known* gates, not a verified list for each build.
2. **Read the gates on a live pawn** with `ue3_conversation.py` and a small extension for
   `InvManager` and `Player`, to confirm which gate is actually closed at a given moment.
3. **Diff LE1 vs LE3** first: LE3 is the build where firing, powers and possession were made to
   work, so the diff between it and LE1 *is* the work list.
