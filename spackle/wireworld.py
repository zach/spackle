"""Wireworld reference simulator and grid encodings.

This module is deliberately simple and slow: it is the ground truth that the
bitboard simulator in ``fastsim`` is cross-checked against.

Cell alphabet (plan.md, "Representation"):
    ' ' empty    '.' wire    'H' electron head    't' electron tail

Rules, applied to every cell simultaneously each tick:
    empty -> empty
    head  -> tail
    tail  -> wire
    wire  -> head if exactly 1 or 2 of its 8 neighbours are heads, else wire
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

EMPTY, WIRE, HEAD, TAIL = " ", ".", "H", "t"
ALPHABET = EMPTY + WIRE + HEAD + TAIL

Grid = list[str]  # equal-length rows over ALPHABET


def blank(rows: int, cols: int) -> Grid:
    return [EMPTY * cols for _ in range(rows)]


def dims(grid: Grid) -> tuple[int, int]:
    return len(grid), (len(grid[0]) if grid else 0)


def check_grid(grid: Grid) -> None:
    """Raise ValueError unless ``grid`` is rectangular and over the alphabet."""
    if not grid:
        raise ValueError("empty grid")
    width = len(grid[0])
    for r, row in enumerate(grid):
        if len(row) != width:
            raise ValueError(f"row {r} has width {len(row)}, expected {width}")
        bad = set(row) - set(ALPHABET)
        if bad:
            raise ValueError(f"row {r} has invalid characters {sorted(bad)}")


def step(grid: Grid) -> Grid:
    rows, cols = dims(grid)
    out = []
    for r in range(rows):
        new_row = []
        for c in range(cols):
            cell = grid[r][c]
            if cell == HEAD:
                new_row.append(TAIL)
            elif cell == TAIL:
                new_row.append(WIRE)
            elif cell == WIRE:
                heads = 0
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        rr, cc = r + dr, c + dc
                        if 0 <= rr < rows and 0 <= cc < cols and grid[rr][cc] == HEAD:
                            heads += 1
                new_row.append(HEAD if heads in (1, 2) else WIRE)
            else:
                new_row.append(EMPTY)
        out.append("".join(new_row))
    return out


def run(grid: Grid, n: int) -> Grid:
    for _ in range(n):
        grid = step(grid)
    return grid


def set_cell(grid: Grid, r: int, c: int, value: str) -> Grid:
    row = grid[r]
    out = list(grid)
    out[r] = row[:c] + value + row[c + 1 :]
    return out


def is_quiet(grid: Grid) -> bool:
    return not any(HEAD in row or TAIL in row for row in grid)


def count_cells(grid: Grid) -> int:
    """Number of non-empty cells (the plan's 'cell count')."""
    return sum(len(row) - row.count(EMPTY) for row in grid)


def hamming(a: Grid, b: Grid) -> int:
    if dims(a) != dims(b):
        raise ValueError("grids differ in shape")
    return sum(x != y for ra, rb in zip(a, b) for x, y in zip(ra, rb))


# --------------------------------------------------------------------------
# Text encodings
# --------------------------------------------------------------------------

def to_ascii(grid: Grid) -> str:
    return "\n".join(grid)


@dataclass
class ParseResult:
    """A grid recovered from model text, plus what had to be repaired.

    ``format_errors`` follows plan.md: every repair counts. The breakdown lets
    the harness separate benign repairs (stripped trailing spaces) from real
    misalignment.
    """

    grid: Grid
    repairs: dict[str, int] = field(default_factory=dict)

    @property
    def format_errors(self) -> int:
        return sum(self.repairs.values())

    def _bump(self, kind: str, n: int = 1) -> None:
        if n:
            self.repairs[kind] = self.repairs.get(kind, 0) + n


_FENCE = re.compile(r"```[^\n]*\n(.*?)\n?```", re.S)


def _strip_fence(text: str, res: ParseResult) -> str:
    m = _FENCE.search(text)
    if m:
        if text[: m.start()].strip() or text[m.end() :].strip():
            res._bump("text_outside_fence")
        return m.group(1)
    return text


def parse_ascii(text: str, rows: int, cols: int) -> ParseResult:
    """Parse an ASCII grid, padding/trimming to ``rows`` x ``cols``.

    Repairs recorded: ``short_row`` (row right-padded with spaces),
    ``long_row`` (row trimmed), ``missing_row``, ``extra_row``,
    ``bad_char`` (replaced by empty), ``text_outside_fence``.
    """
    res = ParseResult(grid=[])
    body = _strip_fence(text, res)
    lines = body.replace("\r\n", "\n").replace("\t", " ").split("\n")
    # A single trailing newline is not an error.
    if lines and lines[-1] == "" and len(lines) > rows:
        lines.pop()
    if len(lines) > rows:
        res._bump("extra_row", len(lines) - rows)
        lines = lines[:rows]
    if len(lines) < rows:
        res._bump("missing_row", rows - len(lines))
        lines = lines + [""] * (rows - len(lines))
    grid = []
    for line in lines:
        if len(line) < cols:
            res._bump("short_row")
            line = line.ljust(cols)
        elif len(line) > cols:
            res._bump("long_row")
            line = line[:cols]
        if set(line) - set(ALPHABET):
            cleaned = "".join(ch if ch in ALPHABET else EMPTY for ch in line)
            res._bump("bad_char", sum(a != b for a, b in zip(line, cleaned)))
            line = cleaned
        grid.append(line)
    res.grid = grid
    return res


_COORD_KEYS = {"wire": WIRE, "head": HEAD, "tail": TAIL}
_PAIR = re.compile(r"\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)")


def to_coords(grid: Grid) -> str:
    """Coordinate-list encoding (plan.md ablation): no column counting needed.

    size 3x5
    wire: (1,0) (1,1)
    head: (1,2)
    tail:
    """
    rows, cols = dims(grid)
    cells: dict[str, list[str]] = {k: [] for k in _COORD_KEYS}
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
            for key, sym in _COORD_KEYS.items():
                if ch == sym:
                    cells[key].append(f"({r},{c})")
    lines = [f"size {rows}x{cols}"]
    lines += [f"{key}: {' '.join(v)}".rstrip() for key, v in cells.items()]
    return "\n".join(lines)


def parse_coords(text: str, rows: int, cols: int) -> ParseResult:
    """Parse the coordinate-list encoding.

    Repairs recorded: ``out_of_bounds`` (dropped), ``duplicate_cell`` (last
    wins), ``unknown_line``, ``size_mismatch``, ``text_outside_fence``.
    """
    res = ParseResult(grid=[])
    body = _strip_fence(text, res)
    cells: dict[tuple[int, int], str] = {}
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("size"):
            m = re.search(r"(\d+)\s*x\s*(\d+)", low)
            if not m or (int(m.group(1)), int(m.group(2))) != (rows, cols):
                res._bump("size_mismatch")
            continue
        key, sep, rest = low.partition(":")
        key = key.strip()
        if not sep or key not in _COORD_KEYS:
            res._bump("unknown_line")
            continue
        for m in _PAIR.finditer(rest):
            r, c = int(m.group(1)), int(m.group(2))
            if not (0 <= r < rows and 0 <= c < cols):
                res._bump("out_of_bounds")
                continue
            if (r, c) in cells:
                res._bump("duplicate_cell")
            cells[(r, c)] = _COORD_KEYS[key]
    res.grid = [
        "".join(cells.get((r, c), EMPTY) for c in range(cols)) for r in range(rows)
    ]
    return res


ENCODINGS = ("ascii", "coords")


def encode(grid: Grid, encoding: str = "ascii") -> str:
    if encoding == "ascii":
        return to_ascii(grid)
    if encoding == "coords":
        return to_coords(grid)
    raise ValueError(f"unknown encoding {encoding!r}")


def parse(text: str, rows: int, cols: int, encoding: str = "ascii") -> ParseResult:
    if encoding == "ascii":
        return parse_ascii(text, rows, cols)
    if encoding == "coords":
        return parse_coords(text, rows, cols)
    raise ValueError(f"unknown encoding {encoding!r}")
