# LAN co-op: a design that can run a full career

Scope from the owner: **LAN-connected co-op for all three games**, host keeps Shepard, client
takes a squadmate, **LE3 first, then LE2, then LE1**, functional on paper for a **full career
playthrough** — not a demo map.

Everything below is built on facts measured from the packages on disk, and every claim is labelled
with how strongly it is held. Where the plan has a hole, the hole is stated rather than smoothed
over: this page exists to be attacked.

| label | means |
|---|---|
| **measured** | read from the packages; a test pins it |
| **derived** | follows from measurements plus engine knowledge, not directly observed |
| **unknown** | not established; a hole |

## 1. Why LAN, and what "LAN" changes

The game has **no campaign netcode**. ME3 shipped a separate wave-mode multiplayer with its own
master server; LE cut it, and LEMP restores it. Campaign co-op therefore has no network layer to
reuse, and this design does not pretend otherwise: it adds one.

What *does* survive is worth stating precisely, because it changes the size of the job. **The entire
UE3 replication model is still in the packages** (measured, all three builds):

* `Actor` declares `Role`, `RemoteRole` (both `ByteProperty`), `NetPriority`, `NetUpdateFrequency`
  (both `FloatProperty`), `bNetTemporary`, `bNetInitial`, `bNetOwner`, `bAlwaysRelevant`,
  `bOnlyRelevantToOwner`, `bReplicateMovement`, `bTearOff`, `NetTag`.
* `ENetRole` and `ENetMode` are real enums on ME1/ME2 (`ROLE_None`, `ROLE_SimulatedProxy`,
  `ROLE_AutonomousProxy`, `ROLE_Authority`; `NM_Standalone`, `NM_DedicatedServer`, `NM_ListenServer`,
  `NM_Client`). ME3 keeps `ENetMode` and folds `ENetRole` into a native type.
* `PlayerController` carries **117 net-named functions on ME1, 113 on ME2 and ME3**, including the
  full `Client*` remote-event family (`ClientAdjustPosition`, `ClientGotoState`, `ClientRestart`,
  `ClientSetLocation`, `ClientMessage`, `ClientPlayCameraShake`, …) and `ServerMove`,
  `ClientUpdatePosition`, `ServerChangeTeam`, `ServerSuicide`.
* `GameInfo` declares the whole listen-server admin surface in all three:
  `Login`, `PostLogin`, `PreLogin`, `Logout`, `SpawnPlayerController`, `RestartPlayer`, `SetGameType`,
  `InitGame`, `StartMatch`, `ChangeTeam`, `InitGameReplicationInfo`, `HandleSeamlessTravelPlayer`.
* `GameReplicationInfo` still has the `GameClass` ClassProperty in all three.
* The exes still contain the **replication runtime diagnostics** —
  `"Attempt to replicate property '%s.%s' in C++ but class '%s' is not a replicated class"`,
  `"Attempt to replicate function"`, `UNetDriver::LowLevelSend`, `UNetConnection::LowLevelDescribe`,
  `UChildConnection`, `UPendingLevel`, `UGuidCache`. Those strings only exist if the replication
  code is compiled in.

What is **gone** is the *transport*, and it is gone in a specific way:

| | ME1 | ME2 | ME3 |
|---|---|---|---|
| `TcpNetDriver` / `DemoRecDriver` as a **script class export** | **absent** | **absent** | **absent** |
| those names in the exe | `UTcpNetDriver`, `UDemoRecDriver` (native) | same | same |
| `engine-ini:Engine.Engine.NetworkDevice` key | present in exe; `BIOEngine.ini` sets `NetworkDevice=IpDrv.TcpNetDriver` | present in exe; `BIOEngine.ini` sets the same | present in exe; **ME3 ships no `BIOEngine.ini`**, so the key is unset |
| `IpDrv.pcc` ships | yes (23 classes) | yes (23) | yes (22) |
| `TcpNetDriver` **class** inside it | **no** | **no** | **no** |
| ME3 extra | – | – | `SFXOnlineFoundation.SFXOnlineNetDriver` referenced from the exe, and **not defined in any package** |

So the class name is in the code (as a native C++ class), the config key that names it is in the code,
and the *class* is not in any shipped package — because it is native. Only ME3 points its
`NetworkDevice` at a custom subclass, `SFXOnlineNetDriver`, which is likewise native.

**This does not mean there is no working transport.** LEMP restores ME3's horde wave mode over its own
master server and P2P, live, and it does so by patching in memory — so **ME3's native
`SFXOnlineNetDriver` path still works under the LE build** (see `README.md` and `NOTES.md`, which
records that LEMP's in-memory patches cannot be refuted by the static check that was run).

Better than that: LEMP's own research probe settles the question this page was going to leave open.
`repl_probe.cpp` stage 2a established that **`open BioP_MPCer?listen` binds a UDP socket on 3659 with
no patching at all** — it is the game's own console command and the game's own native driver. Stage 2b
then asks whether UE3 replication carries a second instance running `open 127.0.0.1`, with the pass
condition stated in advance: *a second `NetConnection` on the host*, and *a second
`PlayerReplicationInfo`* as the stronger result. So **a listen server is not something co-op has to
build, on ME3 at least** — it is something co-op has to *start on a campaign map*.

Two details from that file that change this design:

* **The port is 3659, not 7777.** ME1/ME2's `BIOEngine.ini` says `Port=7777`, and nothing in any exe or
  `Coalesced` file contains `3659` — LEMP passes it in the travel URL (`?port=3659`). So a LAN mod must
  carry the port in the URL, and must not trust `Port=`.
* **The client-side join fails silently, at a known instruction.** `loadmap_hook.cpp` documents that
  after a clean handshake the client's pending level completes, `NotifyReceivedText` accepts all 197
  advertised packages, and then the client returns to the main menu with no net driver and no pending
  level while the host still shows the connection live — no message box, nothing in any log, because
  `UGameEngine::Tick`'s `LoadMap` call at rva `0x5502c0` discards its `Error` out-parameter on the way
  back to the menu. Step 6 of §7 will hit exactly this, and it will hit it silently.

The honest state of the transport question is therefore **three different answers**, and the design
must not assume they are the same:

| build | transport state | label |
|---|---|---|
| ME3 | native driver; `open <map>?listen` binds UDP and carries a peer | **measured, live** (LEMP) |
| ME2 | `NetworkDevice=IpDrv.TcpNetDriver` names a class in no package; native `UTcpNetDriver` is in the exe | **unknown** — never tried |
| ME1 | same as ME2, plus no `BIOGame.ini` at all | **unknown** — never tried |

Even with ME3's answer, one gap remains: **the proven listen server is wave mode's.** It is started by
`open BioP_MPCer?listen` on an MP map, with the MP game mode present. A campaign level does not start
one, so campaign co-op still has to make it happen there. That is a much smaller job than writing a
transport, and it is why §2's `AddMutator`/`BaseMutator` hook plus `GameEngine.CreateNamedNetDriver` —
a real function with real bytecode on all three builds, taking a `NetDriverName` — is the right seam to
investigate next.

**LAN is the right first target** because it removes three problems that are not game problems:
NAT traversal, matchmaking identity, and trust. Two machines on one switch have a known address
each, sub-millisecond latency, and no need for a relay. Every hard thing that remains is engine-side,
which is where the work is.

`networking: lan` also matches this project's rule that a session's difficulty must be mine and not
someone else's account: no Steam login, no relay service, no master server to keep alive.

## 2. The architecture, and the one fact that carries it

**`Mutator` is the extension point, and it is identical on all three builds.** This is the single
most important measurement in the file:

* `GameInfo` has no `MutatorList` array — the usual UE3 way — but it does have a linked list:
  `BaseMutator: ObjectProperty`, `AddMutator`, `RemoveMutator`, `AllowMutator`. **All three builds.**
* `AddMutator(mutname: Str @0x00, bUserAdded: Bool @0x10, mutClass: Class @0x18, mut: Object @0x20,
  I: Int @0x28)` — parms block **`0x2c`, byte-identical on ME1, ME2 and ME3**.
* `Mutator` declares the **same 32 hooks** on every build, including the ones co-op needs:
  `ModifyPlayer`, `NotifyLogin`, `NotifyLogout`, `CheckReplacement`, `InitMutator`,
  `PreBeginPlay` — and `GetSeamlessTravelActorList`, which is **declared but never called by this
  campaign**, because no build enables seamless travel (§4).

So the design is: **one co-op mutator, written once, added to the `BaseMutator` chain of whatever
`GameInfo` the level constructs.** No per-game rewrite at this layer. Everything game-specific sits
*above* it in a small per-build table (section 5) and *below* it in the transport.

Because there is no seamless travel and `GameInfo` is re-constructed per level, the mutator must be
re-added on **every** level, not carried across one. That is the single most important consequence of
§4 for this diagram — the mutator is a per-world install, not a per-session one.

```
┌─────────────────────────────────────────────────────────────┐
│  host + client                   (one binary, role by flag) │
├─────────────────────────────────────────────────────────────┤
│  CO-OP MUTATOR            same 32 hooks on all 3 builds     │
│    re-added to BaseMutator on EVERY level (no seam. travel) │
│    ModifyPlayer    → give it a controller, possess a pawn   │
│    NotifyLogin     → admit a second participant             │
│    CheckReplacement / PreventDeath → death + revive policy  │
├─────────────────────────────────────────────────────────────┤
│  RE-CREATE THE CLIENT PAWN after each hard reload (none of  │
│  GetSeamlessTravelActorList carries it)                     │
├─────────────────────────────────────────────────────────────┤
│  PER-BUILD TABLE          ~20 names, measured (section 5)   │
├─────────────────────────────────────────────────────────────┤
│  TRANSPORT     the game's own native driver, started via     │
│                `open <map>?listen`; UDP, port in the URL     │
└─────────────────────────────────────────────────────────────┘
```

An ASI loader was built and load-tested against the real game (see `NOTES.md`, "ASI loader: built and
load-tested"), so the mutator can be installed without patching a shipped file. **Its source is not in
this repository** — `NOTES.md` references `tools/binkproxy/`, which does not exist here — so anyone
rebuilding it must write the loader from that description: 76 generated `mov rax,[slot]; jmp rax`
stubs, one per stock Bink export, with slot fill by name from `bink2w64_original.dll`. That is a gap
worth closing.

## 3. What must be decided before anything is coded

Three decisions, and the first two are the reason this page exists.

### 3.1 Authority: whose game is it?

Two candidates, and they are not equivalent.

**A. Host-authoritative, client mirrors.** The host runs the campaign. The client sends input and
receives state; the host's engine is the only one that advances the story. The client's pawn for the
squadmate is a *remote puppet* whose authoritative position comes from the host. (Note: on LE1/LE2
the pawn cannot be possessed via a second `LocalPlayer` — that faults, see §3.1a — so the client is a
remote machine, and on a single PC the supported configuration is two processes.)

**B. Both machines run the campaign and we keep them in step.** Two full simulations, reconciled.

**A is the design.** B cannot be made to work for a full career: a career is hundreds of levels,
each with Kismet sequences, conversation nodes, appearance 2DAs and save state, and two independent
simulations will diverge at the first node either machine evaluates differently. B also doubles the
desync surface to everything, including things this project has never looked at.

A has its own cost: **the client must be able to render a level whose story it is not advancing.**
That needs the level loaded, the actors spawned, and the pawn present — and because there is no
seamless travel (§4), it needs that **re-established after every level change**, from scratch:
`ModifyPlayer` to hand out a controller, then a spawn and possess in the new world. `GetSeamlessTravelActorList`
does not help, because nothing calls it.

*Label:* **derived**. Nothing in the packages says "authoritative"; this is a design choice informed
by what the engine can express.

### 3.1a The first blocking decision: does the client need a second `LocalPlayer`?

This is the decision the whole design rests on. **An earlier draft of this section got it wrong** by
recommending split-screen first on the grounds that the `AddLocalPlayer` substrate is present. It is
present — and it does not work. Recorded here rather than deleted, because "the API exists" and "the
API can be called" turned out to be different claims.

The substrate *is* there, measured on all three builds: `GameViewportClient` declares
`AddLocalPlayer`, `RemoveLocalPlayer`, `CreatePlayer`, `CreateInitialPlayer`, `LayoutPlayers`,
`NotifyPlayerAdded`, `RemovePlayer`; `LocalPlayer` is a real class; `SFXGameViewportClient` overrides
none of them; `GameEngine.IsSplitScreen()` exists; and `SplitscreenData.PlayerData` is an array of
`PerPlayerSplitscreenData` pane rectangles.

But this project has already **called it live**, and it faults:

| attempt | game | `bSpawnActor` | result |
|---|---|---|---|
| 1 | LE2 | 1 | crash, `ucrtbase!aligned_realloc`, 7 engine frames past `ProcessEvent` |
| 2 | LE1 | 1 | crash `+0x99611a` (`mov r10,[rax+0x90]`, `rax=0`), on the engine's own thread, after the call returned |
| 3 | LE1 | 0 | crash, `ucrtbase!aligned_realloc`, synchronously inside `CreatePlayer` |

Three attempts, two games, both `bSpawnActor` settings, so the fault is in **creating and registering
the `LocalPlayer` at all** — the login path — not in giving it a pawn or a view. See
`SPLITSCREEN-PLAN.md` for the full analysis and `SECOND-PLAYER-ME2.md` for the correction to the
original "no `AddLocalPlayer` anywhere" claim.

**So the conclusion inverts.** Split-screen is not the cheap first milestone; it is blocked on a
crash this project has already hit three times. The route that avoids it entirely is ME3SquadCoop's,
which is also the scoped design: **a possessed squadmate with its own camera, never a second
`LocalPlayer`.**

That changes what "host and client" means on a single machine. Two *processes* is the supported
configuration (see `TEST-ENVIRONMENT.md`); two *panes in one process* is not. For LAN that is
immaterial — the client is on another machine either way — but it means **split-screen cannot be the
testing strategy**, and the QA plan has to be two processes on one PC or two PCs.

*Label:* the crash table is **measured** (live, three times). The substrate table is **measured**
(offline). "A `LocalPlayer` cannot be registered as a second player on LE1/LE2" is **derived** from
the three crashes, and it is the strongest-supported reading of them.

## 3.2 The squadmate is a *second* squadmate, not an existing one

The scoped design says "client takes a squadmate". For a full career that is a hole, because a squad
is **two** members and the campaign's script talks to them by slot. Options:

| | what the client gets | cost |
|---|---|---|
| **S1** client drives squadmate slot 0 | the campaign's own squadmember, shared | the host loses a companion; every script expecting slot 0 on the host now sees a remote pawn |
| **S2** client is a third member | a new pawn, outside the campaign's two slots | story beats that count the squad ("all squadmates present") will not know about it; conversations will not involve it |
| **S3** client replaces the host's slot-1 companion, and the campaign gets a replacement for slot 1 | one real slot | requires spawning a *replacement* companion, and the campaign's `BioPlayerSquad` bookkeeping must accept a swap |

**Recommendation: start with S2, design for S3.** S2 is the only one that cannot corrupt the host's
campaign state, which matters because the first thing to get working is a full career's *shape*, not
its dialogue. S3 is the real answer and needs `BioPlayerSquad` (present in all three, measured) plus
the henchman spawn path — which is per-build (section 5).

**What the packages now say about this choice (measured, 2026-10-30):** the roster is a plain `TArray`
with no declaration capping it at two, and `AddMember` exists on `BioBaseSquad` on all three builds —
so nothing in the file structure forbids a third entry. What differs is the **element**:
`TArray<MemberData>` on ME1/ME2 (a struct carrying `SquadMember` plus per-member order state, 17
fields on ME1 and 7 on ME2) versus `TArray<Object>` on ME3, where `MemberData` does not exist. So S2
is three different edits, and ME3's is the simplest because there is no orders struct to fill in.

*Label:* the container shapes are **measured**. Whether a third entry is **tolerated** at runtime is
**unknown** and is the top thing to test once a client can move at all.

### 3.3 Save, and what happens when the client leaves

The save systems are **structurally different per build** (measured): LE1 has `BioBaseSaveObject`
subclasses only; LE2/LE3 add `SFXSaveGame`; LE3 adds `SFXOnlineSaveGameMP`. The host's save is the
career. The client keeps no campaign state at all — it is a controller attached to the host's world.

A client drop is therefore a **non-event for the career**: the host removes the puppet, the
squadmate reverts to the host's AI (S2/S3) or the slot returns to host control (S1). This is the
main argument for host-authoritative: it makes the client disposable, and a player who crashes at
hour 20 of a career does not lose it.

## 4. What must be synced, per subsystem

This is where "functional on paper" is decided. Each row states what crosses the wire and what does
not, and what breaks if it does not.

**One structural fact changes every row below it: there is no seamless travel.** `bUseSeamlessTravel`
is declared on `Engine.GameInfo` in all three builds and is **serialised by zero of 6,685
exactly-closing class default objects** (measured; see `NOTES.md`). Un-set means the UE3 default,
False. `SeamlessTravelTo` is not overridden on `BioPlayerController` on any build, while
`PreClientTravel` is. So every level change is a **hard reload that constructs a new `GameInfo`**, and
the client's pawn does not survive it — it must be destroyed and re-created on the far side.

| subsystem | crosses the wire | does not | if it does not work |
|---|---|---|---|
| **movement** | the puppet's position, rotation, velocity, animation state | host's own movement (authoritative) | client pawn is visibly wrong, but input is unaffected |
| **aim/fire** | input state, fire requests | projectile spawn (host spawns, replicates) | shots appear late or not at all |
| **powers** | target + power id request; host resolves | cooldown timers (host owns) | double-fire or no fire |
| **enemies** | host only | client never simulates AI | client sees enemies as puppets; acceptable |
| **conversation** | host drives `BioConversation`; client receives camera + node | client choices (unless host's turn) | client cannot pick dialogue — **acceptable for S2, a hole for S3** |
| **map events / triggers** | host drives; client receives state | client never fires Kismet | client walking through a trigger does nothing — this is the **deliberate** design, see `TRIGGER-DELAY.md` |
| **galaxy map / travel** | host only; client follows | client never starts a jump | client is frozen or in a stale level if the host travels — and the pawn **is** destroyed, see below |
| **saves** | host only | client has none | client crash costs nothing |
| **cinematics / matinee** | host drives | client is a passenger | looks right |
| **level change** | the host's URL only | nothing carries automatically | **the client pawn must be re-created from scratch** — there is no seamless travel (§4) |

Three rows are the holes, and they are stated as such:

* **conversation choices.** In S2 the client is a passenger in every conversation. That is honest but
  thin; a full career has hundreds of them and the client would never choose anything.
* **galaxy map travel.** The host jumping systems is a level transition for the client too — and it is
  a **hard reload**, not seamless: `bUseSeamlessTravel` is False on every build (§4), so no
  `GetSeamlessTravelActorList` is consulted and nothing is carried. The client's puppet is destroyed
  with the old world. Re-creating it on the far side is a per-transition step the mod must perform,
  not something the engine will do.
* **trigger gating.** The client's walking must *not* fire Kismet, or the host's story advances from
  a machine that is not authoritative. `EWhoTriggers` already has `WT_PlayerOnly` (value 0 on all
  three, measured) and ME3 adds `WT_PlayerOnlyLocal` at 1 — **and ME3 renumbers everything after it**,
  so the same integer means a different policy per build. See `TRIGGER-DELAY.md`.

## 5. The per-build table, measured

Everything a port needs to differ per game. All rows confirmed from the install index, not from
memory.

| class / fact | ME1 | ME2 | ME3 | consequence |
|---|---|---|---|---|
| `Mutator` hooks | same 32 | same 32 | same 32 | **one implementation** |
| `AddMutator` parms | `0x2c` | `0x2c` | `0x2c` | same call, same layout |
| `GetSeamlessTravelActorList` | declared on `PlayerController`, `GameInfo`, `Mutator` | same | same | **declared but never called** — no build enables seamless travel |
| `bUseSeamlessTravel` (declared) | yes | yes | yes | …but **set by 0 of 6,685 CDOs**: campaign travel is a hard reload |
| `MaxPlayers` / `MaxPlayersAllowed` on the campaign game class | not set (inherits 16/32) | not set (16/32) | **`MaxPlayersAllowed=1`** | ME3 needs the clamp raised; ME1/ME2 have nothing to raise |
| `BioBaseSquad.Members` element type | `TArray<MemberData>` | `TArray<MemberData>` | `TArray<Object>` | adding a roster entry is a **different edit per build** |
| `MemberData` struct | 17 fields | 7 fields | **absent** | ME1's per-member order state has no ME3 counterpart |
| `BioPlayerSquad` / `BioBaseSquad` | yes | yes | yes | squad container is shared |
| squadmate pawn | *(none)* | `SFXPawn_Henchman : SFXPawn` | `SFXPawn_Henchman : SFXPawn_PlayerParty` | **per-build**, and ME3 re-parents it |
| henchman AI | `BioAI_Henchman : BioAI_Core` | `SFXAI_Henchman : SFXAI_Core` | `SFXAI_Henchman : SFXAI_Cover` | **per-build**, different name *and* ancestor |
| `SFXPawn_Player` parent | `BioPawn` | `SFXPawn` | `SFXPawn_PlayerParty` | player pawn ancestry differs on every build |
| `SFXPlayerController` | **absent** | yes | yes | LE1 has no such class at all |
| `SFXGame` | **absent** | yes | yes | LE1's game class is named differently |
| `GameClass` (on `GameReplicationInfo`) | yes | yes | yes | shared way to name the game class |
| death / revive classes | **1** | **8** | **5** | largest surface is LE2 |
| `m_fRevivalRange`, `m_fPercentHealthOnResurrection` on `BioPlayerSquad`/`BioBaseSquad` | **yes** | **yes** | **yes** | **all three builds**; a per-player revive radius already exists |
| `SFXCameraMode_Spectator` | – | – | yes | LE3's spectator camera |
| `BioSpectator` / `BioSpectatorMode` | – | yes | – | **a photo-mode camera set, not a revive spectator** |
| `SFXCustomAction_Revive` | – | – | yes | **LE3-only revive action** |
| `BioSeqAct_ResurrectHenchman` | yes | yes | yes | shared, but it is a *Kismet* action, not a per-player revive |
| `SFXCameraMode_SplitScreenCombat` | – | – | yes | LE3-only |
| galaxy map handler | `BioSFHandler_GalaxyMap` | + `BioSFHandler_PCGalaxyMap` | + `BioSFHandler_PCGalaxyMap` | LE2/LE3 have a PC-specific handler |
| save classes | `BioBaseSaveObject` tree | + `SFXSaveGame` | + `SFXSaveGame`, `SFXOnlineSaveGameMP` | structurally different per build |

### The three holes this table exposes

1. **Revive is present on all three builds, but per-player revive exists on none of them as a
   *mechanic*.** `m_fRevivalRange` and `m_fPercentHealthOnResurrection` are on `BioPlayerSquad` /
   `BioBaseSquad` in all three builds (measured), so the radius and the health grant already exist —
   what does not exist is a *downed second player* for that radius to act on. LE1's only
   resurrect capability is `BioSeqAct_ResurrectHenchman`, a Kismet action that resurrects a
   squadmate because a level script says so; ME3 has `SFXCustomAction_Revive` and
   `SFXGameModeDying`. Note that **ME2's `BioSpectator`/`BioSpectatorMode` is a photo-mode camera
   set, not a revive spectator** — an earlier draft of this page said otherwise and it was wrong.
   The co-op layer therefore has to *supply* the downed state and the revive prompt, while reusing
   the existing range and health values.

2. **LE1 has no `SFXPlayerController` and no `SFXGame`.** Anything that names those classes — which a
   lot of the existing project notes do — is ME2/ME3-only. LE1's controller is `BioPlayerController`
   (shared, measured) and its game class is named differently (see §5b). A per-build table is not
   optional.

3. **The squadmate pawn does not exist on LE1.** `SFXPawn_Henchman` is ME2/ME3 only, and ME3
   re-parents it from `SFXPawn` to `SFXPawn_PlayerParty`. So "client takes a squadmate" on LE1 has to
   use a plain squadmember pawn, and on ME3 any ancestry test written against ME2 gives the wrong
   answer.

## 5b. The game class is selected two different ways, and one of the two is unusable

This was the open hole for two sessions ("where does ME1's game class come from"). It is now closed.

All three builds ship the **same** routing mechanism in `Engine.pcc`, byte-for-byte the same
property names and the same function bodies:

* `GameInfo.DefaultGameType` (StrProperty) and `GameInfo.DefaultMapPrefixes` (TArray of the script
  struct `GameTypePrefix`) — **declared on `Engine.GameInfo` in all three builds.**
* The struct `GameTypePrefix` has the **identical 5 fields** on ME1 and ME2, and ME3 adds 2:
  `Prefix`, `GameType`, `AdditionalGameTypes`, `ForcedObjects`, `bUsesCommonPackage`
  (+ ME3-only `OverrideCommonPackage`, `bIsMultiplayer`).
* `GameInfo.GetDefaultGameClassPath(MapName, Options, Portal)` is an `EX_LocalVirtualFunction`
  stub with a **19-byte body identical on all three builds**, and it is declared on exactly one
  class in the whole install: `Engine.GameInfo`. Nothing overrides it.
* Every exe reads `engine-ini:Engine.GameInfo.DefaultGame` and
  `engine-ini:Engine.GameInfo.DefaultServerGame`.

But **only ME3 actually uses the property path**, by way of `BIO_COMMON.pcc`'s
`SFXGameInfoSP : SFXGame`:

| | ME1 | ME2 | ME3 |
|---|---|---|---|
| `DefaultGameType` on the live CDO | absent | absent | `SFXGameContent.SFXGameInfoSP` |
| `DefaultMapPrefixes` rows | absent | absent | 3 (`EntryMenu`, `BIOG_UIWorld`, `Bio`) |
| where the game class is named | **not in any file** | `BIOGame.ini` `[Engine.GameInfo] DefaultGame=SFXGame.BioSPGame` | `BIO_COMMON.pcc` CDO + `DefaultMapPrefixes` |
| campaign game class | `BioSPGame : Engine.GameInfo` | `BioSPGame : SFXGame` | `SFXGameInfoSP : SFXGame` |

**ME1 ships no `BIOGame.ini` at all** — its only config files are `BIOEngine.ini` and
`GamerSettings.ini` — and a grep of both finds no `DefaultGame`, no `DefaultMapPrefixes` and no
`DefaultGameType`. The name string `game-ini:Engine.GameInfo.DefaultGame` *is* in ME1's exe, so the
engine will read the key if a file provides it; the shipped install simply provides none and the game
still starts. So ME1's game class is chosen in code, and the two candidate call sites
(`CreateNamedNetDriver`, `SetGameType`) take a **package index** — proven, because each one's
operand equals its own export index — not a name.

*Label:* the table is **measured**. "ME1 selects its game class in native code" is **derived** from
two measurements (no config key anywhere, and no script function resolves "Engine.GameInfo" +
"DefaultGame" — verified against the name table, where that concatenation does not occur as an
FName).

### 5c. The consequence for co-op: do not use the property path on ME1

`GameInfoClassAliases` and `DefaultMapPrefixes` are **the wrong hook** for the host-authoritative
design, on every build, for three reasons:

1. They are consulted only when a map change has no explicit game class. The design needs the *most
   specific* class in the chain, and ME3's `DefaultMapPrefixes` maps every gameplay level to
   `SFXGameInfoSP` — a subclass of `SFXGame`, not a replacement for it.
2. The exe lookup for ME1's class is native and takes a package index, so nothing here can be
   redirected by editing a property.
3. `DefaultMapPrefixes` is at **+0x354 (ME3) / +0x370 (ME1, ME2)** in the CDO tag stream, i.e.
   below both `DefaultGameType` and `DefaultPawnClass`. Writing it means rewriting an object's tag
   stream, not setting a config value.

The right hook is the one already identified and measured: **`AddMutator` / `BaseMutator`, which is
byte-identical on all three builds** (see §2). The per-build difference that actually matters is the
game class *name*, and that is a table with three rows, not a mechanism.

## 6. The transport

Small on purpose. LAN, no relay, no accounts.

```
message := u8 type | u16 length | payload

client → host   INPUT      (tick, move axis, look delta, buttons)
                REQUEST    (fire, power, interact, revive-target)
                ACK        (last authoritative frame applied)
host → client   SNAPSHOT   (tick, puppet transform + anim state, squadmates, nearby actors)
                EVENT      (level change beginning, conversation node, objective)
                CORRECT    (authoritative state after a rejected client action)
```

* **UDP for snapshots**, unsorted and lossy; a late snapshot is worthless.
* **TCP for events**, because a missed "level change beginning" strands the client.

**Both of those are wrong for this engine, and LEMP's evidence says so.** The driver that actually
binds is UDP on port **3659** (`repl_probe.cpp`), and it is created by the game's own
`open <map>?listen` rather than by anything a mod sets up. ME1/ME2's ini says `Port=7777` and no file
in any install contains `3659`, so the port comes from the travel URL. So:

* **the transport is the game's own native driver**, carrying UE3 replication. The co-op layer
  chooses *what* replicates (via `bAlwaysRelevant`, `Role`/`RemoteRole`, and the `Client*` remote
  functions), not *how* it is carried;
* **the port and the listen flag live in the travel URL** — `?listen` and `?port=<n>` — because that
  is where the engine reads them;
* **UDP-only** is therefore the real constraint, and reliable delivery for events has to be built on
  top of it (sequence numbers plus re-send), not obtained by switching protocol.

* **The host is the clock.** Every snapshot carries the host's tick, and the client's input carries
  the last tick it saw. There is no clock negotiation on a LAN.
* **Nothing is trusted from the client.** A shot is a *request*; the host decides.

The bridge protocol in `tools/bridge/PROTOCOL.md` already defines a command channel into the game
(`ue3.*` methods) and is the natural place to attach this, but it is a *test* bridge — the shipping
transport should not go through a debug channel.

## 7. The order of work, and what each step proves

Each step is chosen to be testable **without a human playtest**, per this project's QA rule. Note
that step 2 is *not* "add a second `LocalPlayer`": that was tried live three times and faults on
LE1/LE2 (§3.1a), so the client's controller comes from the squadmate route instead.

| # | step | proves | how it is tested |
|---|---|---|---|
| 1 | mutator installs on all three builds | `AddMutator` + `BaseMutator` work as measured | log from `InitMutator` + `PreBeginPlay` in each game |
| 2 | a client-owned **squadmate** pawn exists and is possessed | the `SFXPawn_Henchman`/squad path works without a second `LocalPlayer` | pawn count, `IsPlayerControlled`, possession log |
| 3 | that pawn moves from recorded input | input reaches a pawn | replay a fixed input tape, compare host-side transform to the tape |
| 4 | re-create the client's pawn after a **hard reload** | the per-transition spawn path works at all | pawn exists and is possessed in the new world; **not** a seamless-travel test, because there is none |
| 5 | survive a **chain** of transitions | state carries | a scripted 5-load run with a state checksum per level |
| 6 | the same over a LAN socket | the transport carries what step 3–5 proved locally | the §6 protocol over two processes, then two machines |
| 7 | a full career | everything above holds for hours | the goal; not yet attempted |

Step 5 is the one that separates "works" from "works for a career". A single transition can pass by
luck; five in a row with a checksum cannot. Step 6 is deliberately last among the mechanics: proving
the transport before the thing it carries is how a project ends up debugging two unknowns at once.

**Step 4 is not a `GetSeamlessTravelActorList` test, and calling it one was a mistake in an earlier
draft of this page.** No build enables seamless travel (§4), so the function is never called and
there is no travel to "survive": the client's pawn is destroyed with the old world and must be spawned
and possessed again. The check is therefore *re-creation*, and a passing step 4 that only verified a
surviving actor would be testing a code path the game does not use.

**Step 6 has a known failure mode, already documented, and it is silent.** `loadmap_hook.cpp` records
that after a clean handshake the client's pending level completes, all 197 advertised packages are
accepted, and the client then returns to the main menu with no net driver and no pending level while
the host still shows the connection live — no message box, nothing in any log, because
`UGameEngine::Tick`'s `LoadMap` call (rva `0x5502c0` on the current ME3 build) discards its `Error`
out-parameter on the way back to the menu. Anyone attempting step 6 should hook vtable slot `0x3e8` on
`UGameEngine` and read that string *before* concluding anything about replication. A silent return to
the menu is not a netcode failure and must not be debugged as one.

## 8. What is unknown, stated plainly

* **Whether a third squad-roster entry is tolerated.** The container does not cap it (measured), but
  the campaign's own bookkeeping may. Top unknown.
* **Whether the host's Kismet tolerates a second possessed pawn in the level** — it may fail an
  assertion nobody has hit yet.
* **Where ME1 selects its game class in native code.** The *fact* that it does is measured (no
  config key anywhere, no script function that resolves the name pair); the call site is not.
* **Whether any of the three builds will start a listen server on a CAMPAIGN map.** ME3's driver is
  proven live in wave mode (`open BioP_MPCer?listen` binds UDP 3659 and carries a peer), which is a
  different question from whether a campaign level creates one. ME1/ME2 have never been tried, and
  both name a `TcpNetDriver` class that is in zero of the 12,704 packages.
* **Conversation participation** for the client.
* **Nothing here has been run.** Every row in §5, §5b and §5c is read from files; none is a live
  result.

## 9. The honest summary

The architecture is sound and rests on one strong measurement — the `Mutator` surface is identical on
all three builds, so the co-op layer can be written once. The design is host-authoritative, which
makes the client disposable and the career safe.

The 2026-10-30 measurements moved two questions from *unknown* to *answered*, and both answers
changed the plan:

1. **The UE3 replication model survived the LE cut intact.** All the `Client*`/`Server*` remote
   functions, `Role`/`RemoteRole`, `ENetRole`/`ENetMode` and `GameReplicationInfo.GameClass` are
   present on all three builds, and the replication runtime's own diagnostic strings are still in all
   three exes. The driver *class* is gone from the packages — it is native — but the driver itself
   works: `open <map>?listen` binds UDP 3659 with no patching, proven live by LEMP's probe. What a
   campaign co-op mod has to do is start it and choose what replicates, not write a transport.
2. **The game-class routing mechanism is shared by all three builds and only ME3 uses it.** The
   `GameTypePrefix` struct, `DefaultMapPrefixes`, `DefaultGameType` and
   `GetDefaultGameClassPath` are byte-identical across ME1/ME2/ME3, but ME1/ME2's CDOs leave the
   values empty and ME1's game class is chosen in native code. `DefaultMapPrefixes` is therefore the
   *wrong* injection point; `AddMutator`/`BaseMutator` is the right one, as §2 already said.

And one question moved the other way. Having found the `AddLocalPlayer` substrate present on all
three builds, an earlier draft of §3.1a concluded that split-screen was the cheap first milestone.
That was wrong: this project has already called it live and it faults on LE1/LE2, three times, with
both `bSpawnActor` settings. The substrate table was a measurement of *declaration*, and declaration
is not callability — the same mistake `SECOND-PLAYER-ME2.md` records from the opposite direction.
**Split-screen is not the testing strategy; the ME3SquadCoop route (a possessed squadmate with its
own camera, no second `LocalPlayer`) is.**

Three further measurements, all on the critical path, and two of them contradict earlier drafts of
this page:

3. **There is no seamless travel.** `bUseSeamlessTravel` is declared on `Engine.GameInfo` on all three
   builds and is serialised by **0 of 6,685** exactly-closing class default objects, so the UE3
   default (False) applies. `SeamlessTravelTo` is overridden on no Bio controller, while
   `PreClientTravel` is. Every level change therefore constructs a new `GameInfo` and destroys the
   old world, so the client's pawn must be **re-created** on the far side. Two sections of this page
   previously planned around `GetSeamlessTravelActorList`; they are corrected.
4. **ME3 clamps capacity to one.** The base `GameInfo` allows 32 players on every build, and ME3's
   campaign class serialises `MaxPlayersAllowed = 1` — an active clamp a co-op mod must raise.
   ME1/ME2 set neither field and inherit 32, so there is nothing to raise there.
5. **The squad roster changes container type on ME3.** `Members` is `TArray<MemberData>` on ME1/ME2
   and `TArray<Object>` on ME3, where the `MemberData` struct does not exist at all. Adding a roster
   entry is three different edits, and no declaration caps the roster at two.

The remaining holes are real: the downed-*player* state has to be supplied on all three builds (the
revive radius and health values exist; the state does not); LE1 has no `SFXPawn_Henchman`; ME3
re-parents the squadmate pawn and renumbers the trigger enum; conversation and galaxy-map travel are
passenger experiences in the S2 design. None of those is fatal to the architecture, and all of them
are the kind of thing that kills a project *after* someone has written a thousand lines assuming
otherwise.

That is what this page is for. Reproduce every claim in §1, §5, §5b and §5c with
`python -I tools/melepkg/mele_props.py routing` and `... coopcheck`.
