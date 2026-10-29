"""Tests for the field allow-list and the verified write path.

The write path is the most dangerous thing in this tool, so most of these tests are about
REFUSAL rather than success. Each case names the corruption it prevents:

1. **No cross-game offset borrowing.** LE2's viewport offsets are not measured. A write to
   `viewport.desired_splitscreen_type` on LE2 must fail, not land at LE1's `0x1C0`. Three fields
   in this project's own notes already differ per build (`PlayerController.Pawn` is
   `+0x3DC`/`+0x294`/`+0x3BC`), so borrowing is a guess and a guess here is a corruption.
2. **No unverified write.** A write whose read-back disagrees must be reported as a failure. A
   write with NO read-back must also be a failure - "applied" and "went somewhere harmless" are
   otherwise the same answer.
3. **No out-of-range value.** A byte holding an enum gets 2 written into it and the render path
   is in an undefined state; there is no error, only a wrong screen.
4. **No unknown field.** A name not in the allow-list cannot be written at any offset.
5. **Read-only fields stay read-only.** `active_splitscreen_type` is maintained by the engine;
   writing it would claim a layout that does not exist.

Run: python -I tools/ue3live/test_fields.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mele_paths  # noqa: E402  upward search: works in the hub and in this bundle
REPO = mele_paths.repo_root()
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bridge"))

import ue3_fields  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def refuses(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return None
    except ue3_fields.FieldError as exc:
        return str(exc)


# ---------------------------------------------------------------------------------------
# Resolution and refusal
# ---------------------------------------------------------------------------------------
@case("a measured field resolves to its offset and width")
def t_measured():
    r = ue3_fields.resolve("viewport.desired_splitscreen_type", "ME1")
    if r["offset"] != 0x1C0 or r["size"] != 1:
        return "fail", "resolved to %r" % (r,)
    if r["role"] != "viewport":
        return "fail", "role is %r, expected viewport" % (r["role"],)
    return "ok", "0x1C0, 1 byte, role viewport"


@case("an unmeasured field for that game is refused, naming the game and the fix")
def t_unmeasured():
    msg = refuses(ue3_fields.resolve, "viewport.desired_splitscreen_type", "ME2")
    if not msg:
        return "fail", "resolved LE2's unmeasured viewport field"
    if "ME2" not in msg:
        return "fail", "the refusal does not name the game: %s" % msg
    if "ue3_discover_external" not in msg:
        return "fail", "the refusal does not say how to fix it: %s" % msg
    return "ok", "refused, named ME2 and the discovery tool"


@case("the refusal never silently falls back to another game's offset")
def t_no_fallback():
    """The specific corruption: LE2 asked for a field only measured on LE1."""
    for field in ("viewport.desired_splitscreen_type", "viewport.active_splitscreen_type",
                  "viewport.splitscreen_info"):
        msg = refuses(ue3_fields.resolve, field, "ME2")
        if not msg:
            return "fail", "%s resolved on ME2 despite having no ME2 offset" % field
    # And the offsets must be visibly different where they DO exist, proving borrowing is real.
    a = ue3_fields.resolve("gameinfo.max_players", "ME1")
    b = ue3_fields.resolve("gameinfo.max_players", "ME2")
    if a["offset"] == b["offset"]:
        return "ok but surprising", "ME1 and ME2 happen to share 0x%X" % a["offset"]
    return "ok", "refused all three; max_players is 0x%X on ME1 vs 0x%X on ME2" % (
        a["offset"], b["offset"])


@case("an unknown field is refused and the allow-list is shown")
def t_unknown():
    msg = refuses(ue3_fields.resolve, "viewport.arbitrary_offset", "ME1")
    if not msg:
        return "fail", "accepted an unknown field"
    if "desired_splitscreen_type" not in msg:
        return "fail", "the refusal does not show what IS allowed: %s" % msg
    return "ok", "refused, listed the known fields"


@case("a read-only field refuses to be written")
def t_readonly():
    for field in ("viewport.active_splitscreen_type", "gameinfo.num_players",
                  "controller.pawn", "viewport.splitscreen_info"):
        msg = refuses(ue3_fields.resolve, field, "ME1", value=1, writing=True)
        if not msg:
            return "fail", "%s accepted a write" % field
        if "read-only" not in msg:
            return "fail", "%s refused without saying it is read-only: %s" % (field, msg)
    # ...but each is readable.
    for field in ("viewport.active_splitscreen_type", "gameinfo.num_players",
                  "controller.pawn"):
        ue3_fields.resolve(field, "ME1")
    return "ok", "4 read-only fields refuse writes, all still readable"


@case("a write with no value is refused")
def t_no_value():
    msg = refuses(ue3_fields.resolve, "viewport.desired_splitscreen_type", "ME1",
                  writing=True)
    if not msg or "value" not in msg:
        return "fail", "accepted a value-less write: %r" % (msg,)
    return "ok", "refused"


@case("values outside the kind's range are refused")
def t_range():
    # NOTE: 2 used to be in this list as "a byte, but not a split member". It IS a member -
    # `eSST_2P_VERTICAL` - so it moved to the accepted set below. The case still discriminates
    # because the out-of-range probes are now values no member has.
    cases = [
        ("viewport.desired_splitscreen_type", 6),       # eSST_MAX, not a usable layout
        ("viewport.desired_splitscreen_type", -1),
        ("gameinfo.max_players", 2 ** 40),
    ]
    for field, val in cases:
        msg = refuses(ue3_fields.resolve, field, "ME1", value=val, writing=True)
        if not msg:
            return "fail", "%s accepted out-of-range value %r" % (field, val)
    # In range must be accepted - including every decoded split member.
    for good in (0, 1, 2, 3, 4, 5):
        ue3_fields.resolve("viewport.desired_splitscreen_type", "ME1", value=good, writing=True)
    ue3_fields.resolve("gameinfo.max_players", "ME1", value=4, writing=True)
    return "ok", "3 out-of-range refused, in-range accepted"


@case("a non-integer value is refused")
def t_non_integer():
    msg = refuses(ue3_fields.resolve, "viewport.desired_splitscreen_type", "ME1",
                  value="two", writing=True)
    if not msg:
        return "fail", "accepted a non-integer value"
    return "ok", "refused 'two'"


# ---------------------------------------------------------------------------------------
# Read-back verification
# ---------------------------------------------------------------------------------------
@case("a matching read-back passes")
def t_readback_ok():
    ok, why = ue3_fields.verify_readback({"value": 1}, 1)
    if not ok:
        return "fail", "a matching read-back failed: %s" % why
    return "ok", why


@case("a mismatched read-back FAILS")
def t_readback_mismatch():
    ok, why = ue3_fields.verify_readback({"value": 1}, 0)
    if ok:
        return "fail", "a write that read back 0 instead of 1 was reported as success"
    if "did not land" not in why:
        return "fail", "failed without explaining: %s" % why
    return "ok", why


@case("a MISSING read-back fails, and says why it matters")
def t_readback_missing():
    ok, why = ue3_fields.verify_readback({"value": 1}, None)
    if ok:
        return "fail", "an unverified write was reported as success"
    if "wrong address" not in why:
        return "fail", "failed without explaining the risk: %s" % why
    return "ok", "unverified write rejected"


@case("a non-integer read-back fails")
def t_readback_garbage():
    ok, why = ue3_fields.verify_readback({"value": 1}, "0x1")
    if ok:
        return "fail", "accepted a string read-back as a match"
    return "ok", "rejected relational/garbage read-back: %s" % why


# ---------------------------------------------------------------------------------------
# The allow-list itself
# ---------------------------------------------------------------------------------------
@case("every entry names its provenance and its effect")
def t_provenance():
    bad = [n for n, s in ue3_fields.FIELDS.items()
           if not s.get("provenance") or not s.get("effect") or not s.get("means")]
    if bad:
        return "fail", "fields without provenance/effect/means: %s" % bad
    return "ok", "%d fields all documented" % len(ue3_fields.FIELDS)


@case("every declared offset fits its kind's width")
def t_offsets_sane():
    for name, spec in ue3_fields.FIELDS.items():
        w = ue3_fields.KINDS[spec["kind"]]["size"]
        for game, off in spec["offsets"].items():
            if not isinstance(off, int) or off <= 0 or off > 0x2000:
                return "fail", "%s has an implausible %s offset %r" % (name, game, off)
            # 4-byte reads on an unaligned offset are legal in x86 but suspicious here.
            if w > 1 and off % 4:
                return "fail", "%s is an %d-byte field at unaligned 0x%X on %s" % (
                    name, w, off, game)
    return "ok", "all offsets plausible and aligned for their width"


@case("the write surface is small and every write is justified")
def t_write_surface():
    """A growing write surface is how a safe primitive becomes an unsafe one. Require that
    every writable field has a stated risk above 'read', and that the surface stays small."""
    writable = [n for n, s in ue3_fields.FIELDS.items() if s["writable"]]
    if len(writable) > 6:
        return "fail", "%d writable fields - the surface has grown past what was reviewed: %s" % (
            len(writable), writable)
    for n in writable:
        if ue3_fields.FIELDS[n]["risk"] == "read":
            return "fail", "%s is writable but carries risk 'read'" % n
    return "ok", "%d writable: %s" % (len(writable), ", ".join(sorted(writable)))


@case("the split-screen plan's fields are all in the allow-list")
def t_plan_fields():
    """The field phases reference names; a typo would only surface on a live run."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cmdsuite", os.path.join(HERE, "ue3_command_suite.py"))
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    unknown = []
    for ph in suite.FIELD_PHASES:
        for op in ph["writes"]:
            if op["field"] not in ue3_fields.FIELDS:
                unknown.append("%s -> %s" % (ph["id"], op["field"]))
    if unknown:
        return "fail", "field phases reference unknown fields: %s" % unknown
    return "ok", "%d phases reference only allow-listed fields" % len(suite.FIELD_PHASES)


@case("no field phase writes a read-only field")
def t_plan_writes_safe():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cmdsuite", os.path.join(HERE, "ue3_command_suite.py"))
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    bad = []
    for ph in suite.FIELD_PHASES:
        for op in ph["writes"]:
            writing = "value" in op and op["value"] is not None
            if writing and not ue3_fields.FIELDS[op["field"]]["writable"]:
                bad.append("%s writes %s" % (ph["id"], op["field"]))
    if bad:
        return "fail", "; ".join(bad)
    return "ok", "every write targets a writable field"


@case("an enum member the engine does not define is refused even though it fits")
def t_enum_members():
    """This was a real bug: kind `byte` accepted 200 for DesiredSplitscreenType. The width
    check passed and the write would have gone in, leaving the render path in an undefined
    state with no error. An enum needs its member set checked, not its width."""
    spec = ue3_fields.FIELDS["viewport.desired_splitscreen_type"]
    if spec["kind"] != "enum8":
        return "fail", "DesiredSplitscreenType is kind %r, not enum8 - the width check alone " \
                       "cannot stop an undefined member" % spec["kind"]
    if not spec.get("values"):
        return "fail", "the enum declares no accepted values"
    # 2, 3, 4 and 5 are now DEFINED members (decoded from the enum's serial data), so the
    # undefined-value probe has to move up: 6 is eSST_MAX and 7..255 are not members at all.
    for bad in (6, 7, 200, 255):
        msg = refuses(ue3_fields.resolve, "viewport.desired_splitscreen_type", "ME1",
                      value=bad, writing=True)
        if not msg:
            return "fail", "accepted undefined enum member %d" % bad
        if "not one of the known members" not in msg:
            return "fail", "refused %d without naming the accepted members: %s" % (bad, msg)
    for good in spec["values"]:
        ue3_fields.resolve("viewport.desired_splitscreen_type", "ME1", value=good,
                           writing=True)
    return "ok", "rejected 4 undefined members, accepted %s" % sorted(spec["values"])


@case("an int field is not silently treated as an enum")
def t_int_not_enum():
    """The mirror of the bug above: a plain int must still accept a range of values."""
    ue3_fields.resolve("gameinfo.max_players", "ME1", value=2, writing=True)
    ue3_fields.resolve("gameinfo.max_players", "ME1", value=16, writing=True)
    return "ok", "int fields accept arbitrary in-range values"


@case("the split-screen enum values match the decoded engine enum")
def t_splitscreen_enum_matches_engine():
    """`ue3_fields.py` used to say the `ESplitScreenType` member list was "not recoverable
    offline". It is - a `UEnum`'s members are in its serial data - and the decoded list is
    identical on ME1, ME2 and ME3:

        0 eSST_NONE  1 eSST_2P_HORIZONTAL  2 eSST_2P_VERTICAL  3 eSST_3P_FAVOR_TOP
        4 eSST_3P_FAVOR_BOTTOM  5 eSST_4P  6 eSST_MAX

    This case reads that from the package and compares it to the allow-list, so the two cannot
    drift. It also pins a detail that matters: **1 is HORIZONTAL**, and the documented working
    step `DesiredSplitscreenType = 1` is therefore a horizontal split, not the vertical one an
    earlier comment claimed.

    Skips when the install is absent; the allow-list itself is still checked in that case by
    `test_fields.py`.
    """
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "mele_tables", os.path.join(os.path.dirname(HERE), "melepkg", "mele_tables.py"))
    if not os.path.isfile(spec.origin if hasattr(spec, "origin") else ""):
        return "skip", "mele_tables.py not reachable from here"
    mt = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mt)
    pkg = mt.load_pkg()
    roots = list(pkg.game_dirs())
    if not roots:
        return "skip", "no install"
    want = ["eSST_NONE", "eSST_2P_HORIZONTAL", "eSST_2P_VERTICAL", "eSST_3P_FAVOR_TOP",
            "eSST_3P_FAVOR_BOTTOM", "eSST_4P", "eSST_MAX"]
    got = None
    for game in ("ME1", "ME2", "ME3"):
        p = os.path.join(roots[0], "Game", game, "BioGame", "CookedPCConsole", "Engine.pcc")
        if not os.path.isfile(p):
            continue
        t = mt.build(p)
        hit = [e for e in t["exports"] if e["object_name"] == "ESplitScreenType"]
        if not hit:
            return "fail", "%s: no ESplitScreenType export" % game
        members = mt.enum_members(t, t["buf"], hit[0])
        if members != want:
            return "fail", "%s: enum is %s" % (game, members)
        got = members
    if got is None:
        return "skip", "no Engine.pcc found"
    field = ue3_fields.FIELDS["viewport.desired_splitscreen_type"]["values"]
    # eSST_MAX (6) is deliberately not writable: it is a bound, not a layout.
    expected = {i: name for i, name in enumerate(
        ["single player", "two-pane horizontal split", "two-pane vertical split",
         "three-pane, top favoured", "three-pane, bottom favoured", "four-pane split"])}
    if field != expected:
        return "fail", "allow-list is %r, expected %r" % (field, expected)
    return "ok", "7 members decoded and matched on all three builds; 1 = horizontal"


def main():
    failures = 0
    for name, fn in CASES:
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = "fail", "%s: %s" % (type(exc).__name__, exc)
        mark = {"ok": "OK  ", "fail": "FAIL", "skip": "SKIP"}.get(status, "OK  ")
        print("%s %-58s %s" % (mark, name, detail))
        if status == "fail":
            failures += 1
    print()
    if failures:
        print("%d/%d FAILED" % (failures, len(CASES)))
        return 1
    print("ALL OK: %d cases" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
