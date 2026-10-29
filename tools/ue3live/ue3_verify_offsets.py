"""
Verify discovered offsets against the running game, read-only.

A discovery result is a hypothesis until it is checked. This checks the things that
would be wrong if the numbers were wrong:

  * the name pool decodes real class names for the objects in the array, and the
    meta-class really is called what we assumed;
  * class pointers of array members point back into the array (the property that
    only the real array has);
  * a known-name object (Engine) is findable and its own name/class agree;
  * the engine global, if found, dereferences to that engine object.

Read-only: OpenProcess(PROCESS_VM_READ), ReadProcessMemory only.

The sys.path insert below makes the sibling modules importable when this file is run
with `python -I`, which omits the script directory from sys.path. Without it these tools
only work when invoked without -I, which is not how the README documents them.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argparse
import collections
import ctypes
import json
import struct
import sys

from ctypes import wintypes

import os
import sys
# Running with -I omits the script directory from sys.path, and the README documents
# these tools as runnable with -I. Make the sibling modules importable regardless.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ue3_reader
from ue3_proc import Reader, module_base


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--offsets", required=True)
    args = ap.parse_args()

    base = module_base(args.pid, args.exe)
    if not base:
        print(json.dumps({"error": "module not found"}))
        return 1
    reader = Reader(args.pid)
    try:
        game = ue3_reader.Game(reader, base, args.offsets)
        out = {"objects": game.count, "read_only": True, "base": hex(base)}

        # 1. Class membership: every class pointer must itself be in the array.
        members = set()
        ptrs = []
        for i in range(game.count):
            o = reader.ptr(game.data + i * 8)
            ptrs.append(o)
            if o:
                members.add(o)
        non_members = 0
        named = 0
        undecodable = 0
        histogram = collections.Counter()
        for o in ptrs:
            if not o:
                continue
            cls = reader.ptr(o + 0x50)
            if cls and cls not in members:
                non_members += 1
            name = game.object_name(o)
            if name:
                named += 1
            else:
                undecodable += 1
            cn = game.obj_class(o)
            if cn:
                histogram[cn] += 1
        out["objects_nonzero"] = sum(1 for p in ptrs if p)
        out["class_pointers_outside_array"] = non_members
        out["objects_with_decodable_name"] = named
        out["objects_with_undecodable_name"] = undecodable
        out["top_classes"] = histogram.most_common(20)

        # 2. The meta-class must be self-classed and named.
        meta = None
        for i in range(min(game.count, 3000)):
            o = reader.ptr(game.data + i * 8)
            if o and reader.ptr(o + 0x50) == o:
                meta = {"ptr": hex(o), "name": game.object_name(o),
                        "index": reader.u32(o + 0x48)}
                break
        out["metaclass"] = meta

        # 3. Engine, by name, using the discovered pool.
        engines = []
        for i in range(game.count):
            o = reader.ptr(game.data + i * 8)
            if not o:
                continue
            n = game.object_name(o)
            if n and n.endswith("Engine"):
                cn = game.obj_class(o)
                if cn and cn.endswith("Engine"):
                    engines.append({"ptr": hex(o), "name": n, "class": cn})
                    if len(engines) >= 6:
                        break
        out["engine_candidates"] = engines

        # 4. If an engine global was recorded, does it point at one of them?
        eg = game.engine_global
        if eg:
            target = reader.ptr(eg)
            out["engine_global"] = {"slot": hex(eg), "target": hex(target),
                                    "name": game.object_name(target),
                                    "class": game.obj_class(target)}
            out["engine_global_matches_a_candidate"] = any(
                int(e["ptr"], 16) == target for e in engines)

        print(json.dumps(out, indent=1))
        return 0
    finally:
        reader.k32.CloseHandle(reader.handle)


if __name__ == "__main__":
    sys.exit(main())
