# The game-side adapter: four bridge methods a mod must implement

The command suite decides *what* to call. It cannot make the call. That has to happen inside
the game process, on the main thread, and this page is the contract between the two halves.

Why it is not negotiable: `ProcessEvent` must run on the game thread, and its vtable slot
differs per game (LE1 `+0x238`, LE2 `+0x228`, LE3 `+0x230` as a wrapper). Worse, LE1 and LE2
crash inside `CreatePlayer` **from the game's own thread after a correct call returned** — so a
dispatcher in a separate process cannot even attribute the fault. Python reads and plans; the
game calls.

| Method | Runs on | Mutates |
|---|---|---|
| `ue3.info` | main thread | no |
| `ue3.call` with `mode: "probe"` | main thread | **no** |
| `ue3.call` with `mode: "call"` | main thread | yes |
| `ue3.read` | main thread | no |
| `ue3.write` | main thread | **yes — allow-listed** |
| `ue3.chain` | main thread | no |

Register them as **custom** methods on the existing bridge
([`tools/bridge`](../tools/bridge/README.md)); custom methods already run on the main thread
through the request queue, which is exactly the requirement.

---

## `ue3.info`

No params. Answer:

```json
{"build": "10352613",
 "processEventSlot": "0x238",
 "peVerified": true,
 "roles": {"player.pawn": "0x1AEE23EECB0", "player.controller": "0x1AE36BA0000"},
 "objects": 141553}
```

`roles` is the important field: it is the run's record of what each role meant **this launch**.
Without it a result file says "SetHidden worked" without saying what it hid.

`peVerified` must mean *the slot's body was read and matched the expected signature*, not that
the slot was writable. Rule 16 exists because a hook that installs and never fires looks inert
rather than broken.

If `ue3.info` is missing, the suite still runs and records why — a build string is a record, not
a prerequisite.

## `ue3.chain`

Resolves a role and reports the object's identity.

```json
params: {"role": "squad.member", "object": "0x..."}       // object wins if both are given
result: {"class": "BioPawn", "chain": ["BioPawn", "Pawn", "Actor"],
         "name": "Kaidan", "address": "0x1AEE23EECB0"}
```

Purpose: prove the role resolved to the class you meant. A role that resolves to the wrong
class is the failure that makes every later result meaningless, and it is cheap to check first.

## `ue3.read`

```json
params: {"role": "player.pawn", "object": "0x...", "offset": 108, "size": 12, "signed": false}
result: {"ok": true, "value": 1, "hex": "0x1", "address": "0x..."}
```

- `offset` is a **byte offset into the object**, not an absolute address. The suite's offsets are
  the per-game ones from `local/MELE/offsets/offsets_LE*.json`.
- `size` 1/2/4/8 returns a number; any other size returns `hex` with the raw bytes.
- **An unreadable field must fail, not return zero.** The project rule is that a value which
  cannot be read is never treated as "absent" — a read that returns 0 for unmapped memory would
  make a gate look closed when it was never asked.

## `ue3.write`

```json
params: {"role": "viewport", "offset": 448, "size": 1, "value": 1}
result: {"ok": true, "readBack": 1}
```

The only method that mutates a named field. Two obligations on the adapter:

1. **Write to `role + offset`, nothing else.** Do not accept an absolute address. The role is
   resolved the same way as for `ue3.call`; a role that resolves to more than one object must be
   refused rather than guessed.
2. **Read the field back and return it as `readBack`.** The runner requires an exact match and
   treats a missing `readBack` as a failure — because "the write applied" and "the write landed
   somewhere harmless" are otherwise the same answer, and this project has already paid for
   trusting a call that silently did nothing.

The runner only ever asks for offsets from the fixed allow-list in
[`tools/ue3live/ue3_fields.py`](../tools/ue3live/ue3_fields.py) — four writable fields, each
with a measured per-game offset and a documented reason. **`ue3.write` must not be a general
write primitive**: the safety comes from the caller being unable to name an address, so the
adapter should refuse an offset it was not asked for by name, and the allow-list should stay
short.

## `ue3.call`

```json
params: {"function": "0x1AE7D3E2070",
         "role": "player.controller",
         "object": "0x...",                  // optional; overrides the role
         "parms": "<base64>",
         "parmsSize": 44,
         "mode": "probe"}
result: {"ok": true, "called": false, "address": "0x..."}
```

Steps, in this order. The order is the point — each step turns a silent wrong answer into a
loud one.

1. **Resolve the target.** `object` if given, else `role`. Failure → `ok: false` with a reason.
   Never fall back to another object.
2. **Validate the function belongs to the target.** Walk the target's `SuperStruct` chain and
   require the `UFunction`'s owning class to be in it. A function from a different class is
   callable in UE3 and does something arbitrary; this check is what stops that.
3. **Validate the parms block size.** Compare `parmsSize` with the function's own parameter
   layout (`ParmsSize`). Mismatch → refuse. The project's own crash log records this exact check
   catching a hand-padding "fix" that would otherwise have gone in.
4. **`mode: "probe"` stops here** and returns `{ok: true, called: false, address: ...}`. No call.
5. **`mode: "call"` invokes through `ProcessEvent`** on the main thread, with the three-argument
   form `(self, fn, parms)`, then returns `{ok: true, called: true}`.

### Rules the adapter must follow

- **Never call `CreatePlayer`.** It is `blocked` in the catalogue for a measured reason: three
  distinct crashes on LE1/LE2. Refuse it by name, so a hand-written plan cannot reintroduce it.
- **Never invent a parms block.** If `parmsSize` is 0 for a function that declares parameters,
  refuse. A zeroed block of the wrong size is a stack overwrite.
- **Do not catch and continue after a fault.** If the call faults, the process is gone; that is
  information, and swallowing it makes the run look clean.
- **Log the resolved address for every call.** The result file is only useful if it can be
  replayed, and a replay needs the address that was actually used.
- **Refuse on ambiguity.** If a role resolves to more than one candidate (two squadmates, two
  local players), return `ok: false` naming them. Picking the first is how the client ends up
  controlling the wrong body.

---

## The flows this enables

### Dry run (safe, and should be the first thing anyone does)

```
python -I tools/ue3live/ue3_command_suite.py probe --pid N --exe MassEffect3.exe \
    --offsets local/MELE/offsets/offsets_LE3.json --out local/probe.json
python -I tools/ue3live/ue3_command_suite.py plan --probe local/probe.json --out local/plan.json
python -I tools/ue3live/ue3_run_plan.py --plan local/plan.json \
    --endpoint tcp://127.0.0.1:PORT --probe-only
```

This proves, in one pass and without mutating anything: every role resolves, every function
exists, every function belongs to the target class, and every parms block is the right size. A
failure here costs nothing.

### Real run

```
python -I tools/ue3live/ue3_run_plan.py --plan local/plan.json \
    --endpoint tcp://127.0.0.1:PORT --out local/run.json
```

Required phases only, by default. `P13-possession` needs `--yes-destructive`, and the save and
travel phases need `--include-optional --yes-destructive` **on a throwaway copy with a restore
point taken first** (`tools/saverestore`).

### Split-screen field run (the `S00`–`S04` phases)

```
python -I tools/ue3live/ue3_fields.py check --game ME1            # what is runnable
python -I tools/ue3live/ue3_command_suite.py phases --fields --game ME1
python -I tools/ue3live/ue3_run_plan.py --plan local/plan_ME1.json \
    --endpoint tcp://127.0.0.1:PORT --out local/run_split.json
```

Every write is read back and must match. On ME2, three of the five phases **cannot run** until
the viewport offsets are measured — the plan refuses rather than borrowing LE1's `0x1C0`.

### Two clients

Run the same plan against both endpoints, then diff the two result files. The phases are written
as comparisons, so the same command appearing with a different `value` on the two clients is the
finding — that is what "sync verification" means here, and it is why every phase states its
assertion rather than just its call.

---

## What is implemented, honestly

| Piece | State |
|---|---|
| `ue3_run_plan.py` (the runner) | **written and tested**, 29 cases against a real bridge server |
| `ue3_fields.py` (write allow-list) | **written and tested**, 19 cases — mostly refusal cases |
| `commands.json` target roles | **written**, 146 of 156 role-addressed, the other 10 justified |
| plan → runner schema | **verified** by an end-to-end test that builds a real plan and runs it |
| The game-side adapter (`ue3.*` methods) | **not written.** That is a C++ job in the mod, and this page is its specification |
| `local/MELE/offsets/offsets_LE3.json` | **not written.** `probe` needs it, and LE3 is the first target game |
| ME2 viewport offsets | **not measured** — `ue3_fields.py check --game ME2` names them |

So the honest summary is: **everything above the game process is done and tested; nothing inside
it is.** The next piece of work is the five methods above in the LE3 plugin (and then LE2/ME1),
plus the offsets files that `probe` and the field allow-list read.
