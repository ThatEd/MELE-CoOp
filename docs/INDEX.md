# Documentation index

One page per topic. Every claim is labelled **proven**, **documented**, **derived** or **unknown**,
and the labels matter — several conclusions in here have already been retracted once, and the
retractions are kept visible rather than quietly edited away.

## Start here

| Page | What it answers |
|---|---|
| [README.md](README.md) | The game's state, the projects that exist, what works and what does not |
| [NOTES.md](NOTES.md) | The working log. **31 rules** at the top, each with the incident that produced it; then every crash with its stack and root cause, and all verified offsets |
| [SPLITSCREEN-PLAN.md](SPLITSCREEN-PLAN.md) | Why two panes cannot be the plan for LE1/LE2, the measured per-game field table, the ordered steps — and two corrections to earlier documents |

## How the engine works, for co-op

| Page | What it covers |
|---|---|
| [CO-OP-FAIL-POINTS.md](CO-OP-FAIL-POINTS.md) | 13 subsystem areas, every fail point, with evidence labels. The single best overview of what blocks co-op and why |
| [ADDING-MISSING-SYSTEMS.md](ADDING-MISSING-SYSTEMS.md) | The capability gap per game and three candidate routes to a second `PlayerController`, cheapest first |
| [CONTROL-GATES.md](CONTROL-GATES.md) | Where the player cannot shoot: the firing chain (`IsLocallyControlled` → `HandleWeaponFiring` → `CanFireWeapon` → `StartFire`), every other gate, and a measured per-build table of which gate names exist |
| [CONVERSATIONS.md](CONVERSATIONS.md) | Making player 2 a **spectator** during dialogue: which engine fields decide it, four insertion points ranked by risk |
| [MAP-EVENTS.md](MAP-EVENTS.md) | Kismet level-event classes per game: 322 / 306 / 225 names, **86 in all three**, and what the per-game sets mean for porting |
| [TRIGGER-DELAY.md](TRIGGER-DELAY.md) | Delaying walk-in events until player 2 passes — `EWhoTriggers`, `m_bInstigatorIsPlayer`, and the per-event policy a mod needs |

## The tooling and the test area

| Page | What it covers |
|---|---|
| [COMMAND-SUITE.md](COMMAND-SUITE.md) | **The test area.** 156 command mechanisms in 20 categories, presence measured per game, 23 ordered phases, and the per-game capability differences they measured |
| [COMMAND-SUITE-ADAPTER.md](COMMAND-SUITE-ADAPTER.md) | The five `ue3.*` bridge methods the game side must implement, and the validation order that turns a silent wrong answer into a loud one |
| [CONTAINER-FORMAT.md](CONTAINER-FORMAT.md) | The **verified** `.pcc` container format — chunk header, derived block count, per-game header offsets — so the script layer can be read offline |
| [TEST-ENVIRONMENT.md](TEST-ENVIRONMENT.md) | Running two isolated copies of each game, the per-game configs, what was verified, and the one thing isolation cannot fix (shared saves) |

## The rules, condensed

The full list with incidents is at the top of [NOTES.md](NOTES.md). The ones that cost the most:

- **Never use `CreatePlayer` on LE1/LE2.** Three crashes, two games, `bSpawnActor` both ways.
- **A per-build number is a hypothesis.** `PlayerController.Pawn` is `+0x3DC` / `+0x294` / `+0x3BC`.
- **Check both encodings.** ME3 stores identifiers UTF-16LE; ASCII-only gives a confident zero.
- **A check that cannot fail is worse than none.** Two of this project's own drift tests passed
  while testing nothing.
- **A value that is per-launch must not be baked into a plan.** Carry a role, resolve it live.
- **Read the faulting instruction, not the frame list.**

## Reading order for someone picking this up

1. [README.md](README.md) — where it stands.
2. [CO-OP-FAIL-POINTS.md](CO-OP-FAIL-POINTS.md) — what blocks it.
3. [SPLITSCREEN-PLAN.md](SPLITSCREEN-PLAN.md) — what to do next, ME2 first then ME1.
4. [NOTES.md](NOTES.md) rules 1–31 — so the same crashes are not repeated.
