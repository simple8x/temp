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
