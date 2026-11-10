"""P1: the M4.0 regression gate is enforced by CI, fail-closed."""
import json
import pathlib
import subprocess
import sys

import pytest

TOOLS = pathlib.Path(__file__).resolve().parents[2] / "tools"
WF = pathlib.Path(__file__).resolve().parents[3] / ".github" / "workflows" / "claude-gt.yml"
sys.path.insert(0, str(TOOLS))
import gt_gate  # noqa: E402

G = gt_gate.load_gate(TOOLS / "gt_gate.txt")


def log(**over):
    cov = {"full_run": True, "evaluated": 100, "projects": 32, "problems": []}
    tot = {"inventory_consistent": True,
           "detected": {"FP_V": 0, "FP_I": 0}, "oracle": {"FP_V": 0, "FP_I": 0},
           "detected_local": {"pin_recall": 0.5509}}
    for k, v in over.items():
        (cov if k in cov else tot)[k] = v
    return f"GT_COVERAGE {json.dumps(cov)}\nGT_TOTAL {json.dumps(tot)}\n"


def test_pinned_thresholds_are_the_frozen_m40_gate():
    assert (G["GATE_MAX_FP_V"], G["GATE_MAX_FP_I"], G["GATE_MIN_PIN_RECALL"], G["GATE_MIN_PAGES"],
            G["GATE_PROJECTS"]) == (0, 0, 0.55, 100, 32)


def test_last_known_good_result_passes():
    assert gt_gate.check(log(), G) == []


@pytest.mark.parametrize("over,needle", [
    ({"full_run": False}, "partial run"),
    ({"inventory_consistent": False}, "inventory_consistent"),
    ({"inventory_consistent": None}, "inventory_consistent"),
    ({"problems": ["x"]}, "coverage problems"),
    ({"evaluated": 99}, "evaluated=99"),
    ({"evaluated": None}, "evaluated=None"),
    ({"projects": 31}, "projects=31"),
    ({"detected": {"FP_V": 1, "FP_I": 0}}, "detected.FP_V=1"),
    ({"detected": {"FP_V": 0, "FP_I": 2}}, "detected.FP_I=2"),
    ({"detected": {"FP_V": 0}}, "detected.FP_I=None"),
    ({"oracle": {"FP_V": 1, "FP_I": 0}}, "oracle.FP_V=1"),
    ({"detected_local": {"pin_recall": 0.5499}}, "pin_recall=0.5499"),
    ({"detected_local": {}}, "pin_recall=None"),
    ({"detected_local": {"pin_recall": True}}, "pin_recall=None"),
])
def test_every_regression_or_missing_field_fails(over, needle):
    probs = gt_gate.check(log(**over), G)
    assert any(needle in p for p in probs), probs


def test_no_result_lines_fail():
    assert gt_gate.check("", G) and gt_gate.check("GT_TOTAL {}\n", G)


def test_cli_exit_codes(tmp_path):
    good, bad = tmp_path / "g.log", tmp_path / "b.log"
    good.write_text(log())
    bad.write_text(log(detected={"FP_V": 1, "FP_I": 0}))
    run = lambda p: subprocess.run([sys.executable, str(TOOLS / "gt_gate.py"), str(p)],  # noqa: E731
                                   capture_output=True, text=True)
    rg, rb = run(good), run(bad)
    assert rg.returncode == 0 and "M4_GATE PASS" in rg.stdout
    assert rb.returncode == 1 and "M4_GATE FAIL" in rb.stdout


def test_missing_gate_pin_is_an_error(tmp_path):
    f = tmp_path / "g.txt"
    f.write_text("GATE_MAX_FP_V=0\n")
    with pytest.raises(ValueError):
        gt_gate.load_gate(f)


def test_workflow_runs_the_gate_on_every_run_and_cannot_skip_it():
    wf = WF.read_text()
    step = wf.split("name: M4.0 regression gate")[1].split("\n      - ")[0]
    assert "if: always()" in step and "gt_gate.py gt_eval.log" in step and "pipefail" in step
    assert "continue-on-error" not in wf
    # the gate runs after the evaluation and the inventory, in the same job
    assert wf.index("Evaluate ECCI") < wf.index("M4.0 regression gate")


# ---------------------------------------------------------------- fail closed on malformed / missing / duplicate records

def raw_log(cov=None, tot=None, drop=(), extra=""):
    c = {"full_run": True, "evaluated": 100, "projects": 32, "problems": []}
    t = {"inventory_consistent": True, "detected": {"FP_V": 0, "FP_I": 0}, "oracle": {"FP_V": 0, "FP_I": 0},
         "detected_local": {"pin_recall": 0.5509}}
    c.update(cov or {})
    t.update(tot or {})
    for path in drop:
        d = c if path[0] in c else t
        for k in path[:-1]:
            d = d[k]
        del d[path[-1]]
    return f"GT_COVERAGE {json.dumps(c)}\nGT_TOTAL {json.dumps(t)}\n" + extra


FP = [(m, k) for m in ("detected", "oracle") for k in ("FP_V", "FP_I")]
NONFINITE = [float("nan"), float("inf"), float("-inf")]


def _with(mode, k, v):
    return {mode: {**{"FP_V": 0, "FP_I": 0}, k: v}}


@pytest.mark.parametrize("mode,k", FP)
@pytest.mark.parametrize("v", NONFINITE + [True, False, "0", None, -1, 0.5, [0], {}])
def test_fp_field_rejects_every_non_count(mode, k, v):
    probs = gt_gate.check(raw_log(tot=_with(mode, k, v)), G)
    assert probs and any(f"{mode}.{k}" in p or "non-finite" in p for p in probs), probs


@pytest.mark.parametrize("v", NONFINITE + [True, "0.6", None, 1.5, -0.1, [0.6]])
def test_pin_recall_rejects_non_finite_and_out_of_range(v):
    probs = gt_gate.check(raw_log(tot={"detected_local": {"pin_recall": v}}), G)
    assert probs and any("pin_recall" in p or "non-finite" in p for p in probs), probs


@pytest.mark.parametrize("field,v", [("evaluated", float("nan")), ("evaluated", float("inf")), ("evaluated", True),
                                     ("evaluated", "142"), ("evaluated", 100.5), ("evaluated", -1),
                                     ("projects", float("nan")), ("projects", True), ("projects", "32"),
                                     ("projects", 32.5), ("full_run", 1), ("full_run", "true"), ("full_run", None)])
def test_coverage_fields_reject_bad_types(field, v):
    assert gt_gate.check(raw_log(cov={field: v}), G)


@pytest.mark.parametrize("v", [None, "", "x", {}, 0, False, ["x"]])
def test_problems_must_be_an_empty_list(v):
    probs = gt_gate.check(raw_log(cov={"problems": v}), G)
    assert any("coverage problems" in p for p in probs), probs


@pytest.mark.parametrize("path", [("problems",), ("full_run",), ("evaluated",), ("projects",),
                                  ("inventory_consistent",), ("detected",), ("oracle",), ("detected_local",),
                                  ("detected", "FP_V"), ("detected", "FP_I"), ("oracle", "FP_V"),
                                  ("oracle", "FP_I"), ("detected_local", "pin_recall")])
def test_every_missing_mandatory_field_fails(path):
    probs = gt_gate.check(raw_log(drop=[path]), G)
    assert any("missing field" in p for p in probs), probs


def test_inventory_consistent_must_be_exactly_true():
    for v in (1, "true", None):
        assert gt_gate.check(raw_log(tot={"inventory_consistent": v}), G)


@pytest.mark.parametrize("extra", [
    'GT_TOTAL {"inventory_consistent": true}\n',                             # conflicting GT_TOTAL
    'GT_COVERAGE {"full_run": true, "evaluated": 142, "projects": 32, "problems": []}\n',  # conflicting GT_COVERAGE
])
def test_conflicting_records_fail(extra):
    probs = gt_gate.check(raw_log(extra=extra), G)
    assert any("conflicting" in p for p in probs), probs


def test_duplicate_identical_records_fail():
    one = raw_log()
    probs = gt_gate.check(one + one, G)
    assert sum("duplicate" in p for p in probs) == 2, probs


@pytest.mark.parametrize("text", [
    "GT_COVERAGE {not json\nGT_TOTAL {}\n",
    'GT_COVERAGE []\nGT_TOTAL []\n',
    'GT_COVERAGE "x"\nGT_TOTAL 3\n',
    'GT_COVERAGE {"full_run": true, "evaluated": 100, "projects": 32, "problems": []}\n',   # no GT_TOTAL
    'GT_TOTAL {"inventory_consistent": true}\n',                                         # no GT_COVERAGE
    'GT_COVERAGE {"full_run": true, "evaluated": NaN, "projects": 32, "problems": []}\nGT_TOTAL {}\n',
])
def test_malformed_record_lines_fail_without_crashing(text):
    assert gt_gate.check(text, G)


def test_contradictory_inventory_and_problems_fail():
    probs = gt_gate.check(raw_log(cov={"problems": ["page missing"]}), G)
    assert any("contradictory" in p for p in probs), probs


def test_raw_log_baseline_passes_and_thresholds_unchanged():
    assert gt_gate.check(raw_log(), G) == []
    assert gt_gate.check(raw_log(cov={"evaluated": 142}), G) == []
    assert (TOOLS / "gt_gate.txt").read_text().count("=") == 5


@pytest.mark.parametrize("bad", ["GATE_MAX_FP_V=nan", "GATE_MAX_FP_V=inf", "GATE_MAX_FP_V=0\nGATE_MAX_FP_V=1"])
def test_gate_pin_file_rejects_non_finite_and_duplicate_pins(tmp_path, bad):
    good = (TOOLS / "gt_gate.txt").read_text().replace("GATE_MAX_FP_V=0", bad)
    p = tmp_path / "g.txt"
    p.write_text(good)
    with pytest.raises(ValueError):
        gt_gate.load_gate(p)
