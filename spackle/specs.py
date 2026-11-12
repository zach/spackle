"""T3 spec library: truth tables and port geometries.

A *spec* is a boolean function. A *geometry* fixes the grid size and the edge
rows the ports sit on. Inputs are cells in column 0, outputs are cells in the
last column (plan.md: "Inputs enter on fixed left-edge rows; outputs leave on
fixed right-edge rows").

NOT needs a note. The plan requires a quiet grid before and after each run, so
a circuit cannot contain a clock, and an all-zero input can never produce an
output pulse. NOT is therefore specified in its strobed form: a strobe input S
that is asserted whenever the gate is read, and Y = S AND NOT A. All four input
combinations are tested, including S=0 (which must stay silent).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Callable


@dataclass(frozen=True)
class Spec:
    name: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    fn: Callable[..., tuple[int, ...]]
    blurb: str

    def table(self) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
        return [(bits, tuple(self.fn(*bits)))
                for bits in product((0, 1), repeat=len(self.inputs))]


SPECS: dict[str, Spec] = {s.name: s for s in [
    Spec("NOT", ("S", "A"), ("Y",), lambda s, a: (s & (1 - a),),
         "strobed NOT: Y pulses when strobe S arrives and A does not"),
    Spec("AND", ("A", "B"), ("Y",), lambda a, b: (a & b,), "Y = A AND B"),
    Spec("OR", ("A", "B"), ("Y",), lambda a, b: (a | b,), "Y = A OR B"),
    Spec("XOR", ("A", "B"), ("Y",), lambda a, b: (a ^ b,), "Y = A XOR B"),
    Spec("HALF_ADDER", ("A", "B"), ("SUM", "CARRY"),
         lambda a, b: (a ^ b, a & b), "SUM = A XOR B, CARRY = A AND B"),
    Spec("MUX2", ("SEL", "A", "B"), ("Y",),
         lambda s, a, b: (b if s else a,), "Y = A when SEL=0, B when SEL=1"),
]}


@dataclass(frozen=True)
class Geometry:
    spec: str
    name: str
    rows: int
    cols: int
    in_rows: tuple[int, ...]
    out_rows: tuple[int, ...]

    @property
    def key(self) -> str:
        return f"{self.spec}/{self.name}"


# Two geometries per spec: the best-known size differs between them, so the
# same truth table yields distinct T3 tasks (and distinct T2 circuit pools).
GEOMETRIES: dict[str, Geometry] = {g.key: g for g in [
    Geometry("NOT", "a", 7, 11, (1, 5), (3,)),
    Geometry("NOT", "b", 9, 13, (2, 6), (2,)),
    Geometry("AND", "a", 7, 11, (1, 5), (3,)),
    Geometry("AND", "b", 9, 13, (2, 6), (2,)),
    Geometry("OR", "a", 7, 11, (1, 5), (3,)),
    Geometry("OR", "b", 9, 13, (2, 6), (2,)),
    Geometry("XOR", "a", 7, 11, (1, 5), (3,)),
    Geometry("XOR", "b", 9, 13, (2, 6), (2,)),
    Geometry("HALF_ADDER", "a", 9, 13, (2, 6), (2, 6)),
    Geometry("HALF_ADDER", "b", 11, 15, (3, 7), (2, 8)),
    Geometry("MUX2", "a", 11, 15, (1, 5, 9), (5,)),
    Geometry("MUX2", "b", 11, 17, (2, 5, 8), (3,)),
]}


def truth_scenarios(spec: Spec):
    """Scenarios (injection tick per input, or None) for every input combo."""
    return [tuple(0 if b else None for b in bits) for bits, _ in spec.table()]


def timing_scenarios(spec: Spec, delays=(1, 2, 3)):
    """Robustness probes (plan.md): every combination with at least two active
    inputs is re-run with one active input delayed by 1..3 ticks."""
    out = []
    for bits, _ in spec.table():
        if sum(bits) < 2:
            continue
        for i, b in enumerate(bits):
            if not b:
                continue
            for d in delays:
                out.append(tuple((d if j == i else 0) if x else None
                                 for j, x in enumerate(bits)))
    return out
