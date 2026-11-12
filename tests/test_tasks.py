import pytest

from spackle import wireworld as ww
from spackle.generate import (build_suite, gen_t1, gen_t2, min_repair_edits,
                              public_view, suite_hash)
from spackle.prompts import render
from spackle.scoring import composite, score
from spackle.search import load
from spackle.specs import GEOMETRIES, SPECS
from spackle.validate import reference_passes


@pytest.fixture(scope="module")
def suite():
    return build_suite(seed=11, n_t1=20, n_t2=12)


def tier(suite, t):
    return [x for x in suite if x["tier"] == t]


def test_parse_ascii_repairs():
    p = ww.parse_ascii("..\n.\n...\n", 2, 2)
    assert p.grid == ["..", ". "]
    assert p.repairs == {"extra_row": 1, "short_row": 1}
    p = ww.parse_ascii("Here you go:\n```\n.x\n```", 2, 2)
    assert p.grid == [". ", "  "]
    assert p.repairs == {"text_outside_fence": 1, "bad_char": 1, "missing_row": 1, "short_row": 1}
    assert ww.parse_ascii("```text\n..\nHt\n```", 2, 2).format_errors == 0


def test_coords_roundtrip_and_repairs():
    g = [" .H", "t. "]
    assert ww.parse_coords(ww.to_coords(g), 2, 3).grid == g
    p = ww.parse_coords("size 2x3\nwire: (0,1) (5,5)\nhead: (0,1)\nnonsense", 2, 3)
    assert p.grid == [" H ", "   "]
    assert p.repairs == {"out_of_bounds": 1, "duplicate_cell": 1, "unknown_line": 1}


def test_composite_formula():
    assert composite(False, 1, 1) == 0
    assert composite(True, 1, 1) == pytest.approx(1.0)
    assert composite(True, 0.5, 0.5) == pytest.approx(0.8)
    assert composite(True, 21 / 22, 0) == pytest.approx(0.6 + 0.2 * 21 / 22)
    assert composite(True, 1.3, 1) == pytest.approx(1.0)  # capped


def test_suite_is_deterministic(suite):
    assert suite_hash(build_suite(seed=11, n_t1=20, n_t2=12)) == suite_hash(suite)
    assert gen_t1(11, 3) == suite[3]
    assert gen_t1(12, 3)["grid"] != suite[3]["grid"]


def test_t1(suite):
    tasks = tier(suite, "T1")
    assert sorted(t["steps"] for t in tasks) == list(range(1, 21))
    for t in tasks:
        target = t["answer_key"]["target"]
        assert ww.run(t["grid"], t["steps"]) == target
        s = score(t, ww.to_ascii(target))
        assert s["passed"] and s["score"] == 1.0
        wrong = ww.set_cell(target, 0, 0, "H" if target[0][0] != "H" else " ")
        s = score(t, ww.to_ascii(wrong))
        assert not s["passed"] and s["wrong_cells"] == 1
        assert s["score"] == pytest.approx(1 - 1 / (t["rows"] * t["cols"]))


def test_t2(suite):
    tasks = tier(suite, "T2")
    assert len(tasks) == 12
    for t in tasks:
        geom = GEOMETRIES[t["geometry"]]
        k = t["answer_key"]["min_edits"]
        ref = t["answer_key"]["reference"]
        assert not reference_passes(t["grid"], geom)
        assert reference_passes(ref, geom)
        assert ww.hamming(ref, t["grid"]) == k
        assert min_repair_edits(t["grid"], geom, k - 1) is None
        assert score(t, ww.to_ascii(t["grid"]))["score"] == 0
        s = score(t, ww.to_ascii(ref))
        assert s["passed"] and s["edits"] == k and s["score"] >= 0.8


def test_t2_extra_edits_cost_score():
    db = load()
    t = gen_t2(0, 0, db)
    geom = GEOMETRIES[t["geometry"]]
    ref = t["answer_key"]["reference"]
    base = score(t, ww.to_ascii(ref))
    # add an inert wire cell that keeps the circuit passing
    for r in range(geom.rows):
        for c in range(geom.cols):
            if ref[r][c] == " ":
                g = ww.set_cell(ref, r, c, ".")
                s = score(t, ww.to_ascii(g))
                if s["passed"] and s["robustness"] == base["robustness"]:
                    assert s["edits"] == base["edits"] + 1 and s["score"] < base["score"]
                    return
    pytest.skip("no inert cell available")


def test_t3(suite):
    tasks = tier(suite, "T3")
    assert {t["spec"] for t in tasks} == set(SPECS)
    for t in tasks:
        best = t["answer_key"]["best_known"]
        s = score(t, ww.to_ascii(best))
        assert s["passed"] and s["cells"] == s["c_min"] <= t["cell_budget"]
        assert s["score"] == pytest.approx(0.8 + 0.2 * s["robustness"])
        assert score(t, "")["score"] == 0
        tight = dict(t, cell_budget=s["cells"] - 1)
        s2 = score(tight, ww.to_ascii(best))
        assert s2["truth_passed"] and not s2["passed"] and s2["score"] == 0


def test_prompts_do_not_leak(suite):
    for t in suite:
        for enc in ww.ENCODINGS:
            text = render(t, enc)
            assert "answer_key" not in text
            for name in ("target", "reference", "best_known"):
                g = t["answer_key"].get(name)
                if g:
                    assert ww.encode(g, enc) not in text
        assert "answer_key" not in public_view(t)


def test_scores_agree_across_encodings(suite):
    for t in suite:
        key = t["answer_key"]
        g = key.get("target") or key.get("reference") or key["best_known"]
        a = score(t, ww.to_ascii(g))
        b = score(t, ww.to_coords(g), "coords")
        assert a["score"] == b["score"] and b["format_errors"] == 0
