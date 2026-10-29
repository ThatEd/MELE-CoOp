# Map events across LE1, LE2 and LE3

Level events are Kismet classes: the script objects that fire on level entry, triggers, story
progress, conversations and combat. For a co-op mod they are the closest thing to a readable
"what is happening in this level" channel, and the place where a level decides the player may not
act.

All of this is measured **offline**, by decompressing the packages with `tools/melepkg` and
inventorying identifiers. Nothing here needed a running game.

## The measurement, and the surprise in it

Counted across each game's whole script surface (all packages, both encodings):

| Game | identifiers | event class names | `BioSeqAct_` | `SFXSeqAct_` | `BioSeqEvt_` | `SFXSeqEvt_` | `SeqAct_` |
|---|---|---|---|---|---|---|---|
| ME1 | 56,899 | **322** | 244 | 4 | 41 | 0 | 12 |
| ME2 | 139,336 | **306** | 145 | 65 | 31 | 25 | 15 |
| ME3 | 141,580 | **225** | 78 | **83** | 11 | 19 | 13 |

**The expectation was wrong.** "ME3 has a lot of these, ME2 and ME1 have some" is the intuitive
model, but ME1 has the *most* event names and ME3 the *fewest*. What changes is not the count but
the **composition**: ME3 shifts from `BioSeqAct_*` (BioWare's general scripting) to `SFXSeqAct_*`
(ME3's own gameplay layer), which is 83 of its 225.

The lesson for porting: comparing counts says nothing. Comparing *which* names exist is the work,
and that is what the rest of this page does.

## Overlap: 86 classes are in all three

That is the genuinely portable base — an event class a mod can rely on in every game. The full
set, grouped by what it is for:

**Story and progression** — `BioSeqAct_PMCheckState`, `PMCheckConditional`, `PMExecuteTransition`,
`PMBase`, `BioSeqVar_StoryManagerInt`, `StoryManagerFloat`, `BioSeqAct_EndGame`,
`BioSeqAct_ActivateGalaxyMap`, `BioSeqEvt_GalaxyMap`

**Conversations** — `BioSeqAct_EndCurrentConvNode`, `BioSeqAct_IsActiveConversation`,
`BioSeqAct_InterruptAmbientConversation`, `BioSeqEvt_ConvNode`, `BioSeqEvt_Conversation`,
`BioSeqEvt_CinematicSkipped`, `BioSeqAct_BioToggleCinematicMode`, `BioSeqAct_CopyPlayerHeadToTarget`

**Player control** — `BioSeqAct_NonCombatArea`, `BioSeqCond_InCombat`, `BioSeqAct_SetActive`,
`BioSeqAct_SetGestureMode`, `BioSeqAct_SetExploreCameraRotation`, `BioSeqAct_SetLookAtTarget`,
`BioSeqAct_ToggleGlobalLookAt`, `BioSeqAct_FaceOnlyVO`

**Squad** — `BioSeqAct_AddToParty`, `BioSeqAct_GetSquad`, `BioSeqAct_ResurrectHenchman`,
`BioSeqAct_SetWeapon`

**Level and streaming** — `BioSeqAct_AreaTransition`, `BioSeqAct_SetStreamingState`,
`BioSeqEvt_ForegroundStreamingDone`, `BioSeqEvt_BackgroundStreamingDone`,
`BioSeqAct_PlayLoadingMovie`, `BioSeqAct_StopLoadingMovie`, `BioSeqAct_InitLoadingMovie`,
`BioSeqAct_ShouldShowSplashSequence`

**Effects and UI** — the `BioSeqAct_DUI*` family (12 classes), `BioSeqAct_AttachVisualEffect`,
`AttachCrustEffect`, `SetEnvironmentEffectIntensity`, `ToggleLightEnv`, `SetLightEnvQuality`,
`ForceLightEnvUpdate`, `BioSeqAct_ShowMessage`, `BioSeqAct_MovieBink`

**Generic engine** — `SeqAct_Interp`, `SeqAct_Teleport`, `SeqAct_Toggle`, `SeqAct_Destroy`,
`SeqAct_Latent`, `SeqAct_ToggleCinematicMode`, and the whole `SeqVar_*` value family

This is a better starting point than any single game's list: an event the mod hooks here works in
all three.

## What only ME1 has — 159 classes, and several are direct control levers

ME1's scripting is unusually rich in things that manipulate the player and the squad:

| Class | Why co-op cares |
|---|---|
| **`BioSeqAct_SetPlayerControlling`** | takes a pawn and makes it **player-controlled** — a scripted possession, the engine's own version of what the mod does |
| **`BioSeqAct_SetInputTrap`** | traps input; an explicit "player cannot act" mechanism |
| **`BioSeqAct_StartFiring` / `StopFiring`** | firing as a *scripted* action |
| **`BioSeqAct_UsePower` / `UsePowerOn` / `IsAbilityEnabled`** | powers driven and gated by script |
| **`BioSeqAct_HideAllWeapons`** | weapons off for the scene |
| **`BioSeqAct_SetBusyState`** | generic "this pawn is busy" |
| **`BioSeqAct_CheckIfInVolume`** | region test |
| **`BioSeqAct_SpawnHenchman` / `LootHenchman` / `FollowSquadLeader`** | squad lifecycle |
| **`BioSeqAct_SquadStartHoldingPosition` / `StopHoldingPosition`** | squad command state |
| **`BioSeqEvt_PlayerInput`** | an **event fired on player input**, the natural injection point for a second player's input |
| **`BioSeqEvt_TakeDamage`**, `OnCombatBegin`/`OnCombatEnd`, `SeePlayer`, `OnLookingAt` | the combat/story stimulus set |
| **`BioSeqEvt_ElevatorSequenceSkipped`**, `BioSeqAct_SkipElevatorSequence` | ME1's long elevator rides, which are scene transitions |
| **`BioSeqAct_EnterVehicle` / `ExitVehicle`**, `BioSeqCond_IsDriving` | vehicle state |

`BioSeqAct_SetPlayerControlling` is the interesting one. If ME1 can hand a pawn to "the player"
by script, that is an engine-supported control transfer — relevant to the problem
`CreatePlayer` could not solve.

Presence verified per game (identifier sets, `yes / - / -` for all six of those ME1-only names):
`SetPlayerControlling`, `PlayerInput`, `SetInputTrap`, `HideAllWeapons`, `StartFiring`,
`IsAbilityEnabled`, `SetBusyState` are **ME1-only**. `SFXSeqEvt_LevelIsLive` and
`SFXSeqAct_ToggleCombatPawn` are **ME3-only**.

## What only ME3 has — 53 classes, combat and level lifecycle

| Class | Why co-op cares |
|---|---|
| **`SFXSeqAct_ToggleCombatPawn`** | toggles a pawn between combat and non-combat |
| **`SFXSeqAct_AddCombatZone` / `RemoveCombatZone`** | explicit combat regions, the ME3-era replacement for a non-combat volume |
| **`SFXSeqEvt_HordeWaveStart` / `HordeWaveEnd` / `HordeEnemyKilled`** | the horde mode events — the multiplayer-adjacent mode |
| **`SFXSeqEvt_LevelIsLive`**, `WaitUntilLevelIsLive`, `MakeLevelLive`, `PersistentLevelStarted` | level readiness; the right gate before any co-op action |
| **`SFXSeqAct_WaitForPlayerSpawn`** | a spawn hook |
| **`SFXSeqEvt_ProtoInputTrap`** | input trap in ME3's own layer |
| **`SFXSeqAct_PossessTurret`**, `HenchmanUseInteractionPoint` | possession-adjacent |
| **`SFXSeqAct_ToggleInjuredShepardMode`** | ME3-specific player state |
| **`SFXSeqAct_DummyWeaponFire`**, `LaunchWeaponSelection`, `GiveWeapon`, `SetAimTarget` | weapon control |
| **`SFXSeqEvt_ImpactedByPower`**, `SFXSeqEvt_Ragdoll` | combat stimulus |

`SFXSeqEvt_LevelIsLive` is the one worth flagging: ME1 has no equivalent name, and "the level is
loaded and live" is exactly the condition a joining player must wait for. In ME1 that has to be
inferred from state; in ME3 there is a named event.

## What this means for the port

1. **The 86-class intersection is the portable base.** Hook those and the logic transfers.
2. **Firing/acting differences are compositional, not naming.** ME1 gates firing through
   `SetInputTrap`, `HideAllWeapons`, `BusyState` and `StartFiring`; ME3 through
   `ToggleCombatPawn` and combat zones. A mod that looks for one name will find it in one game.
3. **`BioSeqAct_NonCombatArea` and `BioSeqCond_InCombat` are in all three** — the portable way to
   ask "is this a place where the player can act".
4. **`BioSeqAct_ModifyPropertyPawn` is in ME1 and ME2 but NOT ME3** (verified against the
   identifier sets: `yes / yes / -`). It is the escape hatch that makes name-matching insufficient —
   a level can write an arbitrary pawn property without using any named gate — and it is absent
   from the newest build, which suggests ME3's `ToggleCombatPawn` and combat zones replaced it.
5. **ME1's `BioSeqAct_SetPlayerControlling` and `BioSeqEvt_PlayerInput` are the most interesting
   non-portable names** and deserve a live test — they are engine-supported control and input
   hooks, which is precisely what a second player needs.

## What a level package actually contains (measured)

Important distinction that the counts above hide: **class names live in the script packages,
instance names live in the level packages.**

| Package | decompressed | identifiers | event *class* names |
|---|---|---|---|
| ME1 `BIOA_CRD00.pcc` | 27,454,501 | 77,735 | **58** |
| ME1 `BIOA_END00.pcc` | - | - | **58** |
| ME1 `BIOA_CRD00_00_DSG.pcc` (streaming) | 78,384,293 | 34,359 | 32 |
| ME3 `BioA_Cat002.pcc` | 652,531 | 1,375 | **0** |
| ME3 `BioA_Cat002_100Intro.pcc` | 6,128,798 | 22,779 | **0** |
| ME3 `BioA_Cat002_050Shuttle.pcc` | - | - | 20 |

The ME1 level carries 58 event class references; the ME3 level carries **none** and instead holds
`Main_Sequence` and `Sequence` — the **instance** names of its Kismet objects. Both are useful, and
they answer different questions:

* ME1-style level: "this level *uses* 58 kinds of event" — a class inventory per level.
* ME3-style level: "this level has a sequence called `Main_Sequence`" — a handle to the actual
  object, which is what a mod would read at run time.

Neither gives class→instance attribution, because the export table is not parsed. But the practical
consequence is clear: **to know what fires in a specific level, the script package tells you the
vocabulary and the level package tells you the handles.** A tool that only reads script packages
will conclude ME3 levels contain no events, which is wrong.

Zero event names in `BioA_Cat002.pcc` is not an error — that package is 652 KB and holds 1,375
identifiers, i.e. it is a small leaf level whose Kismet lives in the intro/shuttle sub-levels.

## Reproduce

    python -I tools/melepkg/mele_script_map.py --package <SFXGame.pcc> --out local/MELE/maps/ME1.json
    python -I tools/melepkg/mele_script_map.py --diff local/MELE/maps/ME1.json local/MELE/maps/ME3.json
    python -I tools/melepkg/mele_script_map.py --package <BIOA_*.pcc> --events --limit 60

The last form is per level: `BIOA_CRD00.pcc` alone carries 58 event class names, i.e. the events a
single level actually uses, which is a much shorter list than the whole build's.

## Limits

- **Inventory, not attribution.** A name here exists *somewhere* in the build. Which class declares
  it, and whether a given level instantiates it, is not established — the export table is not
  parsed yet. Treat every entry as "this lever exists in this game", not "this level uses it".
- The behaviour columns are `[derived]` from names. `BioSeqAct_SetInputTrap` trapping input is
  implied by the name, not observed.
- Per-level counts are real (the 58 above), but which *instances* of those classes a level uses
  requires the export table.
