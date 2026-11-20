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
    assert G == {"GATE_MAX_FP_V": 0, "GATE_MAX_FP_I": 0, "GATE_MIN_PIN_RECALL": 0.55, "GATE_MIN_PAGES": 100,
                 "GATE_PROJECTS": 32}


@pytest.mark.parametrize("bad", ["GATE_MAX_FP_V=nan", "GATE_MAX_FP_V=inf", "GATE_MAX_FP_V=0\nGATE_MAX_FP_V=1"])
def test_gate_pin_file_rejects_non_finite_and_duplicate_pins(tmp_path, bad):
    good = (TOOLS / "gt_gate.txt").read_text().replace("GATE_MAX_FP_V=0", bad)
    p = tmp_path / "g.txt"
    p.write_text(good)
    with pytest.raises(ValueError):
        gt_gate.load_gate(p)


# ---------------------------------------------------------------- the pin file is a closed, frozen schema

CANON = {"GATE_MAX_FP_V": "0", "GATE_MAX_FP_I": "0", "GATE_MIN_PIN_RECALL": "0.55", "GATE_MIN_PAGES": "100",
         "GATE_PROJECTS": "32"}
# a looser value for each pin (max raised, min lowered); GATE_PROJECTS is an exact count, so both directions
WEAKER = {"GATE_MAX_FP_V": ["1", "0.5", "1000"], "GATE_MAX_FP_I": ["1", "0.5", "1000"],
          "GATE_MIN_PIN_RECALL": ["0.54", "0.5", "0"], "GATE_MIN_PAGES": ["99", "1", "0"],
          "GATE_PROJECTS": ["31", "33", "0"]}
OTHER = {"GATE_MAX_FP_V": ["2"], "GATE_MAX_FP_I": ["2"], "GATE_MIN_PIN_RECALL": ["0.56", "1"],
         "GATE_MIN_PAGES": ["101", "142"], "GATE_PROJECTS": ["64"]}
MALFORMED = ["nan", "NaN", "inf", "Infinity", "-inf", "True", "False", "true", "", "zero", "0x0", "1e2", "+0",
             "-0", "0 0", "0,55", "1_00", "0.55.0", "'0'", '"32"', "None", "[0]", "0.55#x", "\uff10", "\u0660"]


def write(tmp_path, lines):
    f = tmp_path / "g.txt"
    f.write_text("# header\n" + "".join(f"{x}\n" for x in lines))
    return f


def canon_lines(**over):
    return [f"{k}={over.get(k, v)}" for k, v in CANON.items()]


def test_canonical_configuration_is_accepted_with_exact_values(tmp_path):
    g = gt_gate.load_gate(write(tmp_path, canon_lines()))
    assert g == {"GATE_MAX_FP_V": 0.0, "GATE_MAX_FP_I": 0.0, "GATE_MIN_PIN_RECALL": 0.55, "GATE_MIN_PAGES": 100.0,
                 "GATE_PROJECTS": 32.0}
    assert gt_gate.load_gate(TOOLS / "gt_gate.txt") == g          # the committed pin file IS the frozen gate
    assert gt_gate.FROZEN == g


@pytest.mark.parametrize("key,val", [(k, v) for k, vs in WEAKER.items() for v in vs]
                         + [(k, v) for k, vs in OTHER.items() for v in vs])
def test_every_changed_or_weakened_value_fails_closed(tmp_path, key, val):
    with pytest.raises(ValueError, match=key):
        gt_gate.load_gate(write(tmp_path, canon_lines(**{key: val})))


@pytest.mark.parametrize("key", list(CANON))
def test_every_missing_key_fails_closed(tmp_path, key):
    with pytest.raises(ValueError, match="missing"):
        gt_gate.load_gate(write(tmp_path, [x for x in canon_lines() if not x.startswith(key + "=")]))


@pytest.mark.parametrize("key", list(CANON))
@pytest.mark.parametrize("second", ["same", "weaker"])
def test_every_duplicated_key_fails_closed(tmp_path, key, second):
    v = CANON[key] if second == "same" else WEAKER[key][0]
    with pytest.raises(ValueError, match="twice"):
        gt_gate.load_gate(write(tmp_path, canon_lines() + [f"{key}={v}"]))


@pytest.mark.parametrize("key", list(CANON))
@pytest.mark.parametrize("val", MALFORMED)
def test_every_malformed_value_fails_closed(tmp_path, key, val):
    with pytest.raises(ValueError):
        gt_gate.load_gate(write(tmp_path, canon_lines(**{key: val})))


@pytest.mark.parametrize("line", ["GATE_EXTRA=0", "GATE_MAX_FP_X=0", "gate_max_fp_v=0", "GATE MAX=0",
                                  "GATE_MAX_FP_V", "GATE_MAX_FP_V==0", "GATE_MAX_FP_V=0=0", "=0", "junk"])
def test_unexpected_or_malformed_lines_fail_closed(tmp_path, line):
    with pytest.raises(ValueError):
        gt_gate.load_gate(write(tmp_path, canon_lines() + [line]))


def test_altered_pin_file_makes_the_cli_fail(tmp_path):
    logf = tmp_path / "gt_eval.log"
    logf.write_text(raw_log())
    ok = subprocess.run([sys.executable, str(TOOLS / "gt_gate.py"), str(logf), str(write(tmp_path, canon_lines()))],
                        capture_output=True, text=True)
    assert ok.returncode == 0 and "M4_GATE PASS" in ok.stdout, ok.stdout + ok.stderr
    bad = write(tmp_path, canon_lines(GATE_MIN_PIN_RECALL="0.5"))
    r = subprocess.run([sys.executable, str(TOOLS / "gt_gate.py"), str(logf), str(bad)], capture_output=True, text=True)
    assert r.returncode != 0 and "M4_GATE PASS" not in r.stdout


# ---------------------------------------------------------------- exact decimal semantics (no float rounding)

TINY = {  # values that round to the frozen float (or very near it) but are NOT the frozen value
    "GATE_MAX_FP_V": ["0.00000000000000000001", "0.0000000000000000000000000000001"],
    "GATE_MAX_FP_I": ["0.00000000000000000001", "0.0000000000000000000000000000001"],
    "GATE_MIN_PIN_RECALL": ["0.54999999999999999", "0.549999999999999999999999", "0.55000000000000001",
                            "0.550000000000000000000001"],
    "GATE_MIN_PAGES": ["99.999999999999999", "99.99999999999999999999", "100.00000000000001",
                       "100.0000000000000000000001"],
    "GATE_PROJECTS": ["31.999999999999999", "31.99999999999999999999", "32.000000000000001",
                      "32.0000000000000000000001"],
}


@pytest.mark.parametrize("key,val", [(k, v) for k, vs in TINY.items() for v in vs])
def test_tiny_deviation_that_rounds_to_the_same_float_still_fails(tmp_path, key, val):
    assert float(val) == gt_gate.FROZEN[key] or abs(float(val) - gt_gate.FROZEN[key]) < 1e-12
    with pytest.raises(ValueError, match=key):
        gt_gate.load_gate(write(tmp_path, canon_lines(**{key: val})))


@pytest.mark.parametrize("key,val", [("GATE_MIN_PIN_RECALL", "0.550"), ("GATE_MIN_PAGES", "100.0"),
                                     ("GATE_PROJECTS", "32.000"), ("GATE_MAX_FP_V", "0.0")])
def test_equal_decimal_spellings_are_the_same_pin(tmp_path, key, val):
    assert gt_gate.load_gate(write(tmp_path, canon_lines(**{key: val}))) == gt_gate.FROZEN


def test_load_gate_returns_the_frozen_constants():
    assert gt_gate.FROZEN_TEXT == CANON
    assert gt_gate.load_gate(TOOLS / "gt_gate.txt") == gt_gate.FROZEN


@pytest.mark.parametrize("raw,ok", [("0.55", True), ("0.5509", True), ("0.54999999999999999", False),
                                    ("0.549999999999999999999", False), ("0.55000000000000001", True)])
def test_log_pin_recall_is_compared_exactly(raw, ok):
    text = raw_log().replace('"pin_recall": 0.5509', f'"pin_recall": {raw}')
    assert f'"pin_recall": {raw}' in text
    assert (gt_gate.check(text, G) == []) is ok, gt_gate.check(text, G)


@pytest.mark.parametrize("field,raw", [("FP_V", "0.00000000000000000001"), ("FP_I", "1e-30"),
                                       ("FP_V", "-0.00000000000000000001")])
def test_log_fp_counts_are_compared_exactly(field, raw):
    fp = {"FP_V": "0", "FP_I": "0", field: raw}
    text = raw_log().replace('"detected": {"FP_V": 0, "FP_I": 0}',
                             '"detected": {"FP_V": %s, "FP_I": %s}' % (fp["FP_V"], fp["FP_I"]))
    assert raw in text
    assert any(f"detected.{field}" in p for p in gt_gate.check(text, G))


@pytest.mark.parametrize("field,raw", [("evaluated", "99.99999999999999999"), ("projects", "32.0000000000000001"),
                                       ("projects", "31.99999999999999999")])
def test_log_page_and_project_counts_are_compared_exactly(field, raw):
    text = raw_log().replace(f'"{field}": {100 if field == "evaluated" else 32}', f'"{field}": {raw}')
    assert raw in text
    assert any(p.startswith(field + "=") for p in gt_gate.check(text, G))


@pytest.mark.parametrize("dup", ['"FP_V": 0, "FP_V": 5', '"FP_V": 5, "FP_V": 0'])
def test_duplicate_json_keys_in_a_record_fail(dup):
    text = raw_log().replace('"detected": {"FP_V": 0', '"detected": {' + dup, 1)
    assert dup in text
    assert any("duplicate JSON keys" in p for p in gt_gate.check(text, G))
