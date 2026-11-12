"""Seeded task generators for the three tiers (plan.md, "Benchmark spec").

A task is a JSON-serialisable dict. Everything a model may see sits at the top
level; ground truth sits under ``answer_key`` and must be stripped before a
task is shown to a model (``public_view``).

T1 and T2 draw fresh instances from the seed, so a new seed gives a new suite.
T3 specs are fixed; their best-known sizes come from ``data/best_known.json``,
produced by ``spackle.search``.
"""

from __future__ import annotations

import hashlib
import json
import random
from itertools import combinations

from . import fastsim
from .circuit import evaluate, harnesses
from .search import load as load_best_known
from .specs import GEOMETRIES, SPECS
from .wireworld import EMPTY, HEAD, TAIL, WIRE, Grid, blank, count_cells, set_cell

GENERATOR_VERSION = "1"
T3_BUDGET_FACTORS = (1.15, 1.5, 2.5)
NEIGHBOURS = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc]


def public_view(task: dict) -> dict:
    return {k: v for k, v in task.items() if k != "answer_key"}


def _rng(seed: int, *parts) -> random.Random:
    """Independent stream per (seed, tier, index): tasks do not shift when the
    suite size changes."""
    h = hashlib.sha256(repr((GENERATOR_VERSION, seed) + parts).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


# --------------------------------------------------------------------------
# T1 Simulate
# --------------------------------------------------------------------------

def _wire_neighbours(cells: set, r: int, c: int):
    return [(r + dr, c + dc) for dr, dc in NEIGHBOURS if (r + dr, c + dc) in cells]


def random_circuit(rng: random.Random, rows: int, cols: int) -> Grid:
    """A random wire network with a few electrons on it.

    Wandering left-to-right tracks, rectangular loops (which keep electrons
    circulating), vertical links (junctions) and a few stray cells (blobs,
    where the 1-or-2-heads rule bites).
    """
    cells: set[tuple[int, int]] = set()
    for _ in range(rng.randint(2, 4)):                       # tracks
        r = rng.randrange(rows)
        c0 = rng.randrange(0, cols // 3)
        c1 = rng.randrange(2 * cols // 3, cols)
        for c in range(c0, c1 + 1):
            cells.add((r, c))
            if rng.random() < 0.25:
                r = min(rows - 1, max(0, r + rng.choice((-1, 1))))
    for _ in range(rng.randint(1, 2)):                       # loops
        h, w = rng.randint(3, min(5, rows)), rng.randint(3, 7)
        r0, c0 = rng.randrange(rows - h + 1), rng.randrange(cols - w + 1)
        for r in range(r0, r0 + h):
            for c in range(c0, c0 + w):
                edge_r, edge_c = r in (r0, r0 + h - 1), c in (c0, c0 + w - 1)
                if (edge_r or edge_c) and not (edge_r and edge_c):
                    cells.add((r, c))
    for _ in range(rng.randint(1, 3)):                       # vertical links
        c = rng.randrange(cols)
        r0 = rng.randrange(rows - 1)
        for r in range(r0, min(rows, r0 + rng.randint(2, 4))):
            cells.add((r, c))
    for _ in range(rng.randint(0, 4)):                       # stray cells
        r, c = rng.choice(sorted(cells))
        dr, dc = rng.choice(NEIGHBOURS)
        if 0 <= r + dr < rows and 0 <= c + dc < cols:
            cells.add((r + dr, c + dc))

    grid = blank(rows, cols)
    for r, c in cells:
        grid = set_cell(grid, r, c, WIRE)
    order = sorted(cells)
    rng.shuffle(order)
    used: set = set()
    wanted = rng.randint(2, 4)
    for r, c in order:
        if wanted == 0:
            break
        if (r, c) in used:
            continue
        nbrs = [n for n in _wire_neighbours(cells, r, c) if n not in used]
        if not nbrs:
            continue
        grid = set_cell(grid, r, c, HEAD)
        used.add((r, c))
        if rng.random() < 0.85:                              # usually a directed electron
            tr, tc = rng.choice(nbrs)
            grid = set_cell(grid, tr, tc, TAIL)
            used.add((tr, tc))
        wanted -= 1
    return grid


def gen_t1(seed: int, index: int, steps: int | None = None) -> dict:
    rng = _rng(seed, "T1", index)
    if steps is None:
        steps = 1 + index % 20        # stratified: every N in 1..20 equally often
    while True:
        rows, cols = rng.randint(6, 10), rng.randint(12, 20)
        grid = random_circuit(rng, rows, cols)
        target = fastsim.run_grid(grid, steps)
        # Reject instances that die out: the answer must still carry signal.
        if any(HEAD in row for row in target) and target != grid:
            break
    return {
        "id": f"T1-s{seed}-{index:03d}", "tier": "T1", "seed": seed,
        "rows": rows, "cols": cols, "grid": grid, "steps": steps,
        "answer_key": {"target": target},
    }


# --------------------------------------------------------------------------
# T2 Repair
# --------------------------------------------------------------------------

def _free_cells(geom):
    ports = {(r, 0) for r in geom.in_rows} | {(r, geom.cols - 1) for r in geom.out_rows}
    return [(r, c) for r in range(geom.rows) for c in range(geom.cols)
            if (r, c) not in ports]


def _passes(K: int, geom) -> bool:
    truth_h, _, expected = harnesses(geom)
    pulses, quiet = truth_h.run(K)
    return all(quiet) and all(tuple(p) == e for p, e in zip(pulses, expected))


def min_repair_edits(grid: Grid, geom, limit: int) -> int | None:
    """Smallest number of cell flips (wire<->empty, ports excluded) that makes
    ``grid`` pass, searching exhaustively up to ``limit`` flips."""
    lay = harnesses(geom)[0].lay
    K = lay.pack(grid)[0]
    bits = [lay.bit(r, c) for r, c in _free_cells(geom)]
    for k in range(0, limit + 1):
        for combo in combinations(bits, k):
            K2 = K
            for b in combo:
                K2 ^= b
            if _passes(K2, geom):
                return k
    return None


def break_circuit(rng: random.Random, good: Grid, geom, flips: int) -> Grid:
    """Flip ``flips`` cells: delete a wire, or add one next to existing wire."""
    cells = _free_cells(geom)
    grid = good
    chosen: set = set()
    while len(chosen) < flips:
        r, c = rng.choice(cells)
        if (r, c) in chosen:
            continue
        if grid[r][c] == WIRE:
            new = EMPTY
        else:
            touching = any(0 <= r + dr < geom.rows and 0 <= c + dc < geom.cols
                           and good[r + dr][c + dc] == WIRE for dr, dc in NEIGHBOURS)
            if not touching:
                continue
            new = WIRE
        grid = set_cell(grid, r, c, new)
        chosen.add((r, c))
    return grid


def gen_t2(seed: int, index: int, db: dict | None = None) -> dict:
    db = load_best_known() if db is None else db
    rng = _rng(seed, "T2", index)
    keys = sorted(k for k in GEOMETRIES if db.get(k, {}).get("designs"))
    if not keys:
        raise RuntimeError("no known-good circuits: run `python -m spackle.search` first")
    key = keys[index % len(keys)]
    geom = GEOMETRIES[key]
    flips = 1 + (index // len(keys)) % 3   # stratified over 1..3 flips
    while True:
        design = rng.choice(db[key]["designs"])
        good = design["grid"]
        broken = break_circuit(rng, good, geom, flips)
        # The instance is kept only if it is broken and cannot be repaired
        # with fewer edits than were made, so min_edits is exact.
        if min_repair_edits(broken, geom, flips - 1) is None:
            break
    return {
        "id": f"T2-s{seed}-{index:03d}", "tier": "T2", "seed": seed,
        "geometry": key, "spec": geom.spec, "rows": geom.rows, "cols": geom.cols,
        "in_rows": list(geom.in_rows), "out_rows": list(geom.out_rows),
        "grid": broken,
        "answer_key": {"reference": good, "min_edits": flips,
                       "reference_cells": count_cells(good),
                       "reference_robustness": evaluate(good, geom).robustness},
    }


# --------------------------------------------------------------------------
# T3 Design
# --------------------------------------------------------------------------

def gen_t3(db: dict | None = None) -> list[dict]:
    db = load_best_known() if db is None else db
    tasks = []
    for key in sorted(GEOMETRIES):
        entry = db.get(key)
        if not entry or not entry.get("c_min"):
            continue
        geom = GEOMETRIES[key]
        c_min = entry["c_min"]
        budgets = sorted({max(c_min + 1, int(c_min * f + 0.5)) for f in T3_BUDGET_FACTORS})
        for budget in budgets:
            tasks.append({
                "id": f"T3-{key.replace('/', '-')}-b{budget}", "tier": "T3", "seed": None,
                "geometry": key, "spec": geom.spec, "rows": geom.rows, "cols": geom.cols,
                "in_rows": list(geom.in_rows), "out_rows": list(geom.out_rows),
                "cell_budget": budget,
                "answer_key": {"c_min": c_min, "best_known": entry["designs"][0]["grid"]},
            })
    return tasks


# --------------------------------------------------------------------------
# Suite
# --------------------------------------------------------------------------

def build_suite(seed: int = 0, n_t1: int = 40, n_t2: int = 40, db: dict | None = None) -> list[dict]:
    db = load_best_known() if db is None else db
    tasks = [gen_t1(seed, i) for i in range(n_t1)]
    tasks += [gen_t2(seed, i, db) for i in range(n_t2)]
    tasks += gen_t3(db)
    return tasks


def suite_hash(tasks: list[dict]) -> str:
    blob = json.dumps(tasks, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def dump_jsonl(tasks: list[dict], path) -> None:
    with open(path, "w") as f:
        for t in tasks:
            f.write(json.dumps(t, sort_keys=True) + "\n")


def load_jsonl(path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]
