"""
Read UE3 objects from any Legendary Edition game, using discovered offsets.

Why this module exists: the ad-hoc scripts kept getting name decoding wrong in the
same way. With ONE name pool (LE2), an index whose high bits are set is not a valid
name - but `pools[index >> 29]` then reads *adjacent memory* as if it were a pool
pointer, producing plausible-looking garbage such as a Wwise sound object appearing
as the class of a property. That bad name then survives every "is this the class I
want" comparison and quietly poisons the answer.

So this refuses to decode an index whose pool does not exist, and validates the
entry: a real FNameEntry has a printable, NUL-terminated string at +12.

The sys.path insert below makes the sibling modules importable when this file is run
with `python -I`, which omits the script directory from sys.path. Without it these tools
only work when invoked without -I, which is not how the README documents them.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import struct


class Game:
    def __init__(self, reader, base, offsets_path):
        self.r = reader
        self.base = base
        with open(offsets_path, encoding="utf-8") as f:
            self.off = json.load(f)
        self.name_pools = base + int(self.off["name_pools_rva"], 16)
        self.objects = base + int(self.off["objects_rva"], 16)
        self.engine_global = (
            base + int(self.off["engine_global_rva"], 16)
            if str(self.off.get("engine_global_rva", "")).startswith("0x")
            else None
        )
        self.pool_count = int(self.off.get("name_pool_count", 1))
        data, count, maximum = struct.unpack("<Qii", self.r.read(self.objects, 16))
        self.data, self.count, self.max = data, count, maximum
        self._name_cache = {}

    # --- names ---------------------------------------------------------------
    def name(self, index):
        if not index:
            return None
        if index in self._name_cache:
            return self._name_cache[index]
        pool_id = index >> 29
        if pool_id >= self.pool_count:
            self._name_cache[index] = None
            return None
        pool = self.r.ptr(self.name_pools + pool_id * 8)
        if not pool:
            return None
        raw = self.r.read(pool + (index & 0x1FFFFFFF) + 12, 96)
        if not raw:
            return None
        text = raw.split(b"\0")[0]
        if not text:
            return None
        try:
            decoded = text.decode("ascii")
        except UnicodeDecodeError:
            self._name_cache[index] = None
            return None
        if not all(32 <= ord(c) < 127 for c in decoded):
            self._name_cache[index] = None
            return None
        self._name_cache[index] = decoded
        return decoded

    def object_name(self, obj):
        if not obj:
            return None
        raw = self.r.read(obj + 0x48, 4)
        if not raw:
            return None
        return self.name(struct.unpack("<I", raw)[0])

    def obj_class(self, obj):
        return self.object_name(self.r.ptr(obj + 0x50)) if obj else None

    def is_kind_of(self, obj, wanted):
        cls = self.r.ptr(obj + 0x50) if obj else 0
        for _ in range(48):
            if not cls:
                return False
            if self.object_name(cls) == wanted:
                return True
            cls = self.r.ptr(cls + 0x60)
        return False

    # --- iteration -----------------------------------------------------------
    def objects_iter(self, skip_defaults=True, limit=None):
        for i in range(1, min(self.count, limit or self.count)):
            obj = self.r.ptr(self.data + i * 8)
            if not obj:
                continue
            name = self.object_name(obj)
            if skip_defaults and name and name.startswith("Default__"):
                continue
            yield i, obj, name, self.obj_class(obj)

    # --- properties ----------------------------------------------------------
    def properties(self, class_obj):
        """Yield (name, kind, offset, size) for a UClass's declared properties."""
        field = self.r.ptr(class_obj + 0x70)
        seen = set()
        while field and field not in seen:
            seen.add(field)
            chunks = self.r.read(field + 0x60, 0x40)
            if chunks:
                off = struct.unpack_from("<i", chunks, 0x94 - 0x60)[0]
                size = struct.unpack_from("<i", chunks, 0x74 - 0x60)[0]
                yield self.object_name(field), self.obj_class(field), off, size
            field = self.r.ptr(field + 0x68)

    def players(self, controller_offset=None):
        """Local players from Engine+0x498, with each one's controller if known."""
        if not self.engine_global:
            return []
        engine = self.r.ptr(self.engine_global)
        if not engine:
            return []
        raw = self.r.read(engine + 0x498, 16)
        if not raw:
            return []
        data, count, maximum = struct.unpack("<Qii", raw)
        if not data or count < 0 or count > 4 or maximum < count:
            return []
        out = []
        for i in range(count):
            lp = self.r.ptr(data + i * 8)
            entry = {"slot": i, "local_player": lp, "name": self.object_name(lp),
                     "controller": None, "controller_name": None}
            if lp and controller_offset is not None:
                pc = self.r.ptr(lp + controller_offset)
                entry["controller"] = pc
                entry["controller_name"] = self.object_name(pc)
            out.append(entry)
        return out
