"""Bitboard Wireworld simulator.

Grids are packed into Python integers so that many scenarios (input
combinations, timing perturbations) advance in one pass of big-int operations.
Each scenario occupies a (rows+2) x (cols+2) padded block; the padding is never
wire, so shifted neighbour masks cannot leak between rows or scenarios.

Cross-checked against ``wireworld.step`` in tests and in the Gate 1 validator.
"""

from __future__ import annotations

from .wireworld import EMPTY, HEAD, TAIL, WIRE, Grid

MAX_STEPS = 60  # plan.md: "inject electrons, run 60 steps"


class Layout:
    def __init__(self, rows: int, cols: int):
        self.rows, self.cols = rows, cols
        self.W = cols + 2
        self.S = (rows + 2) * self.W  # bits per scenario block

    def bit(self, r: int, c: int, k: int = 0) -> int:
        return 1 << (k * self.S + (r + 1) * self.W + c + 1)

    def rep(self, n: int) -> int:
        """Multiplier that copies a one-block mask into ``n`` blocks."""
        return sum(1 << (k * self.S) for k in range(n))

    def block(self, k: int) -> int:
        return ((1 << self.S) - 1) << (k * self.S)

    def pack(self, grid: Grid) -> tuple[int, int, int]:
        """Return (conductors, heads, tails) masks for one block."""
        K = H = T = 0
        for r, row in enumerate(grid):
            for c, ch in enumerate(row):
                if ch == EMPTY:
                    continue
                b = self.bit(r, c)
                K |= b
                if ch == HEAD:
                    H |= b
                elif ch == TAIL:
                    T |= b
        return K, H, T

    def unpack(self, K: int, H: int = 0, T: int = 0, k: int = 0) -> Grid:
        out = []
        for r in range(self.rows):
            row = []
            for c in range(self.cols):
                b = self.bit(r, c, k)
                if H & b:
                    row.append(HEAD)
                elif T & b:
                    row.append(TAIL)
                elif K & b:
                    row.append(WIRE)
                else:
                    row.append(EMPTY)
            out.append("".join(row))
        return out


def step_bits(K: int, H: int, T: int, W: int) -> tuple[int, int]:
    """One tick. K = all conductor cells; H, T subsets of K. Returns (H', T')."""
    ones = twos = more = 0
    for n in (H << 1, H >> 1, H << W, H >> W,
              H << (W + 1), H >> (W + 1), H << (W - 1), H >> (W - 1)):
        carry = ones & n
        ones ^= n
        more |= twos & carry
        twos ^= carry
    return (ones ^ twos) & ~more & K & ~(H | T), H


def run_grid(grid: Grid, n: int) -> Grid:
    """Advance an arbitrary grid ``n`` ticks (T1 ground truth, fast path)."""
    lay = Layout(len(grid), len(grid[0]))
    K, H, T = lay.pack(grid)
    for _ in range(n):
        H, T = step_bits(K, H, T, lay.W)
    return lay.unpack(K, H, T)


Scenario = tuple  # per input: injection tick (int) or None for "not driven"


class Harness:
    """Runs a wire-only design under a batch of injection scenarios."""

    def __init__(self, rows: int, cols: int, in_rows, out_rows, scenarios,
                 max_steps: int = MAX_STEPS):
        self.lay = lay = Layout(rows, cols)
        self.in_rows, self.out_rows = tuple(in_rows), tuple(out_rows)
        self.scenarios = [tuple(s) for s in scenarios]
        self.max_steps = max_steps
        n = len(self.scenarios)
        self.rep = lay.rep(n)
        self.inject: dict[int, int] = {}
        for k, scen in enumerate(self.scenarios):
            for i, tick in enumerate(scen):
                if tick is not None:
                    self.inject[tick] = self.inject.get(tick, 0) | lay.bit(in_rows[i], 0, k)
        self.last_inject = max(self.inject, default=0)
        self.out_bits = [[lay.bit(r, cols - 1, k) for r in out_rows] for k in range(n)]
        self.out_mask = sum(b for bits in self.out_bits for b in bits)
        self.blocks = [lay.block(k) for k in range(n)]
        self.port_mask = sum(lay.bit(r, 0) for r in in_rows) | sum(
            lay.bit(r, cols - 1) for r in out_rows)

    def run(self, K1: int):
        """K1: one-block conductor mask. Returns (pulses, quiet).

        pulses[k][j] = ticks (0..max_steps) at which output j is a head in
        scenario k; quiet[k] = no head or tail remains after max_steps.
        """
        K = K1 * self.rep
        W = self.lay.W
        n = len(self.scenarios)
        pulses = [[0] * len(self.out_rows) for _ in range(n)]
        H = self.inject.get(0, 0) & K
        T = 0
        t = 0
        while True:
            hit = H & self.out_mask
            if hit:
                for k in range(n):
                    for j, b in enumerate(self.out_bits[k]):
                        if hit & b:
                            pulses[k][j] += 1
            if t == self.max_steps:
                break
            if not (H | T) and t >= self.last_inject:
                break
            H, T = step_bits(K, H, T, W)
            t += 1
            inj = self.inject.get(t)
            if inj:
                inj &= K
                H |= inj
                T &= ~inj
        live = H | T
        quiet = [not (live & blk) for blk in self.blocks]
        return pulses, quiet
