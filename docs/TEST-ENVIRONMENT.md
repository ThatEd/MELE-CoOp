# Test environment for all three games

Running two copies of a game for a co-op test is mostly plumbing: isolated installs, private
settings, minimized windows, crash evidence, and the ability to tell "the copy died" from
"the copy is still loading". `tools/gamepool` does all of that and was built for exactly this;
this page is the Mass Effect Legendary Edition configuration for it.

## Ready-to-use configs

| File | Game | Process |
|---|---|---|
| [tools/gamepool/examples/masseffect-le-me1.json](../../tools/gamepool/examples/masseffect-le-me1.json) | ME1 | `MassEffect1.exe` |
| [tools/gamepool/examples/masseffect-le-me2.json](../../tools/gamepool/examples/masseffect-le-me2.json) | ME2 | `MassEffect2.exe` |
| [tools/gamepool/examples/masseffect-le-me3.json](../../tools/gamepool/examples/masseffect-le-me3.json) | ME3 | `MassEffect3.exe` |

Each has been checked against the real install: every referenced file exists, the launcher
path resolves, and per-game port bases differ (27710 / 27720 / 27730).

## Verified behaviour

Provisioning ME1 produced instance 0 with **36,631 hard links** and no copies, so a second
full install costs almost nothing. More importantly the isolation holds where it matters:

| Check | Result |
|---|---|
| Original `GamerSettings.ini` after provisioning | still `ResX=3440 ResY=1440` — untouched |
| Instance `GamerSettings.ini` | its own `ResX=1280 ResY=720` |
| Those two files | **different bytes**, i.e. genuinely unlinked |
| `MassEffect1.exe` in the instance | present, same size and SHA-256 as the original |

That is the property the whole setup depends on: the owner's install and their session stay
exactly as they were.

## Use it

```
cp tools/gamepool/examples/masseffect-le-me1.json local/me1.json
# set instances_root. It must be on the SAME VOLUME as game_dir: hard links cannot
# cross volumes, and the game is installed on B: here.
python -I tools/gamepool/gamepool.py --config local/me1.json provision --n 2
python -I tools/gamepool/gamepool.py --config local/me1.json up --n 2
python -I tools/gamepool/gamepool.py --config local/me1.json status
python -I tools/gamepool/gamepool.py --config local/me1.json down
```

Write the edited config to `local/` (git-ignored), not into `tools/`. And strip any BOM if a
tool complains: `gamepool` reads JSON strictly.

## Two things this does not solve, and why they matter

### Saves are shared between copies

The games derive their save directory from the Windows shell Documents folder, so **every
copy reads and writes the same `Documents\BioWare\Mass Effect Legendary Edition\Save`**. There
is no path in the configs that can redirect it, because the engine resolves it itself.

Consequences, in order of how much they will bite:

1. **Autosave collisions.** Two copies in the same level will fight over the autosave slot.
2. **Character identity.** Both copies load the same save, which is actually what a first test
   wants (identical starting state), but it also means one copy's progress overwrites the
   other's.
3. **Recovery.** Overwriting each other's saves is exactly the data loss `tools/saverestore`
   exists to prevent.

Practical policy for now, and what the test procedure should follow:

- **Snapshot before every test run**: `python -I tools/saverestore/saverestore.py --snapshot
  <label> --games ME1`. It is 48 files and a few seconds.
- **Keep one copy as the author and the other as a reader.** Only the first copy is allowed to
  save at all; the second is expected to discard its saves.
- **Restore to a known state between runs** rather than trying to manage two live save sets.

The stronger option, not yet implemented, is to give each copy its own Windows user profile or
a per-instance Documents directory and launch the game under it. Do not attempt that as a
per-instance `Documents` override until it is tested, because a game that half-relocates its
save path is worse than one that shares it.

### A window is not proof of gameplay

`ready.type` is `window` for all three games because none exposes an in-game bridge yet. That
only proves a process started. Every test therefore needs a read-only state check before it
does anything that mutates:

```
python -I tools/ue3live/ue3_check_chain.py --pid <pid> --exe MassEffect1.exe \
       --offsets local/MELE/offsets/offsets_LE1.json
```

It reports `GamePlayers`, the controller, the pawn and `Physics`. **`Physics == 1 (Walking)`
with a non-zero location is the gate**: physics 7 means a cutscene owns the pawn and no
movement test is valid yet.

## What a test run looks like end to end

1. `saverestore --snapshot before-<test>`.
2. `gamepool up --n 2` (or `--pairs 1` once a transport exists).
3. Wait for the state gate on each pid via `ue3_check_chain.py`.
4. Load the same save in both, in the level the test needs.
5. Run the experiment, then read its log.
6. `gamepool crashlog` if a copy died; `gamepool down` afterwards.
7. `saverestore --restore before-<test>` to get back to a known state.

Step 3 is the one that is easy to skip and expensive to skip: it is the difference between
testing "does P2 move" and testing "what does a cutscene do to an unowned pawn".

## Missing pieces worth building next

- **An in-game bridge** (a named pipe or a log line the tool can tail) so `ready.type` can mean
  "in gameplay" instead of "a window exists". `tools/bridge` in this hub is the reference.
- **Per-instance save isolation**, which needs the game's own save-path resolution to be
  understood first. That is a reverse-engineering task, not a config change.
- **Coordinated scene control**: one command that puts both copies in the same level at the
  same point. This is the prerequisite for testing conversation handling, and it does not
  exist.
