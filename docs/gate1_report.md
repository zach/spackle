# Gate 1 report: generator validated

Suite seed 0: 116 tasks (40 T1, 40 T2, 36 T3), suite hash `2c5deb0b5bad2722`. Regenerate with `python -m spackle.validate --seed 0`.

**Result: PASS** (39/39 checks)

## 1. Simulator

- PASS: an electron on a straight wire moves one cell per tick and leaves the grid quiet
- PASS: a wire fires on 1 or 2 neighbouring heads and not on 3
- PASS: bitboard simulator equals reference simulator on 2000 random grids (0-25 ticks)
- PASS: batched scenario runner (injection, pulse counts, quiet test) equals the reference on 680 truth-table and timing runs

## 2. Determinism and freshness

- PASS: same seed gives a byte-identical suite in-process
- PASS: same seed gives the same suite hash in fresh processes with different PYTHONHASHSEED
- PASS: T1: a different seed shares no instance with this suite
- PASS: T2: a different seed shares no instance with this suite
- PASS: T3 specs are fixed across seeds
- PASS: each task depends only on (seed, tier, index), not on suite size

## 3. T1 Simulate (40 tasks)

- PASS: every stored target equals the reference simulator's output
- PASS: step counts cover 1..20 (found 1..20, 20 distinct)
- PASS: no duplicate instances
- PASS: every target still carries at least one electron head
- PASS: the true answer scores 1.0 with no format errors
- PASS: copying the input never passes

## 4. T2 Repair (40 tasks)

- PASS: every broken circuit fails its truth table (reference simulator)
- PASS: every reference repair passes (reference simulator)
- PASS: the reference repair is exactly min_edits cells away
- PASS: exhaustive search finds no repair with fewer than min_edits edits (min_edits is exact)
- PASS: flip counts cover 1..3 (found [1, 2, 3])
- PASS: all 6 specs appear (12 geometries)
- PASS: no duplicate instances
- PASS: returning the broken circuit unchanged scores 0
- PASS: the reference repair passes with edits == min_edits

## 5. T3 Design (36 tasks)

- PASS: every spec geometry has a best-known design (12 geometries, 6 specs)
- PASS: all six specs have tasks
- PASS: every stored design passes on the reference simulator; c_min is the pool minimum
- PASS: no best-known design can lose a single cell and still pass
- PASS: every cell budget exceeds c_min, so every task is solvable
- PASS: the best-known design scores S = 0.8 + 0.2 r, matching the plan's formula
- PASS: trivial designs (empty grid, straight wires) score 0
- PASS: a correct design over the cell budget fails

## 6. Scoring, encodings and prompts

- PASS: ASCII and coordinate-list encodings round-trip every task grid with no format errors
- PASS: an answer scores the same in either encoding
- PASS: rows with stripped trailing spaces are padded, scored, and counted as format errors
- PASS: a non-grid answer is scored (not a crash), fails, and reports format errors
- PASS: task statements never contain the answer key (prompt version 5494b75afc98)
- PASS: the robustness probe separates designs that pass the same truth table: of 1373 passing variants of the best-known designs, 13% have r < 1 (lowest r = 0.50)

## Calibration notes (not gating)

- T1 copy-the-input baseline: mean cell accuracy 0.810, conductor-cell accuracy 0.458. Cell accuracy has a high floor because empty cells never change; compare conditions on exact-match rate and conductor accuracy as well.
- T1 grid sizes: 90 to 190 cells.
- T2 reference-repair mean score 0.998 (robustness of the underlying circuits: mean 0.99).
- AND/a: c_min=24, pool of 39 designs, sizes 24-29, robustness 1.00-1.00
- AND/b: c_min=25, pool of 37 designs, sizes 25-34, robustness 1.00-1.00
- HALF_ADDER/a: c_min=31, pool of 36 designs, sizes 31-34, robustness 1.00-1.00
- HALF_ADDER/b: c_min=36, pool of 33 designs, sizes 36-40, robustness 1.00-1.00
- MUX2/a: c_min=39, pool of 39 designs, sizes 39-44, robustness 0.89-0.93
- MUX2/b: c_min=37, pool of 38 designs, sizes 37-45, robustness 0.96-1.00
- NOT/a: c_min=15, pool of 40 designs, sizes 15-15, robustness 1.00-1.00
- NOT/b: c_min=17, pool of 40 designs, sizes 17-18, robustness 1.00-1.00
- OR/a: c_min=13, pool of 40 designs, sizes 13-13, robustness 1.00-1.00
- OR/b: c_min=15, pool of 40 designs, sizes 15-15, robustness 1.00-1.00
- XOR/a: c_min=15, pool of 40 designs, sizes 15-15, robustness 1.00-1.00
- XOR/b: c_min=17, pool of 40 designs, sizes 17-17, robustness 1.00-1.00
- Robustness among passing variants (best-known plus 1-4 random wire cells): mean r 0.981, 13% below 1.0. Stored minimal designs are almost all fully robust, so r may saturate; watch its spread in the pilot (H6).
- Most fragile stored design: MUX2/a with 39 cells emits up to 16 output pulses under a timing probe (r=0.93).
