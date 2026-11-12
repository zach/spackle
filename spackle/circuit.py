"""Exact evaluation of a wire-only circuit against a spec (T2 and T3).

Correctness (plan.md): for each input combination, inject electrons at tick 0,
run 60 steps, count output pulses. A pass needs the exact pulse count for every
combination and a quiet grid at the end.

Robustness: re-run the multi-input-active cases with one input delayed 1 to 3
ticks; score = share of perturbed runs that end quiet with at most one pulse on
each output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from .fastsim import Harness
from .specs import GEOMETRIES, SPECS, Geometry, timing_scenarios, truth_scenarios
from .wireworld import EMPTY, WIRE, Grid, count_cells


@dataclass
class Case:
    inputs: tuple            # injection tick per input, None = not driven
    expected: tuple | None   # expected pulses per output (None for timing probes)
    pulses: tuple
    quiet: bool
    ok: bool


@dataclass
class CircuitReport:
    valid: bool
    errors: list[str] = field(default_factory=list)
    cells: int = 0
    passed: bool = False
    robustness: float = 0.0
    truth: list[Case] = field(default_factory=list)
    timing: list[Case] = field(default_factory=list)

    @property
    def truth_ok(self) -> int:
        return sum(c.ok for c in self.truth)


@lru_cache(maxsize=None)
def harnesses(geom: Geometry) -> tuple[Harness, Harness, tuple]:
    spec = SPECS[geom.spec]
    truth = Harness(geom.rows, geom.cols, geom.in_rows, geom.out_rows,
                    truth_scenarios(spec))
    timing = Harness(geom.rows, geom.cols, geom.in_rows, geom.out_rows,
                     timing_scenarios(spec))
    expected = tuple(out for _, out in spec.table())
    return truth, timing, expected


def structural_errors(grid: Grid, geom: Geometry) -> list[str]:
    errs = []
    if len(grid) != geom.rows or any(len(row) != geom.cols for row in grid):
        return [f"grid must be {geom.rows}x{geom.cols}"]
    for r, row in enumerate(grid):
        if set(row) - {EMPTY, WIRE}:
            errs.append(f"row {r}: a design may contain only wire and empty cells")
    for name, r in zip(SPECS[geom.spec].inputs, geom.in_rows):
        if grid[r][0] != WIRE:
            errs.append(f"input {name}: cell (row {r}, col 0) must be wire")
    for name, r in zip(SPECS[geom.spec].outputs, geom.out_rows):
        if grid[r][geom.cols - 1] != WIRE:
            errs.append(f"output {name}: cell (row {r}, col {geom.cols - 1}) must be wire")
    return errs


def evaluate_mask(K: int, geom: Geometry, want_timing: bool = True):
    """Evaluate a packed design. Returns (passed, robustness, truth, timing)."""
    truth_h, timing_h, expected = harnesses(geom)
    pulses, quiet = truth_h.run(K)
    truth = [Case(s, e, tuple(p), q, q and tuple(p) == e)
             for s, e, p, q in zip(truth_h.scenarios, expected, pulses, quiet)]
    passed = all(c.ok for c in truth)
    timing: list[Case] = []
    robustness = 0.0
    if want_timing:
        pulses, quiet = timing_h.run(K)
        timing = [Case(s, None, tuple(p), q, q and max(p) <= 1)
                  for s, p, q in zip(timing_h.scenarios, pulses, quiet)]
        robustness = sum(c.ok for c in timing) / len(timing) if timing else 1.0
    return passed, robustness, truth, timing


def evaluate(grid: Grid, geom: Geometry | str) -> CircuitReport:
    if isinstance(geom, str):
        geom = GEOMETRIES[geom]
    errs = structural_errors(grid, geom)
    if errs:
        return CircuitReport(valid=False, errors=errs)
    K, _, _ = harnesses(geom)[0].lay.pack(grid)
    passed, robustness, truth, timing = evaluate_mask(K, geom)
    return CircuitReport(True, [], count_cells(grid), passed, robustness, truth, timing)


def _fmt_inputs(names, scen) -> str:
    parts = []
    for n, tick in zip(names, scen):
        if tick is None:
            parts.append(f"{n}=0")
        elif tick == 0:
            parts.append(f"{n}=1")
        else:
            parts.append(f"{n}=1(+{tick})")
    return " ".join(parts)


def trace(report: CircuitReport, geom: Geometry | str) -> str:
    """Compact simulator feedback, identical in format for every condition."""
    if isinstance(geom, str):
        geom = GEOMETRIES[geom]
    spec = SPECS[geom.spec]
    if not report.valid:
        return "INVALID\n" + "\n".join(f"- {e}" for e in report.errors)
    lines = [f"cells={report.cells} truth={report.truth_ok}/{len(report.truth)} "
             f"{'PASS' if report.passed else 'FAIL'} robustness={report.robustness:.2f}"]
    for label, cases in (("truth", report.truth), ("timing", report.timing)):
        for c in cases:
            got = " ".join(f"{n}={p}" for n, p in zip(spec.outputs, c.pulses))
            line = f"{label}: {_fmt_inputs(spec.inputs, c.inputs)} -> {got}"
            if c.expected is not None:
                line += " (want " + " ".join(
                    f"{n}={e}" for n, e in zip(spec.outputs, c.expected)) + ")"
            if not c.quiet:
                line += " NOT-QUIET"
            lines.append(line + ("  ok" if c.ok else "  FAIL"))
    return "\n".join(lines)
