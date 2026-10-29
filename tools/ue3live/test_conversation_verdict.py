"""Tests for the read-only conversation verdict logic.

The verdict decides whether a scene owns the player, and every conversation-handling
mechanism depends on it. It is pure logic over already-read values, so it can be tested
without a game running - which is the point: a detector that is wrong is worse than none,
because a mechanism tested against it proves nothing.

Run: python -I tools/ue3live/test_conversation_verdict.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load():
    spec = importlib.util.spec_from_file_location(
        "conv", os.path.join(HERE, "ue3_conversation.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ok(v):
    return {"state": "ok", "value": v}


def unread():
    return {"state": "unread"}


def base(**kw):
    s = {"bPlayersOnly": ok(0), "cinematic": ok(0), "conversations_active": ok(0),
         "conversations_activating": ok(0), "conversation_object": ok("0x0"),
         "pawn_physics": ok(1)}
    s.update(kw)
    return s


CASES = [
    # Ordinary gameplay: nothing positive, everything readable.
    ("ordinary gameplay", base(), "playing"),

    # A world that is PlayersOnly for a reason that is not a conversation must NOT be
    # called a scene on its own - one signal is not evidence.
    ("playersOnly alone", base(bPlayersOnly=ok(1)), "conflicting"),

    # Script-driven pawn with no conversation is a matinee, not dialogue.
    ("physics 7 alone", base(pawn_physics=ok(7)), "conflicting"),

    # Two independent signals agreeing is the bar for 'scene'.
    ("cinematic + conversation count", base(cinematic=ok(1), conversations_active=ok(1)), "scene"),
    ("playersOnly + cinematic", base(bPlayersOnly=ok(1), cinematic=ok(1)), "scene"),
    ("full evidence", base(bPlayersOnly=ok(1), cinematic=ok(1), conversations_active=ok(2),
                           conversation_object=ok("0x1234"), pawn_physics=ok(7)), "scene"),

    # An unreadable field must never be treated as 'no scene' (documented project rule).
    ("unread playersOnly", base(bPlayersOnly=unread()), "incomplete"),
    ("unread counts", base(conversations_active=unread()), "incomplete"),
    ("unread physics", base(pawn_physics=unread()), "incomplete"),

    # Absent is different from unread: a field this build does not have is not a failure
    # to read, so it must not block a verdict that other signals support.
    ("absent cinematic, others quiet",
     base(cinematic={"state": "absent", "tried": ["bCinematicMode"]}), "playing"),
]


def main():
    mod = load()
    failures = []
    for name, sample, expect in CASES:
        got = mod.verdict(sample)["state"]
        status = "OK  " if got == expect else "FAIL"
        if got != expect:
            failures.append((name, got, expect))
        print("%s %-34s -> %-12s (expected %s)" % (status, name, got, expect))
    print()
    if failures:
        print("%d/%d FAILED" % (len(failures), len(CASES)))
        return 1
    print("ALL OK: %d cases" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
