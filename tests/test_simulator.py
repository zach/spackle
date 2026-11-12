import random

from spackle import fastsim, wireworld as ww
from spackle.circuit import evaluate, harnesses, trace
from spackle.specs import GEOMETRIES, SPECS, timing_scenarios, truth_scenarios
from spackle.validate import reference_run


def test_electron_travels_one_cell_per_tick():
    g = ["tH...."]
    assert ww.step(g) == [".tH..."]
    assert ww.run(g, 4) == ["....tH"]
    assert ww.is_quiet(ww.run(g, 6))


def test_fires_on_one_or_two_heads_only():
    assert ww.step(["H.H", " . ", "   "])[1] == " H "
    assert ww.step(["H.H", " . ", " H "])[1] == " . "
    assert ww.step(["   ", " . ", "   "])[1] == " . "


def test_head_on_collision_annihilates():
    assert ww.is_quiet(ww.run(["Ht....tH"], 6))


def test_fast_matches_reference_on_random_grids():
    rng = random.Random(7)
    for _ in range(400):
        rows, cols = rng.randint(1, 10), rng.randint(1, 16)
        g = ["".join(rng.choice("  ...Ht") for _ in range(cols)) for _ in range(rows)]
        n = rng.randint(0, 20)
        assert fastsim.run_grid(g, n) == ww.run(g, n)


def test_scenario_runner_matches_reference_on_random_designs():
    rng = random.Random(3)
    for key in ("XOR/a", "HALF_ADDER/a", "MUX2/a"):
        geom = GEOMETRIES[key]
        truth_h, timing_h, _ = harnesses(geom)
        for _ in range(15):
            g = ["".join(rng.choice(" ..") for _ in range(geom.cols)) for _ in range(geom.rows)]
            K = truth_h.lay.pack(g)[0]
            for h in (truth_h, timing_h):
                pulses, quiet = h.run(K)
                for scen, p, q in zip(h.scenarios, pulses, quiet):
                    assert reference_run(g, geom, scen) == (tuple(p), q)


def test_scenarios():
    assert truth_scenarios(SPECS["XOR"]) == [(None, None), (None, 0), (0, None), (0, 0)]
    assert len(timing_scenarios(SPECS["XOR"])) == 6          # 1 combo x 2 inputs x 3 delays
    assert len(timing_scenarios(SPECS["MUX2"])) == (3 * 2 + 3) * 3
    for spec in SPECS.values():                              # a quiet grid cannot make a pulse
        assert not any(spec.table()[0][1])


def test_or_gate_by_hand():
    # Two wires merging: the simplest possible OR for geometry OR/a.
    g = ["           ",
         "....       ",
         "    .      ",
         "     ......",
         "    .      ",
         "....       ",
         "           "]
    rep = evaluate(g, "OR/a")
    assert rep.valid and rep.passed and rep.cells == 16
    assert "PASS" in trace(rep, "OR/a")
    assert not evaluate(g, "AND/a").passed
    assert not evaluate(g, "XOR/a").passed


def test_structural_errors():
    g = ww.blank(7, 11)
    rep = evaluate(g, "OR/a")
    assert not rep.valid and len(rep.errors) == 3
    assert not evaluate(g[:-1], "OR/a").valid
    g2 = ["....H......" if r in (1, 3, 5) else " " * 11 for r in range(7)]
    assert any("only wire" in e for e in evaluate(g2, "OR/a").errors)
