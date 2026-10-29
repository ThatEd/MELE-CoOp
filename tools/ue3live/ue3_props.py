"""Read UE3 class property lists correctly: including inherited fields.

Why this replaces the earlier helper: the old walk started at UClass.Children (+0x70)
and followed Next (+0x68). On UE3 that yields only the class's OWN children and stops
early, so a subclass looked like it had 6 fields when it inherits hundreds. Every
"the engine does not have that field" conclusion drawn from it was unsound.

This walks the SuperStruct chain and aggregates, so an inherited field such as
GameInfo.MaxPlayers is found on BioSPGame too.
"""
import struct


def _field_chain(reader, start):
    """Follow a UField Next chain, guarding against corruption and cycles."""
    seen = set()
    cur = start
    while cur and cur not in seen:
        seen.add(cur)
        yield cur
        cur = reader.ptr(cur + 0x68)


def class_properties(reader, game, class_obj, include_functions=True):
    """Yield (name, kind, offset, size, declaring_class) for a class and its supers.

    Property offsets are already absolute for the instance (UE3 bakes the final
    offset into each property), so aggregating supers does not need rebasing.
    """
    seen_names = set()
    cur = class_obj
    depth = 0
    while cur and depth < 48:
        declaring = game.object_name(cur)
        for field in _field_chain(reader, reader.ptr(cur + 0x70)):
            raw = reader.read(field + 0x60, 0x40)
            if not raw:
                continue
            name = game.object_name(field)
            kind = game.obj_class(field)
            if not name:
                continue
            if kind == "Function" and not include_functions:
                continue
            size = struct.unpack_from("<i", raw, 0x74 - 0x60)[0]
            offset = struct.unpack_from("<i", raw, 0x94 - 0x60)[0]
            key = (name, kind)
            if key in seen_names:
                continue
            seen_names.add(key)
            yield name, kind, offset, size, declaring
        cur = reader.ptr(cur + 0x60)
        depth += 1


def find_class(reader, game, name):
    for i in range(1, game.count):
        obj = reader.ptr(game.data + i * 8)
        if not obj:
            continue
        if game.object_name(obj) == name and game.obj_class(obj) == "Class":
            return obj
    return 0


def prop_table(reader, game, class_obj, include_functions=False):
    rows = []
    for name, kind, off, size, declaring in class_properties(
            reader, game, class_obj, include_functions):
        rows.append({"name": name, "kind": kind, "offset": off, "size": size,
                     "declared_on": declaring})
    return rows
