# Publishing checklist

For the agent (or person) who pushes this to git. Work top to bottom; every step is a command
with an expected result, not an instruction to eyeball something.

The rule this file exists to enforce: **`local/` is never committed.** It holds decompressed game
data, per-build offsets, raw probe dumps and a 2.6 GB ME3 sandbox install. None of that belongs in
a repository, and the offsets are per-exe-hash artefacts rather than source.

---

## 1. Confirm the working tree is what you think it is

```bash
cd "MELE Co-Op Mod"
git status --porcelain            # expect: the files below, and NO path starting with local/
git check-ignore -v local/x       # expect: a .gitignore rule matches
```

If `local/` appears in `git status`, **stop**. `.gitignore` at the bundle root covers it as well as
at the hub root, but a nested repository or a `-f` flag can override both.

## 2. Check for secrets and identity leaks

```bash
# The one thing that must not ship: a real user's name in a path. The pattern is assembled so
# this file does not match itself (a checklist that always trips its own check teaches you to
# ignore it).
PAT="$(printf 'im%s' 'dea')"
grep -rIn --exclude-dir=__pycache__ --exclude=PUBLISHING.md \
     -e "$PAT" -e 'C:\\Users\\' . || echo "clean"
```

Expected: `clean`. The docs use `<documents>`, `<game dir>` and `%LOCALAPPDATA%` on purpose. If a
real path appears, replace it with the placeholder — the *fact* is usually worth keeping (Documents
is often not on `C:`), the identity is not.

```bash
grep -rn --include=*.json --include=*.ini -e "password" -e "token" -e "api_key" . || echo "no secrets"
```

## 3. Confirm no game data is present

```bash
# Nothing from the game itself. Packages, saves, textures, audio.
find . -iname "*.pcc" -o -iname "*.upk" -o -iname "*.pcsav" -o -iname "*.tfc" | head
```

Expected: nothing. Matches inside markdown prose (`SFXGame.pcc`) are fine; actual files are not.

```bash
du -sh .          # expect a few MB, NOT gigabytes
```

If this says 2.6 GB, someone copied `local/refs/coop-anything`. Remove it.

## 4. Run every test

```bash
python -I run_all_tests.py
```

Expected: `ALL OK`, and two `PASS` lines. **This must be green on a machine with no game
installed** — that is the whole point of the game-free suites, and a fresh clone is the real test.

Skipped cases are expected and named:

```
SKIP drift check passes on the real data    no identifier table for ME1, ME2, ME3 under .../local/maps.
                                            Regenerate with tools/melepkg/mele_script_map.py
```

A skip is fine. A **failure** is not, and a skip with no reason is a bug worth fixing before
pushing.

## 5. Confirm the tool copies have not drifted

Only relevant if this bundle is being published from the hub (where `tools/` also exists):

```bash
python -I tools/ue3live/check_bundle_sync.py
```

Expected: `ALL OK: the bundle's tool copies match the hub`. `--fix` copies hub → bundle; `--reverse`
after editing link paths in the bundle's docs, because the two have different relative paths and a
blind mirror either way destroys work.

## 6. Check the documentation links

```bash
python - <<'PY'
import glob, os, re
bad = 0
for p in glob.glob('docs/*.md') + ['README.md']:
    for m in re.finditer(r'\]\(([^)]+)\)', open(p, encoding='utf-8').read()):
        t = m.group(1)
        if t.startswith(('http', '#', 'mailto')):
            continue
        if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(p), t.split('#')[0]))):
            print('%-40s -> %s' % (p, t)); bad += 1
print('broken links:', bad)
PY
```

Expected: `broken links: 0`.

## 7. What goes in the commit

```
MELE Co-Op Mod/
  README.md                  where the project stands, the correction, the layout
  LICENSE                    MIT
  .gitignore                 local/ and build leftovers, root and nested
  run_all_tests.py           one command, two suites
  docs/                      13 pages + INDEX.md
  tools/ue3live/             live inspection, command suite, runner, field allow-list
  tools/melepkg/             verified .pcc container reader
  tools/bridge/              RPC server + client (the executor seam)
  tools/saverestore/         save restore points
  tools/gamepool/            isolated test copies (+ MELE configs only)
```

Deliberately **not** included: `local/`, `build/`, `__pycache__`, the hub's `tools/test_all.py`
wiring, and other games' `gamepool` example configs.

## 8. After pushing

- The commit is under the owner's GitHub account. Show them the diff first.
- If this is its own repository, add it to the hub's `data/directory.yaml` and run
  `python tools/build_directory.py`, so the directory lists it.
- If the mod is unfinished, set `up_for_adoption: true` and say in the README what works, what is
  broken and how to build it.

---

## Known honest gaps (say these in the PR description, do not paper over them)

| Gap | Status |
|---|---|
| No phase of the test suite has run against a game | the suites and the container model have; a live run has not |
| The game-side `ue3.*` adapter is not written | specified in `docs/COMMAND-SUITE-ADAPTER.md`; it is a C++ job |
| `local/MELE/offsets/offsets_LE3.json` does not exist | `probe` needs it and LE3 is the first target |
| ME2's viewport offsets are unmeasured | `ue3_fields.py check --game ME2` names them |
| `p2v2_input.dll` is built and never run | it decides whether a controller can live without a `LocalPlayer` |
| LE1/LE2 campaign co-op is unbuilt | `CreatePlayer` crashes three ways in both |
