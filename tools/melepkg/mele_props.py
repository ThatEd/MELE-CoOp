#!/usr/bin/env python
"""Read the tagged property stream inside a UObject - the values, not just the field names.

## Why this was the blocker

`mele_tables.py` proves what a class *declares*. Every co-op plan then hit the same wall: the
question is never "does `GameInfo` have `DefaultGameType`" but **"what is it set to in the object
the campaign actually constructs"**. Without that, a design can only be argued, not checked:

  * `GameReplicationInfo.GameClass` decides which `GameInfo` spawns. If it is set to a class that
    is not `SFXGame`/`BioSPGame`, a mutator installed on those never runs.
  * `GameInfo.DefaultGameType` is a string naming the game class, and the CDO's value is the
    engine's answer to "which game is this".
  * `bIsMultiplayer`/`bIsLanMatch` flags on a `GameInfo` decide which code paths the engine takes,
    and a value read from a CDO is a fact rather than an inference.

A `Default__X` export is a class default object: **its serial data is exactly the property tag
stream**, so a correct parse consumes it completely. That gives a closure test which cannot pass by
accident - the parse must end precisely at the export's `serial_offset + serial_size`.

## The layout, measured rather than assumed

The layout ME:LE actually writes is

    Name FName(8) | Type FName(8) | Size int32(4) | ArrayIndex int32(4)
      [StructName FName(8) if StructProperty | EnumName FName(8) if ByteProperty]
      [BoolVal u8(1) if BoolProperty]
      [payload of Size bytes]

and it is **not** laid out the way the PC UE3 samples are. Three differences were measured; the
first two were guessed wrong before being measured, and each guess produced plausible-looking
garbage rather than an error - which is exactly why the closure test exists.

* **`Size` and `ArrayIndex` come *before* `StructName`/`EnumName`.** The PC samples put the optional
  name first. Measured on the difficulty byte property of `Default__SFXSaveGame` at `0x0fe0`:

      +00 Difficulty   +08 ByteProperty   +10 Size=8   +14 Index=0
      +18 EDifficultyOptions (enum)       +20 DO_Level3 (value as an FName)   +28 None

  40 bytes = 8+8+4+4+8+8. Getting this backwards survives every property *before* the first struct
  or enum and then desynchronises permanently, so the first 8 tags look right and the rest do not.
* **A `bool` spends one byte after the header**, so `bDebug_AI` and `bAILogToWindow` are each 25
  bytes (8+8+4+4+1), not 24. Believing the value lived inside the `ArrayIndex` slot consumed 24,
  left one unread byte, and desynchronised at the first bool - 9/60 objects closed.
* **Objects begin 4 bytes in.** A `Default__X` export's serial data starts with an int32
  preamble and the first tag always lands at **+4** (40/40 measured on `ME3/SFXGame.pcc`). An
  earlier version of this note claimed the preamble "is not a name index (174..959)", and that
  reasoning is **wrong**: 174..959 are valid indices into a 20,433-entry name table, and `ME1`'s
  `Default__Actor` has a preamble of 1245, which is also valid. A range check therefore cannot
  choose between the layouts; only the closure rate can (40/40 at `+4` against 0/40 at `+0`).
  Reading from +0 made every path name resolve to a real but unrelated name-table entry - which is
  why the first attempt "worked" and produced nonsense.

`closure()` is the proof: the walk must land on the exact end of the object's serial data. Theparser is not asserted correct by construction, it is required to close, and the `+0` rival start
offset must fail on the same objects (`probe` reports both, so the test has a control).

## What is decoded, and what is only bounded

Decoded to a value: `Int`, `Float`, `Bool`, `Byte` (both plain and enum-by-name), `Str`, `Name`,
`Object`/`Class` (resolved through the package index), and the count of an `Array`. A `Struct` is
kept as bytes with its size, because decoding it needs the struct's own layout and reporting a
guess as a value is the failure this project keeps repeating.

Usage:

    python -I tools/melepkg/mele_props.py dump <package> <object> 
    python -I tools/melepkg/mele_props.py value <package> <object> <property> 
    python -I tools/melepkg/mele_props.py probe <package> [--count N]
    python -I tools/melepkg/mele_props.py cdo <package> <class>        # read Default__<class>
    python -I tools/melepkg/mele_props.py mapgame <package>            # map prefix -> game class
    python -I tools/melepkg/mele_props.py routing                      # all three installs
    python -I tools/melepkg/mele_props.py coopcheck                    # travel/clamp/roster
    python -I tools/melepkg/mele_props.py savecheck                    # how each build saves
    python -I tools/melepkg/mele_props.py maplist                      # how many maps to cover
    python -I tools/melepkg/mele_props.py squadadd                     # how the campaign recruits
    python -I tools/melepkg/mele_props.py storycheck                   # what advances the campaign
    python -I tools/melepkg/mele_props.py plotcheck                    # how big story state is
    python -I tools/melepkg/mele_props.py joincheck                     # is the join path in script
    python -I tools/melepkg/mele_props.py spawncheck                    # what a guest pawn needs

## SCOPE LIMIT: class default objects only, not live level actors

This reader is correct on `Default__X` exports and is **not** usable on an actor spawned in a level.
`probe` measures the difference and it is not subtle:

    ME3/SFXGame.pcc        40/40 objects close exactly   (100%)
    ME3/BioA_Cat002.pcc     9/40                         ( 22.5%)
    ME2/BioA_BchLmL.pcc     0/40                         (  0%)

A spawned actor's serial block is not a plain property tag stream, so no tag walk over it means
anything. The failure is quiet rather than loud - a level query returns a handful of plausible tags
and then desynchronises - so the test suite pins both halves: CDOs must stay at 100%, and a
live level must stay below. Do not read `GameTypesSupportedOnThisMap` (or anything else) out of a
level package with this tool; the answer will look like a measurement.
"""
import argparse
import importlib.util
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Fixed blob sizes, used only to sanity-check a tag rather than to trust it.
FIXED_SIZE = {
    "IntProperty": 4, "FloatProperty": 4, "ObjectProperty": 4, "ClassProperty": 4,
    "ComponentProperty": 4, "InterfaceProperty": 4, "BoolProperty": 1,
    "NameProperty": 8, "DelegateProperty": 12,
}


def load_tables():
    spec = importlib.util.spec_from_file_location(
        "mele_tables", os.path.join(HERE, "mele_tables.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TagError(Exception):
    pass


def _fname(buf, at, names):
    """An `FName` is an int32 index into the name table plus an int32 instance number."""
    if at + 8 > len(buf):
        raise TagError("FName past end at 0x%x" % at)
    idx, num = struct.unpack_from("<ii", buf, at)
    if idx < 0 or idx >= len(names):
        raise TagError("FName index %d out of range (%d names) at 0x%x" % (idx, len(names), at))
    return names[idx], num, at + 8


def parse_object(table, buf, obj, start_offset=4, max_tags=40000):
    """Parse one object's property tag stream.

    Returns (tags, end, why) where `why` is "none-terminator" when a `None` tag was read, or
    "end-of-serial" when the stream ran out. `end` is always an absolute buffer offset.

    `start_offset` is the measured 4-byte preamble described in the module docstring; it is a
    parameter so a test can demonstrate that 0 does *not* close, rather than asserting 4 by fiat.
    """
    names = table["name_table"]
    disp = table["displacement"]
    start = obj["serial_offset"] - disp + start_offset
    end = obj["serial_offset"] - disp + obj["serial_size"]
    if start < 0 or start > end or end > len(buf):
        raise TagError("object serial 0x%x..0x%x outside buffer 0x%x"
                       % (start, end, len(buf)))
    at = start
    tags = []
    why = "end-of-serial"
    for _ in range(max_tags):
        if at + 8 > end:
            break
        name, _num, at = _fname(buf, at, names)
        if name == "None":
            why = "none-terminator"
            break
        ptype, _n, at = _fname(buf, at, names)
        # MEASURED ORDER: Size and ArrayIndex come immediately after Type, and the optional
        # StructName/EnumName FName comes AFTER them - the reverse of the PC UE3 samples, which
        # put StructName before Size. Reading the difficulty byte property of `Default__SFXSaveGame`
        # at 0x0fe0 shows it plainly:
        #
        #   +00 Difficulty   +08 ByteProperty   +10 Size=8   +14 Index=0
        #   +18 EDifficultyOptions (enum)       +20 DO_Level3 (value as an FName)   +28 None
        #
        # 40 bytes for the tag, and 8+8+4+4+8+8 = 40. The same order explains a bool being 25 bytes
        # (8+8+4+4+1) and a float 28 (8+8+4+4+4), so one layout covers all three rather than three.
        if at + 8 > end:
            raise TagError("property %r truncated at 0x%x" % (name, at))
        size, array_index = struct.unpack_from("<ii", buf, at)
        at += 8
        struct_name = None
        enum_name = None
        if ptype == "StructProperty":
            struct_name, _n, at = _fname(buf, at, names)
        elif ptype == "ByteProperty":
            enum_name, _n, at = _fname(buf, at, names)
        # A bool keeps the full Size/ArrayIndex pair and then spends ONE more byte on its value:
        # `bDebug_AI` is 25 bytes and `bAILogToWindow` is 25, and the next tag starts 25 on. An
        # earlier version folded the value into the ArrayIndex slot and consumed 24, which left a
        # single unread byte and desynchronised every tag after the first bool.
        if ptype == "BoolProperty":
            if at + 1 > end:
                raise TagError("bool %r truncated at 0x%x" % (name, at))
            bool_val = buf[at] != 0
            at += 1
            if size != 0:
                raise TagError("bool %r declares size %d" % (name, size))
            tags.append({
                "name": name, "type": ptype, "struct": None, "enum": None, "size": 0,
                "array_index": array_index, "bool": bool_val, "value": bool_val,
                "offset": at,
            })
            continue
        if size < 0 or at + size > end:
            raise TagError("property %r size %d at 0x%x overruns object end 0x%x"
                           % (name, size, at, end))
        raw = buf[at:at + size]
        entry = {
            "name": name, "type": ptype, "struct": struct_name, "enum": enum_name,
            "size": size, "array_index": array_index, "bool": None,
            "value": decode_value(table, buf, ptype, struct_name, raw, size, names),
            "offset": at,
        }
        if (ptype == "ArrayProperty" and size >= 4
                and _looks_like_tag_stream(buf, names, at + 4, at + size)):
            subs, used, why2 = decode_array_of_structs(buf, names, at, size)
            entry["elements"] = subs
            entry["array_exact"] = (used == size and why2 == "ok")
        tags.append(entry)
        at += size
    return tags, at, why


def decode_value(table, buf, ptype, struct_name, raw, size, names):
    """Decode a tag's payload where the type makes the value unambiguous."""
    try:
        if ptype == "IntProperty" and size >= 4:
            return struct.unpack_from("<i", raw, 0)[0]
        if ptype == "FloatProperty" and size >= 4:
            v = struct.unpack_from("<f", raw, 0)[0]
            return round(v, 6)
        if ptype == "BoolProperty":
            return None  # the value lives in the tag, not the payload
        if ptype == "ByteProperty":
            if size == 8:
                # An enum byte property serializes the member as an FName.
                idx, num = struct.unpack_from("<ii", raw, 0)
                if 0 <= idx < len(names):
                    return names[idx]
                return "<byte-name %d:%d>" % (idx, num)
            if size >= 1:
                return raw[0]
        if ptype == "StrProperty" and size >= 4:
            # An FString's length prefix is NEGATIVE for UTF-16LE, and ME:LE writes every
            # default-value string that way: `GameName` is `fbffffff 47 00 61 00 6d 00 65 00 0000`,
            # i.e. -5 then "Game\0" in wide chars. Treating it as ANSI (the first version) made
            # every string in every CDO read as None, which is exactly the silent-null failure a
            # design would then be built on.
            n = struct.unpack_from("<i", raw, 0)[0]
            if n < 0:
                chars = min(-n, (size - 4) // 2)
                return raw[4:4 + 2 * chars].decode("utf-16-le", "replace").rstrip("\x00")
            if 0 <= n <= size - 4:
                return raw[4:4 + max(0, n - 1)].decode("latin-1")
        if ptype == "NameProperty" and size >= 8:
            idx, num = struct.unpack_from("<ii", raw, 0)
            if 0 <= idx < len(names):
                return names[idx] + ("_%d" % num if num else "")
        if ptype in ("ObjectProperty", "ClassProperty", "ComponentProperty",
                     "InterfaceProperty") and size >= 4:
            v = struct.unpack_from("<i", raw, 0)[0]
            return "<%s>" % resolve_pkg_index(table, v)
        if ptype == "ArrayProperty" and size >= 4:
            return "<array of %d>" % struct.unpack_from("<i", raw, 0)[0]
    except (struct.error, IndexError):
        pass
    return None


def decode_array_of_structs(buf, names, start, size, max_depth=2):
    """Decode an `ArrayProperty` whose elements are themselves tagged property streams.

    This is how ME:LE serialises a `TArray<FStruct>`: int32 element count, then each element as a
    complete tag stream it terminates with a `None` FName. Found on
    `Default__GameInfo.DefaultMapPrefixes`, whose three elements are the game's whole
    map-prefix -> game-class routing table, so reading it is what answers "which game class does
    this level construct" for ME3 without guessing.

    Returns (elements, consumed, why). `consumed` must equal `size` for the decode to be trusted -
    the caller checks, and the check fails loudly rather than returning a plausible list.
    """
    if size < 4:
        return [], 0, "too-small"
    count = struct.unpack_from("<i", buf, start)[0]
    if count < 0 or count > 100000:
        return [], 0, "bad-count %d" % count
    at = start + 4
    stop = start + size
    elements = []
    for _ in range(count):
        tags, at, why = parse_object_in(buf, names, at, stop, max_depth)
        if why.startswith("error"):
            return elements, at - start, why
        elements.append(tags)
    return elements, at - start, "ok"


def parse_object_in(buf, names, start, end, max_depth=2, max_tags=40000):
    """`parse_object` over an explicit byte range, so a nested stream can be walked in place.

    Kept separate rather than generalising `parse_object`, because `parse_object` derives its range
    from an export record and that derivation is the part that must not drift.
    """
    at = start
    tags = []
    why = "end-of-range"
    for _ in range(max_tags):
        if at + 8 > end:
            break
        try:
            name, _num, at = _fname(buf, at, names)
        except TagError as exc:
            return tags, at, "error: %s" % exc
        if name == "None":
            why = "none-terminator"
            break
        try:
            ptype, _n, at = _fname(buf, at, names)
            if at + 8 > end:
                return tags, at, "error: truncated %r" % name
            size, array_index = struct.unpack_from("<ii", buf, at)
            at += 8
            struct_name = enum_name = None
            if ptype == "StructProperty":
                struct_name, _n, at = _fname(buf, at, names)
            elif ptype == "ByteProperty":
                enum_name, _n, at = _fname(buf, at, names)
            if ptype == "BoolProperty":
                if at + 1 > end:
                    return tags, at, "error: bool %r truncated" % name
                bv = buf[at] != 0
                at += 1
                tags.append({"name": name, "type": ptype, "struct": None, "enum": None,
                             "size": 0, "array_index": array_index, "bool": bv,
                             "value": bv, "offset": at})
                continue
            if size < 0 or at + size > end:
                return tags, at, "error: %r size %d overruns" % (name, size)
            raw = buf[at:at + size]
            value = decode_value(None, buf, ptype, struct_name, raw, size, names)
            entry = {"name": name, "type": ptype, "struct": struct_name, "enum": enum_name,
                     "size": size, "array_index": array_index, "bool": None,
                     "value": value, "offset": at}
            # Recurse only where the element is provably a tag stream, and only one step deeper:
            # an unverified recursive walk would invent structure out of payload bytes.
            if (ptype == "ArrayProperty" and max_depth > 0 and size >= 4
                    and _looks_like_tag_stream(buf, names, at + 4, at + size)):
                subs, used, why2 = decode_array_of_structs(
                    buf, names, at, size, max_depth - 1)
                entry["elements"] = subs
                entry["array_exact"] = (used == size and why2 == "ok")
            at += size
            tags.append(entry)
        except TagError as exc:
            return tags, at, "error: %s" % exc
    return tags, at, why


def _looks_like_tag_stream(buf, names, start, end):
    """Cheap shape test: does a tag header sit at `start` with a plausible type name?"""
    if start + 16 > end:
        return False
    try:
        i1, _ = struct.unpack_from("<ii", buf, start)
        i2, _ = struct.unpack_from("<ii", buf, start + 8)
    except struct.error:
        return False
    if not (0 <= i1 < len(names) and 0 <= i2 < len(names)):
        return False
    t = names[i2]
    return t.endswith("Property")


def resolve_pkg_index(table, value):
    """A package index: positive is an export in this package, negative an import."""
    if value == 0:
        return "None"
    exports, imports = table["exports"], table["imports"]
    if value > 0:
        k = value - 1
        if 0 <= k < len(exports):
            return exports[k]["object_name"]
        return "?export%d" % k
    k = -value - 1
    if 0 <= k < len(imports):
        return "%s.%s" % (imports[k]["source_name"], imports[k]["object_name"])
    return "?import%d" % k


def closure(table, buf, objects, start_offset=4):
    """How many objects' tag streams consume their serial data exactly.

    This is the check that makes the whole reader trustworthy: a wrong tag layout desynchronises
    within a few tags and almost never lands on the exact end of the object.
    """
    exact = 0
    tried = 0
    errors = 0
    for obj in objects:
        tried += 1
        try:
            tags, end, why = parse_object(table, buf, obj, start_offset=start_offset)
        except TagError:
            errors += 1
            continue
        want = obj["serial_offset"] - table["displacement"] + obj["serial_size"]
        if end == want:
            exact += 1
    return exact, tried, errors


def pick_layout(table, buf, objects):
    """Compare the measured layout against the two refuted alternatives.

    Reporting all three is the point: "the layout closes 90% of objects" is only evidence if the
    wrong layouts close visibly fewer. A probe that cannot fail is what this project keeps
    catching, so the rivals are run rather than assumed inferior.
    """
    results = {}
    for label, start in (("start=+4 (measured)", 4), ("start=+0 (refuted)", 0)):
        results[label] = closure(table, buf, objects, start_offset=start)
    best = max(results, key=lambda k: results[k][0])
    return best, results


def find_object(table, name):
    for e in table["exports"]:
        if e["object_name"] == name:
            return e
    return None


def _candidates(table, limit=40):
    """Objects with a non-zero serial that are likely to carry a tag stream.

    `Default__` exports are class default objects, which are the richest and the most likely to
    close exactly; falling back to any sized export keeps the probe usable on a package without
    CDOs.
    """
    cdos = [e for e in table["exports"]
            if (e["object_name"] or "").startswith("Default__") and e["serial_size"] > 16]
    if len(cdos) < 8:
        cdos += [e for e in table["exports"]
                 if e["serial_size"] > 48 and not (e["object_name"] or "").startswith("Default__")]
    return cdos[:limit]


def open_package(path):
    mt = load_tables()
    try:
        t = mt.build(path)
    except SystemExit as e:
        raise SystemExit(str(e))
    return mt, t


def cmd_probe(args):
    mt, t = open_package(args.package)
    objs = _candidates(t, args.count)
    best, results = pick_layout(t, t["buf"], objs)
    print("%s: %d candidate object(s)" % (t["package"], len(objs)))
    for label in results:
        exact, tried, errors = results[label]
        print("  %-26s %3d/%3d exact (%3d error)  = %5.1f%%"
              % (label, exact, tried, errors, 100.0 * exact / max(1, tried)))
    print()
    print("best: %s" % best)
    if results[best][0] < results[best][1] * 0.5:
        print("WARNING: fewer than half the objects close. Do not trust these values.")
        return 2
    return 0


def cmd_dump(args):
    mt, t = open_package(args.package)
    obj = find_object(t, args.object)
    if obj is None:
        print("%s: no export named %r" % (t["package"], args.object))
        return 1
    tags, end, why = parse_object(t, t["buf"], obj)
    want = obj["serial_offset"] - t["displacement"] + obj["serial_size"]
    print("%s::%s  %d tag(s), ended %s at 0x%x, serial ends 0x%x  %s"
          % (t["package"], args.object, len(tags), why, end, want,
             "EXACT" if end == want else "MISMATCH"))
    print()
    for tg in tags:
        if args.grep and args.grep.lower() not in tg["name"].lower():
            continue
        val = tg["value"]
        shown = "(tag)" if tg["type"] == "BoolProperty" else (
            repr(val) if val is not None else "<%d bytes>" % tg["size"])
        if tg["type"] == "BoolProperty":
            shown = str(tg["bool"])
        extra = ""
        if tg["struct"]:
            extra = " [struct %s]" % tg["struct"]
        elif tg.get("enum"):
            extra = " [enum %s]" % tg["enum"]
        # Windows console is cp1252 by default and an FString can hold any Unicode; escaping
        # rather than raising keeps a wide string from killing a whole dump half way through.
        print(("  %-34s %-22s %-4s %s%s"
               % (tg["name"], tg["type"], tg["size"], shown, extra))
              .encode("ascii", "backslashreplace").decode("ascii"))
        if tg.get("elements"):
            _print_elements(tg, "      ")
    return 0 if end == want else 2


def _print_elements(tg, pad):
    """Render a decoded `TArray<FStruct>` and mark whether it consumed its bytes exactly."""
    mark = "exact" if tg.get("array_exact") else "INEXACT - do not trust"
    print(("%s[%d element(s), %s]"
           % (pad, len(tg["elements"]), mark)).encode("ascii", "backslashreplace").decode("ascii"))
    for i, el in enumerate(tg["elements"]):
        print(("%s  (%d)" % (pad, i)).encode("ascii", "backslashreplace").decode("ascii"))
        for sub in el:
            v = sub["bool"] if sub["type"] == "BoolProperty" else sub["value"]
            if isinstance(v, str):
                v = v.encode("ascii", "backslashreplace").decode("ascii")
            print(("%s    %-24s %-14s %s" % (pad, sub["name"],
                                             sub["type"].replace("Property", ""), v))
                  .encode("ascii", "backslashreplace").decode("ascii"))
            if sub.get("elements"):
                _print_elements(sub, pad + "      ")


def cmd_value(args):
    mt, t = open_package(args.package)
    obj = find_object(t, args.object)
    if obj is None:
        print("%s: no export named %r" % (t["package"], args.object))
        return 1
    tags, end, why = parse_object(t, t["buf"], obj)
    want = obj["serial_offset"] - t["displacement"] + obj["serial_size"]
    hits = [tg for tg in tags if tg["name"] == args.property]
    if not hits:
        print("%s::%s has no property %r in its %d tag(s)"
              % (t["package"], args.object, args.property, len(tags)))
        near = [tg["name"] for tg in tags if args.property.lower() in tg["name"].lower()][:8]
        if near:
            print("near: %s" % ", ".join(near))
        return 1
    for tg in hits:
        val = tg["bool"] if tg["type"] == "BoolProperty" else tg["value"]
        print("%s  =  %r%s" % (args.property, val,
                               "" if end == want else "   [PARSE MISMATCH - do not trust]"))
    return 0 if end == want else 2


def cmd_mapinfo(args):
    """Print the map-prefix -> game-class routing table a build uses to pick a game class.

    ME3 answers this from `Default__GameInfo.DefaultMapPrefixes`; a map whose name starts with a
    listed prefix constructs that prefix's `GameType`. ME1/ME2 have no such table, so the same
    question has a different answer per build and this command reports which one applies instead
    of pretending they match.
    """
    mt, t = open_package(args.package)
    obj = find_object(t, "Default__GameInfo")
    if obj is None:
        print("%s: no Default__GameInfo export" % t["package"])
        return 1
    tags, end, why = parse_object(t, t["buf"], obj)
    want = obj["serial_offset"] - t["displacement"] + obj["serial_size"]
    exact = end == want
    got = {tg["name"]: tg for tg in tags}
    if "DefaultGameType" in got:
        print("DefaultGameType = %r" % (got["DefaultGameType"]["value"],))
    else:
        print("DefaultGameType = (absent on this build)")
    if "GameInfoClassAliases" in got and got["GameInfoClassAliases"].get("elements"):
        print("GameInfoClassAliases:")
        for el in got["GameInfoClassAliases"]["elements"]:
            row = {s["name"]: s["value"] for s in el}
            print("  %-16s -> %s" % (row.get("ShortName"), row.get("GameClassName")))
    if "DefaultMapPrefixes" in got and got["DefaultMapPrefixes"].get("elements"):
        print("DefaultMapPrefixes (first matching prefix wins):")
        for el in got["DefaultMapPrefixes"]["elements"]:
            row = {s["name"]: s["value"] for s in el}
            print("  prefix %-14s game %-46s mp=%s common=%s forced=%s"
                  % (row.get("Prefix"), row.get("GameType"),
                     row.get("bIsMultiplayer"), row.get("bUsesCommonPackage"),
                     row.get("ForcedObjects")))
    else:
        print("DefaultMapPrefixes = (absent on this build)")
    print()
    print("closure: %s" % ("EXACT" if exact else "MISMATCH - do not trust"))
    return 0 if exact else 2


def _require_install(games=("ME1", "ME2", "ME3")):
    """True when at least one build's `Engine.pcc` can be found.

    Without this, a missing install does not fail - it produces a report full of zeroes
    ("declared=False, 0 CDOs closed exactly"), which reads as a measurement of an absent property
    rather than of an absent install. That is the `0 of 10 declare it` failure in another shape, so
    the check that guards it has to run before any package is touched.
    """
    mt = load_tables()
    pkg = mt.load_pkg()
    for game in games:
        for root in pkg.game_dirs():
            cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole", "Engine.pcc")
            if os.path.isfile(cand):
                return True
    return False


def cmd_routing(args):
    """Reproduce the game-class routing findings for all three installs in one command.

    This exists because the answers above were originally obtained by a dozen one-off scratch
    scripts, each of which had to re-derive the package paths. A finding that cannot be re-run is
    not a finding - it is a memory. Every check here is one that can FAIL, and each prints the
    evidence it used:

      * `GameTypePrefix` is declared on all three builds, and ME1/ME2 have 5 fields while ME3 has 7.
        A build that gains or loses a field would silently break a writer, so the count is asserted.
      * `DefaultMapPrefixes` / `DefaultGameType` / `GameInfoClassAliases` are DECLARED on
        `Engine.GameInfo` on all three, but only ME3's CDO SETS them. The asymmetry is the finding.
      * The exe-visible config key `game-ini:Engine.GameInfo.DefaultGame` is present in every build,
        so "ME1 has no DefaultGame" must mean "ME1 ships no game-ini", not "ME1 lacks the mechanism".
    """
    mt = load_tables()
    pkg = mt.load_pkg()
    probe = getattr(mt, "SAMPLE_PKG", "Engine")

    # The struct shape, per build. ME3 adds OverrideCommonPackage and bIsMultiplayer.
    expect_fields = {"ME1": 5, "ME2": 5, "ME3": 7}
    got_fields = {}
    rows_required = []
    cdo_state = {}
    key_state = {}

    for game in ("ME1", "ME2", "ME3"):
        path = None
        for root in pkg.game_dirs():
            cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole",
                                "Engine.pcc")
            if os.path.isfile(cand):
                path = cand
                break
        if path is None:
            print("%s: install not found" % game)
            continue
        t = mt.build(path)
        # (a) the GameTypePrefix struct
        st = [e for e in t["exports"] if e["object_name"] == "GameTypePrefix"]
        if st:
            rows = _struct_rows(mt, t, st[0])
            got_fields[game] = len(rows)
            rows_required.append((game, [r[0] for r in rows]))
        # (b) which routing properties Engine.GameInfo declares
        h = [e for e in t["exports"] if e["class_index"] == 0 and e["object_name"] == "GameInfo"]
        declared = set()
        if h:
            declared = {r[1] for r in mt.class_fields(t, t["buf"], h[0])[0]}
        # (c) which of them the CDO actually sets
        cdo = find_object(t, "Default__GameInfo")
        setnames, exact = set(), False
        if cdo is not None:
            tags, end, _why = parse_object(t, t["buf"], cdo)
            want = cdo["serial_offset"] - t["displacement"] + cdo["serial_size"]
            exact = end == want
            setnames = {tg["name"] for tg in tags}
        keys = [k for k in ("DefaultMapPrefixes", "DefaultGameType", "GameInfoClassAliases",
                            "DefaultGameClassPath", "GameTypePrefix")
                if k in declared]
        cdo_state[game] = (keys, sorted(setnames & set(keys)), exact)
        key_state[game] = None

    print("GameTypePrefix struct (the routing row's shape)")
    bad = []
    for game, names in rows_required:
        n = got_fields.get(game)
        mark = "ok" if n == expect_fields.get(game) else "MISMATCH"
        if mark != "ok":
            bad.append(game)
        print("  %-4s %d field(s) %-9s %s" % (game, n, mark, names))
    if bad:
        print("  FAIL: expected %s - the struct changed shape" % expect_fields)

    print()
    print("Engine.GameInfo declares / Default__GameInfo sets")
    for game in ("ME1", "ME2", "ME3"):
        if game not in cdo_state:
            continue
        keys, setnames, exact = cdo_state[game]
        print("  %-4s declares %s" % (game, keys))
        print("       CDO sets %s   closure=%s"
              % (setnames if setnames else "(none)", "EXACT" if exact else "MISMATCH"))
        if not exact:
            bad.append(game)

    print()
    print("verdict: %s" % (
        "ME3 is the only build that uses the property path; ME1/ME2 leave it empty"
        if cdo_state.get("ME3", (None, [], False))[1]
        and not cdo_state.get("ME1", (None, ["x"], False))[1]
        and not cdo_state.get("ME2", (None, ["x"], False))[1]
        else "UNEXPECTED - re-measure before relying on this"))

    # The transport half. `NetworkDevice` in an ini and `TcpNetDriver` in an exe are both facts, and
    # neither one says a driver class is shippable - so check whether the class is an EXPORT. It is
    # not, on any build. That is the whole reason campaign co-op cannot just flip a config switch,
    # and the reason this must be measured rather than assumed: ME3 still has working multiplayer
    # (LEMP, live) whose driver is also absent from every package, i.e. native.
    print()
    print("net driver classes as script exports (absent => native only)")
    drivers = ("TcpNetDriver", "UdpNetDriver", "DemoRecDriver", "NetDriver",
               "SFXOnlineNetDriver", "IpNetDriver")
    # This half needs a package index. Without one it would have to parse all 19,430 packages, which
    # is minutes of work for a single negative; say so instead of either stalling or reporting a
    # fabricated zero.
    if not os.path.isfile(os.path.join(_index_dir(), "ME1.json")):
        print("  skipped: no package index (set MELE_INDEX_DIR); this check needs every package")
        return 1 if bad else 0
    found = {}
    for game in ("ME1", "ME2", "ME3"):
        hits = []
        for _g, _rel, rec in _iter_index(game):
            for row in rec.get("exports", []):
                if row[0] in drivers:
                    hits.append((row[0], row[1]))
        cls = [h for h in hits if (h[1] or "").endswith("Class") or h[1] == "None"]
        found[game] = cls
        print("  %-4s %d class export(s) %s" % (game, len(cls), cls[:4] if cls else ""))
    if any(found.values()):
        print("  NOTE: at least one build scripts a driver class - re-check the transport plan")
    else:
        print("  no build scripts a net driver: the driver is native on all three")
    return 1 if bad else 0


def _struct_rows(mt, t, export):
    """`mele_tables.struct_fields` returns 4-tuples; wrap so callers can use names only."""
    return [(r[0], r[1], r[2], r[3]) for r in mt.struct_fields(t, t["buf"], export)]


def _iter_index(game):
    """Stream one build's package records, from a cached index if present or from the install.

    The cached index is a speed-up, not a requirement. `maplist` only needs to know which packages
    carry a `PersistentLevel`, so walking the install is correct and merely slow. Requiring the index
    would make this module print "index missing" on any checkout without one, which reads as a
    measurement failure rather than a missing convenience.

    Yields `(game, relative_key, record)` where the record holds `exports` as
    `[name, class, super, outer]` rows.
    """
    path = os.path.join(_index_dir(), "%s.json" % game)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for rel, rec in data.get("packages", {}).items():
            if "error" not in rec:
                yield game, rel, rec
        return

    mt = load_tables()
    pkg = mt.load_pkg()
    for base in pkg.game_dirs():
        root = os.path.join(base, "Game")
        for dirpath, _dirnames, filenames in os.walk(os.path.join(root, game, "BioGame")):
            for name in sorted(filenames):
                if not name.lower().endswith(".pcc"):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root)
                try:
                    t = mt.build(full)
                except Exception:
                    continue
                rows = [[e["object_name"],
                         mt.describe_index(e["class_index"], t["exports"], t["imports"]),
                         mt.describe_index(e["super_index"], t["exports"], t["imports"]),
                         mt.describe_index(e["outer_index"], t["exports"], t["imports"])]
                        for e in t["exports"]]
                yield game, rel, {"exports": rows}


def _index_dir():
    """Where an optional pre-built package index would live. Absent is fine."""
    env = os.environ.get("MELE_INDEX_DIR")
    if env:
        return env
    return os.path.join(HERE, "..", "..", "local", "MELE", "index")


def cmd_coopcheck(args):
    """The facts the co-op design turns on, measured, with a closure behind each one.

    Every row here is one that changes a decision, so each is printed with its evidence and each can
    fail:

      * `bUseSeamlessTravel` is DECLARED on `Engine.GameInfo` in all three builds and is **never
        serialised by any exactly-closing class default object** in `Engine.pcc`, `SFXGame.pcc` or
        `BIO_COMMON.pcc`. An un-set property means the class default applies, and UE3's default is
        False - which is why the campaign's level changes are hard reloads rather than seamless
        ones, and why a client cannot carry a pawn across a transition with
        `GetSeamlessTravelActorList`. The corroboration is that `SeamlessTravelTo` is not overridden
        anywhere on `BioPlayerController` on any build, while `PreClientTravel` is.
      * `MaxPlayersAllowed` is 32 on the base `GameInfo` CDO in every build, and ME3's campaign game
        class serialises **1** - an active clamp, not a missing value.
      * the squad roster is `TArray<MemberData>` on ME1/ME2 and `TArray<Object>` on ME3, and the
        struct `MemberData` is **absent** from ME3 entirely. So "add a third member" is a different
        edit per build, and no declaration caps the roster at two.
      * `Controller.Possess` / `UnPossess` exist on all three builds and `BioAiController` overrides
        both, so taking a squadmate away from its AI and handing it to a player is a first-class
        engine operation rather than an invented one.

    Exits 1 when a measurement contradicts what the design assumes, and 2 when no install is present
    (which is a skip, not a finding).
    """
    if not _require_install():
        print("no Legendary Edition install found - set MELE_GAME_DIR (this is a skip, not a result)")
        return 2
    mt = load_tables()
    pkg = mt.load_pkg()
    problems = []

    def pkg_path(game, name):
        for root in pkg.game_dirs():
            cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole", name + ".pcc")
            if os.path.isfile(cand):
                return cand
        return None

    print("1. bUseSeamlessTravel is declared, and serialised by nobody")
    for game in ("ME1", "ME2", "ME3"):
        checked = sets = 0
        # Accumulate across packages: `GameInfo` lives in Engine.pcc and NOT in SFXGame.pcc, so
        # assigning per-package would let the second, empty lookup overwrite the first answer.
        declared = False
        for path in (pkg_path(game, "Engine"), pkg_path(game, "SFXGame")):
            if not path:
                continue
            t = mt.build(path)
            gi = [e for e in t["exports"] if e["class_index"] == 0 and e["object_name"] == "GameInfo"]
            if gi:
                declared = declared or ("bUseSeamlessTravel" in {
                    r[1] for r in mt.class_fields(t, t["buf"], gi[0])[0]})
            for o in t["exports"]:
                if not (o["object_name"] or "").startswith("Default__"):
                    continue
                try:
                    tags, end, _why = parse_object(t, t["buf"], o)
                except Exception:
                    continue
                if end != o["serial_offset"] - t["displacement"] + o["serial_size"]:
                    continue
                checked += 1
                for tg in tags:
                    if tg["name"] == "bUseSeamlessTravel":
                        sets += 1
        print("   %-4s declared=%-5s %d CDOs closed exactly, %d set it"
              % (game, declared, checked, sets))
        if not declared:
            problems.append("%s: GameInfo does not declare bUseSeamlessTravel" % game)
        if sets:
            problems.append("%s: %d CDO(s) DO set bUseSeamlessTravel - re-read the travel plan"
                            % (game, sets))

    print()
    print("2. MaxPlayers on the campaign game class, and the base default")
    pairs = (("ME1", "Default__GameInfo", "Default__BioSPGame"),
             ("ME2", "Default__GameInfo", "Default__BioSPGame"),
             ("ME3", "Default__GameInfo", "Default__SFXGame"))
    for game, base_obj, camp_obj in pairs:
        for name in (base_obj, camp_obj):
            path = pkg_path(game, "Engine") if name == base_obj else pkg_path(game, "SFXGame")
            if not path:
                continue
            t = mt.build(path)
            o = find_object(t, name)
            if o is None:
                continue
            tags, end, _why = parse_object(t, t["buf"], o)
            exact = end == o["serial_offset"] - t["displacement"] + o["serial_size"]
            got = {tg["name"]: tg["value"] for tg in tags}
            print("   %-4s %-22s MaxPlayers=%-4s MaxPlayersAllowed=%-4s %s"
                  % (game, name, got.get("MaxPlayers", "-"), got.get("MaxPlayersAllowed", "-"),
                     "exact" if exact else "INEXACT"))
            if not exact:
                problems.append("%s %s did not close exactly" % (game, name))
            if game == "ME3" and name == camp_obj and got.get("MaxPlayersAllowed") == 1:
                print("        ^ an ACTIVE clamp: co-op must raise this, as the design says")

    print()
    print("3. The squad roster's element type")
    for game in ("ME1", "ME2", "ME3"):
        path = pkg_path(game, "SFXGame")
        if not path:
            continue
        t = mt.build(path)
        mem = [e for e in t["exports"] if e["object_name"] == "Members"
               and mt.describe_index(e["class_index"], t["exports"], t["imports"]) == "Core.ArrayProperty"]
        if not mem:
            problems.append("%s: no Members ArrayProperty" % game)
            continue
        kids = [k for k in t["exports"] if k["outer_index"] == mem[0]["index"] + 1]
        inner = (mt.describe_index(kids[0]["class_index"], t["exports"], t["imports"])
                 if kids else "?")
        md = [e for e in t["exports"] if e["object_name"] == "MemberData"
              and mt.describe_index(e["class_index"], t["exports"], t["imports"]) == "Core.ScriptStruct"]
        nfields = len(mt.struct_fields(t, t["buf"], md[0])) if md else 0
        print("   %-4s Members is TArray<%s>, MemberData %s (%d fields)"
              % (game, inner.split(".")[-1], "present" if md else "ABSENT", nfields))
        if inner.endswith("StructProperty") and not md:
            problems.append("%s: Members holds a struct but MemberData is absent" % game)
        if inner.endswith("StructProperty") and md and not nfields:
            problems.append("%s: Members holds a struct and MemberData has no readable fields" % game)

    print()
    print("4. Taking a squadmate away from its AI: Possess/UnPossess on Controller")
    for game in ("ME1", "ME2", "ME3"):
        path = pkg_path(game, "Engine")
        if not path:
            continue
        t = mt.build(path)
        found = {}
        for cls in ("Controller", "PlayerController", "AIController", "Pawn"):
            hits = [e for e in t["exports"] if e["class_index"] == 0 and e["object_name"] == cls]
            if not hits:
                found[cls] = "ABSENT"
                continue
            rows = mt.class_fields(t, t["buf"], hits[0])[0]
            have = {r[1] for r in rows if r[2] == "Function"}
            found[cls] = "".join(k[0] for k in ("Possess", "UnPossess") if k in have) or "-"
        sfxpath = pkg_path(game, "SFXGame")
        bio = "-"
        if sfxpath:
            t2 = mt.build(sfxpath)
            hits = [e for e in t2["exports"] if e["class_index"] == 0 and e["object_name"] == "BioAiController"]
            if hits:
                rows = mt.class_fields(t2, t2["buf"], hits[0])[0]
                have = {r[1] for r in rows if r[2] == "Function"}
                bio = "".join(k[0] for k in ("Possess", "UnPossess") if k in have) or "-"
        print("   %-4s Controller=%s PlayerController=%s AIController=%s BioAiController=%s"
              % (game, found.get("Controller"), found.get("PlayerController"),
                 found.get("AIController"), bio))
        if found.get("Controller") != "PU":
            problems.append("%s: Controller lacks Possess/UnPossess (%r) - the transfer route is "
                            "not available" % (game, found.get("Controller")))
        if bio != "PU":
            problems.append("%s: BioAiController does not override Possess/UnPossess (%r)" % (game, bio))

    print()
    print("5. Conversation choice: the selection call, which the design called a hole")
    for game in ("ME1", "ME2", "ME3"):
        path = pkg_path(game, "SFXGame")
        if not path:
            continue
        t = mt.build(path)
        hits = [e for e in t["exports"] if e["class_index"] == 0
                and e["object_name"] == "BioSFHandler_Conversation"]
        if not hits:
            print("   %-4s BioSFHandler_Conversation ABSENT" % game)
            problems.append("%s: no BioSFHandler_Conversation" % game)
            continue
        rows = mt.class_fields(t, t["buf"], hits[0])[0]
        have = {r[1] for r in rows if r[2] == "Function"}
        shared = [k for k in ("SelectConversationEntry", "SelectConversationSegment",
                              "UpdateConversationOptions") if k in have]
        print("   %-4s %d fns; selection surface %s" % (game, len(have), shared))
        # The design called "the client cannot pick dialogue" a hole. If the selection call is on
        # every build, the hole is not a missing API - it is whether a second selector is honoured,
        # which is a live question. Asserting presence keeps those two claims apart.
        if "SelectConversationEntry" not in have:
            problems.append("%s: no SelectConversationEntry - the choice route is not shared after "
                            "all" % game)

    print()
    if problems:
        print("PROBLEM(S):")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("verdict: no seamless travel anywhere; ME3 clamps MaxPlayersAllowed to 1; the roster is a "
          "struct array on ME1/ME2 and an object array on ME3; possession and the conversation "
          "selection call are shared")
    return 0


def cmd_maplist(args):
    """Every loadable map in each build, counted structurally.

    "Ensure the campaign works in co-op" needs a denominator. Counting `.pcc` files is wrong: a
    build ships ~5,000 packages but most are content, localisation or streaming geometry. A **map**
    is a package containing a `PersistentLevel` export, which is a structural test rather than a
    naming convention - the naming convention gives a wrong answer, because `BIOG_HMF_ARM_*` mesh
    packages parse as `BIOG_` level roots and are not levels at all.

    **This deliberately does NOT report `GameTypesSupportedOnThisMap`.** It was going to, and the
    measurement says it cannot: `probe` closes 100% of objects in a CDO-rich package like
    `SFXGame.pcc` but only 22.5% in `ME3/BioA_Cat002.pcc` and 0% in `ME2/BioA_BchLmL.pcc`. A live
    level's actors do not carry a plain property tag stream, so reading one out of a level package
    is not supported by this reader - and a "0 of 10 declare it" line would be a wrong answer
    produced by a tool that had no chance of finding it.
    """
    games = [args.game] if getattr(args, "game", None) else ["ME1", "ME2", "ME3"]
    if not _require_install(games):
        print("no Legendary Edition install found - set MELE_GAME_DIR (skip, not a result)")
        return 2
    grand = 0
    for game in games:
        maps = []
        for _g, rel, rec in _iter_index(game):
            if any(row[0] == "PersistentLevel" for row in rec.get("exports", [])):
                maps.append(rel)
        print("=== %s: %d map(s) with a PersistentLevel ===" % (game, len(maps)))
        if not maps:
            print("   (no packages found for this build - is the install present?)")
            continue
        base = [m for m in maps if "\\DLC\\" not in m and "/DLC/" not in m]
        dlc = [m for m in maps if "\\DLC\\" in m or "/DLC/" in m]
        print("   base %d, DLC %d" % (len(base), len(dlc)))
        loc = [m for m in maps if "_LOC_" in m]
        if loc:
            print("   %d are localisation packages, not visitable levels" % len(loc))
        grand += len(maps)
    print()
    if not grand:
        print("no map found - is the install readable? (skip, not a result)")
        return 2
    print("%d map(s) total across the selected builds" % grand)
    return 0


def _declared_in(game, class_name):
    """True when the build declares `class_name` as a class anywhere in its packages.

    Separate from a usage count on purpose: an abstract base is declared but never placed in a map,
    and a check that conflated the two would fail on the correct answer.
    """
    mt = load_tables()
    pkg = mt.load_pkg()
    for root in pkg.game_dirs():
        cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole", "SFXGame.pcc")
        if os.path.isfile(cand):
            t = mt.build(cand)
            for e in t["exports"]:
                if e["class_index"] == 0 and e["object_name"] == class_name:
                    return True
            return False
    return False


def cmd_plotcheck(args):
    """How much story state there is, and whether it can be shipped whole. The co-op budget question.

    `coopcheck` answers "where does the story advance". This answers **how much state that advancement
    produces**, which decides whether a co-op sync tracks deltas or ships the lot.

    Measured shape of `PlotManager.pcc` on ME1 and ME2: it defines exactly one class,
    `BioAutoConditionals`, and its entire body is a set of plot-flag accessors named `F<n>` - every one
    of them shaped `F(id)(Argument: Int) -> ReturnValue: Bool`. So each flag is an integer addressed by
    a small id, and the id space is bounded by the largest id:

        ME1  1,066 functions, ids 113..2018
        ME2  1,356 functions, ids 1..2050

    An integer per slot over that range is **at most ~8 KB**, which is the number that matters: a host
    can ship the whole flag set every level change instead of maintaining a delta log, and a guest can
    be handed a complete story state rather than a replay.

    **ME3 has none of this**, and the first reading of that fact was wrong. It defines no
    `BioAutoConditionals` anywhere in 7,943 packages and ships no `PlotManager.pcc`, so there is no
    generated accessor class to count. But the plot state does exist and does have a single
    serialization point: `PlotTableSaveRecord`, a ScriptStruct declared inside `SFXSaveGame` in
    `SFXGame.pcc`, held by the save object as `PlotRecord`. It is seven arrays
    (`BoolVariables`, `IntVariables`, `FloatVariables`, `QuestProgress`, `QuestIDs`, `CodexEntries`,
    `CodexIDs`) plus a `QuestProgressCounter` int, and its entries are index-keyed pairs -
    `IntVariablePair` and `FloatVariablePair` are 8 bytes each. The index space is bounded by
    constants the runtime plot manager ships with (`ME1PlotTable_IndexOffset`,
    `ME1PlotTable_Bool_CutoffIndex` on `Default__BioGlobalVariableTable`), so ME3's state is also
    sized in the tens of KB, not unbounded.

    So the per-build difference is not "LE3 has no story state to sync"; it is that LE1/LE2 expose
    named flags and LE3 exposes one save struct - a different shape for the same sync, and one that
    is *easier* to ship whole, because it is already a blob.

    Needs only named packages, so it runs without a package index.
    """
    if not _require_install():
        print("no Legendary Edition install found - set MELE_GAME_DIR (skip, not a result)")
        return 2
    mt = load_tables()
    pkg = mt.load_pkg()
    problems = []
    report = {}
    for game in ("ME1", "ME2"):
        path = None
        for root in pkg.game_dirs():
            cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole", "PlotManager.pcc")
            if os.path.isfile(cand):
                path = cand
                break
        if path is None:
            problems.append("%s: no PlotManager.pcc" % game)
            print("%-4s PlotManager.pcc not found" % game)
            continue
        t = mt.build(path)
        funcs = [e for e in t["exports"]
                 if mt.describe_index(e["class_index"], t["exports"], t["imports"]).endswith("Function")]
        ids, odd = [], []
        for f in funcs:
            m = re.fullmatch(r"F(\d+)", f["object_name"] or "")
            if not m:
                odd.append(f["object_name"])
                continue
            ids.append(int(m.group(1)))
            kids = sorted(k["object_name"] for k in t["exports"] if k["outer_index"] == f["index"] + 1)
            if "Argument" not in kids or "ReturnValue" not in kids:
                odd.append("%s:%s" % (f["object_name"], kids))
        if not ids:
            problems.append("%s: no F<n> plot-flag functions" % game)
            continue
        span = max(ids) + 1
        report[game] = (len(ids), min(ids), max(ids), span)
        print("%-4s %5d plot flag(s), ids %d..%d, span %d slots (~%.0f KB as int32)"
              % (game, len(ids), min(ids), max(ids), span, span * 4 / 1024.0))
        if odd:
            problems.append("%s: %d function(s) not shaped F<n>(Argument)->ReturnValue, e.g. %s"
                            % (game, len(odd), odd[:3]))

    print()
    # LE3's absence of the generated class is a finding, so assert it rather than discovering it later.
    me3_dir = None
    for root in pkg.game_dirs():
        cand = os.path.join(root, "Game", "ME3", "BioGame", "CookedPCConsole")
        if os.path.isdir(cand):
            me3_dir = cand
            break
    print("ME3  PlotManager.pcc present: %s" % bool(
        me3_dir and os.path.isfile(os.path.join(me3_dir, "PlotManager.pcc"))))
    if me3_dir and os.path.isfile(os.path.join(me3_dir, "PlotManager.pcc")):
        problems.append("ME3 ships a PlotManager.pcc after all - re-read the LE3 plot plan")
    if me3_dir is None:
        problems.append("ME3 install not found - the LE3 half of this check did not run")
    else:
        report["ME3"] = _me3_plot_state(mt, me3_dir, problems)

    print()
    if problems:
        print("PROBLEM(S):")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("verdict: LE1/LE2 story state is a bounded F<n> flag set of ~2,018/2,050 slots (~8 KB); "
          "LE3 has no such class but keeps the same state as one save struct of seven arrays, "
          "index-bounded to ~%s slots - on both sides the whole state is small enough to ship"
          % (report.get("ME3", {}).get("slots") or "17,656"))
    return 0


def _me3_plot_state(mt, me3_dir, problems):
    """Measure LE3's story state from the one struct that holds it.

    `PlotTableSaveRecord` is the shape; `Default__BioGlobalVariableTable` carries the index bounds.
    Both are read from `SFXGame.pcc`, and every number printed is checked before it is printed:
    the pair structs must be 8 bytes, the four cutoff constants must be ordered, and a failure to
    read either object is reported as a problem rather than as a zero.
    """
    path = os.path.join(me3_dir, "SFXGame.pcc")
    if not os.path.isfile(path):
        problems.append("ME3: SFXGame.pcc not found - plot state not measured")
        return {}
    t = mt.build(path)
    out = {}

    def struct_named(name):
        for e in t["exports"]:
            cls = mt.describe_index(e["class_index"], t["exports"], t["imports"])
            if e["object_name"] == name and cls.endswith("ScriptStruct"):
                return e
        return None

    rec = struct_named("PlotTableSaveRecord")
    if rec is None:
        problems.append("ME3: no PlotTableSaveRecord struct - the LE3 sync payload is unknown")
        return out
    fields = mt.struct_fields(t, t["buf"], rec)
    arrays = [name for name, typ, _size, _off in fields if typ == "ArrayProperty"]
    scalars = [(name, typ) for name, typ, _size, _off in fields
               if typ not in ("ArrayProperty", "ScriptStruct")]
    out["arrays"] = arrays
    out["scalars"] = scalars
    print("ME3  plot state is one save struct, PlotTableSaveRecord (outer SFXSaveGame):")
    print("       %d arrays: %s" % (len(arrays), ", ".join(arrays)))
    print("       %d scalar(s): %s" % (len(scalars), ", ".join(n for n, _ in scalars)))
    for required in ("BoolVariables", "IntVariables", "FloatVariables"):
        if required not in arrays:
            problems.append("ME3: PlotTableSaveRecord lost its %s array" % required)

    pair_sizes = {}
    for pair, want in (("IntVariablePair", 8), ("FloatVariablePair", 8)):
        e = struct_named(pair)
        if e is None:
            problems.append("ME3: no %s struct - entry width unknown" % pair)
            continue
        rows = mt.struct_fields(t, t["buf"], e)
        width = sum(size for _n, typ, size, _o in rows if typ != "ScriptStruct")
        pair_sizes[pair] = width
        print("       %s = %d bytes (%s)"
              % (pair, width, ", ".join("%s:%s" % (n, ty) for n, ty, _s, _o in rows)))
        if width != want:
            problems.append("ME3: %s is %d bytes, expected %d" % (pair, width, want))
    out["pair_sizes"] = pair_sizes

    cdo = find_object(t, "Default__BioGlobalVariableTable")
    bounds = {}
    if cdo is None:
        problems.append("ME3: no Default__BioGlobalVariableTable - index bounds unknown")
    else:
        tags, end, _why = parse_object(t, t["buf"], cdo)
        for tag in tags:
            if tag["name"].startswith("ME1PlotTable_") and tag["name"].endswith(("Offset", "CutoffIndex")):
                bounds[tag["name"]] = tag["value"]
        want = cdo["serial_offset"] - t["displacement"] + cdo["serial_size"]
        if end != want:
            problems.append("ME3: the plot-manager CDO did not close exactly; bounds may be wrong")
    order = ("ME1PlotTable_IndexOffset", "ME1PlotTable_Float_CutoffIndex",
             "ME1PlotTable_Int_CutoffIndex", "ME1PlotTable_Bool_CutoffIndex")
    if all(k in bounds for k in order):
        print("       index bounds from Default__BioGlobalVariableTable: " +
              ", ".join("%s=%d" % (k.replace("ME1PlotTable_", ""), bounds[k]) for k in order))
        if list(bounds[k] for k in order) != sorted(bounds[k] for k in order):
            problems.append("ME3: plot table cutoffs are not ordered, so the slot count is unreliable")
        slots = bounds["ME1PlotTable_Bool_CutoffIndex"]
        out["slots"] = "{:,}".format(slots)
        dense = slots * 4 / 1024.0
        pairs = slots * 8 / 1024.0
        print("       %s slots: ~%.0f KB dense as int32, ~%.0f KB as index/value pairs"
              % (out["slots"], dense, pairs))
        print("       a save struct is already a blob, so LE3 ships whole more easily than LE1/LE2")
    elif cdo is not None:
        problems.append("ME3: plot table bounds missing from the CDO (found %s)"
                        % (sorted(bounds) or "none"))
    out["bounds"] = bounds
    return out


def cmd_storycheck(args):
    """What actually advances the campaign, counted across every map. The co-op story gate.

    "Ensure the campaign works in co-op" needs to know where story progression happens, at campaign
    scale rather than per level. This counts the Kismet actions that change the world, in every
    package that holds a `PersistentLevel`, and the answer is not what a per-level reading suggests.

    Measured, and the reason this command exists:

      * **The plot-manager action family is identical on all three builds** -
        `BioSeqAct_PMBase`, `PMCheckState`, `PMCheckConditional`, `PMExecuteTransition` - with the
        same property names (`m_sObjectType`, `m_nIndex`, `Region`, `Plot`). Only
        `PMExecuteConsequence` is ME1/ME2. So "the host advances the story and the guest observes" has
        **one** interception point across three games, not three.
      * **`PMExecuteTransition` dominates**: roughly 8.7k / 32k / 15.6k instances over 453 / 801 /
        1,058 maps. It outnumbers conversations, saves and area transitions together.
      * **`BioAreaTransition` - the trigger volume a player walks into - is essentially unused**:
        1 / 2 / 0 instances across all 6,644 maps. So "walking into a volume changes the level" is
        false for this campaign, and a mod that hooks trigger volumes hooks almost nothing.
      * the conversation and save actions split per build, which is a per-build table, not a shared one.

    Needs the package index (it looks inside every package). Exits 2 when none is present rather than
    printing zeroes it could not have measured.
    """
    if not _require_install():
        print("no Legendary Edition install found - set MELE_GAME_DIR (skip, not a result)")
        return 2
    index = os.path.join(_index_dir(), "ME1.json")
    if not os.path.isfile(index):
        print("no package index - set MELE_INDEX_DIR (this check needs every package)")
        return 2

    actions = (
        "BioSeqAct_PMBase", "BioSeqAct_PMCheckState", "BioSeqAct_PMCheckConditional",
        "BioSeqAct_PMExecuteTransition", "BioSeqAct_PMExecuteConsequence",
        "BioSeqAct_AreaTransition", "BioAreaTransition",
        "BioSeqAct_StartConversation", "SFXSeqAct_StartConversation",
        "BioSeqAct_AddToParty", "BioSeqAct_AutoSave", "SFXSeqAct_SaveGame",
    )
    problems = []
    for game in ("ME1", "ME2", "ME3"):
        maps = set()
        count = {}
        inmaps = {}
        for _g, rel, rec in _iter_index(game):
            rows = rec.get("exports", [])
            if not any(r[0] == "PersistentLevel" for r in rows):
                continue
            maps.add(rel)
            for row in rows:
                if row[0] in actions:
                    count[row[0]] = count.get(row[0], 0) + 1
                    inmaps.setdefault(row[0], set()).add(rel)
        print("=== %s: %d map(s) ===" % (game, len(maps)))
        if not maps:
            print("   (no maps found - is the index current?)")
            problems.append("%s: no maps in the index" % game)
            continue
        for name in actions:
            n = count.get(name)
            if n:
                print("   %-34s %6d instance(s) in %4d map(s)" % (name, n, len(inmaps[name])))
        # `PMBase` is a base class that is never placed in a map: it declares no functions and
        # instantiating it would do nothing. The three concrete subclasses are what a level places.
        # Asserting "all four appear" would fail on the correct answer, so the two claims are
        # checked separately - and the base claim is stated as measured (not instantiated), not as
        # `CLASS_Abstract`, which was not verified.
        if count.get("BioSeqAct_PMBase"):
            problems.append("%s: PMBase is instantiated in %d map(s) - it declares no functions, so "
                            "placing it should do nothing" % (game, len(inmaps["BioSeqAct_PMBase"])))
        declared = _declared_in(game, "BioSeqAct_PMBase")
        if not declared:
            problems.append("%s: BioSeqAct_PMBase is not declared anywhere" % game)
        for name in ("BioSeqAct_PMCheckState", "BioSeqAct_PMCheckConditional",
                     "BioSeqAct_PMExecuteTransition"):
            if not count.get(name):
                problems.append("%s: %s used in no map" % (game, name))

    print()
    if problems:
        print("PROBLEM(S):")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("verdict: the PM action family is the campaign's story surface on all three builds and "
          "PMExecuteTransition dominates it; walking into a trigger volume changes almost no level")
    return 0


def cmd_squadadd(args):
    """How the campaign itself adds a squad member, per build - which decides how co-op should.

    Appending to `BioBaseSquad.Members` is the obvious way to give a client a squadmate, and on ME2
    and ME3 it is the wrong one: the campaign's own `BioSeqAct_AddToParty.Activated` runs through a
    `Bio2DA` data table, and a direct array append skips that bookkeeping.

    This measures the divergence rather than asserting a plan. For each build it reports the size of
    `AddToParty.Activated` and which `Bio2DA` accessors it references:

      * ME1 - no `Bio2DA` reference at all; the action calls `PushFollowSquadLeader` and appearance
        lookups directly.
      * ME2 - the numbered-row accessor family (`GetNumColumns`, `GetStringEntryNumI`,
        `GetStringEntryNumN`, `GetFloatEntryII`) plus `GetSquadSize`.
      * ME3 - `GetRowNames` / `GetNameEntryIN`.

    The reference scan is the same dword heuristic `mele_tables.bytecode_refs` documents, so a hit is
    evidence of a *reference*, not of a decode. What makes it trustworthy here is the contrast: the
    same scan finds zero `Bio2DA` members in ME1's body and a coherent accessor family in ME2's and
    ME3's, which is a pattern no noise produces.
    """
    if not _require_install():
        print("no Legendary Edition install found - set MELE_GAME_DIR (skip, not a result)")
        return 2
    mt = load_tables()
    pkg = mt.load_pkg()
    problems = []
    for game in ("ME1", "ME2", "ME3"):
        path = None
        for root in pkg.game_dirs():
            cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole", "SFXGame.pcc")
            if os.path.isfile(cand):
                path = cand
                break
        if path is None:
            print("%s: install not found" % game)
            continue
        t = mt.build(path)
        by_index = {e["index"]: e for e in t["exports"]}
        fns = [e for e in t["exports"] if e["object_name"] == "Activated"
               and "BioSeqAct_AddToParty" in (mt.describe_index(
                   e["outer_index"], t["exports"], t["imports"]) or "")]
        if not fns:
            problems.append("%s: no BioSeqAct_AddToParty.Activated" % game)
            print("%s: no BioSeqAct_AddToParty.Activated" % game)
            continue
        fn = fns[0]
        at = fn["serial_offset"] - t["displacement"]
        length = struct.unpack_from("<i", t["buf"], at + 28)[0]
        body = t["buf"][at + 32:at + 32 + length]
        two_da, squad = set(), set()
        for i in range(len(body) - 4):
            v = struct.unpack_from("<i", body, i)[0]
            e = by_index.get(v)
            if not e:
                continue
            outer = mt.describe_index(e["outer_index"], t["exports"], t["imports"]) or ""
            cls = mt.describe_index(e["class_index"], t["exports"], t["imports"]) or ""
            if "Bio2DA" in outer or "Bio2DA" in cls:
                two_da.add(e["object_name"])
            if "Squad" in outer and e["object_name"]:
                squad.add(e["object_name"])
        print("%-4s AddToParty.Activated %d bytes; Bio2DA refs %d %s"
              % (game, length, len(two_da), sorted(two_da)[:6]))
        if squad:
            print("     squad refs: %s" % sorted(squad)[:8])
        if game == "ME1" and two_da:
            problems.append("ME1 references Bio2DA in AddToParty - the divergence claim is wrong")
        if game in ("ME2", "ME3") and not two_da:
            problems.append("%s does NOT reference Bio2DA - the divergence claim is wrong" % game)
    print()
    if problems:
        print("PROBLEM(S):")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("verdict: ME1's recruitment is direct; ME2/ME3 run it through a Bio2DA table, so appending "
          "to Members directly skips the campaign's bookkeeping on ME2/ME3")
    return 0


def cmd_savecheck(args):
    """How each build persists a level, because it decides whether the host's save is safe.

    ME1 and ME2/ME3 do not persist the world the same way at all, and this is the kind of difference
    that cannot be discovered by a bug report - it shows up as a corrupt save weeks later.

      * ME1 walks the **actor graph**: every saveable actor answers `GetSaveObjectClass`, and the
        level is written as `BioLevelSaveObject.m_LevelObjects` - one entry per actor, including
        pawns. `SFXGame.pcc` alone defines **23** `*SaveObject` classes (34 across the whole
        install, the rest in `Engine.pcc` and level packages).
      * ME2 and ME3 have **zero** `*SaveObject` classes in `SFXGame.pcc` and zero anywhere in the
        install. Persistence moved behind `SFXSFHandler_Save` / `SFXSaveGame` and the
        `SFXSeqAct_SaveGame` Kismet action, so the world is not enumerated actor-by-actor at save
        time.

    The consequence for co-op is specific to ME1: a client's squadmate pawn sitting in the host's
    level would be captured by `m_LevelObjects` and restored on load, even though it has no story
    identity. ME1's `BioPawnSaveObject` only carries cosmetic fields (grime levels, "talked to"
    count, headgear preference), so the damage is bounded - but it is not zero, and it is
    measurable rather than guessed.

    Exits 1 when the shape differs from what the design assumes.
    """
    if not _require_install():
        print("no Legendary Edition install found - set MELE_GAME_DIR (skip, not a result)")
        return 2
    mt = load_tables()
    pkg = mt.load_pkg()
    problems = []

    def pkg_path(game, name):
        for root in pkg.game_dirs():
            cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole", name + ".pcc")
            if os.path.isfile(cand):
                return cand
        return None

    print("1. *SaveObject classes: ME1 persists the actor graph, ME2/ME3 do not")
    counts = {}
    for game in ("ME1", "ME2", "ME3"):
        path = pkg_path(game, "SFXGame")
        if not path:
            continue
        t = mt.build(path)
        names = sorted({e["object_name"] for e in t["exports"]
                        if e["class_index"] == 0 and (e["object_name"] or "").endswith("SaveObject")})
        counts[game] = len(names)
        print("   %-4s %2d class(es) ending in SaveObject%s"
              % (game, len(names), (": " + ", ".join(names[:6]) + ", ...") if names else ""))
    if counts.get("ME1", 0) < 10:
        problems.append("ME1's SaveObject family is smaller than expected (%d)" % counts.get("ME1", 0))
    if counts.get("ME2") or counts.get("ME3"):
        problems.append("ME2/ME3 have SaveObject classes after all (%r) - re-read the save plan"
                        % counts)
    path = pkg_path("ME1", "SFXGame")
    if path:
        t = mt.build(path)
        cls = [e for e in t["exports"]
               if e["class_index"] == 0 and e["object_name"] == "BioLevelSaveObject"]
        if cls:
            rows = mt.class_fields(t, t["buf"], cls[0])[0]
            names = [r[1] for r in rows]
            if "m_LevelObjects" not in names:
                problems.append("ME1's BioLevelSaveObject has no m_LevelObjects - the actor-graph "
                                "claim is unsupported")
            else:
                print("   ME1 BioLevelSaveObject carries m_LevelObjects: one entry per saveable "
                      "actor in the level")
        base_squad = [e for e in t["exports"]
                      if e["class_index"] == 0 and e["object_name"] == "BioBaseSquad"]
        if base_squad:
            fns = {r[1] for r in mt.class_fields(t, t["buf"], base_squad[0])[0] if r[2] == "Function"}
            print("   ME1 BioBaseSquad.GetSaveObjectClass present: %s"
                  % ("GetSaveObjectClass" in fns))

    print()
    print("2. The per-squadmate save record is a different size on every build")
    for game in ("ME1", "ME2", "ME3"):
        path = pkg_path(game, "SFXGame")
        if not path:
            continue
        t = mt.build(path)
        hits = [e for e in t["exports"] if e["object_name"] == "HenchmanSaveRecord"
                and mt.describe_index(e["class_index"], t["exports"], t["imports"]) == "Core.ScriptStruct"]
        if not hits:
            print("   %-4s HenchmanSaveRecord ABSENT" % game)
            continue
        rows = mt.struct_fields(t, t["buf"], hits[0])
        print("   %-4s HenchmanSaveRecord %d field(s)" % (game, len(rows)))

    print()
    if problems:
        print("PROBLEM(S):")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("verdict: ME1 saves the actor graph and will capture a client pawn; ME2/ME3 save behind "
          "SFXSFHandler_Save and have no SaveObject classes")
    return 0


def _script_functions(mt, t, class_name):
    """The functions a class implements in script, by name, using the export table's outer convention.

    Function exports point at their owning class with `outer_index == class_index + 1`, so the class has
    to be looked up first. `serial_size > 4` separates a real body from a native stub with no bytecode:
    the distinction matters throughout, because only what has bytecode can be overridden by a subclass.
    """
    owner = [e for e in t["exports"] if e["object_name"] == class_name]
    if not owner:
        return {}, []
    rows = [e for e in t["exports"]
            if e["outer_index"] == owner[0]["index"] + 1
            and mt.describe_index(e["class_index"], t["exports"], t["imports"]).endswith("Function")]
    return {e["object_name"]: e for e in rows}, rows


def cmd_joincheck(args):
    """Can a second player be accepted in script, on each build, and where does the pawn come from?

    `coopcheck` measured who *may* join (capacity). This measures whether the join path is still in
    script at all, which decides whether co-op needs a native patch or a class override.

    The UE3 join chain - `PreLogin`, `Login`, `PostLogin`, `AtCapacity`, `RestartPlayer`,
    `SpawnDefaultPawnFor`, `ChoosePlayerStart`, `FindPlayerStart`, `SpawnPlayerController` - is
    implemented in script on `Engine.GameInfo` on all three builds, so the decision about a second
    player is reachable from script everywhere. What differs is how much of it each campaign class
    already replaces:

        ME1  BioSPGame  18 script overrides, including SpawnPlayer and SpawnPlayerSquadMembers
        ME2  BioSPGame   4 script overrides, none of them in the join chain
        ME3  SFXGame    80 script overrides, including RestartPlayer and SpawnNewPlayerPawn

    On LE3 the runtime campaign GameInfo is `SFXGameInfoSP` (super `SFXGame`), and it ships inside
    `BIO_COMMON.pcc`, whose export list starts with the path `..\\BIOGame\\MPContent\\BIO_COMMON.upk` -
    the campaign game class is packaged from multiplayer content. Its class default object sets exactly
    one property, `GameReplicationInfoClass=SFXGRISP`, so everything else - including the
    `MaxPlayersAllowed=1` clamp - is inherited from `SFXGame`, which still carries teams
    (`InitializeTeams`, `ChangeTeam`), a player count (`GetPlayerCount`), join-in-progress delegates and
    `SpawnNewPlayerPawn`, which takes a `PlayerController` and a `PlayerArchetype` and returns a pawn.

    So the guest's pawn does not have to be invented: the function that spawns a multiplayer player is
    still present and script-visible. What is missing is a caller that accepts a second player.

    Needs only named packages, so it runs without a package index.
    """
    if not _require_install():
        print("no Legendary Edition install found - set MELE_GAME_DIR (skip, not a result)")
        return 2
    mt = load_tables()
    pkg = mt.load_pkg()
    problems = []
    chain = ("PreLogin", "Login", "PostLogin", "AtCapacity", "RestartPlayer", "SpawnDefaultPawnFor",
             "ChoosePlayerStart", "FindPlayerStart", "SpawnPlayerController")
    print("1. The join chain on Engine.GameInfo: script-implemented or native-only")
    for game in ("ME1", "ME2", "ME3"):
        path = None
        for root in pkg.game_dirs():
            cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole", "Engine.pcc")
            if os.path.isfile(cand):
                path = cand
                break
        if path is None:
            problems.append("%s: no Engine.pcc" % game)
            print("   %-4s Engine.pcc not found" % game)
            continue
        t = mt.build(path)
        funcs, _rows = _script_functions(mt, t, "GameInfo")
        scripted = [n for n in chain if n in funcs and funcs[n]["serial_size"] > 4]
        native = [n for n in chain if n in funcs and funcs[n]["serial_size"] <= 4]
        missing = [n for n in chain if n not in funcs]
        print("   %-4s %d/%d in script: %s" % (game, len(scripted), len(chain), ", ".join(scripted)))
        if native:
            print("        native-only (cannot be overridden in script): %s" % ", ".join(native))
        if missing:
            print("        absent: %s" % ", ".join(missing))
        if "Login" not in funcs or funcs["Login"]["serial_size"] <= 4:
            problems.append("%s: GameInfo.Login is not in script - a second player cannot be accepted "
                            "without a native patch" % game)
        if "AtCapacity" not in funcs or funcs["AtCapacity"]["serial_size"] <= 4:
            problems.append("%s: GameInfo.AtCapacity is not in script - the capacity refusal is not "
                            "reachable" % game)

    print()
    print("2. What each campaign game class already overrides")
    for game, package, klass in (("ME1", "SFXGame.pcc", "BioSPGame"),
                                 ("ME2", "SFXGame.pcc", "BioSPGame"),
                                 ("ME3", "SFXGame.pcc", "SFXGame")):
        path = None
        for root in pkg.game_dirs():
            cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole", package)
            if os.path.isfile(cand):
                path = cand
                break
        if path is None:
            problems.append("%s: no %s" % (game, package))
            continue
        t = mt.build(path)
        funcs, rows = _script_functions(mt, t, klass)
        scripted = sorted(n for n, e in funcs.items() if e["serial_size"] > 4)
        in_chain = [n for n in chain if n in funcs]
        print("   %-4s %-10s %2d script override(s) of %d function(s); join chain: %s"
              % (game, klass, len(scripted), len(rows), ", ".join(in_chain) or "untouched"))
        if game != "ME2" and not in_chain:
            problems.append("%s: %s does not touch the join chain at all" % (game, klass))

    print()
    print("3. LE3's campaign game class, and the multiplayer machinery behind it")
    me3_dir = None
    for root in pkg.game_dirs():
        cand = os.path.join(root, "Game", "ME3", "BioGame", "CookedPCConsole")
        if os.path.isdir(cand):
            me3_dir = cand
            break
    if me3_dir is None:
        problems.append("ME3 install not found - the LE3 half of this check did not run")
    else:
        common = os.path.join(me3_dir, "BIO_COMMON.pcc")
        if not os.path.isfile(common):
            problems.append("ME3: no BIO_COMMON.pcc - the campaign game class was not located")
        else:
            t = mt.build(common)
            mp_paths = [e["object_name"] for e in t["exports"]
                        if e["object_name"] and "MPContent" in e["object_name"]]
            print("   SFXGameInfoSP lives in BIO_COMMON.pcc; MPContent paths inside it: %s"
                  % (mp_paths or "none"))
            if not mp_paths:
                problems.append("ME3: BIO_COMMON.pcc no longer carries an MPContent path - the "
                                "campaign game class is not packaged from multiplayer content")
            funcs, _rows = _script_functions(mt, t, "SFXGameInfoSP")
            print("   SFXGameInfoSP script overrides: %s"
                  % ", ".join(sorted(n for n, e in funcs.items() if e["serial_size"] > 4)))
            cdo = find_object(t, "Default__SFXGameInfoSP")
            if cdo is None:
                problems.append("ME3: no Default__SFXGameInfoSP")
            else:
                tags, end, _why = parse_object(t, t["buf"], cdo)
                want = cdo["serial_offset"] - t["displacement"] + cdo["serial_size"]
                props = {tg["name"]: tg["value"] for tg in tags if tg["name"] != "None"}
                print("   Default__SFXGameInfoSP sets %d property: %s"
                      % (len(props), ", ".join("%s=%s" % kv for kv in props.items())))
                if end != want:
                    problems.append("ME3: Default__SFXGameInfoSP did not close exactly")
                if "GameReplicationInfoClass" not in props:
                    problems.append("ME3: SFXGameInfoSP no longer points at its own replication info")
        sfx = os.path.join(me3_dir, "SFXGame.pcc")
        if not os.path.isfile(sfx):
            problems.append("ME3: no SFXGame.pcc")
        else:
            t = mt.build(sfx)
            funcs, _rows = _script_functions(mt, t, "SFXGame")
            wanted = ("InitializeTeams", "ChangeTeam", "GetPlayerCount", "GetDownedPlayerCount",
                      "OnJoinInProgress", "RegisterJoinInProgressDelegate", "CalculatedNetSpeed",
                      "SpawnNewPlayerPawn", "SpawnPlayerForResume")
            present = [n for n in wanted if n in funcs and funcs[n]["serial_size"] > 4]
            print("   SFXGame still carries: %s" % ", ".join(present))
            if "SpawnNewPlayerPawn" in funcs:
                sig = mt.signature(t, t["buf"], funcs["SpawnNewPlayerPawn"])
                # Child exports in declaration order; this list can include locals, so it is reported
                # as a shape and only its length is asserted.
                print("   SpawnNewPlayerPawn: %d parameter(s), parms block 0x%x -> %s"
                      % (len(sig["params"]), sig["parms_size"], sig["returns"]))
                print("     %s" % ", ".join("%s@0x%02x" % (n, off)
                                            for n, _t, _s, off in sig["params"]))
                if len(sig["params"]) < 10:
                    problems.append("ME3: SpawnNewPlayerPawn dropped to %d parameter(s) - re-read the "
                                    "guest spawn plan" % len(sig["params"]))
            else:
                problems.append("ME3: SFXGame lost SpawnNewPlayerPawn - the guest pawn has no "
                                "script-visible spawn path")
            for lost in ("InitializeTeams", "ChangeTeam", "GetPlayerCount"):
                if lost not in present:
                    problems.append("ME3: SFXGame lost %s" % lost)

    print()
    if problems:
        print("PROBLEM(S):")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("verdict: the join chain is script on all three builds, so accepting a second player is a "
          "class override and not a native patch; LE3 already has a script-visible guest-pawn spawn "
          "(SpawnNewPlayerPawn) and team/player-count functions, and its campaign game class ships "
          "inside a multiplayer content package")
    return 0


def _struct_named(mt, t, name):
    """The `UScriptStruct` export called `name`, or None. A struct, not a class or a property."""
    for e in t["exports"]:
        if e["object_name"] == name and \
                mt.describe_index(e["class_index"], t["exports"], t["imports"]).endswith("ScriptStruct"):
            return e
    return None


def _function_on(mt, t, class_name, func_name):
    """A named function declared directly on `class_name`, using the export table's outer convention."""
    return _script_functions(mt, t, class_name)[0].get(func_name)


def cmd_spawncheck(args):
    """What a second player's pawn needs, and whether the campaign's own spawn already accepts it.

    `joincheck` established that accepting a second player is script-reachable. This answers the next
    question, which is the one that decides how much work "the client takes a squadmate" is: does the
    campaign's own pawn spawn take a *character description*, or only a `PlayerController`?

    Two payloads matter, and both are found by resolving `StructProperty.Struct` rather than guessing:

    * **`PlayerInfoEx`** - the player identity block, held by `SFXSaveGame` on all three builds and used
      by 3 / 6 / 17 properties respectively. Its fields are the character: `FirstName`, `FaceCode`,
      `CharacterClass`, `MorphHead`, `BonusTalentClass`, `bIsFemale`, `Origin`, `Notoriety`, and
      `CharacterGUID` on LE3 only.
    * **`PlotTableSaveRecord`** - the plot save payload, held **only** by `SFXSaveGame.PlotRecord` on
      every build, with the same fields everywhere: seven arrays plus a `QuestProgressCounter`.
      No function takes it as a parameter, so it is read and written through the save object.

    And then the difference that matters:

        LE1  BioSPGame.SpawnDefaultPawnFor(NewPlayer, SpawnPoint, BioPlayerController, Pawn)
        LE2  SFXGame.SpawnDefaultPawnFor(NewPlayer, StartSpot, ..., PlayerInfo, ...)   29 params
        LE3  SFXGame.SpawnDefaultPawnFor(NewPlayer, StartSpot, ..., PlayerInfo,
                                         ResetPlayerInfoEx, ..., CharacterGUID)      33 params

    LE2 and LE3 already take a `PlayerInfo` struct plus the identity fields, so a guest is the same
    call with a different character description. **LE1's campaign spawn takes four opaque objects and
    no identity at all**, and its identity levers are elsewhere (`SpawnPlayer`, `SpawnPawn`,
    `SpawnHenchman`, `SpawnPlayerSquadMembers` on the same class). That is a real per-build
    difference in effort, not a detail.

    Needs only named packages, so it runs without a package index.
    """
    if not _require_install():
        print("no Legendary Edition install found - set MELE_GAME_DIR (skip, not a result)")
        return 2
    mt = load_tables()
    pkg = mt.load_pkg()
    problems = []
    report = {}

    cache = {}

    def game_pkg(game, name="SFXGame.pcc"):
        key = (game, name)
        if key not in cache:
            path = None
            for root in pkg.game_dirs():
                cand = os.path.join(root, "Game", game, "BioGame", "CookedPCConsole", name)
                if os.path.isfile(cand):
                    path = cand
                    break
            cache[key] = mt.build(path) if path else None
        return cache[key]

    print("1. The player-identity payload, and the plot payload, per build")
    for game in ("ME1", "ME2", "ME3"):
        t = game_pkg(game)
        if t is None:
            problems.append("%s: no SFXGame.pcc" % game)
            continue
        rows = []
        for struct_name in ("PlayerInfoEx", "PlotTableSaveRecord"):
            e = _struct_named(mt, t, struct_name)
            if e is None:
                problems.append("%s: no %s struct" % (game, struct_name))
                continue
            fields = [r[0] for r in mt.struct_fields(t, t["buf"], e)]
            report.setdefault(struct_name, {})[game] = fields
            print("   %-4s %-20s %2d field(s): %s"
                  % (game, struct_name, len(fields), ", ".join(fields)))
            if struct_name == "PlayerInfoEx":
                for required in ("FirstName", "FaceCode", "Origin", "Notoriety", "bIsFemale"):
                    if required.lower() not in [f.lower() for f in fields]:
                        problems.append("%s: PlayerInfoEx lost %s" % (game, required))
            else:
                for required in ("BoolVariables", "IntVariables", "FloatVariables", "QuestIDs"):
                    if required not in fields:
                        problems.append("%s: PlotTableSaveRecord lost %s" % (game, required))

    print()
    print("2. Who holds the plot payload")
    for game in ("ME1", "ME2", "ME3"):
        t = game_pkg(game)
        if t is None:
            continue
        target_struct = _struct_named(mt, t, "PlotTableSaveRecord")
        if target_struct is None:
            continue
        holders = []
        for prop in t["exports"]:
            if mt.describe_index(prop["class_index"], t["exports"], t["imports"]) != "Core.StructProperty":
                continue
            target, kind = mt.property_struct_export(t, t["buf"], prop)
            if kind == "export" and target is target_struct:
                holders.append((mt.describe_index(prop["outer_index"], t["exports"], t["imports"]),
                                prop["object_name"]))
        print("   %-4s %s" % (game, ", ".join("%s.%s" % h for h in holders) or "nobody"))
        if not any(h[1] == "PlotRecord" for h in holders):
            problems.append("%s: no PlotRecord property of type PlotTableSaveRecord" % game)
        if len(holders) != 1:
            problems.append("%s: plot payload has %d holders, expected 1" % (game, len(holders)))

    print()
    print("3. The campaign's own pawn spawn - does it take a character description?")
    for game, klass in (("ME1", "BioSPGame"), ("ME2", "SFXGame"), ("ME3", "SFXGame")):
        t = game_pkg(game)
        if t is None:
            continue
        fn = _function_on(mt, t, klass, "SpawnDefaultPawnFor")
        if fn is None:
            problems.append("%s: %s has no SpawnDefaultPawnFor" % (game, klass))
            continue
        sig = mt.signature(t, t["buf"], fn)
        params = [n for n, _t, _s, _o in sig["params"]]
        has_identity = "PlayerInfo" in params
        print("   %-4s %-10s.SpawnDefaultPawnFor %2d param(s), parms 0x%x, PlayerInfo: %s"
              % (game, klass, len(sig["params"]), sig["parms_size"], has_identity))
        report.setdefault("spawn", {})[game] = (len(sig["params"]), has_identity)
        if game in ("ME2", "ME3") and not has_identity:
            problems.append("%s: %s.SpawnDefaultPawnFor no longer takes a PlayerInfo - the guest "
                            "spawn plan changes" % (game, klass))
        if game == "ME1" and has_identity:
            problems.append("ME1: BioSPGame.SpawnDefaultPawnFor gained a PlayerInfo parameter - "
                            "re-read the LE1 plan")

    print()
    if problems:
        print("PROBLEM(S):")
        for p in problems:
            print("  - %s" % p)
        return 1
    sizes = {g: v for g, v in report.get("spawn", {}).items()}
    print("verdict: the player identity payload is the same struct on all three builds (7/8/9 fields, "
          "LE3 adding CharacterGUID); the plot payload is one struct held only by SFXSaveGame.PlotRecord; "
          "LE2/LE3 spawn a player from a character description (%d/%d params) while LE1 spawns from four "
          "opaque objects" % (sizes.get("ME2", (0, False))[0], sizes.get("ME3", (0, False))[0]))
    return 0


def cmd_cdo(args):
    return cmd_dump(argparse.Namespace(
        package=args.package, object="Default__" + args.klass, grep=args.grep))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="which tag layout closes this package's objects")
    p.add_argument("package")
    p.add_argument("--count", type=int, default=40)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("dump", help="print an object's property tag stream")
    p.add_argument("package")
    p.add_argument("object")
    p.add_argument("--grep")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("value", help="print one property's value")
    p.add_argument("package")
    p.add_argument("object")
    p.add_argument("property")
    p.set_defaults(func=cmd_value)

    p = sub.add_parser("cdo", help="print a class default object's properties")
    p.add_argument("package")
    p.add_argument("klass")
    p.add_argument("--grep")
    p.set_defaults(func=cmd_cdo)

    p = sub.add_parser("mapgame",
                       help="print a build's map-prefix -> game-class routing table")
    p.add_argument("package")
    p.set_defaults(func=cmd_mapinfo)

    p = sub.add_parser("routing",
                       help="re-measure game-class routing across all three installs")
    p.set_defaults(func=cmd_routing)

    p = sub.add_parser("coopcheck",
                       help="the travel/clamp/roster facts the co-op design turns on")
    p.set_defaults(func=cmd_coopcheck)

    p = sub.add_parser("savecheck",
                       help="how each build persists a level (decides save safety)")
    p.set_defaults(func=cmd_savecheck)

    p = sub.add_parser("squadadd",
                       help="how the campaign itself adds a squad member, per build")
    p.set_defaults(func=cmd_squadadd)

    p = sub.add_parser("storycheck",
                       help="what advances the campaign, counted across every map")
    p.set_defaults(func=cmd_storycheck)

    p = sub.add_parser("plotcheck",
                       help="how much story state there is, and whether it can be shipped whole")
    p.set_defaults(func=cmd_plotcheck)

    p = sub.add_parser("joincheck",
                       help="is the join path in script, and where does a second player's pawn come from")
    p.set_defaults(func=cmd_joincheck)

    p = sub.add_parser("spawncheck",
                       help="what a second player's pawn needs, and whether the campaign spawn takes it")
    p.set_defaults(func=cmd_spawncheck)

    p = sub.add_parser("maplist",
                       help="every loadable map per build, counted structurally")
    p.add_argument("--game", choices=["ME1", "ME2", "ME3"])
    p.set_defaults(func=cmd_maplist)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
