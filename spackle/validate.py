"""Gate 1 validator: "generator validated" (plan.md, milestones).

Runs every check below against a freshly generated suite and writes
``docs/gate1_report.md``. Exit status 0 means the gate is met.

Usage:  python -m spackle.validate [--seed 0] [--report docs/gate1_report.md]
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import subprocess
import sys
from pathlib import Path

from . import fastsim, wireworld as ww
from .circuit import evaluate, harnesses
from .generate import (build_suite, gen_t1, gen_t2, min_repair_edits, public_view,
                       suite_hash)
from .prompts import PROMPT_VERSION, render
from .scoring import composite, score
from .search import load as load_best_known
from .specs import GEOMETRIES, SPECS, timing_scenarios, truth_scenarios

ROOT = Path(__file__).resolve().parent.parent


class Report:
    def __init__(self):
        self.sections: list[tuple[str, list[tuple[bool, str]]]] = []
        self.notes: list[str] = []

    def section(self, title: str):
        self.sections.append((title, []))

    def check(self, ok: bool, text: str):
        self.sections[-1][1].append((bool(ok), text))

    @property
    def ok(self) -> bool:
        return all(ok for _, checks in self.sections for ok, _ in checks)

    def markdown(self, header: str) -> str:
        n = sum(len(c) for _, c in self.sections)
        good = sum(ok for _, c in self.sections for ok, _ in c)
        lines = ["# Gate 1 report: generator validated", "", header, "",
                 f"**Result: {'PASS' if self.ok else 'FAIL'}** ({good}/{n} checks)", ""]
        for title, checks in self.sections:
            lines += [f"## {title}", ""]
            lines += [f"- {'PASS' if ok else '**FAIL**'}: {text}" for ok, text in checks]
            lines.append("")
        if self.notes:
            lines += ["## Calibration notes (not gating)", ""] + self.notes + [""]
        return "\n".join(lines)


def reference_run(grid, geom, scenario, steps=fastsim.MAX_STEPS):
    """Scenario run on the slow reference simulator: (pulses, quiet)."""
    pulses = [0] * len(geom.out_rows)
    t = 0
    while True:
        for i, tick in enumerate(scenario):
            if tick == t and grid[geom.in_rows[i]][0] != ww.EMPTY:
                grid = ww.set_cell(grid, geom.in_rows[i], 0, ww.HEAD)
        for j, r in enumerate(geom.out_rows):
            pulses[j] += grid[r][geom.cols - 1] == ww.HEAD
        if t == steps:
            break
        grid = ww.step(grid)
        t += 1
    return tuple(pulses), ww.is_quiet(grid)


def reference_passes(grid, geom) -> bool:
    spec = SPECS[geom.spec]
    for scen, (_, want) in zip(truth_scenarios(spec), spec.table()):
        pulses, quiet = reference_run(grid, geom, scen)
        if pulses != want or not quiet:
            return False
    return True


def check_simulator(rep: Report, db: dict):
    rep.section("1. Simulator")
    line = [" " * 6, "tH....", " " * 6]
    ok = ww.step(line)[1] == ".tH..." and ww.run(line, 4)[1] == "....tH" and \
        ww.is_quiet(ww.run(line, 6))
    rep.check(ok, "an electron on a straight wire moves one cell per tick and leaves the grid quiet")
    rep.check(ww.step(["H.H", " . ", " H "])[1][1] == "." and ww.step(["H.H", " . ", "   "])[1][1] == "H",
              "a wire fires on 1 or 2 neighbouring heads and not on 3")
    rng = random.Random(1234)
    n = 2000
    bad = 0
    for _ in range(n):
        rows, cols = rng.randint(1, 12), rng.randint(1, 20)
        g = ["".join(rng.choice("   ....Ht") for _ in range(cols)) for _ in range(rows)]
        k = rng.randint(0, 25)
        bad += ww.run(g, k) != fastsim.run_grid(g, k)
    rep.check(bad == 0, f"bitboard simulator equals reference simulator on {n} random grids (0-25 ticks)")
    runs = mism = 0
    for key, entry in sorted(db.items()):
        geom = GEOMETRIES[key]
        truth_h, timing_h, _ = harnesses(geom)
        for d in entry["designs"][:4]:
            K = truth_h.lay.pack(d["grid"])[0]
            for h in (truth_h, timing_h):
                pulses, quiet = h.run(K)
                for scen, p, q in zip(h.scenarios, pulses, quiet):
                    runs += 1
                    mism += reference_run(d["grid"], geom, scen) != (tuple(p), q)
    rep.check(mism == 0 and runs > 0,
              f"batched scenario runner (injection, pulse counts, quiet test) equals the reference "
              f"on {runs} truth-table and timing runs")


def check_determinism(rep: Report, seed: int, suite, db):
    rep.section("2. Determinism and freshness")
    again = build_suite(seed, db=db)
    rep.check(suite_hash(again) == suite_hash(suite), "same seed gives a byte-identical suite in-process")
    hashes = set()
    for hs in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=hs)
        out = subprocess.run(
            [sys.executable, "-c",
             "from spackle.generate import build_suite, suite_hash;"
             f"print(suite_hash(build_suite({seed})))"],
            capture_output=True, text=True, cwd=ROOT, env=env)
        hashes.add(out.stdout.strip())
    rep.check(hashes == {suite_hash(suite)},
              "same seed gives the same suite hash in fresh processes with different PYTHONHASHSEED")
    other = build_suite(seed + 1, db=db)
    by_tier = lambda ts, t: [tuple(x["grid"]) for x in ts if x["tier"] == t]
    for tier in ("T1", "T2"):
        overlap = set(by_tier(suite, tier)) & set(by_tier(other, tier))
        rep.check(not overlap, f"{tier}: a different seed shares no instance with this suite")
    t3 = lambda ts: [public_view(x) for x in ts if x["tier"] == "T3"]
    rep.check(t3(suite) == t3(other), "T3 specs are fixed across seeds")
    rep.check(gen_t1(seed, 7) == suite[7] and
              gen_t2(seed, 5, db) == [t for t in suite if t["tier"] == "T2"][5],
              "each task depends only on (seed, tier, index), not on suite size")


def check_t1(rep: Report, tasks):
    rep.section(f"3. T1 Simulate ({len(tasks)} tasks)")
    rep.check(all(ww.run(t["grid"], t["steps"]) == t["answer_key"]["target"] for t in tasks),
              "every stored target equals the reference simulator's output")
    steps = sorted({t["steps"] for t in tasks})
    rep.check(steps == list(range(1, 21)), f"step counts cover 1..20 (found {steps[0]}..{steps[-1]}, {len(steps)} distinct)")
    rep.check(len({tuple(t["grid"]) for t in tasks}) == len(tasks), "no duplicate instances")
    rep.check(all(any("H" in r for r in t["answer_key"]["target"]) for t in tasks),
              "every target still carries at least one electron head")
    perfect = [score(t, ww.to_ascii(t["answer_key"]["target"])) for t in tasks]
    rep.check(all(s["passed"] and s["score"] == 1 and s["format_errors"] == 0 for s in perfect),
              "the true answer scores 1.0 with no format errors")
    copy = [score(t, ww.to_ascii(t["grid"])) for t in tasks]
    rep.check(not any(s["passed"] for s in copy), "copying the input never passes")
    rep.notes.append(
        f"- T1 copy-the-input baseline: mean cell accuracy {statistics.mean(s['score'] for s in copy):.3f}, "
        f"conductor-cell accuracy {statistics.mean(s['conductor_accuracy'] for s in copy):.3f}. "
        "Cell accuracy has a high floor because empty cells never change; compare conditions on "
        "exact-match rate and conductor accuracy as well.")
    sizes = [t["rows"] * t["cols"] for t in tasks]
    rep.notes.append(f"- T1 grid sizes: {min(sizes)} to {max(sizes)} cells.")


def check_t2(rep: Report, tasks):
    rep.section(f"4. T2 Repair ({len(tasks)} tasks)")
    geoms = lambda t: GEOMETRIES[t["geometry"]]
    rep.check(all(not reference_passes(t["grid"], geoms(t)) for t in tasks),
              "every broken circuit fails its truth table (reference simulator)")
    rep.check(all(reference_passes(t["answer_key"]["reference"], geoms(t)) for t in tasks),
              "every reference repair passes (reference simulator)")
    rep.check(all(ww.hamming(t["grid"], t["answer_key"]["reference"]) == t["answer_key"]["min_edits"]
                  for t in tasks), "the reference repair is exactly min_edits cells away")
    rep.check(all(min_repair_edits(t["grid"], geoms(t), t["answer_key"]["min_edits"] - 1) is None
                  for t in tasks),
              "exhaustive search finds no repair with fewer than min_edits edits (min_edits is exact)")
    flips = sorted({t["answer_key"]["min_edits"] for t in tasks})
    rep.check(flips == [1, 2, 3], f"flip counts cover 1..3 (found {flips})")
    used = {t["spec"] for t in tasks}
    rep.check(used == set(SPECS), f"all {len(SPECS)} specs appear ({len({t['geometry'] for t in tasks})} geometries)")
    rep.check(len({(t["geometry"], tuple(t["grid"])) for t in tasks}) == len(tasks), "no duplicate instances")
    nothing = [score(t, ww.to_ascii(t["grid"])) for t in tasks]
    rep.check(all(s["score"] == 0 and not s["passed"] for s in nothing),
              "returning the broken circuit unchanged scores 0")
    ref = [score(t, ww.to_ascii(t["answer_key"]["reference"])) for t in tasks]
    rep.check(all(s["passed"] and s["edits"] == s["min_edits"] and
                  abs(s["score"] - composite(True, 1, s["robustness"])) < 1e-12 for s in ref),
              "the reference repair passes with edits == min_edits")
    rep.notes.append(f"- T2 reference-repair mean score {statistics.mean(s['score'] for s in ref):.3f} "
                     f"(robustness of the underlying circuits: mean {statistics.mean(s['robustness'] for s in ref):.2f}).")


def check_t3(rep: Report, tasks, db):
    rep.section(f"5. T3 Design ({len(tasks)} tasks)")
    missing = sorted(set(GEOMETRIES) - {k for k, e in db.items() if e.get("c_min")})
    rep.check(not missing, "every spec geometry has a best-known design" +
              (f" (missing: {missing})" if missing else f" ({len(GEOMETRIES)} geometries, {len(SPECS)} specs)"))
    rep.check({t["spec"] for t in tasks} == set(SPECS), "all six specs have tasks")
    ok = minimal = True
    for key, entry in db.items():
        geom = GEOMETRIES[key]
        for d in entry["designs"]:
            ok &= reference_passes(d["grid"], geom) and ww.count_cells(d["grid"]) == d["cells"]
        ok &= entry["c_min"] == min(d["cells"] for d in entry["designs"])
        best = entry["designs"][0]["grid"]
        for r, row in enumerate(best):
            for c, ch in enumerate(row):
                if ch == ww.WIRE:
                    smaller = ww.set_cell(best, r, c, ww.EMPTY)
                    rpt = evaluate(smaller, geom)
                    minimal &= not (rpt.valid and rpt.passed)
    rep.check(ok, "every stored design passes on the reference simulator; c_min is the pool minimum")
    rep.check(minimal, "no best-known design can lose a single cell and still pass")
    rep.check(all(t["cell_budget"] > t["answer_key"]["c_min"] for t in tasks),
              "every cell budget exceeds c_min, so every task is solvable")
    best = [score(t, ww.to_ascii(t["answer_key"]["best_known"])) for t in tasks]
    rep.check(all(s["passed"] and abs(s["score"] - (0.8 + 0.2 * s["robustness"])) < 1e-12 for s in best),
              "the best-known design scores S = 0.8 + 0.2 r, matching the plan's formula")
    empty = [score(t, ww.to_ascii(ww.blank(t["rows"], t["cols"]))) for t in tasks]
    straight = []
    for t in tasks:
        g = ww.blank(t["rows"], t["cols"])
        for r in t["in_rows"] + t["out_rows"]:
            g[r] = ww.WIRE * t["cols"]
        straight.append(score(t, ww.to_ascii(g)))
    rep.check(all(s["score"] == 0 for s in empty + straight),
              "trivial designs (empty grid, straight wires) score 0")
    over = True
    for t in tasks:
        geom = GEOMETRIES[t["geometry"]]
        g = list(t["answer_key"]["best_known"])
        need = t["cell_budget"] + 1 - ww.count_cells(g)
        # Pad with inert wire far from the circuit if a free corner exists.
        cells = [(r, c) for r in range(geom.rows) for c in range(geom.cols) if g[r][c] == ww.EMPTY]
        for r, c in cells:
            if need <= 0:
                break
            g2 = ww.set_cell(g, r, c, ww.WIRE)
            if evaluate(g2, geom).passed:
                g, need = g2, need - 1
        if need <= 0:
            s = score(t, ww.to_ascii(g))
            over &= s["truth_passed"] and not s["passed"] and s["score"] == 0
    rep.check(over, "a correct design over the cell budget fails")
    for key in sorted(db):
        e = db[key]
        rs = [d["robustness"] for d in e["designs"]]
        rep.notes.append(f"- {key}: c_min={e['c_min']}, pool of {len(rs)} designs, "
                         f"sizes {min(d['cells'] for d in e['designs'])}-{max(d['cells'] for d in e['designs'])}, "
                         f"robustness {min(rs):.2f}-{max(rs):.2f}")


def check_scoring(rep: Report, suite, db):
    rep.section("6. Scoring, encodings and prompts")
    ok = True
    for t in suite:
        g = t.get("grid") or t["answer_key"]["best_known"]
        for enc in ww.ENCODINGS:
            p = ww.parse(ww.encode(g, enc), t["rows"], t["cols"], enc)
            ok &= p.grid == g and p.format_errors == 0
    rep.check(ok, "ASCII and coordinate-list encodings round-trip every task grid with no format errors")
    same = repaired = True
    for t in suite:
        key = t["answer_key"]
        g = key.get("target") or key.get("reference") or key["best_known"]
        a = score(t, ww.to_ascii(g))
        b = score(t, "```\n" + ww.to_coords(g) + "\n```", "coords")
        same &= a["score"] == b["score"] and a["passed"] == b["passed"]
        stripped = "\n".join(r.rstrip() for r in g)
        c = score(t, stripped)
        repaired &= c["score"] == a["score"] and (c["format_errors"] > 0) == (stripped != ww.to_ascii(g))
    rep.check(same, "an answer scores the same in either encoding")
    rep.check(repaired, "rows with stripped trailing spaces are padded, scored, and counted as format errors")
    junk = [score(t, "I cannot solve this.") for t in suite]
    rep.check(all(not s["passed"] and s["format_errors"] > 0 for s in junk),
              "a non-grid answer is scored (not a crash), fails, and reports format errors")
    leak = False
    for t in suite:
        for enc in ww.ENCODINGS:
            text = render(t, enc)
            for name in ("target", "reference", "best_known"):
                g = t["answer_key"].get(name)
                leak |= bool(g) and ww.encode(g, enc) in text
            leak |= "answer_key" in text or "c_min" in text
    rep.check(not leak, f"task statements never contain the answer key (prompt version {PROMPT_VERSION})")
    # Robustness on passing designs beyond the stored minima: best-known designs
    # with 1-4 extra wire cells that still meet the truth table.
    rng = random.Random(99)
    rs = []
    for key, e in sorted(db.items()):
        geom = GEOMETRIES[key]
        best = e["designs"][0]["grid"]
        empties = [(r, c) for r in range(geom.rows) for c in range(geom.cols) if best[r][c] == ww.EMPTY]
        for _ in range(150):
            g = best
            for r, c in rng.sample(empties, rng.randint(1, 4)):
                g = ww.set_cell(g, r, c, ww.WIRE)
            rpt = evaluate(g, geom)
            if rpt.passed:
                rs.append(rpt.robustness)
    fragile_share = sum(r < 1 for r in rs) / len(rs) if rs else 0.0
    rep.check(rs and 0 < fragile_share < 1,
              f"the robustness probe separates designs that pass the same truth table: of {len(rs)} "
              f"passing variants of the best-known designs, {fragile_share:.0%} have r < 1 "
              f"(lowest r = {min(rs, default=1):.2f})")
    rep.notes.append(f"- Robustness among passing variants (best-known plus 1-4 random wire cells): "
                     f"mean r {statistics.mean(rs):.3f}, {fragile_share:.0%} below 1.0. Stored minimal designs "
                     "are almost all fully robust, so r may saturate; watch its spread in the pilot (H6).")
    fragile = None
    for key, e in sorted(db.items()):
        geom = GEOMETRIES[key]
        for d in e["designs"]:
            worst = max((max(c.pulses) for c in evaluate(d["grid"], geom).timing), default=0)
            if fragile is None or worst > fragile[0]:
                fragile = (worst, key, d["cells"], d["robustness"])
    if fragile:
        rep.notes.append(f"- Most fragile stored design: {fragile[1]} with {fragile[2]} cells emits up to "
                         f"{fragile[0]} output pulses under a timing probe (r={fragile[3]:.2f}).")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--report", default=str(ROOT / "docs" / "gate1_report.md"))
    args = ap.parse_args(argv)
    db = load_best_known()
    suite = build_suite(args.seed, db=db)
    tier = lambda t: [x for x in suite if x["tier"] == t]
    rep = Report()
    check_simulator(rep, db)
    check_determinism(rep, args.seed, suite, db)
    check_t1(rep, tier("T1"))
    check_t2(rep, tier("T2"))
    check_t3(rep, tier("T3"), db)
    check_scoring(rep, suite, db)
    header = (f"Suite seed {args.seed}: {len(suite)} tasks "
              f"({len(tier('T1'))} T1, {len(tier('T2'))} T2, {len(tier('T3'))} T3), "
              f"suite hash `{suite_hash(suite)}`. Regenerate with `python -m spackle.validate --seed {args.seed}`.")
    text = rep.markdown(header)
    Path(args.report).parent.mkdir(exist_ok=True)
    Path(args.report).write_text(text)
    print(text)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
