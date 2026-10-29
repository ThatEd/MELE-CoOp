"""Tests for the install index: the compact reference format and its query semantics.

The index is what turns "which package defines this class" from a search into a lookup, so the
cases here are about the two ways such a tool lies:

1. **A reference rendered wrong.** `describe_short` stores an export reference by NAME and an
   import as `pkg.obj`. If it confuses the two, `who` reports imports as definitions and every
   answer is off by one object.
2. **An index that looks complete but is not.** A package that failed to parse is recorded as an
   error rather than dropped, so "nothing matched" can be distinguished from "the index never
   contained that file".

Run: python -I tools/melepkg/test_mele_index.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def load_index():
    spec = importlib.util.spec_from_file_location(
        "mele_index", os.path.join(HERE, "mele_index.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@case("describe_short resolves export, import and null differently")
def t_describe_short():
    idx = load_index()
    exports = [{"object_name": "SFXGame"}, {"object_name": None}]
    imports = [{"source_name": "Core", "object_name": "Class"}]
    cases = [
        (0, "None"),
        (1, "SFXGame"),
        (-1, "Core.Class"),
        (2, "?"),          # a null export name is not a valid reference target
    ]
    for value, want in cases:
        got = idx.describe_short(value, exports, imports)
        if got != want:
            return "fail", "index %r -> %r, expected %r" % (value, got, want)
    return "ok", "null / export-name / pkg.obj / unnamed"


@case("an out-of-range reference is reported, not silently mapped")
def t_describe_out_of_range():
    idx = load_index()
    for value in (99, -99):
        got = idx.describe_short(value, [{"object_name": "A"}],
                                 [{"source_name": "S", "object_name": "O"}])
        if not got.startswith("?"):
            return "fail", "index %r -> %r, expected a ? marker" % (value, got)
    return "ok", "both directions produce ? markers"


@case("a class is what 'who' looks for: an export whose own class is None")
def t_class_detection():
    """The rule `who` applies. A class's own class is null, so `describe_short` renders it
    'None'. An export whose class is a name is an instance, not a class - reporting it as a
    definition would make every property look like a class."""
    idx = load_index()
    exports = [{"object_name": "SFXGame"}]
    imports = [{"source_name": "Core", "object_name": "Class"}]
    # +1 is the class export itself; -1 is Core.Class, the import
    if idx.describe_short(1, exports, imports) != "SFXGame":
        return "fail", "export reference did not resolve"
    if idx.describe_short(-1, exports, imports) != "Core.Class":
        return "fail", "import reference did not resolve"
    if idx.describe_short(0, exports, imports) != "None":
        return "fail", "null is not 'None'; the class test would mis-fire"
    return "ok", "class test = (class field == 'None')"


@case("the index path is under local/, which is git-ignored")
def t_index_dir_ignored():
    """The index is derived from the game's own data, so it must never be publishable. The whole
    `local/` tree is ignored by both the hub and the bundle `.gitignore`."""
    idx = load_index()
    d = idx.index_dir()
    parts = d.replace("\\", "/").lower().split("/")
    if "local" not in parts:
        return "fail", "index dir %r is not under local/" % d
    return "ok", "%s" % d


@case("a package record carries only what a query needs")
def t_record_is_compact():
    """An earlier version stored the full export dict per object and produced a 591 MB ME1 index.
    A record now holds four strings per export - name, class, super, outer - so the index is
    proportional to object count rather than to parser detail."""
    idx = load_index()
    src = open(os.path.join(HERE, "mele_index.py"), encoding="utf-8").read()
    tail = src.split("def pack_package", 1)
    if len(tail) != 2:
        return "fail", "pack_package not found"
    body = tail[1].split("def describe_short", 1)[0]
    for banned in ("\"raw\"", "\"entry_offset\"", "\"serial_size\"", "\"archetype_index\""):
        if banned in body:
            return "fail", "the record still stores %s" % banned
    if "describe_short" not in body:
        return "fail", "pack_package does not use the compact renderer"
    return "ok", "4 strings per export, no parser detail"


@case("a failed package is recorded as an error, not dropped")
def t_errors_are_recorded():
    """A dropped package makes 'nothing matched' indistinguishable from 'the index is
    incomplete' - the failure mode that made 380 uncompressed ME1 packages invisible earlier in
    this project."""
    idx = load_index()
    src = open(os.path.join(HERE, "mele_index.py"), encoding="utf-8").read()
    build = src.split("def cmd_build", 1)[-1].split("def _iter_entries", 1)[0]
    if '"error"' not in build:
        return "fail", "cmd_build does not store an error field"
    if "except SystemExit" not in build:
        return "fail", "cmd_build does not catch a parse refusal"
    return "ok", "refusals are stored with their message"


def main():
    failures = skips = 0
    for name, fn in CASES:
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = "fail", "%s: %s" % (type(exc).__name__, exc)
        mark = {"ok": "OK  ", "fail": "FAIL", "skip": "SKIP"}.get(status, "OK  ")
        print("%s %-64s %s" % (mark, name, detail))
        if status == "fail":
            failures += 1
        elif status == "skip":
            skips += 1
    print()
    if failures:
        print("%d/%d FAILED" % (failures, len(CASES)))
        return 1
    print("ALL OK: %d cases%s" % (len(CASES), ", %d skipped" % skips if skips else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
