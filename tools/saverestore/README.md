# saverestore

Cheap save restore points, so a failed experiment costs seconds instead of minutes.

## Why this exists

The most expensive thing in a reverse-engineering session is not finding an offset — it is
**recovering from a crash**. When a mutation kills the game and the only way back to the test
point is an unskippable cutscene, every experiment carries a cost that changes how you work:
you stop testing small things, you batch risky changes together, and you avoid the
experiments that would actually answer the question.

That is exactly what happened in the session this tool came from. Four crashes meant four
reloads, one of them roughly twenty minutes. The missing capability was never knowledge — it
was a restore point.

## Usage

Read-only by default; it only lists and reports until you pass `--restore`.

```
python -I tools/saverestore/saverestore.py --list
python -I tools/saverestore/saverestore.py --snapshot before-p2-test --games ME1
python -I tools/saverestore/saverestore.py --restore before-p2-test
```

`--games` accepts several: `--games ME1 ME2`. The default lists all three.

## The path trap this tool exists partly to avoid

**`%USERPROFILE%\Documents` is frequently not the real Documents folder.** Windows lets a
user relocate it to any drive, and on the machine this was written for it lived on `F:`.
Searching the profile directory therefore finds no saves at all, which reads as *"this game
has no saves"* rather than *"looked in the wrong place"* — and that wrong conclusion cost
real time before it was caught.

So the tool resolves the folder from the registry's shell-folder entry
(`HKCU\...\Explorer\User Shell Folders\Personal`) and expands the environment variable. If
that fails there is `--save-root` to point at the directory directly.

The layout it expects:

```
<Documents>\BioWare\Mass Effect Legendary Edition\Save\{ME1,ME2,ME3}\<slot>\*.pcsav
```

## Rules it follows

Save data is the one thing in this workflow that cannot be regenerated from the game's
files, so the tool is deliberately conservative:

| Rule | Why |
|---|---|
| **Read-only unless `--restore`** | Listing must never be able to change anything. |
| **Never deletes** | A restore moves the current state to a timestamped `quarantine__*` folder beside the snapshot. A wrong restore is itself reversible. |
| **Manifest with SHA-256** | Every snapshot records size and hash per file, and a restore re-hashes what it laid down. A restore that silently half-worked is worse than none. |
| **Refuses a path in the label** | A label containing `/` or `\` is rejected rather than used to build a path. |

## Verified behaviour

Tested on a scratch copy, not on the owner's saves:

1. Snapshot two files; corrupt one and delete the other.
2. `--restore` reported the quarantine, moved the corrupted file there, and restored both.
3. Directory contents afterwards: both files present, contents `ORIGINAL` and `SECOND`.
4. `all restored files match the manifest` and the quarantine still held `CORRUPTED`.

A snapshot of the real saves was then taken as `me1-pre-coop-test` (48 files).

## Limits

- **Windows only** — it reads the registry for the real Documents path.
- It copies whole `Save/<GAME>/` folders, so a snapshot cost is proportional to save count
  (48 files for one game here, a few MB).
- It does not stop the game. **Close the game before `--restore`**, or the running process
  may write its own state over the restored files.
- Snapshots live inside the save directory under `saverestore-snapshots/`. That keeps them
  next to what they protect, but it also means they are inside the folder a game scans;
  delete old ones if a game's save picker starts listing oddities.
