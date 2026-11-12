"""Scoring of model answers for all three tiers.

``score(task, answer_text)`` is the single entry point the harness needs. It
parses the answer (repairing row widths and counting each repair as a format
error, per plan.md), runs the simulator and returns a flat dict:

    score          primary scalar in [0, 1]
    passed         bool
    format_errors  int, with ``repairs`` giving the breakdown
    trace          simulator feedback text (same format in every condition)

T1  score = share of cells matching the grid after N steps; passed = exact.
T2  passed = repaired circuit meets the truth table. The plan's primary scores
    are pass/fail and edit count; ``score`` folds them together as
    pass * (0.6 + 0.2 * min_edits/edits + 0.2 * r), mirroring the T3 formula.
T3  score = S = pass * (0.6 + 0.2 * c_min/c + 0.2 * r); pass also requires
    c <= cell budget. c_min/c is capped at 1; a design smaller than the
    best-known one is flagged ``beats_best_known`` so the database can be
    updated.
"""

from __future__ import annotations

from .circuit import evaluate, trace
from .specs import GEOMETRIES
from .wireworld import EMPTY, hamming, parse


def composite(passed: bool, size_ratio: float, robustness: float) -> float:
    """plan.md: S = pass * (0.6 + 0.2 * c_min/c + 0.2 * r)."""
    if not passed:
        return 0.0
    return 0.6 + 0.2 * min(1.0, size_ratio) + 0.2 * robustness


def score(task: dict, answer: str, encoding: str = "ascii") -> dict:
    parsed = parse(answer, task["rows"], task["cols"], encoding)
    out = {"task_id": task["id"], "tier": task["tier"], "encoding": encoding,
           "format_errors": parsed.format_errors, "repairs": dict(parsed.repairs),
           "grid": parsed.grid}
    out.update(score_grid(task, parsed.grid))
    return out


def score_grid(task: dict, grid) -> dict:
    return {"T1": _score_t1, "T2": _score_t2, "T3": _score_t3}[task["tier"]](task, grid)


def _score_t1(task: dict, grid) -> dict:
    target = task["answer_key"]["target"]
    total = task["rows"] * task["cols"]
    wrong = hamming(grid, target)
    # Secondary: accuracy on conductor cells only (empty cells are free points).
    live = [(r, c) for r, row in enumerate(target) for c, ch in enumerate(row) if ch != EMPTY]
    live_ok = sum(grid[r][c] == target[r][c] for r, c in live)
    wrong_rows = [r for r in range(task["rows"]) if grid[r] != target[r]]
    return {
        "score": 1 - wrong / total, "passed": wrong == 0,
        "cell_accuracy": 1 - wrong / total,
        "conductor_accuracy": live_ok / len(live) if live else 1.0,
        "wrong_cells": wrong,
        "trace": "exact match" if not wrong else
                 f"{wrong} of {total} cells wrong; rows with errors: {wrong_rows}",
    }


def _score_t2(task: dict, grid) -> dict:
    geom = GEOMETRIES[task["geometry"]]
    rep = evaluate(grid, geom)
    edits = hamming(grid, task["grid"])
    min_edits = task["answer_key"]["min_edits"]
    passed = rep.valid and rep.passed
    ratio = min_edits / edits if edits else 0.0
    return {
        "score": composite(passed, ratio, rep.robustness), "passed": passed,
        "valid": rep.valid, "edits": edits, "min_edits": min_edits,
        "cells": rep.cells, "robustness": rep.robustness,
        "truth_ok": rep.truth_ok, "truth_total": len(rep.truth),
        "trace": f"edits={edits}\n" + trace(rep, geom),
    }


def _score_t3(task: dict, grid) -> dict:
    geom = GEOMETRIES[task["geometry"]]
    rep = evaluate(grid, geom)
    c_min, budget = task["answer_key"]["c_min"], task["cell_budget"]
    within = rep.valid and rep.cells <= budget
    passed = rep.valid and rep.passed and within
    text = trace(rep, geom)
    if rep.valid and not within:
        text += f"\nOVER BUDGET: {rep.cells} cells > budget {budget}"
    return {
        "score": composite(passed, c_min / rep.cells if rep.cells else 0.0, rep.robustness),
        "passed": passed, "valid": rep.valid, "truth_passed": rep.valid and rep.passed,
        "within_budget": within, "cells": rep.cells, "cell_budget": budget, "c_min": c_min,
        "robustness": rep.robustness,
        "truth_ok": rep.truth_ok, "truth_total": len(rep.truth),
        "beats_best_known": passed and rep.cells < c_min,
        "trace": text,
    }
