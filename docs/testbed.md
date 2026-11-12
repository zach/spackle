# Wireworld testbed (Phase 1)

This is the design of the testbed that `plan.md` calls Phase 1: simulator and
scorer, task generator, best-known sizes. It ends at Gate 1, "generator
validated". Nothing here calls a model or spends API budget.

## What Gate 1 means here

`plan.md` names the gate but not its criterion. This testbed defines it as:
**`python -m spackle.validate` exits 0.** That run generates a fresh suite and
checks six groups of properties; the result is written to
[`gate1_report.md`](gate1_report.md).

| Group | What must hold |
| --- | --- |
| Simulator | The fast simulator equals the slow reference on random grids; the scenario runner (injection, pulse counting, quiet test) equals the reference on every stored design. |
| Determinism and freshness | Same seed gives the same suite, also across processes; a new seed gives all-new T1 and T2 instances; T3 is fixed. |
| T1 | Stored targets are right; step counts cover 1 to 20; no duplicates; no instance dies out; copying the input never passes. |
| T2 | Every broken circuit fails; the reference repair passes; `min_edits` is proven exact by exhaustive search; 1, 2 and 3 flips all occur; doing nothing scores 0. |
| T3 | All six specs have a best-known design that passes on the reference simulator and cannot lose a cell; every budget is reachable; trivial designs score 0; over-budget designs fail. |
| Scoring | Both encodings round-trip and score identically; malformed answers are repaired and counted, never crash; prompts never leak the answer key; the robustness probe separates designs with the same truth table. |

## Layout

| File | Role |
| --- | --- |
| `spackle/wireworld.py` | Reference simulator (slow, obviously correct), ASCII and coordinate-list encodings, answer parsing with repair counts |
| `spackle/fastsim.py` | Bitboard simulator; runs all input combinations of a design in one pass |
| `spackle/specs.py` | Six truth tables and twelve port geometries |
| `spackle/circuit.py` | Correctness and robustness evaluation, simulator trace text |
| `spackle/search.py`, `_anneal.c` | Simulated-annealing search for best-known designs |
| `spackle/generate.py` | Seeded generators for T1, T2, T3; suite builder |
| `spackle/scoring.py` | `score(task, answer_text)` for every tier |
| `spackle/prompts.py` | Task statements, one per task and encoding, with a version hash |
| `spackle/validate.py` | Gate 1 checks |
| `data/best_known.json` | Best-known designs and the T2 circuit pool, with provenance |

The package has no runtime dependencies. The C kernel is used only by the
search and is compiled on demand; without a compiler the search falls back to
Python. Every design the search returns is re-checked in Python before it is
stored.

## Semantics fixed by the testbed

The plan leaves a few details open. These are the choices made, so that the
harness and the analysis can rely on them.

**A run.** A design is a grid of wire and empty cells. Inputs are the cells in
column 0 on the task's input rows; outputs are the cells in the last column on
its output rows; all must be wire. For one input combination, the grid starts
with no electrons, a head is placed on each active input cell at tick 0, and
the grid advances 60 ticks. An output's pulse count is the number of ticks
(0 to 60) at which its cell is a head. The run is quiet if no head or tail
remains after tick 60.

**Pass.** Every input combination gives exactly the truth table's pulse count
(0 or 1) on every output, and ends quiet.

**Robustness r.** Every combination with two or more active inputs is re-run
with one active input injected 1, 2 or 3 ticks late. r is the share of those
runs that end quiet with at most one pulse on each output. Two-input specs get
6 probes; the mux gets 27.

**NOT is strobed.** A quiet grid cannot hold a clock, so no circuit can pulse
on an all-zero input. NOT therefore takes a strobe S and a data input A and
must output S AND NOT A, tested on all four combinations. This is the one place
the spec library departs from the plan's wording.

**Cell count c.** All non-empty cells, port cells included. A T3 answer over
its cell budget fails.

**Composite scores.** T3 uses the plan's formula,
S = pass · (0.6 + 0.2 · c_min/c + 0.2 · r), with c_min/c capped at 1. A passing
design smaller than c_min is flagged `beats_best_known` so the database can be
updated. T2's primary scores are pass/fail and edit count, as in the plan; its
scalar `score` reuses the same shape with min_edits/edits in place of c_min/c.
That scalar is this testbed's addition, not the plan's.

**Format errors.** Answers are padded or trimmed to the grid size and every
repair is counted, as the plan requires. The count is broken down by kind
(`short_row`, `long_row`, `missing_row`, `extra_row`, `bad_char`,
`text_outside_fence`), because stripped trailing spaces are far more common
and far less interesting than real misalignment.

## Tasks

A task is a JSON object. Ground truth sits under `answer_key`;
`generate.public_view` strips it. Each task depends only on (seed, tier,
index), so changing the suite size does not change existing tasks.

**T1 Simulate.** A random circuit on a grid of 6-10 rows by 12-20 columns:
wandering tracks, rectangular loops, vertical links, a few stray cells, and 2
to 4 electrons. Step counts are stratified so each N from 1 to 20 appears
equally often. Instances whose activity has died out by step N are rejected.
Score: share of matching cells; `passed` means exact match. Conductor-cell
accuracy is reported too, since empty cells make plain cell accuracy start
high.

**T2 Repair.** A known-good circuit from the pool has 1, 2 or 3 cells flipped
(a wire deleted, or a wire added next to existing wire). An instance is kept
only if no repair with fewer flips exists, checked exhaustively, so `min_edits`
is exact rather than an upper bound. Geometries and flip counts are stratified.
Freshness here is statistical, not absolute: one-flip instances are drawn from
a finite space (pool size times roughly a hundred flips per geometry), so two
seeds can occasionally produce the same instance.

**T3 Design.** Six specs, two port geometries each, three cell budgets each
(1.15×, 1.5× and 2.5× c_min, rounded). T3 is fixed across seeds.

## Best-known sizes

`python -m spackle.search` anneals over wire layouts with cost
`penalty × truth-table errors + cell count`, then deletes cells greedily until
no single deletion still passes. The smallest design per geometry sets c_min;
up to forty of the smallest are kept as the T2 pool. Where annealing finds few
distinct designs (AND, half adder, mux), `--diversify` fills the pool with
neutral variants: random walks through layouts that still pass. Designs come
only from this search, never from published layouts.

c_min is a best-known value, not a proven minimum. For NOT, OR and XOR it
stopped moving after the first pass. For AND, the half adder and the mux it was
still dropping by a few cells per pass when the search was stopped, so expect
models to beat it occasionally. When one does, the scorer says so; add the
design with `search.merge` and regenerate. Changing c_min changes T3 budgets
and scores, so freeze `data/best_known.json` before the pilot and record the
suite hash in every run log.

## Using it from the harness (Phase 2)

```python
from spackle.generate import build_suite, public_view
from spackle.prompts import render, PROMPT_VERSION
from spackle.scoring import score

tasks = build_suite(seed=0)
text = render(tasks[0], encoding="ascii")        # same text for every condition
result = score(tasks[0], model_answer)           # score, passed, format_errors, trace
```

`result["trace"]` is the simulator feedback the supervisor reads each round.
`result["grid"]` is the repaired grid, ready to be cut into whole-row spans.

## Not in Phase 1

Model clients, `fill_span`, the loop, logging and budgets are Phase 2.
Difficulty calibration against real models is the pilot's job; the validator
only reports trivial-baseline floors.
