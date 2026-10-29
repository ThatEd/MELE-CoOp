"""Extract UnrealScript bytecode from ME:LE packages - extent proven, opcode table NOT.

`CONTAINER-FORMAT.md` listed "whether function bytecode survives in these packages" as an open
unknown. It does survive. `GameViewportClient.CreatePlayer`'s serial data contains the literal
string "Creating new player with ControllerId", which is an argument to `LogInternal`; that is
compiled bytecode, not metadata.

## What IS proven

The function serial layout, because it closes exactly:

    +0    NetIndex (FName)
    +4    a constant (13240 in ME3's Engine.pcc)
    +8    8 zero bytes
    +16   StateIndex FName
    +20   FuncIndex (next function in the state)
    +24   (unknown, correlates with +28)
    +28   bytecode length
    +32   NativeFunctionIndex FName

    bytecode occupies serial[H : H + u32(serial+28)]   where H is 47 on ME1/ME2 and 38 on ME3

`H + u32(+28) == serial_size` for every `Core.Function` in all three builds: ME1 5416/5416, ME2 5350/5350, ME3 5279/5279
- zero exceptions. That is a fact about the layout, not a correlation.

## What is NOT proven, and must not be claimed

The **opcode table**. UE3 bytecode has no end marker and no per-instruction length, so the only
honest test is "walk with a candidate table and require the cursor to land exactly on the end".

That test was written and it is worthless here, which is worth recording rather than deleting: a
dynamic-programming partition of the stream into instructions of any length 1..24 succeeded on
**5279 of 5279** functions. A partition exists for almost any byte string because the search space
is large, so "the walk closed" is evidence of nothing. That is rule 25 of this project's NOTES.md -
a query that cannot fail - in a new shape.

So this module deliberately does **not** disassemble. It extracts the bytecode and reports the
closure statistics a real opcode table would have to satisfy; proving the table needs a reference
(the `EExprToken` enum for this exact engine version) or an independent implementation to compare
against, and neither is present here.

Two observations a future attempt should start from, from reading the streams:

  * `02 00` is the whole body of 514 two-byte functions (e.g. `Actor.Destroy`). In UE3 `0x02` is
    `EX_DefaultVariable`, so such a function is probably "return the default value of property 0" -
    a plausible native stub, but that is inference, not proof.
  * `LayoutPlayers` ends with `53 00 00 0? 0? 00`, i.e. `0x53` then what looks like a two-byte
    count and that many two-byte entries. `0x53` is beyond the stock UE3 `EExprToken` range, so
    this build's table already differs from the SDK's.

Usage:

    python -I tools/melepkg/ue3_bytecode.py survey  <package>
    python -I tools/melepkg/ue3_bytecode.py body    <package> <function> [--hex]
    python -I tools/melepkg/ue3_bytecode.py strings <package> <function>
"""
import argparse
import importlib.util
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FUNCTION_SERIAL_HEADER = {"ME1": 47, "ME2": 47, "ME3": 38}
BYTECODE_LEN_AT = 28              # int32 inside that header holding the bytecode length

# How the header length was established, because it is per-build and must not be hardcoded blindly.
#
# `serial_size - u32(serial+28)` is a CONSTANT for every `Core.Function` in a package:
#
#     ME1  47   (5416/5416 functions in Engine.pcc)
#     ME2  47   (5350/5350)
#     ME3  38   (5279/5279)
#
# Zero variance, so the bytecode extent is `serial[HEADER : HEADER + u32(serial+28)]` exactly. The
# 9-byte difference is the same kind of per-build variation as the packfile header's 9-byte shift,
# and assuming ONE value - which the first version of this module did, using ME3's 38 for all
# three - fails closed rather than silently: `verify()` reports every ME1/ME2 function as not
# closing.
#
# A first attempt to find this used `size == H + field` with H in 34..42 for all builds at once and
# reported 0/5416, because it searched the wrong pair. The right question was "what is
# `size - field`", not "which H makes them equal" - the difference matters when the field is not at
# the end of the header.

ASCII_RUN = re.compile(rb"[\x20-\x7e]{4,}")


def load_tables():
    spec = importlib.util.spec_from_file_location(
        "mele_tables", os.path.join(HERE, "mele_tables.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def functions(table, mt):
    """Every export whose class is `Core.Function`, with its bytecode extent resolved."""
    out = []
    header = FUNCTION_SERIAL_HEADER.get(table["game"])
    if header is None:
        return out
    disp = mt.buffer_base(table["header"])
    exports, imports = table["exports"], table["imports"]
    for e in exports:
        if mt.describe_index(e["class_index"], exports, imports) != "Core.Function":
            continue
        if e["serial_size"] < header:
            continue
        at = e["serial_offset"] - disp
        if at + BYTECODE_LEN_AT + 4 > len(table["buf"]):
            continue
        length = struct.unpack_from("<i", table["buf"], at + BYTECODE_LEN_AT)[0]
        start = at + header
        if length < 0 or start + length > len(table["buf"]):
            continue
        out.append({"export": e, "start": start, "length": length,
                    "body": table["buf"][start:start + length],
                    "extra": e["serial_size"] - header - length})
    return out


def verify(table, mt):
    """Does `header + bytecode_length == serial_size` hold for every function? Returns (ok, why)."""
    header = FUNCTION_SERIAL_HEADER.get(table["game"])
    if header is None:
        return False, "unknown build %r" % (table["game"],)
    fns = functions(table, mt)
    if not fns:
        return False, "no Core.Function exports"
    short = [f["export"]["object_name"] for f in fns if f["extra"] != 0]
    if short:
        return False, ("%d/%d functions do not close with header %d (first: %s)"
                       % (len(short), len(fns), header, short[:4]))
    return True, "%d functions close with header %d" % (len(fns), header)


def cmd_survey(args):
    mt = load_tables()
    t = mt.build(args.package)
    ok, why = verify(t, mt)
    fns = functions(t, mt)
    print("%s [%s]: %s" % (t["package"], t["game"], why))
    if not fns:
        return 0
    lens = [f["length"] for f in fns]
    print("  %d functions with bytecode; total %d bytes" % (len(fns), sum(lens)))
    print("  length min=%d median=%d max=%d ; two-byte bodies=%d"
          % (min(lens), sorted(lens)[len(lens) // 2], max(lens),
             sum(1 for n in lens if n == 2)))
    print()
    print("  The opcode table is NOT proven - see this module's docstring. Every walk of")
    print("  these streams that closes is uninformative, because a length-1..24 partition")
    print("  exists for essentially any byte string.")
    return 0 if ok else 1


def cmd_body(args):
    mt = load_tables()
    t = mt.build(args.package)
    hit = [f for f in functions(t, mt) if f["export"]["object_name"] == args.name]
    if not hit:
        print("%s: no function named %r" % (t["package"], args.name))
        return 1
    f = hit[0]
    print("%s: %d bytes of bytecode" % (args.name, f["length"]))
    if args.hex:
        for i in range(0, f["length"], 16):
            chunk = f["body"][i:i + 16]
            print("  %04x  %s" % (i, " ".join("%02x" % b for b in chunk)))
    else:
        print("  %s" % f["body"].hex())
    return 0


def cmd_strings(args):
    """String constants embedded in a function's bytecode.

    The one extraction that is independently checkable: a string is stored as a NUL-terminated
    run, so seeing a readable sentence proves those bytes are string constants and that the
    extent is right. It says nothing about the opcode table.
    """
    mt = load_tables()
    t = mt.build(args.package)
    hit = [f for f in functions(t, mt) if f["export"]["object_name"] == args.name]
    if not hit:
        print("%s: no function named %r" % (t["package"], args.name))
        return 1
    f = hit[0]
    found = [m.group().decode("latin-1") for m in ASCII_RUN.finditer(f["body"])]
    print("%s: %d string run(s) in %d bytes of bytecode" % (args.name, len(found), f["length"]))
    for s in found[:args.limit]:
        print("  %r" % s)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in (
            ("survey", cmd_survey, "bytecode extent statistics for a package"),
            ("body", cmd_body, "raw bytecode of one function"),
            ("strings", cmd_strings, "string constants inside one function's bytecode")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("package")
        if name != "survey":
            p.add_argument("name")
        p.add_argument("--hex", action="store_true")
        p.add_argument("--limit", type=int, default=40)
        p.set_defaults(func=fn)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
