"""
Discover UE3 offsets in a RUNNING game from outside, with no injection at all.

Why this exists: every previous discovery attempt put a DLL inside the game. Even a
read-only probe needs a LoadLibrary, and a bad one costs the owner a long cutscene.
The facts discovery needs - GObjects, the name pool, the engine global - are all
plain data reachable through ReadProcessMemory, so the process never has to trust a
single line of new code.

How GObjects is found without knowing its address or any name:

  1. A TArray<UObject*> is {data, count, max}. Scan the image's writable sections for
     those three fields where count is plausible for a UE3 game and max >= count.
  2. Build the candidate's pointer set, then require that the objects' Class pointers
     are THEMSELVES members of that set. That is a property only the real object array
     has: a class is an object, and every object points at its class.
  3. Require an object that is its own class (a meta-class such as `Class`), which is
     what makes the name check below possible.

How the name pool is found, given that mapping:

  The meta-class object is named e.g. "Class", and its Name index is a known integer.
  So search the image for a pool-array slot P where the string at
  P + (index & 0x1FFFFFFF) + 12 decodes to that name. Only the real pool satisfies it.

Read-only: OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION), ReadProcessMemory.
It cannot write, allocate in the target, or execute anything there.

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

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_IMAGE = 0x1000000


class Reader:
    def __init__(self, pid):
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.ReadProcessMemory.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k32.ReadProcessMemory.restype = wintypes.BOOL
        k32.VirtualQueryEx.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        k32.VirtualQueryEx.restype = ctypes.c_size_t
        self.k32 = k32
        self.handle = k32.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")

    def close(self):
        self.k32.CloseHandle(self.handle)

    def read(self, address, size):
        if not address:
            return None
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        ok = self.k32.ReadProcessMemory(
            self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(got))
        if not ok or got.value != size:
            return None
        return buf.raw

    def ptr(self, address):
        raw = self.read(address, 8)
        return struct.unpack("<Q", raw)[0] if raw else 0

    def u32(self, address):
        raw = self.read(address, 4)
        return struct.unpack("<I", raw)[0] if raw else 0

    def chunks(self, address, size, chunk=1 << 20):
        """Yield (offset, bytes) readable runs, skipping unreadable holes."""
        done = 0
        while done < size:
            n = min(chunk, size - done)
            raw = self.read(address + done, n)
            if raw is None:
                # Fall back to smaller reads so one bad page does not lose the block.
                step = 0x1000
                piece = b""
                for off in range(0, n, step):
                    sub = self.read(address + done + off, min(step, n - off))
                    piece += sub if sub else b"\x00" * min(step, n - off)
                raw = piece
            yield done, raw
            done += n


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD)]


def module_info(pid, name):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    class ME(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("GlblcntUsage", wintypes.DWORD),
                    ("ProccntUsage", wintypes.DWORD), ("modBaseAddr", ctypes.c_void_p),
                    ("modBaseSize", wintypes.DWORD), ("hModule", wintypes.HMODULE),
                    ("szModule", wintypes.WCHAR * 256), ("szExePath", wintypes.WCHAR * 260)]

    snap = k32.CreateToolhelp32Snapshot(0x8 | 0x10, pid)
    if snap == wintypes.HANDLE(-1).value:
        return None
    k32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ME)]
    k32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ME)]
    try:
        entry = ME()
        entry.dwSize = ctypes.sizeof(ME)
        ok = k32.Module32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szModule.lower() == name.lower():
                return int(entry.modBaseAddr), int(entry.modBaseSize), entry.szExePath
            ok = k32.Module32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return None


def writable_image_regions(reader, base, size):
    """Committed, writable regions of the main image: .data/.bss hold these globals."""
    out = []
    addr = base
    end = base + size
    while addr < end:
        mbi = MBI()
        got = reader.k32.VirtualQueryEx(reader.handle, ctypes.c_void_p(addr),
                                        ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not got:
            break
        region_base = int(mbi.BaseAddress or 0)
        region_size = int(mbi.RegionSize or 0)
        if not region_size:
            break
        writable = bool(mbi.Protect & 0xCC) and not (mbi.Protect & 0x100)
        committed = mbi.State == 0x1000
        if committed and writable and region_base >= base:
            out.append((region_base, region_size))
        addr = region_base + region_size
        if addr <= region_base:
            break
    return out


def find_gobjects(reader, regions, count_min, count_max, sample=64):
    """Locate the UObject array by structure alone; returns candidates + pointer set."""
    found = []
    for rbase, rsize in regions:
        for off, blob in reader.chunks(rbase, rsize):
            limit = len(blob) - 24
            for i in range(0, limit, 8):
                data, count, mx = struct.unpack_from("<Qii", blob, i)
                if not (count_min <= count <= count_max) or mx < count or mx > 4_000_000:
                    continue
                if not data or data % 8:
                    continue
                # The array itself must be readable.
                head = reader.read(data, min(count, sample) * 8)
                if not head:
                    continue
                ptrs = struct.unpack("<%dQ" % (len(head) // 8), head)
                live = [p for p in ptrs if p and p % 8 == 0]
                if len(live) * 2 < len(ptrs):
                    continue
                # Classes must be objects too. Read each class pointer and require it
                # to be 8-aligned and itself look like an object (has a readable class
                # pointer at +0x50).
                classes_ok = 0
                classes = []
                for p in live[:32]:
                    cls = reader.ptr(p + 0x50)
                    if cls and cls % 8 == 0:
                        up = reader.ptr(cls + 0x50)
                        if up and up % 8 == 0:
                            classes.append(cls)
                            classes_ok += 1
                if classes_ok < 24:
                    continue
                found.append({
                    "array_field_at": rbase + off + i,
                    "array_offset_in_region": off + i,
                    "region": [hex(rbase), hex(rsize)],
                    "data": data, "count": count, "max": mx,
                    "class_sample": classes[:8],
                })
    return found


def name_at(reader, pool, index):
    raw = reader.read(pool + (index & 0x1FFFFFFF) + 12, 96)
    if not raw:
        return None
    text = raw.split(b"\0")[0]
    if not text:
        return None
    try:
        decoded = text.decode("ascii")
    except UnicodeDecodeError:
        return None
    if not all(32 <= ord(c) < 127 for c in decoded):
        return None
    return decoded


def find_name_pool(reader, regions, objects_data, objects_count, wanted_name):
    """Find the pool-array slot whose entry `index` decodes to wanted_name.

    Needs the index of the object that is its own class; that object's name is
    wanted_name by construction, which turns a search over pools into a check.
    """
    index = None
    for i in range(min(objects_count, 2000)):
        obj = reader.ptr(objects_data + i * 8)
        if not obj:
            continue
        cls = reader.ptr(obj + 0x50)
        if cls == obj:                       # a meta-class
            index = reader.u32(obj + 0x48)
            if index:
                break
            index = None
    if index is None:
        return None, None
    hits = []
    for rbase, rsize in regions:
        for off, blob in reader.chunks(rbase, rsize):
            for i in range(0, len(blob) - 8, 8):
                pool = struct.unpack_from("<Q", blob, i)[0]
                if not pool or pool % 8:
                    continue
                if name_at(reader, pool, index) == wanted_name:
                    hits.append({"pool_array_at": rbase + off + i, "pool": pool})
                    if len(hits) >= 8:
                        return index, hits
    return index, hits


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--min-objects", type=int, default=20000)
    ap.add_argument("--max-objects", type=int, default=900000)
    ap.add_argument("--metaclass-name", default="Class",
                    help="name of the object that is its own class")
    ap.add_argument("--out")
    args = ap.parse_args()

    info = module_info(args.pid, args.exe)
    if not info:
        print(json.dumps({"error": "%s not found in pid %d" % (args.exe, args.pid)}))
        return 1
    base, size, path = info
    reader = Reader(args.pid)
    try:
        result = {"pid": args.pid, "exe": args.exe, "path": path,
                  "image_base": hex(base), "image_size": hex(size),
                  "read_only": True,
                  "method": "structural scan of writable image regions, no injection"}

        regions = writable_image_regions(reader, base, size)
        result["writable_regions"] = [[hex(a), hex(b)] for a, b in regions]
        if not regions:
            result["error"] = "no writable image regions found"
            print(json.dumps(result, indent=1))
            return 1

        candidates = find_gobjects(reader, regions, args.min_objects, args.max_objects)
        result["gobjects_candidates"] = [{k: (hex(v) if k in ("array_field_at", "data")
                                              else v) for k, v in c.items()
                                          if k != "class_sample"}
                                         for c in candidates]
        if not candidates:
            result["error"] = "no UObject array candidate passed the class-membership test"
            print(json.dumps(result, indent=1))
            return 1
        # The real array has the most objects of any candidate that passed.
        best = max(candidates, key=lambda c: c["count"])
        gobjects_rva = best["array_field_at"] - base
        result["objects_rva"] = hex(gobjects_rva)
        result["objects_count"] = best["count"]
        result["objects_max"] = best["max"]
        result["objects_data"] = hex(best["data"])

        index, pools = find_name_pool(reader, regions, best["data"], best["count"],
                                      args.metaclass_name)
        result["metaclass_name_index"] = index
        result["name_pool_candidates"] = [
            {"pool_array_at": hex(p["pool_array_at"]),
             "rva": hex(p["pool_array_at"] - base),
             "pool": hex(p["pool"])} for p in (pools or [])]
        if pools:
            result["name_pools_rva"] = hex(pools[0]["pool_array_at"] - base)

        # Engine global: find the LIVE UEngine instance (not a Default__ archetype), then
        # the image slot holding it. Picking a Default__ here was a real bug: it found
        # Default__Engine and reported no engine global at all.
        engine = None
        pool = pools[0]["pool"] if pools else 0
        best_live = []
        for i in range(best["count"]):
            obj = reader.ptr(best["data"] + i * 8)
            if not obj or not pool:
                continue
            cls = reader.ptr(obj + 0x50)
            if not cls:
                continue
            cn = name_at(reader, pool, reader.u32(cls + 0x48))
            if not cn or not cn.endswith("Engine"):
                continue
            on = name_at(reader, pool, reader.u32(obj + 0x48))
            if not on or on.startswith("Default__"):
                continue
            best_live.append({"ptr": obj, "name": on, "class": cn})
        result["live_engines"] = [{"ptr": hex(e["ptr"]), "name": e["name"],
                                   "class": e["class"]} for e in best_live[:8]]
        # Prefer the SFX engine, which is what the BioWare games actually instantiate.
        for e in best_live:
            if e["class"] == "SFXEngine":
                engine = e["ptr"]
                result["engine_class"] = e["class"]
                result["engine_name"] = e["name"]
                break
        if engine is None and best_live:
            engine = best_live[0]["ptr"]
            result["engine_class"] = best_live[0]["class"]
            result["engine_name"] = best_live[0]["name"]
        if engine:
            needle = struct.pack("<Q", engine)
            hits = []
            for rbase, rsize in regions:
                for off, blob in reader.chunks(rbase, rsize):
                    pos = blob.find(needle)
                    while pos != -1:
                        if pos % 8 == 0:
                            hits.append(rbase + off + pos)
                        pos = blob.find(needle, pos + 1)
            result["engine_global_candidates"] = [hex(h) for h in hits[:8]]
            if hits:
                result["engine_global_rva"] = hex(hits[0] - base)

        text = json.dumps(result, indent=1)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
            print("wrote %s" % args.out)
        else:
            print(text)
        return 0
    finally:
        reader.close()


if __name__ == "__main__":
    sys.exit(main())
