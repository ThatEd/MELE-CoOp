"""Catalogue and exercise every kind of command a Mass Effect Legendary Edition build exposes.

Why this tool exists, and why it is a catalogue first:

A co-op port fails on the commands nobody thought to check. The interesting ones are not
`SetLocation` - they are the second copy of a command the engine assumed only one player
would ever issue: a Kismet event that already fired, a `CanFireWeapon` that is false for a
pawn the CPU owns, a `SetCinematicMode` that freezes the world for both clients but was
written as if there were one viewer. So the unit of work is not "a test", it is **one entry
per command mechanism**, each carrying the evidence for whether the build even has it.

The catalogue is measured, not typed. Per-command presence comes from the decompressed
identifier tables in `local/MELE/maps/ME{1,2,3}.json` (see tools/melepkg). `verify` recomputes
that and fails when the two disagree, so a hand-edit cannot silently rot.

Modes, in increasing order of what they can cost you:

    catalogue   rebuild commands.json from the taxonomy + the map files   (offline)
    verify      re-check commands.json against the map files              (offline)
    list        print the catalogue, filtered                              (offline)
    phases      print the ordered test area                                (offline)
    probe       resolve every command in a RUNNING game, read-only         (no writes)
    plan        turn a probe result into an ordered dispatch plan          (offline)

`probe` opens the process with `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION` only and calls
nothing. It answers the question the offline identifier table cannot: *which class owns this
function, what are its parameters, and is it the copy I intend* - because a name is not unique
in a UE3 build and the wrong `SetLocation` is a silent no-op that reads as a gate.

**There is deliberately no `run` mode here.** `plan` produces the ordered dispatch work, and
the in-game bridge (tools/bridge) executes it on the game's main thread. That split is not
fussiness: `ProcessEvent` runs on the game thread and its vtable slot differs per game
(LE1 +0x238, LE2 +0x228, LE3 +0x230 as a wrapper), and LE1/LE2 crash inside `CreatePlayer`
from their own thread after a correct call. Anything that dispatches has to be in-process, on
the main thread, with the parms block it verified - so Python decides what should happen, and
the bridge does it.

Usage:

    python -I tools/ue3live/ue3_command_suite.py list
    python -I tools/ue3live/ue3_command_suite.py list --game ME3 --risk low
    python -I tools/ue3live/ue3_command_suite.py catalogue --out tools/ue3live/commands.json
    python -I tools/ue3live/ue3_command_suite.py verify
    python -I tools/ue3live/ue3_command_suite.py phases --verbose
    python -I tools/ue3live/ue3_command_suite.py probe --pid 1234 --exe MassEffect1.exe \
        --offsets local/MELE/offsets/offsets_LE1.json \
        --out local/MELE/probes/commands_probe_LE1.json
    python -I tools/ue3live/ue3_command_suite.py plan --probe local/MELE/probes/commands_probe_LE1.json

`probe` needs an offsets file, and **only LE1 has a complete one**. LE3 and LE2 have
discovered RVAs recorded in the ue3live README but no `offsets_LE3.json` / `offsets_LE2.json`
written yet, so probing LE3 - the first target game - is blocked on running
`ue3_discover_external.py` and `ue3_verify_offsets.py` against a live LE3. That is a
prerequisite, not an oversight; writing an offsets file from the README's RVAs without
running the verifier would produce a file that looks authoritative and has never been checked.
"""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mele_paths  # noqa: E402  upward search: works in the hub and in this bundle
REPO = mele_paths.repo_root()
sys.path.insert(0, HERE)

CATALOGUE = os.path.join(HERE, "commands.json")

GAMES = ("ME1", "ME2", "ME3")
EXE_FOR_GAME = {"ME1": "MassEffect1.exe", "ME2": "MassEffect2.exe", "ME3": "MassEffect3.exe"}

# Risk is about what a WRONG call costs, not about how interesting the command is.
#   read      queries only; cannot change state
#   low       visual or self-restoring; a wrong value is undone by the next call
#   medium    moves/attaches/controls something; a wrong value strands the pawn
#   high      creates or destroys objects, changes level, moves the player between levels
#   blocked   measured to crash this build; listed so nobody "tries it again to be sure"
RISK_ORDER = {"read": 0, "low": 1, "medium": 2, "high": 3, "blocked": 4}

# Target roles: WHICH live object a command acts on.
#
# This is a role name, not an address, and that is deliberate. Addresses are per-launch (the
# project's own offsets file records `function_pointers_are_per_launch: true`), so a plan that
# baked one in would be stale the next time the game started, and a stale address is the worst
# possible outcome: it looks like a valid pointer and writes somewhere arbitrary. So the plan
# says `player.pawn`, and the game-side adapter resolves that to the live pawn at call time.
#
# `none` means the command does not act on a specific instance (a class-level or global
# lookup), and the game side decides - which is also the honest answer for commands whose
# owner we resolved but whose instance we have not.
ROLES = {
    "player.pawn": "the local player's pawn (Physics, Location, Firing)",
    "player.controller": "the local player's PlayerController",
    "player.input": "the local player's PlayerInput object",
    "squad.member": "the squadmate pawn the client is meant to control",
    "worldinfo": "the level's WorldInfo",
    "gameinfo": "the active GameInfo",
    "level": "the current ULevel actor list",
    "sequence": "the level's Kismet Sequence",
    "hud": "the local player's HUD",
    "weapon": "the local player's current weapon",
    "viewport": "the GameViewportClient (players, split layout, HUD class)",
    "none": "no specific instance: a class-level lookup, or a Kismet action the game side "
            "must find by name",
}

# Per-category default role, so a role is stated once instead of 156 times. Entries that need a
# different role say so explicitly.
CATEGORY_ROLE = {
    "console": "player.controller",
    "kismet": "sequence",
    "pawn": "player.controller",
    "transform": "player.pawn",
    "state": "player.pawn",
    "input": "player.controller",
    "combat": "player.pawn",
    "conversation": "player.pawn",
    "camera": "player.controller",
    "timer": "player.pawn",
    "save": "gameinfo",
    "hud": "hud",
    "replication": "player.pawn",
    "message": "player.controller",
    "travel": "level",
    "query": "none",
    "audio": "player.pawn",
    "anim": "player.pawn",
    "pause": "player.controller",
    "squad": "squad.member",
}

# Explicit role overrides. Most commands act on the live object implied by their category, so
# the table above covers 150 of 156; these are the ones where the category default would be
# wrong, and each is a genuine distinction rather than a preference:
ROLE_OVERRIDES = {
    # Kismet: the container and the graph structure live on objects, but every SeqAct_* acts
    # on its own instance, which only the game side can find. Saying so is better than aiming
    # an Activate at the level's Sequence root.
    "kismet.sequence": "none",
    "kismet.sequence_op": "none",
    "kismet.delay": "none",
    "kismet.interp": "none",
    "kismet.teleport": "none",
    "kismet.physics": "none",
    "kismet.cinematic_mode": "none",
    "kismet.remote_event": "none",
    # Possession acts on the CONTROLLER, not the pawn: the pawn is the argument. Getting this
    # backwards would call Possess on a pawn, which is what crashed the LE1 Actor.Spawn route.
    "pawn.possess": "player.controller",
    "pawn.unpossess": "player.controller",
    "pawn.spawn_default": "gameinfo",
    "pawn.restart_player": "gameinfo",
    "pawn.spawn_from_type": "gameinfo",
    "pawn.post_login": "gameinfo",
    "pawn.create_player": "none",
    # RemovePlayer and LayoutPlayers are GameViewportClient methods - they act on the
    # viewport that owns the local-player array, not on a player.
    "pawn.remove_player": "viewport",
    "pawn.layout_players": "viewport",
    "pawn.debug_spawn": "level",
    # Query commands are class-level or take the index as an argument. IsLocallyControlled is
    # asked OF a controller, which is the whole question for a co-op client.
    "q.local_controlled": "player.controller",
    "q.map_name": "worldinfo",
    "q.map_name_me2": "worldinfo",
    "q.player_controller": "gameinfo",
    "q.player_pawn": "gameinfo",
    "q.local_controller": "gameinfo",
    # A class object, not an instance: nothing to aim at.
    "q.local_player_controller": "none",
    # Timers are per-object, and a timer set on the pawn is a different timer from one set on
    # the controller - that is the whole point of the timer phase.
    "timer.set": "player.pawn",
    "timer.clear": "player.pawn",
    "timer.is_active": "player.pawn",
    # Damage and inventory go through the GameInfo/pawn split the engine actually uses.
    "combat.take_damage": "player.pawn",
    "combat.apply_damage": "player.pawn",
    "combat.give_weapon": "player.pawn",
    "combat.add_inventory": "player.pawn",
    # Firing lives on the weapon where the build has one, on the pawn where it does not.
    "combat.can_fire": "weapon",
    "combat.start_fire": "weapon",
    "combat.stop_fire": "weapon",
    "combat.can_fire_me3": "player.pawn",
    "combat.start_firing": "player.pawn",
    "combat.weapon_fire": "weapon",
    # Replication roles are a property of the ACTOR being asked about.
    "repl.role": "player.pawn",
    "repl.remote_role": "player.pawn",
    "repl.net_owner": "player.pawn",
    # Audio: the replicated form is addressed to the controller, not the pawn.
    "audio.client_hear": "player.controller",
    "audio.stop_music": "player.controller",
    # Pause is per-controller; the world flag is on WorldInfo.
    "pause.players_only": "worldinfo",
    "pause.pause_game": "gameinfo",
    # Save/profile are GameInfo-level.
    "save.save_game": "gameinfo",
    "save.load_game": "gameinfo",
    "save.save_profile": "gameinfo",
    "save.can_save": "gameinfo",
    # Travel is Engine/level-level.
    "travel.server_travel": "level",
    "travel.open_map": "player.controller",
    "travel.switch_level": "player.controller",
    "travel.restart_level": "player.controller",
    "travel.client_travel": "player.controller",
    # Messages are addressed to a controller.
    "msg.broadcast": "gameinfo",
    # The input object itself, not the controller that owns it.
    "input.player_input": "player.controller",
}


def role_of(entry):
    """The live object a command acts on, overrides first."""
    return ROLE_OVERRIDES.get(entry["id"], entry["role"])

CATEGORIES = {
    "console": "Engine exec / console verbs. The one channel that exists in all three builds "
               "and needs no code, which makes it the reference implementation for every "
               "other command: if a console verb works for player 2 but a direct call does "
               "not, the difference is the path, not the engine.",
    "kismet": "Sequence (Kismet) objects and their activation. Map events fire from here, and "
              "this is where 'the door opened for player 1 and player 2 walked into the "
              "back of it' comes from.",
    "pawn": "Creating, possessing and releasing a body. The whole point of a second player.",
    "transform": "Moving, attaching, hiding, colliding. Cheap to verify and the most common "
                 "source of a client that is playing a different game.",
    "state": "State machine entry and exit. A state a client enters and the host does not is "
             "a permanent divergence with no error message.",
    "input": "Whether input reaches the pawn, and who it is routed to.",
    "combat": "Firing, powers, damage. The player-visible half of the game and the half most "
              "likely to be gated per-pawn rather than per-client.",
    "conversation": "Dialogue ownership. Player 2 has to become a spectator here without "
                    "leaving the level.",
    "camera": "View target and camera mode. Two clients, two viewports, one set of "
              "SetViewTarget calls written for one.",
    "timer": "Timers and their identity. A timer set on the host's pawn and read on the "
             "client's is a different timer.",
    "save": "Save and load. A co-op session that saves half a world is worse than one that "
            "refuses.",
    "hud": "HUD presence and ownership. Cheap proxy for 'is this client still being rendered "
           "as a player'.",
    "replication": "Actor roles and the client/server function family. LE3 is the only build "
                   "with a real replication layer, which is why LE3 goes first.",
    "message": "Text to a player. The first thing that breaks when there are two of them and "
               "the code says 'the player'.",
    "travel": "Level transitions. Every one of them is a place a second client gets dropped.",
    "query": "Read-only identity and context lookups. Must all succeed on both clients before "
             "any mutating command means anything.",
    "audio": "Sound and music. One engine, two listeners; per-player audio is a later problem "
             "but the trigger path is the same one as everything else.",
    "anim": "Animation and morph control. Scripted animation that assumes the player pawn is "
            "the one the script is driving.",
    "pause": "Pausing and `bPlayersOnly`. The mechanism that already freezes both players, "
             "so it is the one place where a naive implementation looks correct.",
    "squad": "Party membership. The client IS a squadmate, so these calls run on the thing "
             "the client is trying to control.",
}


def C(cid, category, call, presence, risk, sync, why, mechanism=None,
      targets=None, note=None, crashes=None, args=None, role=None):
    """One catalogue entry.

    `presence` is a 3-character string 'MMM' in ME1/ME2/ME3 order where each character is
    'T' if the identifier is in that build's decompressed data and 'F' if not. A compact
    form keeps the table readable and forces the three-way answer to be stated.

    `targets` names the OWNER classes the function must belong to (a resolution concern, used
    by `probe`). `role` names the live INSTANCE the command acts on (an execution concern,
    used by the runner). They are different questions and the catalogue keeps them apart:
    resolving `Possess` to `BioPlayerController` says which function, not which controller.
    """
    if len(presence) != 3 or set(presence) - set("TF"):
        raise ValueError("presence must be 3 chars of T/F, got %r" % (presence,))
    if role is not None and role not in ROLES:
        raise ValueError("%s: unknown role %r" % (cid, role))
    return {
        "id": cid,
        "category": category,
        "mechanism": mechanism or category,
        "call": call,
        "targets": list(targets or []),
        "role": role or CATEGORY_ROLE.get(category, "none"),
        "risk": risk,
        "present": {g: presence[i] == "T" for i, g in enumerate(GAMES)},
        "sync": sync,
        "why": why,
        "args": list(args or []),
        "note": note,
        "crashes": list(crashes or []),
    }


# ---------------------------------------------------------------------------------------
# The taxonomy. One entry per command MECHANISM, not per spelling: `SetLocation` and
# `ClientSetLocation` are separate entries because they are separate engine paths (the
# second is a replicated RPC that only ME3 has), and that difference is the finding.
# ---------------------------------------------------------------------------------------
COMMANDS = [
    # ---- console: the reference channel -----------------------------------------------
    C("console.exec", "console", "ConsoleCommand", "TTT", "low",
      "Issue the same console line on both clients and compare what changed; a verb that "
      "changes only the caller's world is a desync the console can prove in seconds.",
      "The only command channel present in all three builds with no injected code. Every "
      "other route in this catalogue has to reproduce what this one already does.",
      targets=["PlayerController", "BioPlayerController", "SFXPlayerController"],
      args=[("Command", "FString")]),
    C("console.channel", "console", "Console", "FTT", "read",
      "Confirms the console object exists and is reachable, which is what the exec path "
      "needs before a line can be delivered.",
      "The console state object. Present in LE2/LE3 and absent in LE1, which is a real "
      "difference in how a line reaches the engine."),
    C("console.on_command", "console", "OnConsoleCommand", "FFT", "read",
      "Intercept point: if the hook fires on both clients for one issued line, command "
      "delivery is shared and every console-driven test becomes a sync test.",
      "A hook callback, ME3 only. Useful as an observation point rather than a call."),
    C("console.exec_verb", "console", "Exec", "TTT", "low",
      "The lower-level entry the console verb table dispatches through; distinguishes "
      "'the verb refused' from 'the line never arrived'.",
      "Separate from ConsoleCommand in every build, so testing one does not test the other.",
      targets=["PlayerController", "UIInteraction", "GameInfo"]),
    C("console.god", "console", "God", "TTT", "low",
      "Toggles invulnerability on the LOCAL controller only in a naive implementation; a "
      "co-op build must decide whether that is per-player or per-world.",
      "The clearest example of a verb that is conceptually per-player and is implemented "
      "per-controller.", args=[("bEnable", "bool")]),
    C("console.ghost", "console", "Ghost", "FFT", "medium",
      "Sets collision off and physics to flying; a client that ghosts its own pawn and not "
      "the host's is the classic asymmetric cheat and the classic desync.",
      "Combines the collision and physics paths in one verb, so it tests two entries at "
      "once.", args=[("bEnable", "bool")]),
    C("console.fly", "console", "Fly", "TTT", "medium",
      "Changes pawn physics mode. Physics is replicated in LE3 and not in LE1/LE2, so the "
      "same verb produces different sync behaviour per game - worth measuring once.",
      "Physics-mode change via console. The measured value is visible in the pawn's "
      "Physics byte, so this is verifiable without trusting either client's log."),
    C("console.walk", "console", "Walk", "TTT", "medium",
      "The inverse of Fly; the pair is a round-trip test that leaves the pawn in its "
      "original physics mode.",
      "Round-trip partner for Fly. Always run both, then assert the physics byte is back.",
      args=[("bEnable", "bool")]),
    C("console.loaded", "console", "Loaded", "TFT", "read",
      "Reports whether the current level finished loading. The first question to ask a "
      "client that is behaving as if the level is not there.",
      "A ready flag, not a command. Cheap gate before any level-dependent test."),
    C("console.slomo", "console", "Slomo", "FTT", "low",
      "Time dilation is world state; if it is applied per-client the two clients run at "
      "different speeds and every timestamp-based check lies.",
      "Directly changes the world's time scale, which is exactly the quantity a naive "
      "lockstep implementation assumes is shared.", args=[("NewTimeDilation", "float")]),
    C("console.timedilation", "console", "TimeDilation", "TTT", "low",
      "The same quantity as Slomo but through a property-set path present in LE1, so it "
      "gives a third game a way to test time scale.",
      "Present in all three builds while Slomo is not, so it is the portable form.",
      args=[("NewTimeDilation", "float")]),
    C("console.freezeframe", "console", "FreezeFrame", "FFT", "low",
      "Freezes rendering without stopping the simulation - a state that is easy to mistake "
      "for a pause and will be mistaken for one during testing.",
      "Separates 'not rendering' from 'not simulating', which a pause test must not conflate."),
    C("console.playersonly", "console", "PlayersOnly", "FFT", "medium",
      "Freezes every pawn in the level for everyone. Whether player 2 counts as a player "
      "here is the question, and it is measurable: the pawn's Physics byte changes.",
      "The world-level freeze that conversations and cutscenes already rely on, and the "
      "reason a conversation looks 'handled' when it is not."),
    C("console.killpawns", "console", "KillPawns", "FFT", "high",
      "Kills every pawn in the level. Use on a throwaway copy only; it is the fastest way "
      "to prove which pawns a client still counts as alive.",
      "Bulk state change with a visible, countable result, so it makes a good assertion "
      "target even though it is destructive."),
    C("console.allweapons", "console", "AllWeapons", "FFT", "low",
      "Gives the inventory set. A client that receives the weapons but cannot use them "
      "separates inventory sync from input and gate problems.",
      "Distinguishes 'the client has the item' from 'the client may use the item', which "
      "are different bugs with the same symptom."),
    C("console.suicide", "console", "Suicide", "TFT", "high",
      "Kills the caller's own pawn. The cheapest way to test death and respawn ownership, "
      "which is where a second player most often ends up stuck as a spectator.",
      "Forces the death path on demand instead of waiting for combat."),
    C("console.stat", "console", "Stat", "TTT", "read",
      "Toggles the perf overlay. Harmless and a reliable positive control: if this does not "
      "change the screen, the console route is broken and nothing else in the run counts.",
      "A positive control. Every suite needs one command that must visibly work.",
      args=[("Command", "FString")]),
    C("console.physics", "console", "Physics", "TTT", "low",
      "Reports/toggles physics debug. Read-mostly and present everywhere, so it doubles as "
      "a three-game control.",
      "Debug rendering path, present in all three, safe for control runs."),
    C("console.collision", "console", "Collision", "TTT", "low",
      "Toggles collision debug draw. The visual form of what the collision commands change.",
      "Turns a boolean into something a screenshot can prove.",
      args=[("Command", "FString")]),
    C("console.say", "console", "Say", "TTT", "read",
      "Text to other players. Used for timing markers: both clients log the same string at "
      "the same moment, which is the cheapest cross-client clock.",
      "A message channel that already exists. Markers sent through it give logs a shared "
      "reference without inventing a protocol.", args=[("Msg", "FString")]),
    C("console.team", "console", "Team", "FTT", "low",
      "Team index change. In a one-player build this is meaningless, which is exactly why "
      "it is worth knowing whether the plumbing is there.",
      "Present only where the engine kept team support, so it is a cheap probe of how much "
      "multiplayer machinery survived into the single-player build.",
      args=[("TeamNumber", "int")]),
    C("console.open", "console", "Open", "TTT", "high",
      "Travels to a map. Every level transition is a place a second client is dropped, so "
      "this is the transition test itself.",
      "The level-transition primitive. Present in all three, so transitions can be tested "
      "identically across games.", args=[("URL", "FString")]),
    C("console.login", "console", "Login", "TTT", "high",
      "The login path that ME3SquadCoop's working co-op uses instead of CreatePlayer: it "
      "brings up the engine's own multi-player plumbing, which LE1/LE2 never load.",
      "For LE3 this is the documented route to a second player. For LE1/LE2 the same verb "
      "exists but there is no netcode behind it, so it must not be assumed equivalent.",
      args=[("Portal", "string"), ("Options", "string"), ("Id", "string")]),
    C("console.logout", "console", "Logout", "TTT", "medium",
      "The inverse of Login; needed to test a session that ends cleanly, which is where "
      "half-written co-op mods leave the engine.",
      "Every login test needs a logout or the copy must be restarted.",
      args=[("Portal", "string")]),

    # ---- kismet ----------------------------------------------------------------------
    C("kismet.sequence", "kismet", "Sequence", "TTT", "read",
      "The level's sequence container. Counting sequences on both clients tells you whether "
      "both loaded the same Kismet graph, which is upstream of every event divergence.",
      "The container for all map events. If the graphs differ, nothing downstream can "
      "match.", targets=["Sequence"]),
    C("kismet.sequence_op", "kismet", "SequenceOp", "TTT", "read",
      "Base class of every action and event. Reading its links is what makes it possible "
      "to name an event that fires on one client only.",
      "Common base for actions and events; its input/output link arrays are the graph.",
      targets=["SequenceOp"]),
    C("kismet.activate", "kismet", "Activate", "FTT", "medium",
      "Activates a sequence object remotely. This is the command that makes 'delay this "
      "until player 2 arrives' possible without editing the level.",
      "Remote activation of a Kismet object, present in LE2/LE3. In LE1 the same effect "
      "needs TriggerEvent/CauseEvent.", targets=["SequenceOp"]),
    C("kismet.activate_output_link", "kismet", "ActivateOutputLink", "FFT", "medium",
      "Fires one specific output link instead of the whole action, so a door can be held "
      "open for player 2 without re-running the trigger.",
      "Finer-grained than Activate: it selects a link, which is what a per-player delay "
      "actually needs.", targets=["SequenceOp"], args=[("OutputLinkIdx", "int")]),
    C("kismet.input_links", "kismet", "InputLinks", "TTT", "read",
      "The inbound edges of an action. Reading them tells you which event would have to be "
      "held back to delay this action.",
      "Graph structure, readable offline in principle and live right now.",
      targets=["SequenceOp"]),
    C("kismet.output_links", "kismet", "OutputLinks", "TTT", "read",
      "The outbound edges. An action with two output links can usually be split so one "
      "branch waits.",
      "Graph structure again; the split point for a delayed event.",
      targets=["SequenceOp"]),
    C("kismet.active", "kismet", "bActive", "TTT", "read",
      "Whether the action is currently running. The direct measurement for 'did this fire "
      "on both clients'.",
      "The state bit to compare after triggering. Cheap and unambiguous.",
      targets=["SequenceOp"]),
    C("kismet.activated", "kismet", "Activated", "TTT", "read",
      "Whether the action ever fired at all. Different from bActive, which can be true "
      "again after a restart.",
      "Latches the 'has run' question, which bActive does not.",
      targets=["SequenceOp"]),
    C("kismet.used", "kismet", "Used", "TTT", "read",
      "The used-flag on events that must only fire once. The exact flag that makes a map "
      "event fire for player 1 and be spent before player 2 walks in.",
      "This is the flag to inspect when a trigger 'already happened'. Naming it is most of "
      "the diagnosis.", targets=["SeqEvt_", "SequenceEvent"]),
    C("kismet.teleport", "kismet", "SeqAct_Teleport", "TTT", "medium",
      "A map-level teleport. Whether it moves the acting client only or both is the whole "
      "question for any level that moves the player by Kismet.",
      "Player movement expressed as level data rather than code, so it exercises the "
      "Kismet-to-actor path.", targets=["SeqAct_Teleport"]),
    C("kismet.interp", "kismet", "SeqAct_Interp", "TTT", "medium",
      "Matinee playback. Scripted camera and animation is where a second viewer is most "
      "obviously in the wrong place.",
      "The cutscene driver. Conversations and scripted sequences both route through it.",
      targets=["SeqAct_Interp"]),
    C("kismet.delay", "kismet", "SeqAct_Delay", "TTF", "medium",
      "A built-in delay node. If the level already has one, delaying for player 2 costs a "
      "parameter change rather than new code.",
      "Present in LE1/LE2 and absent in LE3, which is itself worth knowing before "
      "designing a delay that assumes it.", targets=["SeqAct_Delay"]),
    C("kismet.cinematic_mode", "kismet", "SeqAct_ToggleCinematicMode", "TTT", "medium",
      "The level's own way into cinematic mode. Same effect as the code path, so a client "
      "that behaves differently under one and not the other is a client-path bug.",
      "Two routes to one state is a useful A/B, and it is how the game actually enters "
      "cutscenes.", targets=["SeqAct_ToggleCinematicMode"]),
    C("kismet.physics", "kismet", "SeqAct_SetPhysics", "FTT", "medium",
      "Sets a pawn's physics from level data. The documented working placement sequence "
      "needs physics 1 before ClientSetLocation, and this is the level-data form of that.",
      "Ties the placement order that is already proven to a Kismet action, so a level can "
      "reproduce it.", targets=["SeqAct_SetPhysics"], args=[("Physics", "byte")]),
    C("kismet.cause_event", "kismet", "CauseEvent", "TTT", "medium",
      "Fires a named remote event. The portability answer for delayed triggers: all three "
      "builds have it, so one mechanism covers all three games.",
      "Present everywhere, which makes it the safest cross-game hook for 'player 2 has "
      "arrived'.", args=[("EventName", "FString")]),
    C("kismet.trigger_event", "kismet", "TriggerEvent", "TTT", "medium",
      "The older named-event entry. Coexists with CauseEvent in all three, so both should "
      "be measured rather than one assumed to subsume the other.",
      "A second spelling of the same idea. Present in every build; whether it reaches the "
      "same handlers is measurable.", args=[("EventName", "FString")]),
    C("kismet.remote_event", "kismet", "RemoteEvent", "FTT", "medium",
      "Explicitly remote activation, absent in LE1. Where it exists it is the intended "
      "route rather than a side effect.",
      "Named for what a co-op trigger needs. Its absence in LE1 is why LE1 needs a "
      "different mechanism.", args=[("EventName", "FString")]),

    # ---- pawn ------------------------------------------------------------------------
    C("pawn.spawn", "pawn", "Spawn", "TTT", "high",
      "Creates an actor. Present in all three, which means a second body can be created "
      "everywhere; what differs is what the created actor needs attached.",
      "The generic creation primitive. It works in all three builds - the failures were "
      "always in what a PlayerController needs afterwards, not in Spawn itself.",
      targets=["Actor"], args=[("SpawnClass", "class")]),
    C("pawn.spawn_default", "pawn", "SpawnDefaultPawnFor", "TTT", "high",
      "The GameInfo path that spawns a pawn FOR a controller, including the default pawn "
      "class and start spot. This is the call a CreatePlayer would have made internally.",
      "Presents in all three. If a second player can be given a body without "
      "CreatePlayer, this is where that happens - it takes a controller, so it needs one "
      "first.", targets=["GameInfo"]),
    C("pawn.restart_player", "pawn", "RestartPlayer", "TTT", "high",
      "Respawns a controller's pawn using the normal game rules. The natural way to give "
      "an existing second controller a body after death or after a level change.",
      "Already the respawn path, so it is exercised by the game itself and less likely to "
      "hit an untested branch than a hand-built spawn.", targets=["GameInfo"]),
    C("pawn.possess", "pawn", "Possess", "TTT", "high",
      "Points a controller at a body. The ME3SquadCoop takeover is exactly this: possess a "
      "squadmate pawn. Present in all three, so the mechanism ports.",
      "The proven co-op route in LE3. Everything else in this catalogue assumes it worked.",
      targets=["Controller", "PlayerController", "BioPlayerController"],
      args=[("inPawn", "Actor"), ("bVehicleTransition", "bool")]),
    C("pawn.unpossess", "pawn", "UnPossess", "TTT", "medium",
      "Releases a pawn. Needed to test takeover reversibility; a takeover that cannot be "
      "undone is a bug that only shows up when the second player leaves.",
      "The inverse of the working route, and the thing to call between test phases so one "
      "phase cannot contaminate the next.", targets=["Controller"]),
    C("pawn.spawn_from_type", "pawn", "SpawnActorFromType", "TTF", "high",
      "Spawns by class name, which is what Kismet and mods use. Absent in LE3, where "
      "Spawn(default) is the route.",
      "The class-name form a mod would call. Present in LE1/LE2, so those two can create a "
      "second body by name.", targets=["GameInfo", "Actor"]),
    C("pawn.debug_spawn", "pawn", "DebugSpawnActor", "TFF", "high",
      "A debug spawn entry in LE1 only. Worth knowing about because it exists precisely to "
      "create actors from a console, which is the fastest experiment loop.",
      "LE1-only debug path. Useful, and only in the game that has the fewest alternatives.",
      targets=["Actor"]),
    C("pawn.create_player", "pawn", "CreatePlayer", "FFT", "blocked",
      "The UE3 call that would create a second local player outright. It is present only in "
      "LE3, where it works, and measured to crash LE1 and LE2 in three distinct ways.",
      "The obvious answer and the wrong one for two of three games. Listed so it is not "
      "re-attempted: see CO-OP-FAIL-POINTS.md for the three crash signatures.",
      targets=["GameViewportClient"],
      crashes=["ME1", "ME2"],
      note="ME1/LE2 each crashed three ways: ucrtbase!aligned_realloc during the call, an "
           "engine-thread fault at +0x99611a AFTER a successful return, and a null "
           "PlayerInput call at [rax+0x6D0]. Do not retry without a new mechanism.",
      args=[("ControllerId", "int"), ("OutError", "string")]),
    C("pawn.remove_player", "pawn", "RemovePlayer", "FFT", "high",
      "The inverse of CreatePlayer, ME3 only. If a session can be created and not removed, "
      "every test needs a restart.",
      "Every CreatePlayer test in LE3 needs this to leave a clean process.",
      targets=["GameViewportClient"], args=[("ExPlayer", "LocalPlayer")]),
    C("pawn.layout_players", "pawn", "LayoutPlayers", "FFT", "medium",
      "Recomputes split-screen layout, which is where the split-screen harness gets its "
      "two viewports. The value is a layout enum, and a wrong value silently keeps one pane.",
      "Directly controls whether two viewports exist, which is the prerequisite for a "
      "local two-player proof before any network work.",
      targets=["GameViewportClient"],
      args=[("SplitscreenInfo", "array")]),
    C("pawn.post_login", "pawn", "PostLogin", "TFT", "high",
      "The engine's own new-player hook. Calling it by hand exercises exactly what "
      "CreatePlayer would have called, with the option to attach things before it runs.",
      "Reaching past CreatePlayer to the hook it delegates to is the remaining unproven "
      "route, and this catalogues it as such.", targets=["GameInfo"]),

    # ---- transform -------------------------------------------------------------------
    C("xform.set_location", "transform", "SetLocation", "TTT", "medium",
      "The base teleport. Measured to fail on a falling pawn and to work after collisions "
      "and physics are corrected, so the ORDER is the finding, not the call.",
      "Documented result: Actor.SetLocation alone did not move a falling pawn (z went the "
      "wrong way), while SetCollision -> SeqAct_SetPhysics(1) -> ClientSetLocation did.",
      targets=["Actor"], args=[("NewLocation", "vector")]),
    C("xform.set_rotation", "transform", "SetRotation", "TTT", "medium",
      "Rotation follows location. A client that is in the right place facing the wrong way "
      "is a camera or a replication problem, and this separates them.",
      "Tests rotation independently of position so the two failures are distinguishable.",
      targets=["Actor"], args=[("NewRotation", "rotator")]),
    C("xform.client_set_location", "transform", "ClientSetLocation", "FFT", "medium",
      "The replicated form, ME3 only. Its existence in LE3 and absence in LE1/LE2 is the "
      "clearest evidence of how much more of the replication layer LE3 kept.",
      "The call that actually worked for placement. Present only where a client exists.",
      targets=["PlayerController"],
      args=[("NewLocation", "vector"), ("NewRotation", "rotator")]),
    C("xform.client_set_rotation", "transform", "ClientSetRotation", "FTT", "medium",
      "Rotation-only replicated form, absent in LE1. Same argument as ClientSetLocation.",
      "Completes the replicated pair in LE2/LE3.", targets=["PlayerController"],
      args=[("NewRotation", "rotator")]),
    C("xform.teleport", "transform", "Teleport", "FTT", "medium",
      "A higher-level teleport that preserves velocity and physics handling. Present where "
      "the engine kept it; absent in LE1.",
      "Often a better answer than SetLocation for a pawn, because it resolves the physics "
      "problem rather than requiring the caller to.", targets=["Actor"],
      args=[("DestLocation", "vector"), ("DestRotation", "rotator")]),
    C("xform.set_base", "transform", "SetBase", "TTT", "medium",
      "Attaches a pawn to a moving base. On a moving platform, host and client disagree "
      "about position by however far the platform moved.",
      "Present everywhere, and the mechanism behind every 'the elevator left without me'.",
      targets=["Actor"], args=[("NewBase", "Actor")]),
    C("xform.set_hard_attach", "transform", "SetHardAttach", "TTT", "medium",
      "The rigid form of SetBase. Hard attach makes the pawn part of the base, which is "
      "right for a vehicle and wrong for anything that should walk off.",
      "Distinguishes a soft from a hard attachment, which behave differently across a "
      "network boundary.", targets=["Actor"], args=[("bNewHardAttach", "bool")]),
    C("xform.set_hidden", "transform", "SetHidden", "TTT", "low",
      "Hides an actor. The cheapest round-trip test in the catalogue: toggle, read "
      "bHidden, toggle back, assert identical.",
      "Self-restoring and visible. A positive control for the whole dispatch path.",
      targets=["Actor"], args=[("bNewHidden", "bool")]),
    C("xform.set_draw_scale", "transform", "SetDrawScale", "FTT", "low",
      "Scale. A client that applied scale twice looks like a collision bug and is not.",
      "Cheap to read back via the scale property, so it verifies numerically.",
      targets=["Actor"], args=[("NewScale", "float")]),
    C("xform.set_scale", "transform", "SetScale", "TTT", "low",
      "The portable spelling of scale, in all three builds.",
      "Gives LE1 the same test SetDrawScale gives the other two.",
      targets=["Actor"], args=[("NewScale", "float")]),
    C("xform.set_collision", "transform", "SetCollision", "TTF", "medium",
      "The call that has to succeed before a pawn can be placed. Measured as the first step "
      "of the working placement order.",
      "Proven necessary. Present in LE1/LE2 and replaced by SetCollisionType in LE3.",
      targets=["Actor"],
      args=[("bNewCollideActors", "bool"), ("bNewBlockActors", "bool"),
            ("bNewIgnoreEncroachers", "bool")]),
    C("xform.set_collision_type", "transform", "SetCollisionType", "FFT", "medium",
      "LE3's replacement for SetCollision. A port that calls SetCollision on LE3 silently "
      "does nothing, which is exactly the class of failure this catalogue is for.",
      "Same job, different name. The rename is the porting work.",
      targets=["Actor"], args=[("NewCollisionType", "byte")]),
    C("xform.collide_world", "transform", "bCollideWorld", "TTT", "read",
      "The state bit SetCollision changes. Read it to verify, rather than trusting the call.",
      "Turns a command into an observable, which is what makes the test real.",
      targets=["Actor"]),

    # ---- state -----------------------------------------------------------------------
    C("state.push", "state", "PushState", "TTT", "medium",
      "Enters a stacked state. A state a client enters and the host does not is a permanent "
      "divergence with no error.",
      "Present in all three. State divergence is silent, so it has to be measured rather "
      "than observed.", targets=["Object"], args=[("NewState", "name")]),
    C("state.pop", "state", "PopState", "TTT", "medium",
      "Leaves the stacked state. The inverse, needed to make a state test repeatable.",
      "Round-trip partner for PushState.", targets=["Object"], args=[("bPopAll", "bool")]),
    C("state.begin", "state", "BeginState", "TTT", "low",
      "The state entry callback. Firing it directly is a way to exercise state logic "
      "without a transition.",
      "Calling the callback tests the state body without needing a legal transition.",
      targets=["Object", "State"]),
    C("state.end", "state", "EndState", "TTT", "low",
      "The state exit callback, and the inverse of BeginState.",
      "Confirms that whatever BeginState set up can be torn down.", targets=["Object", "State"]),
    C("state.get_name", "state", "GetStateName", "FTT", "read",
      "Reports the current state name. THE assertion for every state test: compare the name "
      "on both clients, not a guess about what should have happened.",
      "Gives state divergence a comparable value.", targets=["Object"]),

    # ---- input -----------------------------------------------------------------------
    C("input.player_input", "input", "PlayerInput", "TTT", "read",
      "The input object on a controller. LE1 crashed with a NULL PlayerInput when spawning a "
      "controller, so this pointer is the thing a second player is missing.",
      "Measured null on a hand-built controller, and the crash followed. Read it first.",
      targets=["PlayerController"]),
    C("input.apply_bindings", "input", "ApplyPCBindings", "TTT", "medium",
      "Pushes the PC bindings into a controller's input. The step that turns a created input "
      "object into one that responds to keys.",
      "Present in all three. Runs after PlayerInput exists, and is the safe half of input "
      "setup.", targets=["PlayerController", "PlayerInput"]),
    C("input.enable", "input", "EnableInput", "FFT", "medium",
      "Grants an actor input processing. LE3 only, so input gating is expressible in LE3 and "
      "must be done another way in LE1/LE2.",
      "The explicit gate. Its absence in LE1/LE2 is a real capability difference.",
      targets=["Actor", "PlayerController"], args=[("PlayerController", "PlayerController")]),
    C("input.disable", "input", "DisableInput", "FFT", "medium",
      "The inverse gate, and the mechanism a spectator mode would use.",
      "Reverses EnableInput; needed so a spectator can be un-spectated.",
      targets=["Actor", "PlayerController"], args=[("PlayerController", "PlayerController")]),
    C("input.cinematic_mode", "input", "SetCinematicMode", "TTT", "medium",
      "The code path into the same state ToggleCinematicMode reaches from level data. "
      "Measured to be present in all three, so a spectator lock can be built on it.",
      "The control hook with the widest availability. Sets bCinematicMode, which is "
      "readable, so the effect is verifiable.",
      targets=["PlayerController"],
      args=[("bInCinematicMode", "bool"), ("bHidePlayer", "bool"),
            ("bAffectsHUD", "bool"), ("bAffectsMovement", "bool"),
            ("bAffectsTurning", "bool")]),
    C("input.cinematic_bit", "input", "bCinematicMode", "TTT", "read",
      "The bit SetCinematicMode writes. The assertion, not the command.",
      "Readable in all three, so the effect of the command can be proved.",
      targets=["PlayerController"]),
    C("input.bio_player_control", "input", "BioSeqAct_SetPlayerControlling", "TFF", "medium",
      "ME1's own Kismet action for changing which pawn the player controls. This is the "
      "closest thing LE1 has to a built-in takeover.",
      "The single most promising LE1-specific hook found. Present in LE1 only, and it is a "
      "level-data command, so it needs no injected code to try.",
      targets=["BioSeqAct_SetPlayerControlling"]),
    C("input.bio_player_input_event", "input", "BioSeqEvt_PlayerInput", "TFF", "read",
      "An ME1 Kismet event that fires on player input. An existing per-input observation "
      "point, which a spectator mode would need.",
      "LE1-only observation hook. Useful for logging what player 1 did, in order.",
      targets=["BioSeqEvt_PlayerInput"]),
    C("input.bio_input_trap", "input", "BioSeqAct_SetInputTrap", "TFF", "medium",
      "ME1's input trap: a level-data way to stop the player acting for a while. The closest "
      "existing analogue to a control gate.",
      "LE1-only. It is the game's own 'the player cannot act here', which is the semantic a "
      "spectator needs.", targets=["BioSeqAct_SetInputTrap"]),

    # ---- combat ----------------------------------------------------------------------
    C("combat.can_fire", "combat", "CanFireWeapon", "TTT", "read",
      "The gate. Present in all three, which makes it the first thing to read on a pawn the "
      "client is supposed to control.",
      "Documented as the function that decides whether firing is allowed. If it is false on "
      "a squadmate, possession alone does not produce a working client.",
      targets=["Weapon", "Pawn"]),
    C("combat.start_fire", "combat", "StartFire", "TTT", "low",
      "Begins firing. Present everywhere, so a second player's ability to shoot can be "
      "tested in all three games.",
      "The player-visible half of the game. A silent no-op here is the bug the whole "
      "project is about.", targets=["Weapon", "Pawn"]),
    C("combat.stop_fire", "combat", "StopFire", "FTT", "low",
      "Ends firing. Absent in LE1, so LE1's fire loop must be closed another way.",
      "Round-trip partner, and a real per-game difference.", targets=["Weapon", "Pawn"]),
    C("combat.can_fire_me3", "combat", "CanFire", "FFT", "read",
      "ME3's differently-named gate. A port that calls CanFireWeapon on LE3 may be reading a "
      "different answer.",
      "The rename that matters. Two similar names, only one is live per build.",
      targets=["Pawn", "SFXPawn"]),
    C("combat.handle_firing", "combat", "HandleWeaponFiring", "FFT", "low",
      "The ME3 internal fire path. It is where a client-only fire decision would be made if "
      "one exists.",
      "Documented in the firing chain notes as the ME3 route, hence worth naming separately.",
      targets=["Pawn", "SFXPawn"]),
    C("combat.weapon_fire", "combat", "WeaponFire", "TFF", "low",
      "LE1's spelling of the fire event. The third spelling across three builds, which is "
      "the point: one concept, three names.",
      "Names must be resolved per build, not assumed.",
      targets=["Weapon", "BioWeapon"]),
    C("combat.start_firing", "combat", "StartFiring", "FTT", "low",
      "A second fire spelling in LE2/LE3. Coexists with StartFire, so both should be measured.",
      "Two plausible entry points in the same build means guessing is unsafe.",
      targets=["Pawn", "Weapon"]),
    C("combat.use_power", "combat", "UsePower", "TTT", "low",
      "Uses a power by id. Powers are the most per-pawn-gated action after firing, and the "
      "easiest to make silent.",
      "Present in all three, so power gating can be tested identically across games.",
      targets=["Pawn", "PlayerController", "SFXPawn"], args=[("PowerId", "int")]),
    C("combat.use_ability", "combat", "UseAbility", "TTT", "low",
      "The ability-level form. Present everywhere, distinct from UsePower in the same builds.",
      "A second power path in all three: measuring one does not measure the other.",
      targets=["Pawn", "SFXPawn"], args=[("AbilityId", "int")]),
    C("combat.activate_power", "combat", "ActivatePower", "TTT", "low",
      "Explicit activation rather than use. Present in all three.",
      "Completes the trio; a port that maps only one of three names leaves two paths dead.",
      targets=["Pawn", "SFXPawn"]),
    C("combat.hotkey", "combat", "doHotKey", "TTT", "low",
      "The hotkey route into powers, i.e. the input-driven one. A power that works via "
      "UsePower and not via doHotKey has an input problem, not a power problem.",
      "Separates the power path from the input path, which are otherwise conflated.",
      targets=["PlayerController", "SFXPlayerController"], args=[("HotKey", "int")]),
    C("combat.squad_power", "combat", "SquadOrderUsePower", "TTT", "low",
      "Orders a squadmate to use a power. THIS is the command a client playing a squadmate "
      "collides with: the host is already issuing it.",
      "Present in all three and directly about squad AI, so a co-op client fights it for "
      "control of the same pawn.", targets=["Squad", "SFXPawn_SquadMember"]),
    C("combat.take_damage", "combat", "TakeDamage", "TTT", "medium",
      "Damage entry. Whether a client's pawn is damageable decides whether combat is "
      "authoritative on one machine or two.",
      "Present everywhere. The single call that decides if the client's squadmate is "
      "invulnerable or doubly damageable.", targets=["Pawn", "Actor"]),
    C("combat.apply_damage", "combat", "ApplyDamage", "FTT", "medium",
      "The caller side of damage. Absent in LE1, so LE1 tests must use TakeDamage.",
      "Shows the asymmetry: the same concept has different names for caller and callee per "
      "build.", targets=["Actor", "Pawn"]),
    C("combat.busy_state", "combat", "SetBusyState", "TTF", "medium",
      "Puts the pawn in a busy state, which blocks actions. One of the fewer-known gates "
      "that leaves the player unable to shoot with no obvious cause.",
      "Absent in LE3, present in LE1/LE2. A gate that exists in two builds and not the "
      "third, and is invisible unless measured.", targets=["Pawn", "BioPawn"]),
    C("combat.give_weapon", "combat", "GiveWeapon", "FFT", "low",
      "Adds a weapon. ME3 only; LE1/LE2 use AddInventory.",
      "Weapon ownership has three different entry points across three builds.",
      targets=["Pawn", "SFXPawn"], args=[("WeaponClass", "class")]),
    C("combat.add_inventory", "combat", "AddInventory", "FTT", "low",
      "The LE2/LE3 inventory route. Distinct from GiveWeapon even where both exist.",
      "Same outcome, different path; a port must not assume one implies the other.",
      targets=["Pawn", "BioPawn"]),

    # ---- conversation ----------------------------------------------------------------
    C("conv.start", "conversation", "StartConversation", "TTF", "medium",
      "Starts dialogue. Absent in LE3, so LE3's conversation entry is elsewhere and must not "
      "be assumed identical.",
      "The obvious hook, present in two of three builds. The winner of a conversation does "
      "not need it, but the loser does.",
      targets=["Pawn", "BioPawn", "SFXPawn"]),
    C("conv.end", "conversation", "EndConversation", "TTF", "medium",
      "Ends dialogue and returns control. Where a spectator must be un-spectated.",
      "The inverse, and the point where a client most often stays stuck in a cinematic "
      "camera.", targets=["Pawn", "PlayerController"]),
    C("conv.mode", "conversation", "ConversationMode", "FFT", "read",
      "LE3's conversation state. The readable form of 'a conversation owns the player'.",
      "Gives LE3 the same assertion the conversation detector provides live.",
      targets=["SFXPawn", "SFXPawn_Player", "Pawn"]),
    C("conv.facefx", "conversation", "PlayFaceFXAnim", "TFF", "low",
      "Facial animation from a name. LE1 only; it is how dialogue is lip-synced there.",
      "LE1-only, and it is the visible sign of a conversation, so it doubles as an "
      "observation point.", targets=["Pawn", "BioPawn"]),

    # ---- camera ----------------------------------------------------------------------
    C("camera.set_view_target", "camera", "SetViewTarget", "TFT", "medium",
      "Chooses what a client looks at. With two clients this is the call that must become "
      "per-player, and getting it wrong makes the second player's screen unusable.",
      "Present in LE1/LE3. Directly controls the second viewport's contents.",
      targets=["PlayerController"], args=[("NewViewTarget", "Actor")]),
    C("camera.client_set_view_target", "camera", "ClientSetViewTarget", "TFT", "medium",
      "The replicated form. Its presence only in LE1 and LE3 is another per-game split.",
      "The form that would actually be used over a network.", targets=["PlayerController"],
      args=[("A", "Actor")]),
    C("camera.set_mode", "camera", "SetCameraMode", "TTT", "medium",
      "Camera mode selection. Present in all three, so the second player's camera mode is "
      "expressible everywhere.",
      "The portable camera lever.", targets=["PlayerController", "Camera"],
      args=[("NewMode", "name")]),
    C("camera.view_point", "camera", "GetPlayerViewPoint", "TTT", "read",
      "Reads the current view. The assertion for every camera test: compare the two "
      "clients' reported viewpoints rather than guessing.",
      "Present in all three and gives a comparable value.", targets=["PlayerController", "Camera"]),
    C("camera.update", "camera", "UpdateCamera", "FFT", "read",
      "The per-frame camera update. ME3 only; useful as a hook point for a second camera.",
      "Where a second viewport's camera would be driven.", targets=["PlayerController"]),
    C("camera.calc_camera", "camera", "CalcCamera", "FFT", "read",
      "Computes the camera position for one controller. The function a second viewport needs "
      "to be called for it.",
      "ME3-only. Naming it is the first step to calling it twice.", targets=["PlayerController"]),

    # ---- timer -----------------------------------------------------------------------
    C("timer.set", "timer", "SetTimer", "TTT", "low",
      "Sets a named timer. Timers are keyed by name AND function, so a timer set on one "
      "object is invisible to another with the same name.",
      "Present everywhere. A cheap, verifiable round-trip and a real trap for anything that "
      "assumes timers are global.",
      targets=["Object"], args=[("TimerName", "name"), ("Rate", "float")]),
    C("timer.clear", "timer", "ClearTimer", "TTT", "low",
      "Clears the named timer. The inverse.",
      "Round-trip partner; without it a timer test leaves a callback firing.",
      targets=["Object"], args=[("TimerName", "name")]),
    C("timer.is_active", "timer", "IsTimerActive", "TTT", "read",
      "Whether the timer exists. The assertion, and it distinguishes 'never set' from "
      "'already fired'.",
      "The observable that makes a timer test real.", targets=["Object"],
      args=[("TimerName", "name")]),

    # ---- save ------------------------------------------------------------------------
    C("save.save_game", "save", "SaveGame", "TTT", "high",
      "Writes a save. A co-op session that saves one player's world is worse than one that "
      "refuses, so the behaviour has to be known before it is allowed.",
      "Present in all three, and the save system is shared between copies through a fixed "
      "Documents path - so this is a real hazard during testing, not a hypothetical.",
      targets=["GameInfo", "SFXSaveGame"], args=[("SlotName", "string")]),
    C("save.load_game", "save", "LoadGame", "TTT", "high",
      "Loads a save, i.e. tears down and rebuilds the world. The most destructive command "
      "in the catalogue.",
      "Needs a restore point and a throwaway copy. Listed so it is used deliberately.",
      targets=["GameInfo"], args=[("SlotName", "string")]),
    C("save.save_profile", "save", "SaveProfile", "TTT", "high",
      "Writes the profile. Shared across every copy on the machine, so a test that runs it "
      "changes player 1's real profile.",
      "The sharpest shared-state hazard in the whole test environment.",
      targets=["GameInfo", "SFXProfile"]),
    C("save.can_save", "save", "CanSave", "FTT", "read",
      "Whether saving is currently legal. The gate to read before considering a save test.",
      "Absent in LE1, so LE1 has no safe way to ask first.", targets=["GameInfo"]),

    # ---- hud -------------------------------------------------------------------------
    C("hud.show", "hud", "ShowHUD", "TFT", "low",
      "Shows or hides the HUD. A reliable, visible per-client effect, so it is the cheapest "
      "proof that a command reached a specific client.",
      "Two clients have two HUDs, so this is the clearest 'did it arrive' signal.",
      targets=["PlayerController", "HUD"], args=[("bShow", "bool")]),
    C("hud.toggle", "hud", "ToggleHUD", "FFT", "low",
      "The inverse toggle, ME3 only.",
      "Completes the HUD pair where it exists.", targets=["PlayerController"]),
    C("hud.client_set_hud", "hud", "ClientSetHUD", "FFT", "medium",
      "Replaces a client's HUD class. The replicated form, and the one a second viewport "
      "would need a distinct instance of.",
      "ME3-only. Named for the client, which is exactly the distinction that matters here.",
      targets=["PlayerController"], args=[("NewHUDClass", "class")]),

    # ---- replication -----------------------------------------------------------------
    C("repl.role", "replication", "Role", "TTT", "read",
      "An actor's authority role. The single field that says whether a client's copy is "
      "allowed to act, and it exists in all three builds even though only LE3 has netcode.",
      "Readable everywhere. In LE1/LE2 it is vestigial, which is itself worth measuring "
      "rather than assuming.", targets=["Actor"]),
    C("repl.remote_role", "replication", "RemoteRole", "FTT", "read",
      "The other end's role. Absent in LE1, so LE1 cannot express an asymmetric authority.",
      "A per-game capability difference with a clean observable.",
      targets=["Actor"]),
    C("repl.net_owner", "replication", "bNetOwner", "FFT", "read",
      "Whether the local player owns this actor. The exact question a co-op client is "
      "asking about the squadmate it is controlling.",
      "ME3-only, and it is the engine's own answer to 'who may act on this'.",
      targets=["Actor"]),
    C("repl.net_update_frequency", "replication", "NetUpdateFrequency", "FFT", "low",
      "How often an actor replicates. ME3 only; the tuning knob for a second player's "
      "smoothness.",
      "Only meaningful where a replication layer exists, i.e. LE3.",
      targets=["Actor"]),
    C("repl.replicated_event", "replication", "ReplicatedEvent", "FFT", "medium",
      "The replicated-property notification. Calling it by hand simulates a replicate that "
      "never arrived, which is the cheapest way to test the notification path.",
      "ME3-only. Turns 'what happens on a late replicate' into a testable call.",
      targets=["Actor"], args=[("VarName", "name")]),
    C("repl.server_move", "replication", "ServerMove", "FFT", "medium",
      "The movement RPC a client sends to the server. Named here because it is the exact "
      "route a second client's movement would take if the engine path were used.",
      "ME3-only. 198 of the 283 client/server-shaped names live only in LE3, and this is "
      "the one that matters most.",
      targets=["PlayerController"], args=[("TimeStamp", "float"), ("NewLoc", "vector")]),
    C("repl.client_ack_move", "replication", "ClientAckGoodMove", "FFT", "low",
      "The server's acknowledgement of a client move. The other half of ServerMove.",
      "Together with ServerMove it is the engine's own movement sync, which is what a "
      "hand-built co-op would otherwise reimplement.", targets=["PlayerController"]),
    C("repl.client_adjust_position", "replication", "ClientAdjustPosition", "FFT", "medium",
      "Server correction of a client position. The mechanism that resolves a client's "
      "disagreement about where it is.",
      "The correction pass. Relevant to any desync where positions drift but movement works.",
      targets=["PlayerController"]),

    # ---- message ---------------------------------------------------------------------
    C("msg.client_message", "message", "ClientMessage", "TTT", "read",
      "Text to the local player. Present in all three, so it works as a marker channel in "
      "every build.",
      "A per-client message that cannot affect state, which makes it a safe cross-client "
      "timing marker.", targets=["PlayerController"],
      args=[("S", "string"), ("Type", "name"), ("MsgTime", "float")]),
    C("msg.broadcast", "message", "BroadcastLocalizedMessage", "FTT", "read",
      "Localized text to all players. Absent in LE1, where the localized path is different.",
      "Another per-game split in a subsystem a co-op mod will want for its own UI.",
      targets=["GameInfo"], args=[("MessageClass", "class"), ("Switch", "int")]),
    C("msg.send", "message", "SendMessage", "FFT", "read",
      "LE3's message route, distinct from ClientMessage in the same build.",
      "Two message paths in one build is exactly why names must be resolved, not assumed.",
      targets=["PlayerController"]),
    C("msg.receive_localized", "message", "ReceiveLocalizedMessage", "FFT", "read",
      "The receiving end of the localized path.",
      "Completes the pair so a message test can assert receipt, not just send.",
      targets=["PlayerController"]),

    # ---- travel ----------------------------------------------------------------------
    C("travel.server_travel", "travel", "ServerTravel", "FTT", "high",
      "Changes the level for everyone. The mechanism a co-op session would use to keep two "
      "clients together, and the one that drops a client when it fails.",
      "Absent in LE1, so LE1 transitions are all local. Another capability difference that "
      "changes the design per game.", targets=["Engine", "GameInfo"],
      args=[("URL", "string"), ("bAbsolute", "bool")]),
    C("travel.client_travel", "travel", "ClientTravel", "FFT", "high",
      "Sends one client to a URL. The per-client half of a transition.",
      "ME3-only. The call that would let the client follow the host.", targets=["PlayerController"],
      args=[("URL", "string"), ("TravelType", "byte"), ("bSeamless", "bool")]),
    C("travel.open_map", "travel", "OpenMap", "FFT", "high",
      "ME3 console-level map change. Distinct from ServerTravel in the same build.",
      "A third transition spelling, all in LE3.",
      targets=["PlayerController"], args=[("URL", "string")]),
    C("travel.switch_level", "travel", "SwitchLevel", "FFT", "high",
      "Switches level without a full travel. ME3 only.",
      "The cheap transition, useful for a test area that needs a reset without a reload.",
      targets=["PlayerController"], args=[("URL", "string")]),
    C("travel.restart_level", "travel", "RestartLevel", "FFT", "high",
      "Restarts the current level. ME3 only; the cleanest way to reset a test phase.",
      "A reset that does not need a process restart, which is the expensive thing here.",
      targets=["PlayerController"]),

    # ---- query -----------------------------------------------------------------------
    C("q.local_controlled", "query", "IsLocallyControlled", "TFT", "read",
      "Whether this controller is locally controlled. For a co-op client the answer should "
      "become true for the second player, and it is the single best gate before any input "
      "or camera test.",
      "Absent in LE2, present in LE1/LE3. The direct answer to 'is this client a player or "
      "a spectator'.", targets=["Controller"]),
    C("q.local_controller", "query", "GetALocalPlayerController", "TFT", "read",
      "Returns a local player controller. Note the 'A': it returns an arbitrary one, which "
      "in a two-player build is exactly the ambiguity this project is about.",
      "Present in LE1/LE3. A function whose name promises less than a caller assumes.",
      targets=["GameInfo", "Engine"]),
    C("q.player_controller", "query", "GetPlayerController", "TTT", "read",
      "Controller by index. Present in all three, so 'which player am I' is expressible "
      "everywhere.",
      "The portable way to address player 2 rather than 'the' player.",
      targets=["GameInfo"], args=[("PlayerIndex", "int")]),
    C("q.player_pawn", "query", "GetPlayerPawn", "FFT", "read",
      "Pawn by index. ME3 only, so LE1/LE2 must go through the controller.",
      "A convenience that only one build has; a port must not rely on it.",
      targets=["GameInfo"], args=[("PlayerIndex", "int")]),
    C("q.local_player_controller", "query", "LocalPlayerController", "TTT", "read",
      "The class of a local controller. Present in all three, so type checks are portable.",
      "Useful to verify that a hand-built controller is the class the rest of the code "
      "expects.", targets=["LocalPlayerController"]),
    C("q.map_name", "query", "GetURLMap", "TTT", "read",
      "The current map name. Present in all three and the cheapest way to prove two clients "
      "are in the same level.",
      "The first assertion of every multi-client test.",
      targets=["WorldInfo", "GameInfo"]),
    C("q.map_name_me2", "query", "GetMapName", "FTT", "read",
      "A second map-name accessor, absent in LE1.",
      "A port that calls only one of the two will fail on one game.",
      targets=["WorldInfo"]),

    # ---- audio -----------------------------------------------------------------------
    C("audio.play_sound", "audio", "PlaySound", "TTT", "low",
      "Plays a sound locally. Present in all three, so it is a safe positive control that "
      "produces an observable change without touching game state.",
      "A control with a visible log line and no state change - rare and useful.",
      targets=["Actor"], args=[("SoundCue", "object"), ("bNoReplication", "bool")]),
    C("audio.client_hear", "audio", "ClientHearSound", "FFT", "low",
      "The replicated sound path. ME3 only, and the one that matters once two clients exist.",
      "Only meaningful with a client, which is the definition of LE3's advantage.",
      targets=["PlayerController"]),
    C("audio.stop_all", "audio", "StopAllSounds", "FFT", "low",
      "Silences everything. A blunt reset that is useful when a test leaves audio looping.",
      "Cleanup, not a test.", targets=["Actor"]),
    C("audio.stop_music", "audio", "StopMusic", "TFF", "low",
      "Stops the music track. LE1 only in this spelling.",
      "LE1-only audio control; listed because LE1 has the fewest levers.",
      targets=["PlayerController"]),

    # ---- anim ------------------------------------------------------------------------
    C("anim.play", "anim", "PlayAnim", "TTT", "low",
      "Plays an animation on a skeletal mesh. Present everywhere, so scripted animation can "
      "be exercised in every build.",
      "Animation is a common source of a client looking wrong while behaving right.",
      targets=["SkeletalMeshComponent", "Actor"], args=[("AnimSeqName", "name")]),
    C("anim.stop", "anim", "StopAnim", "TTT", "low",
      "Stops it. The inverse.",
      "Round-trip partner.", targets=["SkeletalMeshComponent", "Actor"]),
    C("anim.play_anim_me3", "anim", "PlayAnimation", "FFT", "low",
      "The ME3 actor-level form, distinct from PlayAnim in the same build.",
      "A third spelling; measuring one does not measure the other.",
      targets=["Actor"]),
    C("anim.morph", "anim", "SetMorphWeight", "FFT", "low",
      "Facial morph weight. ME3 only, and the mechanism behind expressions in dialogue.",
      "Relevant to a spectator: expression changes are visible to both, so they must be "
      "either shared or suppressed.", targets=["SkeletalMeshComponent"]),
    C("anim.skeletal_mesh", "anim", "SetSkeletalMesh", "TTT", "medium",
      "Swaps a mesh. The most visible possible test of whether a command reached a "
      "specific pawn.",
      "Present everywhere and unmistakable, which makes it a good end-to-end control.",
      targets=["SkeletalMeshComponent"]),

    # ---- pause -----------------------------------------------------------------------
    C("pause.set", "pause", "SetPause", "TTT", "medium",
      "Pauses the game for the caller. In a one-player build this is global; with two "
      "clients it has to become per-session or it freezes both.",
      "Present in all three. The first command that must change meaning for co-op.",
      targets=["PlayerController"], args=[("bPause", "bool")]),
    C("pause.clear", "pause", "ClearPause", "FFT", "medium",
      "Unpauses. Absent in LE1, so LE1's pause must be closed by SetPause(false).",
      "An asymmetry that would leave LE1 stuck if a port assumed ClearPause.",
      targets=["PlayerController"]),
    C("pause.pause_game", "pause", "PauseGame", "TFT", "medium",
      "Pauses via the game object rather than a controller.",
      "A second pause route; the one that is world-wide by construction.",
      targets=["GameInfo"], args=[("bPause", "bool")]),
    C("pause.players_only", "pause", "bPlayersOnly", "TTT", "read",
      "The world flag that freezes pawns. Readable in all three and the same flag "
      "conversations set.",
      "The observable that makes 'the world thinks a cutscene is running' measurable.",
      targets=["WorldInfo"]),

    # ---- squad -----------------------------------------------------------------------
    C("squad.add", "squad", "AddMember", "TTT", "medium",
      "Adds a squad member. Present everywhere, so the party can be arranged identically "
      "across games.",
      "The party container is what the client is trying to control, so knowing how it is "
      "edited matters.",
      targets=["Squad", "SFXSquad", "BioSquad"]),
    C("squad.remove", "squad", "RemoveMember", "TTT", "medium",
      "Removes one. The inverse.",
      "Round-trip partner; also the way to test a client leaving.",
      targets=["Squad", "SFXSquad", "BioSquad"]),
    C("squad.member", "squad", "SquadMember", "TTT", "read",
      "Type/marker for a squad member pawn. The classification a takeover has to recognise.",
      "The offline and live way to tell a squadmate from the player, which is the first "
      "discrimination a co-op mod needs.", targets=["Pawn"]),
]


def cmd_list(args):
    data = load_catalogue() if os.path.isfile(CATALOGUE) else {"commands": COMMANDS}
    rows = data["commands"]
    if args.game:
        rows = [c for c in rows if c["present"].get(args.game)]
    if args.category:
        rows = [c for c in rows if c["category"] == args.category]
    if args.risk:
        want = {r.strip() for r in args.risk.split(",")}
        rows = [c for c in rows if c["risk"] in want]
    if args.mechanism:
        rows = [c for c in rows if c["mechanism"] == args.mechanism]

    print("%-30s %-13s %-7s %5s %5s %5s  %s"
          % ("ID", "CATEGORY", "RISK", "ME1", "ME2", "ME3", "CALL"))
    for c in sorted(rows, key=lambda c: (list(CATEGORIES).index(c["category"])
                                         if c["category"] in CATEGORIES else 99,
                                         RISK_ORDER.get(c["risk"], 9), c["id"])):
        print("%-30s %-13s %-7s %5s %5s %5s  %s"
              % (c["id"], c["category"], c["risk"],
                 *("yes" if c["present"][g] else "-" for g in GAMES), c["call"]))
    print("\n%d command(s)." % len(rows))
    if args.verbose:
        for c in rows:
            print("\n%s  [%s/%s]" % (c["id"], c["category"], c["risk"]))
            print("  call    : %s" % c["call"])
            print("  present : %s" % ", ".join(g for g in GAMES if c["present"][g]))
            print("  sync    : %s" % c["sync"])
            print("  why     : %s" % c["why"])
            if c["note"]:
                print("  note    : %s" % c["note"])
            if c["crashes"]:
                print("  CRASHES : %s" % ", ".join(c["crashes"]))
    return 0


def map_paths(root=None):
    """Per-game identifier tables, via the shared MELE data resolver."""
    import mele_paths
    root = root or mele_paths.maps_dir()
    return {g: os.path.join(root, "%s.json" % g) for g in GAMES}


def load_identifiers(root=None):
    """Measured identifier sets per game, lower-cased for lookup but keeping the spelling."""
    out = {}
    for g, path in map_paths(root).items():
        if not os.path.isfile(path):
            out[g] = None
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        out[g] = {}
        for ident in data.get("identifiers", []):
            out.setdefault(g, {})[ident.lower()] = ident
    return out


def load_catalogue(path=None):
    path = path or CATALOGUE
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_catalogue(args):
    idents = load_identifiers(args.maps)
    missing = [g for g, v in idents.items() if v is None]
    if missing:
        print("no map file for %s; run tools/melepkg/mele_script_map.py first" % ", ".join(missing))
        if not args.allow_missing_maps:
            return 1

    entries = []
    disagreements = []
    for c in COMMANDS:
        entry = dict(c)
        entry["role"] = role_of(c)
        entry["role_meaning"] = ROLES.get(entry["role"])
        entry["present"] = {}
        entry["present_evidence"] = {}
        for g in GAMES:
            if idents.get(g) is None:
                entry["present"][g] = c["present"][g]
                entry["present_evidence"][g] = "no map file; value taken from the taxonomy"
                continue
            hit = idents[g].get(c["call"].lower())
            entry["present"][g] = bool(hit)
            if hit != (c["call"] if hit else None) and hit:
                entry["present_evidence"][g] = "found as %r" % hit
            else:
                entry["present_evidence"][g] = "absent from identifier table" if not hit else "exact"
            if bool(hit) != c["present"][g]:
                disagreements.append((c["id"], c["call"], g, c["present"][g], bool(hit)))
        entries.append(entry)

    if disagreements:
        print("TAXONOMY DISAGREES WITH THE MAP FILES on %d point(s):" % len(disagreements))
        for cid, call, g, claimed, measured in disagreements:
            print("  %-30s %-28s %s claimed=%s measured=%s"
                  % (cid, call, g, claimed, measured))
        print("\nThe measured value wins. Fix the taxonomy, then regenerate. This check is the")
        print("reason a hand-edited 'present' flag cannot survive: it is recomputed, not trusted.")
        if not args.force:
            return 2

    doc = {
        "schema": 1,
        "generated_by": "tools/ue3live/ue3_command_suite.py catalogue",
        "regenerate_when": "the taxonomy in ue3_command_suite.py changes, or a new map file "
                           "appears in local/MELE/maps",
        "what_this_is": "One entry per kind of command a Legendary Edition build exposes, "
                        "grouped by the MECHANISM used to reach it. 'present' is measured "
                        "from local/MELE/maps/<game>.json (decompressed identifier tables), never "
                        "typed by hand; `verify` recomputes it and fails on drift.",
        "evidence_level": "present = the identifier exists in that build's decompressed script "
                          "data. It does NOT prove the function is reachable, owned by the "
                          "class we intend, or safe to call. `probe` answers those live.",
        "games": list(GAMES),
        "categories": CATEGORIES,
        "risk_levels": {
            "read": "queries only; cannot change state",
            "low": "visual or self-restoring",
            "medium": "moves, attaches or controls something",
            "high": "creates/destroys objects, changes level, touches shared save state",
            "blocked": "measured to crash this build; listed so it is not retried",
        },
        "counts": {
            "commands": len(entries),
            "by_game": {g: sum(1 for e in entries if e["present"][g]) for g in GAMES},
            "by_category": {k: sum(1 for e in entries if e["category"] == k) for k in CATEGORIES},
            "by_risk": {k: sum(1 for e in entries if e["risk"] == k)
                        for k in sorted(RISK_ORDER, key=RISK_ORDER.get)},
            "in_all_three": sum(1 for e in entries if all(e["present"][g] for g in GAMES)),
        },
        "commands": entries,
    }

    out = args.out or CATALOGUE
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")

    c = doc["counts"]
    print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
    print("  %d command mechanisms" % c["commands"])
    print("  present: ME1 %d, ME2 %d, ME3 %d, all three %d"
          % (c["by_game"]["ME1"], c["by_game"]["ME2"], c["by_game"]["ME3"], c["in_all_three"]))
    print("  risk: " + ", ".join("%s %d" % (k, v) for k, v in c["by_risk"].items()))
    return 0


def cmd_verify(args):
    """Recompute presence from the map files and compare with commands.json."""
    doc = load_catalogue(args.catalogue)
    idents = load_identifiers(args.maps)
    missing = [g for g, v in idents.items() if v is None]
    if missing:
        print("cannot verify: no map file for %s" % ", ".join(missing))
        return 1

    bad = []
    for c in doc["commands"]:
        for g in GAMES:
            measured = idents[g].get(c["call"].lower()) is not None
            claimed = bool(c["present"].get(g))
            if measured != claimed:
                bad.append((c["id"], c["call"], g, claimed, measured))

    unknown_risk = [c["id"] for c in doc["commands"] if c["risk"] not in RISK_ORDER]
    unknown_cat = [c["id"] for c in doc["commands"] if c["category"] not in CATEGORIES]
    blocked_without_crash = [c["id"] for c in doc["commands"]
                             if c["risk"] == "blocked" and not c["crashes"]]
    ids = [c["id"] for c in doc["commands"]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})

    print("%s: %d commands" % (os.path.basename(args.catalogue or CATALOGUE), len(doc["commands"])))
    for label, items in (("presence drift vs local/MELE/maps", bad),
                         ("unknown risk", unknown_risk),
                         ("unknown category", unknown_cat),
                         ("'blocked' with no recorded crash", blocked_without_crash),
                         ("duplicate id", dupes)):
        print("  %-38s %s" % (label, "ok" if not items else "%d problem(s)" % len(items)))
        for item in items[:20]:
            print("      %s" % (item,))
    ok = not (bad or unknown_risk or unknown_cat or blocked_without_crash or dupes)
    print("\n%s" % ("ALL OK" if ok else "DRIFT - regenerate with `catalogue --force` after fixing"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------------------
# Live resolution. Read-only; calls nothing into the game.
# ---------------------------------------------------------------------------------------
def resolve_target(cands, targets, chain_of):
    """Pick one candidate UFunction, or explain why none was picked.

    Pure, and separated from the process reading on purpose: this is the decision that
    decides which UFunction a plan dispatches, and getting it wrong is what an untested
    resolver costs. Returning a reason on every non-match is part of the contract - a
    resolver that returns None silently is indistinguishable from one that never looked.

    Returns (chosen_or_None, reason_or_None).
    """
    if not cands:
        return None, None                      # caller supplies the "absent" wording
    if not targets:
        if len(cands) == 1:
            return cands[0], None
        return cands[0], ("%d candidates and no `targets` in the catalogue, so the choice is "
                          "unverified: %s"
                          % (len(cands),
                             ", ".join(sorted({x["owner"] or "?" for x in cands}))))
    accept = {t.lower() for t in targets}
    for cand in cands:
        owner = cand["owner"]
        if not owner:
            continue
        if owner.lower() in accept or any(x.lower() in accept for x in chain_of(owner)):
            if len(cands) == 1:
                return cand, None
            return cand, ("resolved to %s.%s out of %d candidates - verify this is the "
                          "intended one" % (owner, cand.get("call"), len(cands)))
    return None, ("%d candidate(s) but none owned by %s; candidates: %s"
                  % (len(cands), "/".join(targets),
                     ", ".join(sorted({x["owner"] or "?" for x in cands}))))


def probe(pid, exe, offsets_path, catalogue=None):
    import ue3_reader
    from ue3_proc import Reader, module_base
    from ue3_playerchain import live_engines

    doc = catalogue or load_catalogue()
    base = module_base(pid, exe)
    if not base:
        return {"error": "%s not loaded in pid %d" % (exe, pid)}
    reader = Reader(pid)
    try:
        game = ue3_reader.Game(reader, base, offsets_path)

        # One pass: index every UFunction by name, with its owning class, and every Class by
        # name so the target matching below does not rescan GObjects per candidate.
        # A name is NOT unique, so every candidate is kept and the command's `targets`
        # selects between them. Silently taking the first hit is how a port calls the wrong
        # SetLocation and gets a no-op that reads as a gate.
        by_name = {}
        classes = {}
        for i in range(1, game.count):
            obj = reader.ptr(game.data + i * 8)
            if not obj:
                continue
            cls = game.obj_class(obj)
            if cls == "Class":
                n = game.object_name(obj)
                if n:
                    classes.setdefault(n, obj)
                continue
            if cls != "Function":
                continue
            name = game.object_name(obj)
            if not name:
                continue
            owner_obj = reader.ptr(obj + 0x40)
            owner = game.object_name(owner_obj) if owner_obj else None
            parms = []
            field = reader.ptr(obj + 0x70)
            seen = set()
            while field and field not in seen:
                seen.add(field)
                kind = game.obj_class(field)
                fname = game.object_name(field)
                raw = reader.read(field + 0x60, 0x40)
                if raw and kind and kind != "Function":
                    parms.append({
                        "name": fname, "kind": kind,
                        "offset": struct.unpack_from("<i", raw, 0x94 - 0x60)[0],
                        "size": struct.unpack_from("<i", raw, 0x74 - 0x60)[0],
                    })
                field = reader.ptr(field + 0x68)
            parms.sort(key=lambda p: p["offset"])
            by_name.setdefault(name.lower(), []).append({
                "call": name, "owner": owner, "ptr": hex(obj),
                "parms": parms,
                "parms_size": (parms[-1]["offset"] + parms[-1]["size"]) if parms else 0,
            })

        # Chain lookup for target matching: a target may name a superclass.
        chain_cache = {}

        def chain_of(owner_name):
            if owner_name in chain_cache:
                return chain_cache[owner_name]
            chain = []
            o = classes.get(owner_name)
            if o:
                cur = reader.ptr(o + 0x50)
                for _ in range(48):
                    if not cur:
                        break
                    n = game.object_name(cur)
                    if n:
                        chain.append(n)
                    cur = reader.ptr(cur + 0x60)
            chain_cache[owner_name] = chain
            return chain

        results = []
        for c in doc["commands"]:
            cands = by_name.get(c["call"].lower(), [])
            chosen, why = resolve_target(cands, c["targets"], chain_of)
            if not cands:
                why = "no UFunction named %s in this process" % c["call"]

            results.append({
                "id": c["id"], "category": c["category"], "risk": c["risk"],
                "call": c["call"], "present_in_build": c["present"],
                "resolved": chosen is not None,
                "resolved_to": chosen,
                "candidates": len(cands),
                "candidate_owners": sorted({x["owner"] or "?" for x in cands}),
                "note": why,
            })

        engines = live_engines(game)
        return {
            "schema": 1,
            "read_only": True,
            "exe": exe,
            "pid": pid,
            "objects": game.count,
            "engine_global": hex(game.engine_global) if game.engine_global else None,
            "live_engines": [{"name": e["name"], "class": e["class"], "ptr": hex(e["ptr"])}
                             for e in engines],
            "resolved_count": sum(1 for r in results if r["resolved"]),
            "unresolved_count": sum(1 for r in results if not r["resolved"]),
            "commands": results,
        }
    finally:
        reader.k32.CloseHandle(reader.handle)


def cmd_probe(args):
    p = probe(args.pid, args.exe, args.offsets,
              load_catalogue(args.catalogue) if args.catalogue else None)
    if "error" in p:
        print(p["error"])
        return 1
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(p, f, indent=1)
        print("wrote %s (%d bytes)" % (args.out, os.path.getsize(args.out)))
    print("%s pid %d: %d objects, %d resolved / %d unresolved"
          % (p["exe"], p["pid"], p["objects"], p["resolved_count"], p["unresolved_count"]))
    for r in p["commands"]:
        if args.all or not r["resolved"]:
            mark = "RESOLVED" if r["resolved"] else "MISSING "
            print("  %s %-30s %-28s %s" % (mark, r["id"], r["call"],
                                           (r["resolved_to"] or {}).get("owner") or ""))
            if r["note"]:
                print("           %s" % r["note"])
    return 0 if p["unresolved_count"] == 0 else 3


# ---------------------------------------------------------------------------------------
# Phases: the actual test area. Ordered so that a failure in one phase cannot be blamed on
# a later one, and so that everything reversible is proven reversible before anything
# destructive runs. Each phase names the commands it uses, so the plan is a real plan and
# not a cross-product: a command appears in exactly one phase.
#
# `optional` marks a phase a normal run does NOT do, because it touches state that is shared
# with the player's real game (saves, the profile) or drops both clients (travel).
# ---------------------------------------------------------------------------------------
PHASES = [
    {"id": "P00-baseline", "title": "Baseline", "risk": "read",
     "uses": ["q.local_controlled", "input.player_input", "input.cinematic_bit",
              "pause.players_only", "xform.collide_world", "repl.role"],
     "does": "Read-only. Player chain, physics, location, view target, HUD, paused, "
             "bPlayersOnly, bCinematicMode, collision.",
     "proves": "The session is in gameplay and every offset resolves. Nothing else counts "
               "until this passes.",
     "abort_if": "any read fails - that is an offsets problem, not a co-op problem"},

    {"id": "P01-query", "title": "Queries", "risk": "read",
     "uses": ["q.map_name", "q.map_name_me2", "q.local_controller", "q.player_controller",
              "q.player_pawn", "q.local_player_controller", "console.loaded",
              "console.channel", "console.on_command", "kismet.active", "kismet.activated",
              "kismet.used", "kismet.input_links", "kismet.output_links",
              "repl.remote_role", "repl.net_owner", "save.can_save",
              "camera.view_point", "combat.can_fire", "combat.can_fire_me3"],
     "does": "Every query-shaped command in the catalogue, on both clients.",
     "proves": "The two clients agree on identity and context: same map, same controller "
               "count, same ownership answers. The first real sync comparison, and it "
               "mutates nothing.",
     "abort_if": "map name or player index differs - the clients are not in the same world"},

    {"id": "P02-console", "title": "Console control", "risk": "low",
     "uses": ["console.stat", "console.physics", "console.collision", "console.say",
              "console.exec", "console.exec_verb"],
     "does": "A verb that must visibly work (Stat / Physics / Collision), then Say as a "
             "shared timing marker on both clients, then the same line through Exec.",
     "proves": "The reference command channel works, and Exec and ConsoleCommand reach the "
               "same place. Everything after this is compared against this baseline.",
     "abort_if": "the control verb does not change the screen on either client"},

    {"id": "P02b-console-state", "title": "Console state verbs (reversible)", "risk": "low",
     "uses": ["console.god", "console.fly", "console.walk", "console.slomo",
              "console.timedilation", "console.freezeframe", "console.ghost"],
     "does": "Each verb ON then OFF, reading pawn physics and world time dilation between. "
             "Every one of these restores itself when issued twice.",
     "proves": "The console verbs that CHANGE state reach the engine and are reversible. "
               "P02 proved the channel; this proves the channel carries a mutation.",
     "abort_if": "physics or time dilation does not return to its P00 value - stop, the "
                 "copy now has a state the later phases did not plan for"},

    {"id": "P02c-console-destructive", "title": "Console verbs that cannot be undone",
     "risk": "high", "optional": True,
     "uses": ["console.killpawns", "console.allweapons", "console.suicide",
              "console.playersonly", "console.team"],
     "does": "Only on a throwaway copy with a restore point. KillPawns, AllWeapons, "
             "Suicide, PlayersOnly, Team - each changes the world in a way no second call "
             "reverses.",
     "proves": "How the world-level verbs treat a second player. PlayersOnly is the one "
               "that matters: it is already the mechanism a scene uses to freeze pawns.",
     "abort_if": "the world is left with bPlayersOnly set - a stuck freeze is "
                 "indistinguishable from a hang when it happens an hour into a session"},

    {"id": "P03-timer", "title": "Timer round-trip", "risk": "low",
     "uses": ["timer.set", "timer.is_active", "timer.clear"],
     "does": "SetTimer, IsTimerActive, ClearTimer, IsTimerActive again.",
     "proves": "A named, per-object, self-restoring command works and can be observed. The "
               "cheapest real mutation test in the suite.",
     "abort_if": "IsTimerActive disagrees between the two reads on one client"},

    {"id": "P04-visual", "title": "Visual round-trip", "risk": "low",
     "uses": ["xform.set_hidden", "xform.set_scale", "xform.set_draw_scale",
              "audio.play_sound", "audio.stop_music", "audio.stop_all",
              "audio.client_hear", "anim.play", "anim.stop", "anim.play_anim_me3",
              "anim.skeletal_mesh", "anim.morph", "hud.show", "hud.toggle",
              "hud.client_set_hud", "msg.client_message", "msg.broadcast", "msg.send",
              "msg.receive_localized"],
     "does": "SetHidden(true) -> read bHidden -> SetHidden(false); scale to its current "
             "value; PlaySound; PlayAnim then StopAnim; HUD and message visibility.",
     "proves": "Commands reach the intended object and are reversible. A positive control "
               "for the whole dispatch path, with the HUD as the per-client visible proof.",
     "abort_if": "bHidden did not change after SetHidden - the call went somewhere else"},

    {"id": "P05-state", "title": "State machine", "risk": "medium",
     "uses": ["state.get_name", "state.push", "state.begin", "state.pop", "state.end"],
     "does": "GetStateName, PushState/BeginState, GetStateName, PopState/EndState, "
             "GetStateName. Compare the three names.",
     "proves": "State is readable and comparable across clients. State divergence is "
               "silent, so it has to be measured here rather than noticed later.",
     "abort_if": "GetStateName is absent (ME1) - skip the assertion, do not substitute a "
                 "guess about what the state should be"},

    {"id": "P06-transform", "title": "Transform, zero displacement", "risk": "medium",
     "uses": ["xform.set_location", "xform.set_rotation", "xform.teleport",
              "xform.set_base", "xform.set_hard_attach"],
     "does": "SetLocation to the pawn's CURRENT location; SetRotation to its CURRENT "
             "rotation; Teleport to the same point; SetBase/SetHardAttach to null. Read "
             "both back after each.",
     "proves": "The transform path works without moving anything, so a later failure is "
               "about the value and not about the call.",
     "abort_if": "location changed - the call is not idempotent and must be understood "
                 "before anything is placed"},

    {"id": "P07-camera", "title": "Camera and view target", "risk": "medium",
     "uses": ["camera.set_view_target", "camera.client_set_view_target", "camera.set_mode",
              "camera.update", "camera.calc_camera"],
     "does": "Read the current view target and mode, set the view target to the SAME actor, "
             "set the camera mode to its current value, read back.",
     "proves": "The camera path is per-controller, which is what a second viewport needs. A "
               "camera that is world-level cannot be made per-player later.",
     "abort_if": "setting the view target to the current target changes the view"},

    {"id": "P08-placement", "title": "Placement order", "risk": "medium",
     "uses": ["xform.set_collision", "xform.set_collision_type", "kismet.physics",
              "xform.client_set_location", "xform.client_set_rotation"],
     "does": "The documented working sequence: SetCollision(false) -> SeqAct_SetPhysics(1) "
             "-> ClientSetLocation (ME3) or SetLocation (LE1/LE2) to the same point -> "
             "restore collision.",
     "proves": "Placement works, which is the prerequisite for putting a second body "
               "anywhere. Measured to fail without this exact order.",
     "abort_if": "the pawn is at physics 7 (scripted) - no placement test is meaningful"},

    {"id": "P09-input", "title": "Input gate", "risk": "medium",
     "uses": ["input.apply_bindings", "input.cinematic_mode", "input.enable",
              "input.disable", "input.bio_player_control", "input.bio_input_trap",
              "input.bio_player_input_event"],
     "does": "SetCinematicMode(true), read bCinematicMode, SetCinematicMode(false), read "
             "bCinematicMode. In LE1 also inventory the BioSeqAct/BioSeqEvt input hooks.",
     "proves": "The control gate is settable and observable. This is the mechanism a "
               "spectator lock is built on, so it must be proven before it is designed on.",
     "abort_if": "bCinematicMode does not follow the call - the gate in use is a different "
                 "one, and the whole spectator design rests on the wrong field"},

    {"id": "P10-combat", "title": "Combat gates", "risk": "medium",
     "uses": ["combat.start_fire", "combat.stop_fire", "combat.weapon_fire",
              "combat.start_firing", "combat.handle_firing", "combat.use_power",
              "combat.use_ability", "combat.activate_power", "combat.hotkey",
              "combat.squad_power", "combat.busy_state", "combat.give_weapon",
              "combat.add_inventory", "combat.take_damage", "combat.apply_damage"],
     "does": "CanFireWeapon on both the host pawn and the client pawn (done in P01); then "
             "StartFire / StopFire / UsePower / UseAbility / ActivatePower / doHotKey on "
             "each, and read busy state before and after. Last: one TakeDamage of 0 damage "
             "on the client pawn, to prove the damage path reaches it.",
     "proves": "Whether the gate answer differs between the two pawns. That difference IS "
               "the co-op bug, stated as a value rather than a symptom.",
     "abort_if": "no weapon is equipped - equip first, or the answer means nothing"},

    {"id": "P11-kismet", "title": "Kismet events", "risk": "medium",
     "uses": ["kismet.sequence", "kismet.sequence_op", "kismet.cause_event",
              "kismet.trigger_event", "kismet.remote_event", "kismet.activate",
              "kismet.activate_output_link", "kismet.teleport", "kismet.interp",
              "kismet.delay", "kismet.cinematic_mode"],
     "does": "Count Sequence and SequenceOp objects on both clients; read bActive / "
             "Activated / Used before and after; CauseEvent and TriggerEvent on a "
             "throwaway name; in LE2/LE3 also Activate on a harmless action.",
     "proves": "Both clients loaded the same Kismet graph and the named-event path reaches "
               "it. This is the base for delaying a walk-in trigger until player 2 arrives.",
     "abort_if": "sequence counts differ - the level graphs are not identical, and no "
                 "trigger-delay work is meaningful on top of that"},

    {"id": "P12-conversation", "title": "Conversation and spectator", "risk": "medium",
     "uses": ["conv.start", "conv.end", "conv.mode", "conv.facefx"],
     "does": "Read the conversation verdict; SetCinematicMode(true) as a stand-in for a "
             "scene; read bPlayersOnly and the pawn's physics; restore. Do NOT start a "
             "real conversation - that needs a story context and burns a save.",
     "proves": "The spectator state is reachable and observable without a real dialogue, "
               "so the mechanism can be built and tested before a scene is ever involved.",
     "abort_if": "bPlayersOnly is already 1 at baseline - something else owns the world and "
                 "the phase cannot be attributed"},

    {"id": "P13-possession", "title": "Possession (the actual takeover)", "risk": "high",
     "uses": ["pawn.unpossess", "pawn.possess", "pawn.spawn_default", "pawn.restart_player",
              "pawn.spawn", "pawn.layout_players"],
     "does": "UnPossess the current pawn, Possess the squadmate pawn, read the chain back, "
             "re-run P10 on the new pawn, then Possess the original pawn back. In LE3 only, "
             "PostLogin and RemovePlayer are available to test the engine's own new-player "
             "path; both are absent from LE1/LE2 so the phase degrades to possession alone.",
     "proves": "The ME3SquadCoop mechanism works on this build. Everything before this "
               "phase exists to make this phase debuggable instead of a black box.",
     "abort_if": "no second pawn, or the first UnPossess leaves the controller without a "
                 "pawn - restore the chain before going further"},

    {"id": "P13b-engine-player-path", "title": "Engine new-player path (LE3 only)",
     "risk": "high", "optional": True,
     "uses": ["pawn.post_login", "pawn.remove_player"],
     "does": "LE3 only, throwaway copy only. Call PostLogin for a synthetic login, then "
             "RemovePlayer to undo it. Never CreatePlayer - it is blocked on LE1/LE2 and "
             "unnecessary where PostLogin works.",
     "proves": "Whether the engine's own new-player hook can be driven without CreatePlayer, "
               "which is the one unproven route left. LE3 is the only build that can answer.",
     "abort_if": "the process faults - do not retry on another build; the LE1/LE2 pattern "
                 "is that it crashes in the engine's own thread AFTER returning success"},

    {"id": "P14-replication", "title": "Replication surface", "risk": "medium",
     "uses": ["repl.net_update_frequency", "repl.replicated_event", "repl.server_move",
              "repl.client_ack_move", "repl.client_adjust_position"],
     "does": "Read Role / RemoteRole / bNetOwner on both pawns in all three games (P01); in "
             "LE3 also read NetUpdateFrequency and fire ReplicatedEvent on a harmless "
             "property. Do not call ServerMove by hand.",
     "proves": "How much authority machinery the build actually has. LE1 cannot express an "
               "asymmetric role at all, which changes the co-op design for that game.",
     "abort_if": "never - this phase is informational and cannot fail destructively"},

    {"id": "P15-squad", "title": "Squad membership", "risk": "medium",
     "uses": ["squad.member", "squad.add", "squad.remove"],
     "does": "Read the squad list; remove a member and re-add the same one; read again and "
             "compare the list.",
     "proves": "The party container can be edited and restored. The client IS a squadmate, "
               "so this container is the thing the client is fighting the AI over.",
     "abort_if": "the squad list is not restored exactly - the run leaves the party altered"},

    {"id": "P16-pause", "title": "Pause semantics", "risk": "medium",
     "uses": ["pause.set", "pause.clear", "pause.pause_game"],
     "does": "SetPause(true), read paused on both clients, SetPause(false), read again. "
             "Then the same via PauseGame.",
     "proves": "Whether pause is per-controller or world-wide on this build. It is the "
               "command whose meaning must change most for co-op.",
     "abort_if": "either client stays paused after SetPause(false)"},

    {"id": "P17-travel", "title": "Level transition", "risk": "high", "optional": True,
     "uses": ["console.open", "travel.server_travel", "travel.client_travel",
              "travel.open_map", "travel.switch_level", "travel.restart_level",
              "console.login", "console.logout"],
     "does": "One transition and back, on a throwaway copy with a restore point. Login and "
             "Logout may only be used here, because this is the only phase where a "
             "dropped client is the expected outcome.",
     "proves": "Whether a second client survives a level change. Every transition is a "
               "place a client gets dropped, so this is the transition test itself.",
     "abort_if": "either client is gone after the return - stop, the copy is no longer "
                 "trustworthy for later phases"},

    {"id": "P18-save", "title": "Save and load", "risk": "high", "optional": True,
     "uses": ["save.save_game", "save.load_game", "save.save_profile"],
     "does": "Nothing, unless someone deliberately opts in. When they do: save to a "
             "throwaway slot, load it, and confirm the session rebuilds.",
     "proves": "What a save does when there are two players. It is here to be measured "
               "once, on purpose, never as a side effect of another test.",
     "abort_if": "any doubt at all - the save path is shared with the player's real "
                 "profile through a fixed Documents folder, so this phase can lose real "
                 "progress. Off by default for that reason"},

    {"id": "P19-cleanup", "title": "Cleanup and re-baseline", "risk": "low",
     "uses": [],
     "repeats": ["pawn.unpossess", "xform.set_collision", "xform.set_collision_type",
                 "xform.set_hidden", "input.cinematic_mode", "pause.set", "pause.clear",
                 "state.get_name", "timer.clear"],
     "does": "UnPossess anything possessed, restore physics, collision and hidden flags to "
             "the P00 values, then re-run P00 and diff.",
     "proves": "The suite left the game as it found it. Any diff is a leak, and a leak "
               "found here is one that would have appeared mid-session an hour later.",
     "abort_if": "any P00 value differs - the run is void and the copy should be restarted"},
]

# ---------------------------------------------------------------------------------------
# Field phases: the split-screen plan, expressed as verified reads and writes.
#
# These are separate from the command phases above because they are a different kind of act -
# they change a named engine field rather than calling a function - and because the write path
# has a stricter contract (allow-list + mandatory read-back). Keeping them in one list with the
# calls would hide which entries can write.
#
# Every one of these is written to be SAFE TO RE-RUN: it reads the current value, records it,
# writes, verifies, and the cleanup phase restores. A phase that cannot be re-run safely does
# not belong here.
# ---------------------------------------------------------------------------------------
FIELD_PHASES = [
    {"id": "S00-splitscreen-baseline", "title": "Split-screen baseline (read only)",
     "risk": "read",
     "writes": [
         {"field": "viewport.desired_splitscreen_type"},
         {"field": "viewport.active_splitscreen_type"},
         {"field": "viewport.splitscreen_info"},
         {"field": "gameinfo.num_players"},
         {"field": "gameinfo.max_players"},
         {"field": "gameinfo.max_players_allowed"},
     ],
     "does": "Read the whole split-screen and player-cap surface and record it.",
     "proves": "What the build currently says about itself: how many players it admits, how "
               "many layouts the viewport holds, and whether a split is already active.",
     "abort_if": "any read fails - that is an offsets gap for this build, not a split problem"},

    {"id": "S01-request-split", "title": "Request a split layout",
     "risk": "medium",
     "writes": [{"field": "viewport.desired_splitscreen_type", "value": 1},
                {"field": "viewport.active_splitscreen_type"}],
     "does": "Set DesiredSplitscreenType to 1 and read ActiveSplitscreenType back. The engine "
             "sets Active itself; we never write it.",
     "proves": "The documented working step: the viewport accepts a split request without a "
               "crash, and reports what it did.",
     "abort_if": "the read-back of desired does not equal 1 - the write did not land",
     "note": "With one LocalPlayer the engine may set Active=1 while still rendering one view. "
             "That is fail point A3 become measurable rather than argued."},

    {"id": "S02-restore-split", "title": "Restore the split layout",
     "risk": "medium",
     "writes": [{"field": "viewport.desired_splitscreen_type", "value": 0},
                {"field": "viewport.active_splitscreen_type"}],
     "does": "Set DesiredSplitscreenType back to 0 and read Active back.",
     "proves": "The change is reversible, which is the condition for the split experiment "
               "being cheap to repeat.",
     "abort_if": "desired does not read back 0"},

    {"id": "S03-player-cap", "title": "Player cap (read, and correct the record)",
     "risk": "read",
     "writes": [{"field": "gameinfo.max_players"},
                {"field": "gameinfo.max_players_allowed"},
                {"field": "gameinfo.num_players"}],
     "does": "Read MaxPlayers, MaxPlayersAllowed and NumPlayers. Writes nothing: both LE1 and "
             "LE2 already read 16/32, so the cap raise this project previously planned is "
             "probably unnecessary.",
     "proves": "Whether the cap actually blocks anything on this build. A planned step that "
               "turns out to be unnecessary is risk removed, not work saved quietly.",
     "abort_if": "never - this phase only reads"},

    {"id": "S04-player-input-slot", "title": "Controller PlayerInput slot (read only)",
     "risk": "read",
     "writes": [{"field": "controller.player_input"}],
     "does": "Read the PlayerInput pointer on the local player's controller. Does NOT write it: "
             "p2v2_input.dll was built for that step and has never been run, and a wrong "
             "pointer here faults on the next input tick.",
     "proves": "The slot exists and is readable, so the p2v2_input experiment has a known "
               "target and a known expected value before it is attempted.",
     "abort_if": "never - this phase only reads"},
]




def cmd_phases(args):
    print("%-24s %-6s %-8s %s" % ("PHASE", "RISK", "OPTIONAL", "TITLE"))
    for p in PHASES:
        print("%-24s %-6s %-8s %s" % (p["id"], p["risk"],
                                      "yes" if p.get("optional") else "-", p["title"]))
        if args.verbose:
            print("   uses     : %s" % (", ".join(p["uses"]) or "(none - restore only)"))
            if p.get("repeats"):
                print("   repeats  : %s" % ", ".join(p["repeats"]))
            print("   does     : %s" % p["does"])
            print("   proves   : %s" % p["proves"])
            print("   abort if : %s" % p["abort_if"])
    optional = [p["id"] for p in PHASES if p.get("optional")]
    print("\n%d command phases, %d of them optional (%s)."
          % (len(PHASES), len(optional), ", ".join(optional) or "none"))
    print("Run in order. Optional phases are off by default because they touch state shared")
    print("with the player's real game: saves and the profile live in one Documents folder")
    print("for every copy, and a level transition drops both clients.")
    print("\nField phases (the split-screen plan) are separate: `phases --fields`.")
    return 0


def cmd_fields(args):
    """The split-screen field phases, with per-game coverage checked before printing."""
    import ue3_fields
    game = args.game or "ME1"
    print("Field phases for %s - reads and verified writes, on an allow-list.\n" % game)
    blocked = {}
    for p in FIELD_PHASES:
        print("%-24s %-6s %s" % (p["id"], p["risk"], p["title"]))
        print("   does     : %s" % p["does"])
        print("   proves   : %s" % p["proves"])
        print("   abort if : %s" % p["abort_if"])
        if p.get("note"):
            print("   note     : %s" % p["note"])
        for op in p["writes"]:
            status, off = ue3_fields.coverage(op["field"], game)
            verb = "write" if ("value" in op and op["value"] is not None) else "read "
            if status == "measured":
                print("     [%-5s] %-40s 0x%-5X" % (verb, op["field"], off))
            else:
                print("     [%-5s] %-40s UNMEASURED on %s" % (verb, op["field"], game))
                blocked.setdefault(p["id"], []).append(op["field"])
        print()
    if blocked:
        print("%d of %d phases cannot run on %s:" % (len(blocked), len(FIELD_PHASES), game))
        for pid, fields in blocked.items():
            print("  %s needs %s" % (pid, ", ".join(fields)))
        print("\nMeasure those offsets on a live %s first: ue3_discover_external.py then "
              "ue3_verify_offsets.py. Do not borrow another build's offsets - "
              "PlayerController.Pawn alone is +0x3DC / +0x294 / +0x3BC across the three."
              % game)
    else:
        print("All %d phases are runnable on %s." % (len(FIELD_PHASES), game))
    return 0


def build_plan(probe_doc, catalogue, phases, game, include_optional=False):
    """Turn a probe result into ordered, per-phase bridge requests.

    A plan entry carries the object to act on, the UFunction it resolved to, the parms
    bytes it must be called with, and the assertion that decides pass/fail. Python does not
    execute it: the in-game bridge does, on the game's main thread, because ProcessEvent
    must run there and its vtable slot differs per game (LE1 +0x238, LE2 +0x228, LE3 +0x230
    as a wrapper). LE1/LE2 also crash inside CreatePlayer from the game's own thread AFTER
    a correct call, so dispatch has to stay in-process where the crash can at least be
    attributed.

    Commands absent from this build, unresolved in the probe, or blocked by a measured
    crash are reported as `skipped` with a reason, never dropped: a plan that silently
    omits a command reads as "passed".
    """
    resolved = {r["id"]: r for r in probe_doc["commands"]}
    by_id = {c["id"]: c for c in catalogue["commands"]}
    plan = {"schema": 1, "game": game, "exe": probe_doc["exe"], "pid": probe_doc["pid"],
            "executed_by": "the in-game bridge, on the game's main thread",
            "why_not_here": "ProcessEvent runs on the game thread and its vtable slot "
                            "differs per game. LE1/LE2 also crash inside CreatePlayer from "
                            "their own thread after a correct call, so dispatch must stay "
                            "in-process.",
            "include_optional": include_optional,
            "phases": []}
    seen = set()
    for ph in phases:
        entries = []
        # `repeats` are commands a later phase must run AGAIN (cleanup restores what
        # earlier phases changed). They are separate from `uses` so that "scheduled
        # twice" means something: only phases that say so may reuse a command.
        for cid in ph["uses"] + ph.get("repeats", []):
            repeat = cid in seen
            seen.add(cid)
            c = by_id.get(cid)
            if c is None:
                entries.append({"id": cid, "status": "skipped",
                                "note": "named by the phase but absent from the catalogue"})
                continue
            if c["risk"] == "blocked":
                entries.append({"id": cid, "call": c["call"], "status": "skipped",
                                "note": "blocked: measured to crash %s"
                                        % "/".join(c["crashes"])})
                continue
            if not c["present"].get(game):
                entries.append({"id": cid, "call": c["call"], "status": "skipped",
                                "note": "identifier absent from the %s build" % game})
                continue
            r = resolved.get(cid)
            if not r or not r.get("resolved"):
                entries.append({"id": cid, "call": c["call"], "status": "unresolved",
                                "note": (r or {}).get("note") or "not probed"})
                continue
            entries.append({
                "id": cid, "call": c["call"], "status": "ready",
                "repeat": repeat,
                "owner": r["resolved_to"]["owner"],
                "function": r["resolved_to"]["ptr"],
                "role": role_of(c),
                "role_meaning": ROLES.get(role_of(c)),
                "parms": r["resolved_to"]["parms"],
                "parms_size": r["resolved_to"]["parms_size"],
                "risk": c["risk"],
                "sync": c["sync"],
                "assert": c["sync"],
            })
        plan["phases"].append({
            "id": ph["id"], "title": ph["title"], "risk": ph["risk"],
            "optional": bool(ph.get("optional")),
            "does": ph["does"], "proves": ph["proves"], "abort_if": ph["abort_if"],
            "commands": entries,
        })
    # A command present in the build, used by no phase, is coverage this suite does not
    # have. Report it rather than let the list look complete.
    unused = [c["id"] for c in catalogue["commands"]
              if c["present"].get(game) and c["risk"] != "blocked" and c["id"] not in seen]
    plan["uncovered"] = sorted(unused)
    return plan


def cmd_plan(args):
    probe_doc = json.load(open(args.probe, encoding="utf-8"))
    cat = load_catalogue(args.catalogue)
    game = args.game
    if not game:
        exe = (probe_doc.get("exe") or "").lower()
        game = {"masseffect1.exe": "ME1", "masseffect2.exe": "ME2",
                "masseffect3.exe": "ME3"}.get(exe)
    if game not in GAMES:
        print("cannot tell which game this probe is from; pass --game ME1|ME2|ME3")
        return 1
    plan = build_plan(probe_doc, cat, PHASES, game, include_optional=args.include_optional)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=1)
        print("wrote %s (%d bytes)" % (args.out, os.path.getsize(args.out)))
    total_ready = 0
    for ph in plan["phases"]:
        ready = sum(1 for c in ph["commands"] if c["status"] == "ready")
        total_ready += ready
        total = len(ph["commands"])
        tag = " (optional)" if ph["optional"] else ""
        print("%-16s %-6s %3d/%-3d ready  %s%s"
              % (ph["id"], ph["risk"], ready, total, ph["title"], tag))
        if args.verbose:
            for c in ph["commands"]:
                print("    %-30s %-10s %s" % (c["id"], c["status"],
                                              (c.get("note") or c.get("owner") or "")))
    if plan["uncovered"]:
        print("\n%d command(s) present in %s are used by no phase: %s"
              % (len(plan["uncovered"]), game, ", ".join(plan["uncovered"])))
        print("Coverage gap, stated rather than hidden. Add them to a phase or accept the gap.")
    print("\n%d command calls ready to dispatch." % total_ready)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="print the catalogue")
    p.add_argument("--game", choices=GAMES)
    p.add_argument("--category", choices=sorted(CATEGORIES))
    p.add_argument("--mechanism")
    p.add_argument("--risk")
    p.add_argument("--catalogue")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("catalogue", help="rebuild commands.json from the taxonomy + map files")
    p.add_argument("--out")
    p.add_argument("--maps", help="directory holding ME1.json/ME2.json/ME3.json")
    p.add_argument("--force", action="store_true",
                   help="write even when the taxonomy disagrees with the measured data")
    p.add_argument("--allow-missing-maps", action="store_true")
    p.set_defaults(func=cmd_catalogue)

    p = sub.add_parser("verify", help="recompute presence and check commands.json for drift")
    p.add_argument("--catalogue")
    p.add_argument("--maps")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("probe", help="resolve every command in a running game (read-only)")
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--exe", required=True)
    p.add_argument("--offsets", required=True)
    p.add_argument("--catalogue")
    p.add_argument("--out")
    p.add_argument("--all", action="store_true", help="list resolved commands too")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("phases", help="print the ordered test-area phases")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--fields", action="store_true",
                   help="print the split-screen field phases instead")
    p.add_argument("--game", choices=GAMES, default="ME1")
    p.set_defaults(func=cmd_phases)

    p = sub.add_parser("plan", help="turn a probe result into an ordered dispatch plan")
    p.add_argument("--probe", required=True)
    p.add_argument("--catalogue")
    p.add_argument("--game", choices=GAMES)
    p.add_argument("--out")
    p.add_argument("--include-optional", action="store_true",
                   help="include the phases that touch shared save state or drop both clients")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_plan)

    args = ap.parse_args()
    if getattr(args, "fields", False):
        return cmd_fields(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
