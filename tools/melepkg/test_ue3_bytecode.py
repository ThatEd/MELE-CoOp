"""Tests for the bytecode extractor and its deliberate refusal to disassemble.

The value here is a boundary: the *extent* of UnrealScript bytecode is proven by an identity that
holds 5279/5279 times, while the *opcode table* is explicitly unproven because the only available
closure test cannot fail. These cases pin both halves - the proven one as an assertion, and the
unproven one as an absence, so a future agent cannot quietly add a disassembler and call it done.

Run: python -I tools/melepkg/test_ue3_bytecode.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ue3_bytecode as bc  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def package(game, name="Engine"):
    mt = bc.load_tables()
    pkg = mt.load_pkg()
    rel = os.path.join("Game", game, "BioGame", "CookedPCConsole", name + ".pcc")
    for root in pkg.game_dirs():
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return p
    return None


def table(game, name="Engine"):
    p = package(game, name)
    if not p:
        return None, "no %s %s.pcc" % (game, name)
    mt = bc.load_tables()
    return mt.build(p), None


# ---------------------------------------------------------------------------------------
@case("the module does not expose a disassembler")
def t_no_disassembler():
    """The single most important assertion here.

    A walk of UE3 bytecode that "closes" proves nothing: a partition into instructions of any
    length 1..24 exists for 5279 of 5279 functions, because the search space is large. Shipping a
    disassembler on that basis would be the sixth false pass in this project. If someone adds one,
    it must come with a reference table and a test that can fail - so this case makes the absence
    deliberate rather than accidental.
    """
    banned = [n for n in ("walk", "disassemble", "decode", "OPCODES", "opcodes")
              if hasattr(bc, n)]
    if banned:
        return "fail", ("%s exists. An opcode table is unproven here - see this module's "
                        "docstring before adding one" % banned)
    return "ok", "no walk()/OPCODES; only extent extraction is offered"


@case("the function serial header is per-build: 47, 47, 38 with the length at +28")
def t_header_constants():
    """The header length differs by build and is NOT a constant. Measuring it wrong is silent:
    the first version used ME3's 38 for all three and every ME1/ME2 function reported as not
    closing, which looks like a parser bug rather than a wrong constant."""
    if bc.FUNCTION_SERIAL_HEADER != {"ME1": 47, "ME2": 47, "ME3": 38}:
        return "fail", "header table is %r" % (bc.FUNCTION_SERIAL_HEADER,)
    if bc.BYTECODE_LEN_AT != 28:
        return "fail", "length field at %r" % bc.BYTECODE_LEN_AT
    return "ok", "ME1/ME2 47, ME3 38, length always at +28"


@case("every function in ME3 Engine.pcc closes exactly")
def t_extent_closes():
    t, why = table("ME3")
    if t is None:
        return "skip", why
    mt = bc.load_tables()
    ok, detail = bc.verify(t, mt)
    if not ok:
        return "fail", detail
    fns = bc.functions(t, mt)
    if len(fns) < 5000:
        return "fail", "only %d functions found; expected ~5279" % len(fns)
    if any(f["extra"] != 0 for f in fns):
        return "fail", "some functions do not close"
    return "ok", detail


@case("the extent identity holds on all three builds")
def t_extent_all_builds():
    results, skipped = {}, []
    mt = bc.load_tables()
    for game in ("ME1", "ME2", "ME3"):
        t, why = table(game)
        if t is None:
            skipped.append(game)
            continue
        ok, detail = bc.verify(t, mt)
        if not ok:
            return "fail", "%s: %s" % (game, detail)
        results[game] = len(bc.functions(t, mt))
    if not results:
        return "skip", "no build available (%s)" % ", ".join(skipped)
    return "ok", ", ".join("%s %d" % (g, n) for g, n in sorted(results.items()))


@case("CreatePlayer's bytecode contains its log strings, proving it is real code")
def t_strings_are_real():
    """An independent check that the extent is right: a NUL-terminated readable sentence inside
    the extracted range cannot be there by accident."""
    t, why = table("ME3")
    if t is None:
        return "skip", why
    mt = bc.load_tables()
    fns = [f for f in bc.functions(t, mt)
           if f["export"]["object_name"] == "CreatePlayer"]
    if not fns:
        return "fail", "no CreatePlayer function"
    text = fns[0]["body"].decode("latin-1")
    for want in ("Creating new player with ControllerId",
                 "Successfully created new player"):
        if want not in text:
            return "fail", "%r is not in the bytecode" % want
    if fns[0]["length"] < 100:
        return "fail", "body is only %d bytes" % fns[0]["length"]
    return "ok", "%d bytes containing the log strings" % fns[0]["length"]


@case("RemovePlayer enforces a role check, which the strings show")
def t_removeplayer_role_check():
    """Recovered offline with no game running, and directly relevant to co-op: removing the
    second player is gated on the player having 'appropriate role'."""
    t, why = table("ME3")
    if t is None:
        return "skip", why
    mt = bc.load_tables()
    fns = [f for f in bc.functions(t, mt)
           if f["export"]["object_name"] == "RemovePlayer"]
    if not fns:
        return "fail", "no RemovePlayer function"
    text = fns[0]["body"].decode("latin-1")
    if "appropriate role" not in text:
        return "fail", "the role check string is absent"
    return "ok", "'player does not have appropriate role' present"


@case("a function name that is not there is reported, not guessed")
def t_missing_function():
    t, why = table("ME3")
    if t is None:
        return "skip", why
    mt = bc.load_tables()
    hits = [f for f in bc.functions(t, mt)
            if f["export"]["object_name"] == "NoSuchFunctionXYZ"]
    if hits:
        return "fail", "found a function that does not exist"
    return "ok", "absent"


def main():
    failures = skips = 0
    for name, fn in CASES:
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = "fail", "%s: %s" % (type(exc).__name__, exc)
        mark = {"ok": "OK  ", "fail": "FAIL", "skip": "SKIP"}.get(status, "OK  ")
        print("%s %-66s %s" % (mark, name, detail))
        if status == "fail":
            failures += 1
        elif status == "skip":
            skips += 1
    print()
    if failures:
        print("%d/%d FAILED" % (failures, len(CASES)))
        return 1
    print("ALL OK: %d cases%s" % (len(CASES),
                                  ", %d skipped (no game)" % skips if skips else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
