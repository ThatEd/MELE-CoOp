"""Check an offsets file is fit to use, WITHOUT a running game.

Why this exists: an offsets file that is missing a key fails at the moment a tool needs it, deep in
a dispatch run, after a game has been launched. One that is missing the `verified` flag fails
silently - the tools read whatever offsets the file supplies and report results that look fine.

So this validates the FILE's shape and its own claims before anything is started:

  * every key a tool will actually ask for is present;
  * offsets that are known to differ per build are checked against the per-build expectations, so a
    copied-from-another-game file is caught rather than used;
  * a file that says `verified: false` is reported loudly, because the whole point of the flag is
    that a value nobody re-checked is not the same as a value that was.

It cannot check whether the offsets are CORRECT - that needs the live game. What it can do is make
sure that "I have an offsets file" is never mistaken for "I have verified offsets".

Usage:

    python -I tools/ue3live/ue3_preflight_offsets.py
    python -I tools/ue3live/ue3_preflight_offsets.py --game LE3 --strict
    python -I tools/ue3live/ue3_preflight_offsets.py --offsets local/MELE/offsets/offsets_LE3.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mele_paths  # noqa: E402

# Keys the tools actually read. Split by which tool, so a failure names the consequence.
REQUIRED_DISCOVERY = {
    "objects_rva": "every tool: the GObjects array",
    "name_pools_rva": "every tool: decoding any object name",
    "engine_global_rva": "the player chain and GamePlayers",
}
REQUIRED_CHAIN = {
    "engine_game_players": "ue3_playerchain / ue3_check_chain: the local-player array",
    "local_player_actor": "the chain: LocalPlayer -> controller",
    "controller_pawn": "the chain: controller -> pawn",
}

# Offsets that DIFFER between the three releases. A file that carries another build's value is the
# specific failure this catches: `PlayerController.Pawn` is +0x3DC / +0x294 / +0x3BC, so a file
# copied from LE1 and relabelled LE3 would be wrong by 0x20 and nothing else would notice.
EXPECTED = {
    "LE1": {"controller_pawn": 0x3DC, "pawn_controller": 0x400, "pawn_physics": 0x27C,
            "process_event_vtable_slot": 0x238, "engine_game_viewport": 0x7C8},
    "LE2": {"controller_pawn": 0x294, "pawn_controller": 0x3D8, "pawn_physics": 0x284,
            "process_event_vtable_slot": 0x228, "engine_game_viewport": 0x7A0},
    "LE3": {"controller_pawn": 0x3BC, "pawn_controller": 0x374, "pawn_physics": 0x268,
            "process_event_vtable_slot": 0x230, "engine_game_viewport": 0x7A0},
}

# Keys allowed to be missing because they are game-specific lookups rather than constants.
OPTIONAL = {
    "controller_player": "absent in LE3's recorded set; resolve by name",
    "controller_player_input": "resolve by name, never assume a ported offset",
    "pawn_location": "not recorded for LE3",
    "objects_count": "changes as the game loads; re-read the header",
}


def as_int(v):
    if v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        return int(str(v), 0)
    except ValueError:
        return None


def check(game, path, strict=False):
    """Returns (problems, warnings, info). Nothing here raises: a caller prints and decides."""
    problems, warnings, info = [], [], {}
    if not os.path.isfile(path):
        problems.append("no offsets file at %s" % path)
        return problems, warnings, info
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as exc:
        problems.append("cannot read %s: %s" % (path, exc))
        return problems, warnings, info

    info["path"] = path
    info["game"] = d.get("game")
    info["exe"] = d.get("exe")
    info["verified"] = d.get("verified")

    for k, why in REQUIRED_DISCOVERY.items():
        v = as_int(d.get(k))
        if v is None:
            problems.append("missing %s - without it: %s" % (k, why))
        elif v <= 0 or v > 0x4000000:
            problems.append("%s = %r is not a plausible RVA - affects: %s" % (k, d.get(k), why))
    for k, why in REQUIRED_CHAIN.items():
        if as_int(d.get(k)) is None:
            problems.append("missing %s - without it: %s" % (k, why))

    # Claims about itself must be consistent.
    if d.get("game") and d["game"] != game:
        problems.append("file says game=%r but was checked as %s" % (d["game"], game))
    # MassEffect1.exe <-> LE1: the release name and the exe number differ, so compare the digit.
    want_digit = game[-1]
    if d.get("exe") and want_digit not in str(d["exe"]):
        warnings.append("exe %r does not contain %r for %s" % (d["exe"], want_digit, game))

    # Per-build offsets: the check a copied file cannot pass.
    for k, want in EXPECTED.get(game, {}).items():
        got = as_int(d.get(k))
        if got is None:
            warnings.append("per-build offset %s not recorded (expected 0x%X)" % (k, want))
        elif got != want:
            others = {g: v[k] for g, v in EXPECTED.items() if k in v and v[k] == got}
            hint = (" - that is %s's value" % "/".join(others)) if others else ""
            problems.append("%s = 0x%X but %s's is 0x%X%s"
                            % (k, got, game, want, hint))

    if d.get("verified") is not True:
        note = d.get("verified_note") or d.get("source") or ""
        warnings.append("this file does NOT claim to be verified%s"
                        % (": " + str(note)[:200] if note else ""))
        if strict:
            problems.append("--strict: refusing an unverified offsets file")

    for k, why in OPTIONAL.items():
        if d.get(k) is None:
            warnings.append("optional %s absent (%s)" % (k, why))

    # Offset sanity that catches a byte/field mix-up.
    for k, v in d.items():
        iv = as_int(v)
        if iv is None or not k.endswith(("_rva", "_pawn", "_players", "_actor", "_physics",
                                         "_controller", "_viewport", "_slot", "_type",
                                         "_input", "_player")):
            continue
        if k.endswith("_rva"):
            continue
        if iv > 0x10000:
            warnings.append("%s = 0x%X is large for an object field offset" % (k, iv))
    return problems, warnings, info


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", choices=("LE1", "LE2", "LE3"))
    ap.add_argument("--offsets", help="an explicit file instead of the per-game default")
    ap.add_argument("--strict", action="store_true",
                    help="treat an unverified file as a problem, not a warning")
    args = ap.parse_args()

    if args.offsets:
        path = args.offsets
        game = args.game or os.path.basename(path).replace("offsets_", "").replace(".json", "")
    else:
        game = args.game or "LE3"
        path = mele_paths.offsets_file(game)

    problems, warnings, info = check(game, path, strict=args.strict)
    print("%s offsets: %s" % (game, path))
    if info:
        print("  claims   : game=%r exe=%r verified=%r"
              % (info.get("game"), info.get("exe"), info.get("verified")))
    for w in warnings:
        print("  WARN  %s" % w)
    for p in problems:
        print("  FAIL  %s" % p)
    print()
    if problems:
        print("%d problem(s). This file is not fit to use yet." % len(problems))
        if any("verified" in p for p in problems):
            print("To verify: run a game, then ue3_verify_offsets.py and ue3_check_chain.py,")
            print("and set \"verified\": true only if both pass.")
        return 1
    print("Fit to use.%s"
          % ("" if info.get("verified") is True
             else " NOT verified - the offsets are present but nobody re-checked them."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
