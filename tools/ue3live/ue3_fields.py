"""Named field reads and writes, with a fixed allow-list and mandatory read-back.

Why this module exists, and why it is not a general write primitive:

The split-screen plan for LE1/LE2 is mostly **field writes**, not UFunction calls. The one
documented working step on this project is `DesiredSplitscreenType = 1` - the engine then sets
`ActiveSplitscreenType` itself. Raising `MaxPlayers` before `CreatePlayer` is the same shape. A
command suite that can only call functions cannot express any of that.

So a write path is needed. But a general "write these bytes at this address" is the single most
dangerous thing that could be added to this tool: every crash in this project's notes came from
touching something the engine owns, and a write goes further than a call. The mitigation is not
care - it is that **the caller cannot say where to write**.

A caller names a FIELD, never an address:

    ue3.write {field: "viewport.desired_splitscreen_type", value: 1}

and this module resolves the name to (offset, size, kind) for the running game. A field that is
not in the table below cannot be written, at any offset, by any caller. The table is short on
purpose and every entry carries the evidence for its offset.

Three rules the tests enforce, each because guessing here is unrecoverable:

1. **No measured offset, no write.** LE1's viewport fields are measured; LE2's are not. Asking to
   write LE2's `desired_splitscreen_type` must fail loudly, not write to LE1's offset. Offsets do
   not port between these builds - `PlayerController.Pawn` is `+0x3DC` in LE1, `+0x294` in LE2 and
   `+0x3BC` in LE3, so a ported offset is a guess and a guess here is a corruption.
2. **Read-back is mandatory.** Every write is followed by a read of the same field and the value
   must match what was asked for. A write that reports success without verifying cannot
   distinguish "applied" from "went somewhere else", and this project has already paid for
   believing a call that silently did nothing (the placement order in NOTES.md).
3. **Range is checked before writing.** A bool is 0/1; writing 2 into a byte the engine
   interprets as an enum is how a render path ends up in an undefined state.

Provenance: each entry names where its offset came from. `local/MELE/offsets/offsets_LE1.json` is the verified file
written by `ue3_discover_external.py` + `ue3_verify_offsets.py`; `local/MELE/probes/le2_gameinfo_fields.json` is a
live property walk of the running LE2 `BioSPGame`. Anything without such a source is marked
`unmeasured` and is read-write refused.

Usage:

    python -I tools/ue3live/ue3_fields.py list
    python -I tools/ue3live/ue3_fields.py list --game LE1 --writable
    python -I tools/ue3live/ue3_fields.py check --game LE2
    python -I tools/ue3live/ue3_fields.py coverage --field viewport.desired_splitscreen_type

The runner (`ue3_run_plan.py`) imports this module; it does not re-implement any of it.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mele_paths  # noqa: E402  upward search: works in the hub and in this bundle
REPO = mele_paths.repo_root()
sys.path.insert(0, HERE)

GAMES = ("ME1", "ME2", "ME3")

# Kinds map to the bridge's read/write widths AND to an accepted value set. The width alone is
# not enough: `DesiredSplitscreenType` is one byte, so kind `byte` would accept 200, but the
# engine interprets it as an enum with a handful of members and 200 puts the render path in an
# undefined state with no error - only a wrong screen.
KINDS = {
    "bool": {"size": 1, "min": 0, "max": 1},
    "byte": {"size": 1, "min": 0, "max": 255},
    # A one-byte engine enum. `values` is the set that is safe to write; anything else is
    # refused even though it fits in a byte.
    "enum8": {"size": 1, "min": 0, "max": 255},
    "int": {"size": 4, "min": -2147483648, "max": 2147483647},
    "ptr": {"size": 8, "min": 0, "max": 0xFFFFFFFFFFFFFFFF},
}

# The allow-list. `offsets` holds only the games where the offset was MEASURED; a game absent
# from it cannot be written, and that is the point.
#
# `role` is the object the field belongs to, using the same role vocabulary as the command
# catalogue, so the game side resolves it the same way it resolves a call target.
FIELDS = {
    # ---- viewport: the split-screen control surface -----------------------------------
    "viewport.desired_splitscreen_type": {
        "role": "viewport", "kind": "enum8", "writable": True,
        # ESplitScreenType, decoded from `Engine.pcc`'s enum serial data (see `enum_members` in
        # melepkg/mele_tables.py), identical on ME1, ME2 and ME3:
        #
        #   0 eSST_NONE   1 eSST_2P_HORIZONTAL   2 eSST_2P_VERTICAL
        #   3 eSST_3P_FAVOR_TOP   4 eSST_3P_FAVOR_BOTTOM   5 eSST_4P   6 eSST_MAX
        #
        # This corrects an earlier comment here that said the member list was "not recoverable
        # offline". It is, and the values are now named rather than numbered, so a wrong write is
        # a wrong NAME rather than an unexplained index. Note 1 is HORIZONTAL, not vertical - the
        # documented working step (`DesiredSplitscreenType = 1`) is a horizontal two-pane split.
        "values": {0: "single player", 1: "two-pane horizontal split",
                   2: "two-pane vertical split", 3: "three-pane, top favoured",
                   4: "three-pane, bottom favoured", 5: "four-pane split"},
        "means": "Ask the viewport for a split layout. The engine then sets Active itself - "
                 "this is the one step this project has actually observed working.",
        "offsets": {"ME1": 0x1C0},
        "provenance": "local/MELE/offsets/offsets_LE1.json viewport_desired_splitscreen_type, measured live "
                      "on GFxGameViewportClient. LE2/LE3 not measured - do not borrow LE1's.",
        "risk": "medium",
        "effect": "With a single LocalPlayer the engine sets Active=1 but there is only one view "
                  "to split (fail point A3). Expect no visible change until a second "
                  "LocalPlayer exists.",
    },
    "viewport.active_splitscreen_type": {
        "role": "viewport", "kind": "enum8", "writable": False,
        "values": {0: "single player", 1: "two-pane horizontal split",
                   2: "two-pane vertical split", 3: "three-pane, top favoured",
                   4: "three-pane, bottom favoured", 5: "four-pane split"},
        "means": "What the viewport says it is currently doing. READ ONLY: the engine maintains "
                 "this, and writing it would claim a layout that does not exist.",
        "offsets": {"ME1": 0x1C1},
        "provenance": "local/MELE/offsets/offsets_LE1.json viewport_active_splitscreen_type, measured live.",
        "risk": "read",
        "effect": "The assertion for a split test: compare it with desired before and after.",
    },
    "viewport.splitscreen_info": {
        "role": "viewport", "kind": "ptr", "writable": False,
        "means": "The array of PerPlayerSplitscreenData, indexed BY LOCAL PLAYER.",
        "offsets": {"ME1": 0x118},
        "provenance": "local/MELE/offsets/offsets_LE1.json viewport_splitscreen_info, measured live.",
        "risk": "read",
        "effect": "Read its count to learn how many layouts the viewport believes in. This is "
                  "the field that makes 'one LocalPlayer means one view' measurable rather "
                  "than asserted.",
    },
    # ---- game info: the player cap ---------------------------------------------------
    "gameinfo.max_players": {
        "role": "gameinfo", "kind": "int", "writable": True,
        "means": "How many players the game mode will admit.",
        "offsets": {"ME1": 0x440, "ME2": 0x448},
        "provenance": "LE1: local/MELE/offsets/offsets_LE1.json game_maxplayers. LE2: live property walk of "
                      "BioSPGame in local/MELE/probes/le2_gameinfo_fields.json (measured value 16).",
        "risk": "medium",
        "effect": "Both builds ALREADY read 16, so no raise is needed. The earlier note saying "
                  "LE2 is capped at 1 was a reading of LE3's SFXGameInfoSP (1/1), not LE2.",
    },
    "gameinfo.max_players_allowed": {
        "role": "gameinfo", "kind": "int", "writable": True,
        "means": "The ceiling MaxPlayers may be set to.",
        "offsets": {"ME1": 0x444, "ME2": 0x44C},
        "provenance": "LE1: local/MELE/offsets/offsets_LE1.json game_maxplayers_allowed. LE2: "
                      "local/MELE/probes/le2_gameinfo_fields.json (measured value 32).",
        "risk": "medium",
        "effect": "Already 32 in both. Raising MaxPlayers above this would be clamped, which is "
                  "why the pair is listed together.",
    },
    "gameinfo.num_players": {
        "role": "gameinfo", "kind": "int", "writable": False,
        "means": "How many players the game mode believes are in the game.",
        "offsets": {"ME1": 0x448, "ME2": 0x450},
        "provenance": "LE1: local/MELE/offsets/offsets_LE1.json game_numplayers. LE2: local/MELE/probes/le2_gameinfo_fields.json.",
        "risk": "read",
        "effect": "The cheap check after any player-creation attempt: if this is still 1, nothing "
                  "happened, whatever the array says.",
    },
    # ---- controller: the input object ------------------------------------------------
    "controller.player_input": {
        "role": "player.controller", "kind": "ptr", "writable": True,
        "means": "The input object on a controller. Null on a hand-built controller, which is "
                 "what crash 13 faulted on at [rax+0x6D0].",
        "offsets": {"ME1": 0x708, "ME2": 0x6F8},
        "provenance": "LE1: local/MELE/offsets/offsets_LE1.json controller_player_input. LE2: measured in the LE2 "
                      "player-chain work (NOTES.md).",
        "risk": "high",
        "effect": "This is the field p2v2_input.dll was built to set. Writing it by hand is what "
                  "Route 1 tests; a wrong pointer here faults on the next input tick.",
    },
    "controller.pawn": {
        "role": "player.controller", "kind": "ptr", "writable": False,
        "means": "The pawn a controller currently drives.",
        "offsets": {"ME1": 0x3DC, "ME2": 0x294, "ME3": 0x3BC},
        "provenance": "All three measured; the three values differ, which is why offsets must "
                      "never be ported between builds.",
        "risk": "read",
        "effect": "The assertion for possession: after Possess, this must point at the squadmate.",
    },
}

# Per-game coverage, computed rather than declared, so a missing offset is visible in `check`.
def coverage(field, game):
    """('measured', offset) | ('unmeasured', None) | ('wrong-game', None)."""
    if field not in FIELDS:
        return "unknown-field", None
    offsets = FIELDS[field]["offsets"]
    if game in offsets:
        return "measured", offsets[game]
    return "unmeasured", None


def resolve(field, game, value=None, writing=False):
    """Resolve a named field for a game. Raises FieldError with a specific reason.

    Every refusal names what is missing, because the caller cannot fix "invalid field" but can
    act on "LE2's viewport offsets were never measured".
    """
    if field not in FIELDS:
        known = sorted(FIELDS)
        raise FieldError("unknown field %r. The allow-list is fixed: %s"
                         % (field, ", ".join(known)))
    spec = FIELDS[field]
    if writing and not spec["writable"]:
        raise FieldError("%s is read-only: %s" % (field, spec["means"]))
    status, offset = coverage(field, game)
    if status != "measured":
        raise FieldError(
            "%s has no measured offset for %s. Provenance: %s. Writing another game's offset "
            "would land somewhere arbitrary - measure it first with "
            "ue3_discover_external.py + ue3_verify_offsets.py."
            % (field, game, spec["provenance"]))
    k = KINDS[spec["kind"]]
    out = {"field": field, "role": spec["role"], "offset": offset,
           "size": k["size"], "kind": spec["kind"], "risk": spec["risk"]}
    if writing:
        if value is None:
            raise FieldError("%s needs a value" % field)
        try:
            ival = int(value)
        except (TypeError, ValueError):
            raise FieldError("%s value %r is not an integer" % (field, value))
        if not (k["min"] <= ival <= k["max"]):
            raise FieldError("%s is a %s: value %d is outside [%d, %d]"
                             % (field, spec["kind"], ival, k["min"], k["max"]))
        # An enum is not an integer even though it is stored as one. Writing a member the
        # engine does not define produces an undefined render state with no error.
        allowed = spec.get("values")
        if allowed and ival not in allowed:
            raise FieldError(
                "%s is an enum: %d is not one of the known members (%s). The full member list "
                "is not recoverable offline, so only values with a known meaning are accepted."
                % (field, ival, ", ".join("%d=%s" % (v, n) for v, n in sorted(allowed.items()))))
        out["value"] = ival
    return out


class FieldError(Exception):
    pass


def verify_readback(resolved, got):
    """Compare a read-back value with what was written.

    Returns (ok, reason). A missing read-back is a FAILURE, not a pass: an unverified write is
    indistinguishable from a write to the wrong place.
    """
    if got is None:
        return False, ("no read-back was returned. A write that is not verified cannot be told "
                       "apart from a write to the wrong address")
    try:
        gv = int(got)
    except (TypeError, ValueError):
        return False, "read-back %r is not an integer" % (got,)
    want = resolved["value"]
    if gv != want:
        return False, ("wrote %d but the field reads %d - the write did not land where it was "
                       "aimed" % (want, gv))
    return True, "read back %d, matches" % gv


# ---------------------------------------------------------------------------------------
def cmd_list(args):
    rows = []
    for name, spec in FIELDS.items():
        status, off = coverage(name, args.game) if args.game else ("-", None)
        if args.writable and not spec["writable"]:
            continue
        if args.game and status != "measured":
            continue
        rows.append((name, spec, status, off))
    print("%-40s %-6s %-18s %-8s %s"
          % ("FIELD", "KIND", "ROLE", "RW", "MEASURED"))
    for name, spec, status, off in sorted(rows):
        meas = ", ".join("%s 0x%X" % (g, o) for g, o in sorted(spec["offsets"].items())) or "-"
        print("%-40s %-6s %-18s %-8s %s"
              % (name, spec["kind"], spec["role"],
                 "write" if spec["writable"] else "read", meas))
    if args.verbose:
        for name, spec, _, _ in sorted(rows):
            print("\n%s" % name)
            print("  means      : %s" % spec["means"])
            print("  provenance : %s" % spec["provenance"])
            print("  effect     : %s" % spec["effect"])
    print("\n%d field(s)." % len(rows))
    return 0


def cmd_check(args):
    """Per-game coverage report: which fields can be written, which cannot, and why not."""
    bad = []
    print("%s coverage\n" % args.game)
    for name, spec in sorted(FIELDS.items()):
        status, off = coverage(name, args.game)
        if status == "measured":
            print("  [%s] %-40s 0x%-5X %s"
                  % ("RW" if spec["writable"] else "r ", name, off, spec["kind"]))
        else:
            print("  [--] %-40s %s - cannot be used on this build"
                  % (name, "UNMEASURED"))
            if spec["risk"] in ("high", "medium"):
                bad.append(name)
    if bad:
        print("\n%d field(s) needed for the split/possession plan are unmeasured on %s:"
              % (len(bad), args.game))
        for n in bad:
            print("  - %s" % n)
        print("\nThat is the work: measure them with ue3_discover_external.py and "
              "ue3_verify_offsets.py against a live %s." % args.game)
    else:
        print("\nEvery field is measured on %s." % args.game)
    return 0


def cmd_coverage(args):
    status, off = coverage(args.field, args.game) if args.game else ("?", None)
    if args.field not in FIELDS:
        print("unknown field %r" % args.field)
        return 1
    spec = FIELDS[args.field]
    print(args.field)
    print("  means      : %s" % spec["means"])
    print("  role       : %s  kind: %s  %s"
          % (spec["role"], spec["kind"], "writable" if spec["writable"] else "read-only"))
    print("  provenance : %s" % spec["provenance"])
    print("  effect     : %s" % spec["effect"])
    print("  offsets    : %s" % (", ".join("%s=0x%X" % (g, o)
                                           for g, o in sorted(spec["offsets"].items())) or "none"))
    if args.game:
        print("  on %s     : %s%s" % (args.game, status,
                                      " 0x%X" % off if off is not None else ""))
    missing = [g for g in GAMES if g not in spec["offsets"]]
    if missing:
        print("  unmeasured : %s" % ", ".join(missing))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="the allow-list")
    p.add_argument("--game", choices=GAMES)
    p.add_argument("--writable", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("check", help="per-game coverage, naming what cannot be used")
    p.add_argument("--game", choices=GAMES, required=True)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("coverage", help="everything known about one field")
    p.add_argument("--field", required=True)
    p.add_argument("--game", choices=GAMES)
    p.set_defaults(func=cmd_coverage)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
