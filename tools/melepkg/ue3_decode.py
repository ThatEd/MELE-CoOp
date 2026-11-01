"""UE3 UnrealScript bytecode: the real EExprToken grammar, and how far it actually gets. A PARTIAL decoder.

The grammar here is transcribed from `UStruct::SerializeExpr` in `CodeRedModding/UnrealEngine3`
(`Development/Src/Core/Inc/ScriptSerialization.h`, blob ee67f58; `UnStack.h`, blob 774de7f), not guessed. Two
things about this build were *measured* with a closure test rather than assumed:

**`ScriptPointerType` is 32-bit.** `XFER_PROP_POINTER` and `XFER_FUNC_POINTER` are `XFERPTR`, whose width is
`sizeof(ScriptPointerType)`. Trying both widths over the same functions:

    build   ptr=8 closed   ptr=4 closed
    ME1          7.8%          23.5%
    ME2          5.8%          22.5%
    ME3          6.2%          16.7%

4 wins by 3x everywhere, so UE3 serialized script pointers as 32-bit even though the game is a 64-bit
executable.

**`FName` in bytecode is 8 bytes**, the same shape as in property tags - refuted the alternative by the same
test (8 B 21.9%/12.5%, 4 B 17.6%/12.4%, 12 B 16.8%/12.3%).

## What this module does NOT claim

**The decoder closes exactly on only ~40% / ~41% / ~14% of functions**, over every function with a body:

    build   functions   closed        desynced   unsupported token
    ME1       4,314     1,702 (39.5%)   1,013        1,599
    ME2       4,265     1,748 (41.0%)     949        1,568
    ME3       4,209       597 (14.2%)     814        2,798

Two findings sit behind those numbers.

**A bug in this module was worth 2x.** The native-final-function range (`token >= 0x70`) was raising as "not in
the table" *before* the handler that consumes its parameters could run. Fixing that took ME1 from 21.9% to 39.5%
and ME2 from 22.5% to 41.0%. The earlier note that "adding opcode cases did not help" was true but for the wrong
reason: the cases being added were never the problem.

**ME3's opcode table genuinely differs.** ME1/ME2 close walks containing native tokens up to `0xFF`, so the
native path works there. ME3 fails on `0x5b`-`0x65` and `0x2b`, `0x4c`-`0x50` - **gaps in the stock enum that this
build evidently uses**. Those are not desyncs: their presence is systematic, and ME2's walks never contain them.
Deriving their operand sizes empirically failed to converge - trying every length 0..12 and asking which lets
the rest of the body close gave near-ties (token `0x62`: 82 bodies at length 5, 78 at length 10), which is the
signature of a desync rather than an identifiable operand.

So this is a **partial** decoder for ME1/ME2 and a poor one for ME3. It deliberately exposes no per-function
disassembler: 40% is not enough to trust the one function you care about.

What it IS for:

  * the token names, so a byte can be identified where a token is expected;
  * `closure()` - the check that a candidate grammar is right, which is what found the native-token bug and
    established both operand widths;
  * `tokens_present()` - the tokens seen in walks that *do* close, as evidence about which this build uses.

Usage:

    python -I tools/melepkg/ue3_decode.py closure  <package>
    python -I tools/melepkg/ue3_decode.py tokens   <package>
"""
import argparse
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mele_tables as mt  # noqa: E402

# From `enum EExprToken` in UnStack.h, blob 774de7f. Names only - operands are in `Bird.expr`.
NAMES = {
    0x00: "EX_LocalVariable", 0x01: "EX_InstanceVariable", 0x02: "EX_DefaultVariable",
    0x03: "EX_StateVariable", 0x04: "EX_Return", 0x05: "EX_Switch", 0x06: "EX_Jump",
    0x07: "EX_JumpIfNot", 0x08: "EX_Stop", 0x09: "EX_Assert", 0x0A: "EX_Case",
    0x0B: "EX_Nothing", 0x0C: "EX_LabelTable", 0x0D: "EX_GotoLabel", 0x0E: "EX_EatReturnValue",
    0x0F: "EX_Let", 0x10: "EX_DynArrayElement", 0x11: "EX_New", 0x12: "EX_ClassContext",
    0x13: "EX_MetaCast", 0x14: "EX_LetBool", 0x15: "EX_EndParmValue", 0x16: "EX_EndFunctionParms",
    0x17: "EX_Self", 0x18: "EX_Skip", 0x19: "EX_Context", 0x1A: "EX_ArrayElement",
    0x1B: "EX_VirtualFunction", 0x1C: "EX_FinalFunction", 0x1D: "EX_IntConst",
    0x1E: "EX_FloatConst", 0x1F: "EX_StringConst", 0x20: "EX_ObjectConst", 0x21: "EX_NameConst",
    0x22: "EX_RotationConst", 0x23: "EX_VectorConst", 0x24: "EX_ByteConst", 0x25: "EX_IntZero",
    0x26: "EX_IntOne", 0x27: "EX_True", 0x28: "EX_False", 0x29: "EX_NativeParm",
    0x2A: "EX_NoObject", 0x2C: "EX_IntConstByte", 0x2D: "EX_BoolVariable",
    0x2E: "EX_DynamicCast", 0x2F: "EX_Iterator", 0x30: "EX_IteratorPop", 0x31: "EX_IteratorNext",
    0x32: "EX_StructCmpEq", 0x33: "EX_StructCmpNe", 0x34: "EX_UnicodeStringConst",
    0x35: "EX_StructMember", 0x36: "EX_DynArrayLength", 0x37: "EX_GlobalFunction",
    0x38: "EX_PrimitiveCast", 0x39: "EX_DynArrayInsert", 0x3A: "EX_ReturnNothing",
    0x3B: "EX_EqualEqual_DelDel", 0x3C: "EX_NotEqual_DelDel", 0x3D: "EX_EqualEqual_DelFunc",
    0x3E: "EX_NotEqual_DelFunc", 0x3F: "EX_EmptyDelegate", 0x40: "EX_DynArrayRemove",
    0x41: "EX_DebugInfo", 0x42: "EX_DelegateFunction", 0x43: "EX_DelegateProperty",
    0x44: "EX_LetDelegate", 0x45: "EX_Conditional", 0x46: "EX_DynArrayFind",
    0x47: "EX_DynArrayFindStruct", 0x48: "EX_LocalOutVariable", 0x49: "EX_DefaultParmValue",
    0x4A: "EX_EmptyParmValue", 0x4B: "EX_InstanceDelegate", 0x51: "EX_InterfaceContext",
    0x52: "EX_InterfaceCast", 0x53: "EX_EndOfScript", 0x54: "EX_DynArrayAdd",
    0x55: "EX_DynArrayAddItem", 0x56: "EX_DynArrayRemoveItem", 0x57: "EX_DynArrayInsertItem",
    0x58: "EX_DynArrayIterator", 0x59: "EX_DynArraySort", 0x5A: "EX_JumpIfFilterEditorOnly",
    0x60: "EX_ExtendedNative",
}

ZERO = frozenset((0x0B, 0x16, 0x17, 0x25, 0x26, 0x27, 0x28, 0x2A, 0x30, 0x31, 0x3F, 0x4A, 0x08, 0x15))
PROP_PTR = frozenset((0x00, 0x01, 0x02, 0x03, 0x48, 0x3A, 0x0E))
UNSUPPORTED = frozenset((0x4B, 0x5A, 0x44, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x46, 0x47,
                         0x39, 0x40, 0x11, 0x52, 0x4C, 0x4D, 0x4E, 0x4F, 0x50))

PTR_SIZE = 4      # measured, see the module docstring
NAME_SIZE = 8     # measured, see the module docstring


class Unsupported(Exception):
    pass


class Truncated(Exception):
    pass


class Bird:
    """A bytecode cursor implementing `SerializeExpr`'s operand grammar."""

    def __init__(self, data, ptr_size=PTR_SIZE, name_size=NAME_SIZE):
        self.d = data
        self.ptr = ptr_size
        self.name_size = name_size
        self.at = 0

    def need(self, n):
        if self.at + n > len(self.d):
            raise Truncated("want %d at %d of %d" % (n, self.at, len(self.d)))

    def u8(self):
        self.need(1)
        v = self.d[self.at]
        self.at += 1
        return v

    def u16(self):
        self.need(2)
        v = struct.unpack_from("<H", self.d, self.at)[0]
        self.at += 2
        return v

    def u32(self):
        self.need(4)
        v = struct.unpack_from("<I", self.d, self.at)[0]
        self.at += 4
        return v

    def ptrv(self):
        self.need(self.ptr)
        v = int.from_bytes(self.d[self.at:self.at + self.ptr], "little")
        self.at += self.ptr
        return v

    def skip_name(self):
        self.need(self.name_size)
        self.at += self.name_size

    def _parms(self, depth):
        out = []
        while True:
            got = self.expr(depth + 1)
            out += got
            if got and got[-1][0] == "EX_EndFunctionParms":
                return out

    def expr(self, depth=0):
        """Consume one expression; returns the [(token_name, detail)] seen at this level."""
        if depth > 64:
            raise Unsupported("recursion")
        token = self.u8()
        name = NAMES.get(token)
        if name is None:
            # `Expr >= EX_FirstNative` (0x70) is a native final function whose token IS the id - it is not a
            # gap in the table. Handled below, so this must not raise for it first.
            if token < 0x70:
                raise Unsupported("token 0x%02x not in the table" % token)
            return [("EX_NativeFinal(0x%02x)" % token, "")] + self._parms(depth)
        seen = [(name, "")]
        if token in ZERO or token == 0x29:
            return seen
        if token in PROP_PTR:
            seen[-1] = (name, str(self.ptrv()))
            return seen
        if token in UNSUPPORTED:
            raise Unsupported(name)
        if token == 0x38:                                    # EX_PrimitiveCast
            self.u8()
            return seen + self.expr(depth + 1)
        if token in (0x2D, 0x51):                            # EX_BoolVariable / EX_InterfaceContext
            return seen + self.expr(depth + 1)
        if token in (0x0F, 0x14):                            # EX_Let / EX_LetBool
            return seen + self.expr(depth + 1) + self.expr(depth + 1)
        if token == 0x06:                                    # EX_Jump
            self.u16()
            return seen
        if token == 0x41:                                    # EX_DebugInfo
            self.u32(); self.u32(); self.u32(); self.u8()
            return seen
        if token == 0x0A:                                    # EX_Case
            if self.u16() == 0xFFFF:
                return seen
            return seen + self.expr(depth + 1)
        if token == 0x0C:                                    # EX_LabelTable
            while True:
                idx = self.u32()
                self.u32()
                if idx == 0:
                    return seen
        if token == 0x0D:                                    # EX_GotoLabel
            return seen + self.expr(depth + 1)
        if token in (0x32, 0x33):                            # struct comparisons
            self.skip_name()
            return seen + self.expr(depth + 1) + self.expr(depth + 1)
        if token in (0x3B, 0x3C, 0x3D, 0x3E):                # delegate comparisons
            return seen + self.expr(depth + 1) + self.expr(depth + 1)
        if token == 0x42:                                    # EX_DelegateFunction
            self.u8(); self.ptrv(); self.skip_name()
            return seen
        if token == 0x43:                                    # EX_DelegateProperty
            self.skip_name(); self.ptrv()
            return seen
        if token in (0x13, 0x2E):                            # casts
            self.ptrv()
            return seen + self.expr(depth + 1)
        if token == 0x18:                                    # EX_Skip
            self.u16()
            return seen + self.expr(depth + 1)
        if token == 0x49:                                    # EX_DefaultParmValue
            self.u16()
            got = self.expr(depth + 1)
            self.u8()
            return seen + got
        if token == 0x35:                                    # EX_StructMember
            self.ptrv(); self.ptrv(); self.u8(); self.u8()
            return seen + self.expr(depth + 1)
        if token == 0x04:                                    # EX_Return
            return seen + self.expr(depth + 1)
        if token == 0x07:                                    # EX_JumpIfNot
            self.u16()
            return seen + self.expr(depth + 1)
        if token == 0x2F:                                    # EX_Iterator
            got = self.expr(depth + 1)
            self.u16()
            return seen + got
        if token == 0x05:                                    # EX_Switch
            self.ptrv(); self.u8()
            return seen + self.expr(depth + 1)
        if token == 0x09:                                    # EX_Assert
            self.u16(); self.u8()
            return seen + self.expr(depth + 1)
        if token in (0x19, 0x12):                            # EX_Context / EX_ClassContext
            got = self.expr(depth + 1)
            self.u16(); self.ptrv(); self.u8()
            return seen + got + self.expr(depth + 1)
        if token in (0x1A, 0x10):                            # array element
            return seen + self.expr(depth + 1) + self.expr(depth + 1)
        if token == 0x36:                                    # EX_DynArrayLength
            return seen + self.expr(depth + 1)
        if token == 0x45:                                    # EX_Conditional
            got = self.expr(depth + 1)
            self.u16()
            got += self.expr(depth + 1)
            self.u16()
            return seen + got + self.expr(depth + 1)
        if token in (0x1B, 0x37):                            # EX_VirtualFunction / EX_GlobalFunction
            self.skip_name()
            return seen + self._parms(depth)
        if token == 0x1C:                                    # EX_FinalFunction
            self.ptrv()
            return seen + self._parms(depth)
        if token == 0x60:                                    # EX_ExtendedNative
            self.u8()
            return seen + self._parms(depth)
        if token >= 0x70:                                    # native final function
            return seen + self._parms(depth)
        return seen


def functions_with_bodies(table):
    """Every `Core.Function` whose body is longer than a native stub."""
    for e in table["exports"]:
        if mt.describe_index(e["class_index"], table["exports"], table["imports"]) != "Core.Function":
            continue
        body = mt.function_body(table, e)
        if len(body) > 4:
            yield e, body


def closure(table, ptr_size=PTR_SIZE, name_size=NAME_SIZE):
    """(closed, desynced, unsupported, total) over every function with a body."""
    closed = desynced = unsupported = 0
    for _e, body in functions_with_bodies(table):
        b = Bird(body, ptr_size, name_size)
        try:
            while b.at < len(body):
                b.expr()
            if b.at == len(body):
                closed += 1
            else:
                desynced += 1
        except Unsupported:
            unsupported += 1
        except Truncated:
            desynced += 1
    return closed, desynced, unsupported, closed + desynced + unsupported


def tokens_present(table):
    """Token bytes that appear inside walks which close exactly, with counts."""
    counts = collections.Counter()
    for _e, body in functions_with_bodies(table):
        b = Bird(body)
        seen = []
        try:
            while b.at < len(body):
                start = b.at
                b.expr()
                seen.append(body[start])
            if b.at == len(body):
                for tok in set(seen):
                    counts[tok] += 1
        except (Unsupported, Truncated):
            continue
    return counts


def cmd_closure(args):
    t = mt.build(args.package)
    print("%s: closure rate by pointer width and FName width" % t["package"])
    for ptr in (4, 8):
        for nm in (8, 4):
            c, d, u, total = closure(t, ptr, nm)
            print("   ptr=%d name=%d: closed %d/%d (%.1f%%), desynced %d, unsupported %d"
                  % (ptr, nm, c, total, 100.0 * c / max(total, 1), d, u))
    print()
    print("verdict: ptr=4 / name=8 is the best fit; the absolute rate is too low to decode any one "
          "function, which is why this module exposes no disassembler")
    return 0


def cmd_tokens(args):
    t = mt.build(args.package)
    counts = tokens_present(t)
    print("%s: token bytes seen in walks that close exactly" % t["package"])
    for tok, n in sorted(counts.items()):
        print("   0x%02x %-28s %d" % (tok, NAMES.get(tok, "UNKNOWN"), n))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("closure", help="how often a walk closes exactly, by operand width")
    p.add_argument("package")
    p.set_defaults(func=cmd_closure)
    p = sub.add_parser("tokens", help="token bytes present in walks that close exactly")
    p.add_argument("package")
    p.set_defaults(func=cmd_tokens)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
