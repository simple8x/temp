"""P0 acceptance tooling: recovery ON vs OFF comparison and GT scoring of recovery candidates, fail-closed."""
import copy
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

TOOLS = pathlib.Path(__file__).resolve().parents[2] / "tools"
WF = pathlib.Path(__file__).resolve().parents[3] / ".github" / "workflows" / "claude-gt.yml"
sys.path.insert(0, str(TOOLS))
import recovery_compare as rcmp  # noqa: E402


def page(name, found=10, local=20, rec=None):
    r = {"project": name, "sheet": name + ".kicad_sch", "instance": None, "seconds": 1.0,
         "local_pins": local, "local_pins_found": found, "gt_pins_visible_with_net": local + 2, "detected_pins": found,
         "detected": {"VERIFIED": {"tp": 3, "fp": 0}, "INFERRED": {"tp": 1, "fp": 0}}}
    if rec is not False:
        r["recovery"] = rec or {"status": "OK", "invariant": True, "new_local_found": 0, "new_tp": 0, "fp": 0,
                                "dup": 0, "number_mismatch": 0, "promotable_pins": 0, "candidates": {}, "reasons": {},
                                "seconds": 0.1}
    return r


def write(tmp, name, pages, total=None, cov=None):
    p = tmp / name
    lines = [f"GT_EVAL {json.dumps(r)}" for r in pages]
    lines.append("GT_COVERAGE " + json.dumps(cov or {"problems": []}))
    lines.append("GT_TOTAL " + json.dumps(total or {"detected": {"FP_V": 0}}))
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def pair(tmp, mutate_on=None, mutate_off=None, names=None):
    names = names or rcmp.CORPUS
    on = [page(n) for n in names]
    off = [page(n, rec=False) for n in names]
    if mutate_on:
        mutate_on(on)
    if mutate_off:
        mutate_off(off)
    tot_on = {"detected": {"FP_V": 0}, "recovery": {"pages": len(on)}}
    return write(tmp, "on.log", on, tot_on), write(tmp, "off.log", off)


def test_identical_baseline_with_safe_recovery_passes(tmp_path, capsys):
    def gain(on):
        on[-1]["recovery"].update(new_local_found=5, new_tp=5, promotable_pins=5)
    assert rcmp.main(*pair(tmp_path, gain)) == 0
    out = capsys.readouterr().out
    assert "RECOVERY_COMPARE PASS" in out and "candidate=15" in out


@pytest.mark.parametrize("bad,needle", [
    (lambda on: on[0].update(local_pins_found=9), "BASELINE REGRESSION"),
    (lambda on: on[0]["detected"]["VERIFIED"].update(fp=1), "BASELINE REGRESSION"),
    (lambda on: on[0]["recovery"].update(fp=1), "recovery fp=1"),
    (lambda on: on[0]["recovery"].update(dup=1), "recovery dup=1"),
    (lambda on: on[0]["recovery"].update(number_mismatch=2), "number_mismatch=2"),
    (lambda on: on[0]["recovery"].update(invariant=False, status="INVARIANCE_FAIL"), "invariant=False"),
    (lambda on: on[0]["recovery"].update(invariant=None, status="TIMEOUT"), "TIMEOUT"),
    (lambda on: on[0].pop("recovery"), "no recovery block"),
    (lambda on: on.pop(0), "page sets differ"),
])
def test_any_regression_or_unsafe_candidate_fails(tmp_path, capsys, bad, needle):
    assert rcmp.main(*pair(tmp_path, bad)) == 1
    out = capsys.readouterr().out
    assert "RECOVERY_COMPARE FAIL" in out and needle in out


def test_missing_required_corpus_page_fails(tmp_path, capsys):
    assert rcmp.main(*pair(tmp_path, names=rcmp.CORPUS[1:])) == 1
    assert f"required corpus page {rcmp.CORPUS[0]} not evaluated" in capsys.readouterr().out


def test_off_log_with_recovery_block_fails(tmp_path, capsys):
    def leak(off):
        off[0]["recovery"] = page("x")["recovery"]
    assert rcmp.main(*pair(tmp_path, mutate_off=leak)) == 1


def test_unfinished_run_fails(tmp_path):
    on, off = pair(tmp_path)
    pathlib.Path(off).write_text("\n".join(ln for ln in pathlib.Path(off).read_text().splitlines()
                                           if not ln.startswith("GT_TOTAL")))
    assert rcmp.main(on, off) == 1


def test_corpus_lists_cover_the_p0_pages():
    assert rcmp.HARMED == ["One-Air-Max#p1", "One-Air-Max#p2", "sonde xilinx", "complex_hierarchy#p1",
                           "complex_hierarchy#p2", "video#p3"]
    assert rcmp.GAINED == ["interf_u", "video#p6", "Q17ng", "Class-D", "RoyalBlue54L-Feather#p2"]


def test_workflow_runs_recovery_off_and_the_comparison_always():
    wf = WF.read_text()
    assert "GT_RECOVERY=0" in wf and "recovery_compare.py gt_eval.log gt_eval_off.log" in wf
    block = wf.split("P0 recovery acceptance")[1].split("- name:")[0]
    assert "if: always()" in block and "set -o pipefail" in block


# ----------------------------------------------------------------------------------------------- gt_eval scoring

def _rep(pins, id_state="CONFIRMED"):
    from ecci import recovery as rc
    c = SimpleNamespace(id="RC0", state=rc.PROMOTABLE, identity={"id_state": id_state, "pins": pins})
    rep = SimpleNamespace(status="OK", problems=[], stats={"baseline_fingerprint": "f"}, candidates=[c])
    rep.promotable_pins = lambda: [(c.id, SimpleNamespace(predicted_tip=t, numbers=n)) for t, n in pins]
    rep.by_state = lambda: {rc.PROMOTABLE: 1}
    rep.reason_counts = lambda: {}
    return rep


def test_recovery_score_counts_new_dup_fp_and_number_mismatch():
    import gt_eval
    vis = [SimpleNamespace(x=0.0, y=0.0, number="1"), SimpleNamespace(x=10.0, y=0.0, number="2"),
           SimpleNamespace(x=20.0, y=0.0, number="3")]
    pins = [((0.2, 0.0), ("1",)),          # GT pin 0, already found by the baseline -> dup
            ((10.1, 0.0), ("9",)),         # GT pin 1, new, wrong number -> new_tp + number_mismatch
            ((20.0, 0.3), ("3",)),         # GT pin 2, new
            ((50.0, 50.0), ("4",))]        # nothing near -> fp
    r = gt_eval.recovery_score(_rep(pins), vis, {0}, lambda x, y: 1 if x < 15 else None)
    assert (r["dup"], r["new_tp"], r["fp"], r["number_mismatch"], r["new_local_found"]) == (1, 2, 1, 1, 1)
    assert r["invariant"] is True and r["promotable_pins"] == 4


def test_recovery_score_does_not_double_claim_one_gt_pin():
    import gt_eval
    vis = [SimpleNamespace(x=0.0, y=0.0, number="1")]
    r = gt_eval.recovery_score(_rep([((0.1, 0.0), ("1",)), ((0.2, 0.0), ("1",))]), vis, set(), lambda x, y: 1)
    assert r["new_tp"] == 1 and r["fp"] == 1


def test_recovery_block_reports_invariance_failure_without_dropping_the_page(monkeypatch):
    import gt_eval
    from ecci import recovery as rc

    def boom(*a, **k):
        raise AssertionError("recovery mutated the baseline")

    monkeypatch.setattr(rc, "recover_page", boom)
    out = gt_eval.recovery_block(None, None, [], None, None, SimpleNamespace(symbols={}), [], set(), None,
                                 __import__("time").time())
    assert out["status"] == "INVARIANCE_FAIL" and out["invariant"] is False


def test_recovery_block_skips_when_the_page_budget_is_spent():
    import gt_eval
    out = gt_eval.recovery_block(None, None, [], None, None, None, [], set(), None, 0.0)
    assert out["status"] == "SKIPPED_BUDGET" and out["invariant"] is None
