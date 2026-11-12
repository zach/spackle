"""Task statements. One text per (task, encoding), identical across conditions
(plan.md, "Fairness checks"). ``PROMPT_VERSION`` is a hash of the templates and
belongs in every run-log record.
"""

from __future__ import annotations

import hashlib

from .specs import GEOMETRIES, SPECS
from .wireworld import encode

RULES = """\
Wireworld is a cellular automaton on a grid. Each cell is empty, wire, an \
electron head or an electron tail. Every tick, all cells update at once:
- empty stays empty
- head becomes tail
- tail becomes wire
- wire becomes head if exactly 1 or 2 of its 8 neighbours (including diagonals) \
are heads; otherwise it stays wire"""

FORMAT = {
    "ascii": """\
Grids are written as {rows} lines of exactly {cols} characters: space = empty, \
'.' = wire, 'H' = head, 't' = tail. Rows are numbered from 0 at the top, columns \
from 0 at the left. Leading and trailing spaces are significant.""",
    "coords": """\
Grids are written as coordinate lists on a {rows}x{cols} grid: a line \
'size {rows}x{cols}', then lines 'wire:', 'head:' and 'tail:' each followed by \
(row,col) pairs. Rows are numbered from 0 at the top, columns from 0 at the left. \
Cells not listed are empty.""",
}

ANSWER = "Reply with the complete grid in the same format inside one ``` code block."

CIRCUIT = """\
Ports: {ports}. Input cells are in column 0 and output cells in column {last}; \
all must be wire.
Test: for every combination of inputs, the grid starts with no electrons, a head \
is placed on each active input cell at tick 0, and the grid runs for 60 ticks. \
An output "pulses" each tick its cell is a head.
Required behaviour ({blurb}):
{table}
Each output must pulse exactly the stated number of times, and no head or tail \
may remain after 60 ticks. The design may contain only wire and empty cells.
Designs are also probed for timing robustness: cases with two or more active \
inputs are re-run with one input delayed by 1 to 3 ticks, and should still end \
quiet with at most one pulse per output."""

TASK = {
    "T1": "Task: give the grid after exactly {steps} tick{s}.\n\n{grid}",
    "T2": ("Task: the circuit below is broken: {flips_hint} cells were flipped "
           "between wire and empty. Repair it so it meets the required behaviour, "
           "changing as few cells as possible.\n\n{grid}"),
    "T3": ("Task: design a circuit on a {rows}x{cols} grid that meets the required "
           "behaviour using at most {budget} wire cells. Fewer cells and better "
           "timing robustness score higher."),
}

PROMPT_VERSION = hashlib.sha256(
    repr((RULES, sorted(FORMAT.items()), ANSWER, CIRCUIT, sorted(TASK.items()))).encode()
).hexdigest()[:12]


def _circuit_block(task: dict) -> str:
    geom = GEOMETRIES[task["geometry"]]
    spec = SPECS[geom.spec]
    ports = ", ".join(
        [f"input {n} at row {r}" for n, r in zip(spec.inputs, geom.in_rows)]
        + [f"output {n} at row {r}" for n, r in zip(spec.outputs, geom.out_rows)])
    table = "\n".join(
        "  " + " ".join(f"{n}={b}" for n, b in zip(spec.inputs, bits)) + " -> "
        + ", ".join(f"{n} pulses {o}x" for n, o in zip(spec.outputs, outs))
        for bits, outs in spec.table())
    return CIRCUIT.format(ports=ports, last=geom.cols - 1, blurb=spec.blurb, table=table)


def render(task: dict, encoding: str = "ascii") -> str:
    """Task statement shown to every model. Never includes the answer key."""
    parts = [RULES, FORMAT[encoding].format(rows=task["rows"], cols=task["cols"])]
    tier = task["tier"]
    if tier == "T1":
        parts.append(TASK["T1"].format(steps=task["steps"], s="" if task["steps"] == 1 else "s",
                                       grid=encode(task["grid"], encoding)))
    elif tier == "T2":
        parts.append(_circuit_block(task))
        parts.append(TASK["T2"].format(flips_hint="1 to 3",
                                       grid=encode(task["grid"], encoding)))
    else:
        parts.append(_circuit_block(task))
        parts.append(TASK["T3"].format(rows=task["rows"], cols=task["cols"],
                                       budget=task["cell_budget"]))
    parts.append(ANSWER)
    return "\n\n".join(parts)
