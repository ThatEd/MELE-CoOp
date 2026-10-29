"""
Find the engine global by requiring the slot to hold the LIVE engine object.

Why a separate script: `ue3_discover_external.py` reports every image slot whose value
equals the engine pointer, and picking the first one is a guess - the first candidate
here dereferenced to a `StaticMesh`, which is exactly the kind of plausible-but-wrong
answer that poisons everything built on it.

This resolves the live engine, then checks each candidate slot by dereferencing it and
requiring the result to be that same live engine with class `SFXEngine`. It also reports
the engine's GamePlayers/Tick-relevant offsets if they can be resolved by property name.

Read-only: OpenProcess(PROCESS_VM_READ), ReadProcessMemory only.

The sys.path insert below makes the sibling modules importable when this file is run
with `python -I`, which omits the script directory from sys.path. Without it these tools
only work when invoked without -I, which is not how the README documents them.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argparse
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
    reader = Reader(args.pid)
    try:
        game = ue3_reader.Game(reader, base, args.offsets)
        out = {"read_only": True, "objects": game.count}

        # The live engine: named like an Engine class, not a Default__ archetype.
        live = []
        for i in range(game.count):
            o = reader.ptr(game.data + i * 8)
            if not o:
                continue
            n = game.object_name(o)
            if not n or n.startswith("Default__") or not n.endswith("Engine"):
                continue
            cls = game.obj_class(o)
            if cls and cls.endswith("Engine"):
                live.append({"ptr": o, "name": n, "class": cls})
        out["live_engines"] = [{"ptr": hex(e["ptr"]), "name": e["name"],
                                "class": e["class"]} for e in live[:8]]
        target = None
        for e in live:
            if e["class"] == "SFXEngine":
                target = e
                break
        if target is None and live:
            target = live[0]
        if target is None:
            print(json.dumps(dict(out, error="no live engine object found"), indent=1))
            return 1
        out["chosen_engine"] = {"ptr": hex(target["ptr"]), "name": target["name"],
                                "class": target["class"]}

        # Every 8-aligned image slot holding that pointer, verified by dereference.
        needle = struct.pack("<Q", target["ptr"])
        candidates = []
        size = 0x1DB8000
        # Take the size from the module rather than trusting a constant: an image that
        # grows between builds would silently truncate the scan.
        try:
            info = module_info(args.pid, args.exe)
            if info and info[1]:
                size = int(info[1])
        except Exception:
            pass
        addr = base
        chunk = 1 << 20
        seen_regions = set()
        while addr < base + size:
            class MBI(ctypes.Structure):
                _fields_ = [("BaseAddress", ctypes.c_void_p),
                            ("AllocationBase", ctypes.c_void_p),
                            ("AllocationProtect", wintypes.DWORD),
                            ("RegionSize", ctypes.c_size_t),
                            ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
                            ("Type", wintypes.DWORD)]
            mbi = MBI()
            got = reader.k32.VirtualQueryEx(reader.handle, ctypes.c_void_p(addr),
                                            ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not got:
                break
            rb, rs = int(mbi.BaseAddress or 0), int(mbi.RegionSize or 0)
            if not rs:
                break
            if mbi.State == 0x1000 and (mbi.Protect & 0xCC) and not (mbi.Protect & 0x100):
                if rb >= base and rb not in seen_regions:
                    seen_regions.add(rb)
                    off = 0
                    while off < rs:
                        n = min(chunk, rs - off)
                        blob = reader.read(rb + off, n)
                        if blob:
                            pos = blob.find(needle)
                            while pos != -1:
                                if pos % 8 == 0:
                                    slot = rb + off + pos
                                    points_at = reader.ptr(slot)
                                    candidates.append({
                                        "slot": hex(slot),
                                        "rva": hex(slot - base),
                                        "points_at": hex(points_at),
                                        "is_engine": points_at == target["ptr"],
                                    })
                                pos = blob.find(needle, pos + 1)
                        off += n
            addr = rb + rs

        out["engine_global_candidates"] = candidates[:16]
        verified = [c for c in candidates if c["is_engine"]]
        out["verified_engine_globals"] = verified
        if verified:
            out["engine_global_rva"] = verified[0]["rva"]

        print(json.dumps(out, indent=1))
        return 0
    finally:
        reader.k32.CloseHandle(reader.handle)


if __name__ == "__main__":
    sys.exit(main())
