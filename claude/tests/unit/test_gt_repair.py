"""GT forensic repair: fail-closed reader fixes F1-F3, separated claims, anti-beautification guards.
The baseline reader is the frozen 873bfbb copy (tools/baseline/gt_kicad_873bfbb.py)."""
import ast
import json
import importlib.util
import itertools
import math
import pathlib
import random
import subprocess
import sys

import pytest
from hypothesis import given, settings, strategies as st

from ecci import gt_kicad as G

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import gt_audit  # noqa: E402

LIB = """(lib_symbols
  (symbol "Device:R"
   (symbol "R_1_1"
    (pin passive line (at 0 3.81 270) (length 1.27) (name "~") (number "1"))
    (pin passive line (at 0 -3.81 90) (length 1.27) (name "~") (number "2"))))
  (symbol "Device:X"
   (symbol "X_1_1"
    (pin passive line (at 0 3.81 270) (length 1.27) (name "~") (number "1"))
    (pin passive line (at 2.54 -1.27 180) (length 1.27) (name "~") (number "2")))))"""


def sym(ref, x, y, rot=0, mirror=None, extra="", lib="Device:R"):
    m = f" (mirror {mirror})" if mirror else ""
    return (f'(symbol (lib_id "{lib}") (at {x} {y} {rot}){m} (unit 1){extra} (uuid "u-{ref}-{x}-{y}")'
            f' (property "Reference" "{ref}" (at 0 0 0)))')


def write(tmp, syms, nets, wires=()):
    s, x = tmp / "a.kicad_sch", tmp / "a.xml"
    w = " ".join(f"(wire (pts (xy {a} {b}) (xy {c} {d})))" for a, b, c, d in wires)
    s.write_text(f'(kicad_sch (version 20250114) (generator "eeschema") {LIB} {" ".join(syms)} {w})')
    body = "".join(f'<net code="{i}" name="{n}">' + "".join(f'<node ref="{r}" pin="{p}"/>' for r, p in nodes) + "</net>"
                   for i, (n, nodes) in enumerate(nets.items(), 1))
    x.write_text(f"<export><nets>{body}</nets></export>")
    return str(s), str(x)


BASE = gt_audit.load_baseline()


def recs_by(recs):
    return {(r["ref"], r["number"]): r for r in recs}


# ---- the frozen baseline is the 873bfbb blob ---------------------------------------------------------------
def test_frozen_baseline_matches_873bfbb_blob():
    try:
        blob = subprocess.run(["git", "show", "873bfbb:claude/ecci/gt_kicad.py"], capture_output=True, text=True,
                              cwd=ROOT, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("873bfbb not in this clone (shallow)")
    frozen = gt_audit.BASELINE_FILE.read_text().replace("from ecci.sexpr import", "from .sexpr import")
    assert frozen == blob


# ---- F1: a node on several nets is CONFLICT, never last-wins ----------------------------------------------
def test_f1_netlist_conflict_is_conflict_not_last_wins(tmp_path):
    s, x = write(tmp_path, [sym("R1", 50, 50)], {"/A": [("R1", "1")], "/B": [("R1", "1"), ("R1", "2")]})
    old, _ = BASE.gt_pins(s, x)
    assert {p.number: p.net for p in old}["1"] == "/B"               # the defect: last net silently wins
    recs, info = G.gt_pin_records(s, x)
    r = recs_by(recs)[("R1", "1")]
    assert r["net"] is None and r["net_status"] == "CONFLICT" and r["status"] == "CONFLICT"
    assert recs_by(recs)[("R1", "2")]["net"] == "/B" and info["netlist_conflicts"] == 1
    assert G.parse_netlist_checked(x)[1] == {("R1", "1"): ["/A", "/B"]}
    assert G.parse_netlist_xml(x) == {("R1", "2"): "/B"}            # the public map never guesses either
    t = gt_audit.audit_task(BASE, s, x, None, "p")
    assert [e["rule"] for e in t["ledger"]] == ["F1"] and not t["ledger"][0]["promotion"]
    assert gt_audit.check([t]) == []
    assert t["denominator_before"] - t["denominator_after"] == 1


# ---- F2: same point means the same point ------------------------------------------------------------------
def test_f2_pins_apart_are_not_a_same_point_conflict(tmp_path):
    # R2 pin 1 sits 5 um (0.014 pt) from R1 pin 1: distinct points in KiCad (100 nm IU), different nets are
    # consistent; both land in one baseline 0.1 pt cell, so the baseline dropped both symbols from scoring
    s, x = write(tmp_path, [sym("R1", 50, 50), sym("R2", 50.005, 50)],
                 {"/A": [("R1", "1")], "/B": [("R2", "1")], "/C": [("R1", "2")], "/D": [("R2", "2")]})
    old, oi = BASE.gt_pins(s, x)
    recs, info = G.gt_pin_records(s, x)
    assert info["inconsistent_refs"] == 0 and all(r["net"] for r in recs)
    assert oi["inconsistent_refs"] == 2 and all(p.net is None for p in old)
    t = gt_audit.audit_task(BASE, s, x, None, "p")
    assert {e["rule"] for e in t["ledger"]} == {"F2"} and all(e["promotion"] for e in t["ledger"])
    assert gt_audit.check([t]) == [] and t["denominator_after"] - t["denominator_before"] == 4


def test_f2_coincident_pins_on_different_nets_still_conflict(tmp_path):
    s, x = write(tmp_path, [sym("R1", 50, 50), sym("R2", 50, 50)],
                 {"/A": [("R1", "1")], "/B": [("R2", "1")], "/C": [("R1", "2"), ("R2", "2")]})
    recs, info = G.gt_pin_records(s, x)
    assert info["inconsistent_refs"] == 2 and all(r["net"] is None for r in recs)
    assert all(r["status"] == "CONFLICT" for r in recs)


def test_f2_border_split_is_caught_with_neighbour_cells(tmp_path):
    # pin 1 of R1 at x=49.9998 mm and of R2 at x=50.0002 mm: 0.4 um apart (one point at 100 nm IU) but in
    # different 1 um cells. Different nets at one point = conflict; only the neighbour scan sees it.
    s, x = write(tmp_path, [sym("R1", 49.9998, 50), sym("R2", 50.0002, 50)],
                 {"/A": [("R1", "1")], "/B": [("R2", "1")], "/C": [("R1", "2")], "/D": [("R2", "2")]})
    cs = G.SAME_POINT_MM * G.MM_TO_PT
    assert math.floor(49.9998 * G.MM_TO_PT / cs) != math.floor(50.0002 * G.MM_TO_PT / cs)
    recs, info = G.gt_pin_records(s, x)
    assert info["inconsistent_refs"] == 2 and all(r["net"] is None and r["status"] == "CONFLICT" for r in recs)


def test_gt_pins_impl_selection_fails_closed(monkeypatch):
    monkeypatch.setenv("GT_PINS_IMPL", "873bfbb")
    assert gt_audit.select_gt_pins().__module__ == "gt_kicad_873bfbb"
    monkeypatch.setenv("GT_PINS_IMPL", "repaired")
    assert gt_audit.select_gt_pins() is G.gt_pins
    monkeypatch.setenv("GT_PINS_IMPL", "latest")
    with pytest.raises(SystemExit):
        gt_audit.select_gt_pins()


# ---- F3: unhandled body_style fails closed ----------------------------------------------------------------
def test_f3_body_style_without_convert_is_unknown(tmp_path):
    s, x = write(tmp_path, [sym("R1", 50, 50, extra=" (body_style 2)")], {"/A": [("R1", "1")], "/B": [("R1", "2")]})
    recs, info = G.gt_pin_records(s, x)
    assert info["body_style_unhandled"] == 1
    assert all(r["net"] is None and r["position_status"] == "UNKNOWN" and "body_style_unhandled" in r["reasons"]
               for r in recs)
    t = gt_audit.audit_task(BASE, s, x, None, "p")
    assert {e["rule"] for e in t["ledger"]} == {"F3"} and gt_audit.check([t]) == []


# ---- case catalogue: duplicate, missing pin, wrong pin number, ambiguous identity ------------------------
def test_duplicate_reference_is_unknown_and_kept(tmp_path):
    s, x = write(tmp_path, [sym("R1", 50, 50), sym("R1", 80, 50)], {"/A": [("R1", "1")], "/B": [("R1", "2")]})
    recs, _ = G.gt_pin_records(s, x)
    assert len(recs) == 4 and all(r["net"] is None and "duplicate_ref_pin" in r["reasons"] for r in recs)
    assert all(r["status"] != "VERIFIED" for r in recs)


def test_missing_netlist_node_keeps_the_record_as_unknown(tmp_path):
    s, x = write(tmp_path, [sym("R1", 50, 50)], {"/A": [("R1", "1")]})
    recs, _ = G.gt_pin_records(s, x)
    r = recs_by(recs)[("R1", "2")]
    assert r["net"] is None and r["net_status"] == "UNKNOWN" and "node_not_in_netlist" in r["reasons"]
    assert len(recs) == 2                                   # an FN cannot vanish: the record stays


def test_wrong_pin_number_in_netlist_creates_no_record_and_leaves_real_pin_unknown(tmp_path):
    s, x = write(tmp_path, [sym("R1", 50, 50)], {"/A": [("R1", "3")], "/B": [("R1", "2")]})
    recs, _ = G.gt_pin_records(s, x)
    assert sorted(r["number"] for r in recs) == ["1", "2"]
    assert recs_by(recs)[("R1", "1")]["net_status"] == "UNKNOWN"


def test_ambiguous_identity_multi_instance_is_unknown(tmp_path):
    inst = ' (instances (project "p" (path "/a" (reference "R1")) (path "/b" (reference "R7"))))'
    s, x = write(tmp_path, [sym("R1", 50, 50, extra=inst)], {"/A": [("R1", "1")], "/B": [("R1", "2")]})
    recs, info = G.gt_pin_records(s, x)
    pins, _ = G.gt_pins(s, x)
    assert pins == [] and info["ambiguous_instances"] == 1
    assert recs and all(r["status"] == "UNKNOWN" and "multi_instance_reference" in r["reasons"] for r in recs)


def test_orientation_by_vote_is_inferred_not_verified(tmp_path):
    a = G._place(0, 3.81, (50, 50, 90), "x", "MR")
    s, x = write(tmp_path, [sym("U1", 50, 50, 90, "x", lib="Device:X")], {"/A": [("U1", "1")], "/B": [("U1", "2")]},
                 wires=[(a[0], a[1], a[0] + 5, a[1])])
    recs, _ = G.gt_pin_records(s, x)
    assert all("orientation_by_wire_end_vote" in r["reasons"] for r in recs)
    assert all(r["position_status"] == "INFERRED" and r["status"] == "INFERRED" and r["net"] for r in recs)
    s, x = write(tmp_path, [sym("U1", 50, 50, 0, None, lib="Device:X")], {"/A": [("U1", "1")], "/B": [("U1", "2")]})
    assert all(r["status"] == "VERIFIED" for r in G.gt_pin_records(s, x)[0])   # order-invariant placement


# ---- no promotion path -------------------------------------------------------------------------------------
STATES = ["VERIFIED", "INFERRED", "UNKNOWN", "CONFLICT"]


@pytest.mark.parametrize("pos,net,cons", list(itertools.product(STATES, STATES, ["OK", "CONFLICT"])))
def test_claim_status_is_never_stronger_than_any_claim(pos, net, cons):
    s_ = G.claim_status({"position_status": pos, "net_status": net, "consistency": cons})
    rank = G._RANK
    assert rank[s_] <= rank[pos] and rank[s_] <= rank[net] and (cons == "OK" or s_ == "CONFLICT")
    if "UNKNOWN" in (pos, net):
        assert s_ != "VERIFIED"


def test_claim_status_rejects_unknown_states():
    with pytest.raises(ValueError):
        G.claim_status({"position_status": "PROMOTABLE", "net_status": "VERIFIED"})


def test_audit_check_rejects_promotion_and_unexplained_changes():
    led = {"record_id": "x", "before": None, "after": "/A", "rule": "F1", "status": "VERIFIED", "promotion": True}
    t = {"page": "p", "problems": [], "denominator_before": 0, "denominator_after": 1, "ledger": [led]}
    assert any("promotion" in e for e in gt_audit.check([t]))
    t["ledger"] = [led | {"rule": None, "promotion": False}]
    assert any("unexplained" in e for e in gt_audit.check([t]))
    t["ledger"] = []
    assert any("denominator" in e for e in gt_audit.check([t]))       # a silent denominator change fails
    t["problems"] = ["record_set_differs"]
    assert any("record_set_differs" in e for e in gt_audit.check([t]))


# ---- GT does not feed prediction ---------------------------------------------------------------------------
def test_prediction_modules_never_import_ground_truth():
    gt_mods = {"gt_kicad", "gt_audit", "gt_eval", "m4_gt"}
    for f in (ROOT / "ecci").glob("*.py"):
        if f.stem in ("gt_kicad", "m4_pipeline", "kicad_roundtrip"):   # GT reader / evaluation-side comparison
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            assert not any(n.split(".")[-1] in gt_mods for n in names), f"{f.name} imports GT"


def test_gt_repair_does_not_change_the_netlist_independent_parts(tmp_path):
    # changing ONLY the netlist changes nets/claims, never pin identity or position (what M4 is compared on)
    s, x = write(tmp_path, [sym("R1", 50, 50), sym("R2", 70, 50, 90)], {"/A": [("R1", "1"), ("R2", "1")]})
    a, _ = G.gt_pin_records(s, x)
    pathlib.Path(x).write_text("<export><nets></nets></export>")
    b, _ = G.gt_pin_records(s, x)
    key = lambda rs: sorted((r["ref"], r["number"], r["x"], r["y"]) for r in rs)  # noqa: E731
    assert key(a) == key(b) and len(a) == len(b)


# ---- holdout: random schematics without F1-F3 triggers give the baseline output exactly --------------------
@settings(max_examples=60, deadline=None)
@given(st.integers(0, 10_000))
def test_repaired_equals_baseline_when_no_documented_rule_applies(tmp_path_factory, seed):
    rnd = random.Random(seed)
    tmp = tmp_path_factory.mktemp("h")
    syms, refs = [], []
    for i in range(rnd.randint(1, 6)):
        ref = f"R{rnd.choice([i, i, i, 0])}"                     # sometimes a duplicate reference
        refs.append(ref)
        syms.append(sym(ref, 10 + 12.7 * rnd.randint(0, 8), 10 + 12.7 * rnd.randint(0, 8),
                        rnd.choice([0, 90, 180, 270]), rnd.choice([None, "x", "y"]),
                        lib=rnd.choice(["Device:R", "Device:X"])))
    nets = {}
    for ref in sorted(set(refs)):
        for pin in ("1", "2"):
            if rnd.random() < 0.9:
                nets.setdefault(f"/N{rnd.randint(0, 3)}", []).append((ref, pin))
    wires = [(12.7 * rnd.randint(0, 9), 12.7 * rnd.randint(0, 9), 12.7 * rnd.randint(0, 9), 12.7 * rnd.randint(0, 9))
             for _ in range(rnd.randint(0, 5))]
    s, x = write(tmp, syms, nets, wires)
    old, oi = BASE.gt_pins(s, x)
    new, ni = G.gt_pins(s, x)
    t = gt_audit.audit_task(BASE, s, x, None, "h")
    assert gt_audit.check([t]) == []
    tup = lambda ps: [(p.ref, p.number, p.x, p.y, p.net, p.hidden, p.lib_id) for p in ps]  # noqa: E731
    if not t["ledger"]:
        assert tup(old) == tup(new)
    assert {k: oi[k] for k in oi} == {k: ni[k] for k in oi}


# ---- before/after: same prediction or fail ------------------------------------------------------------------
def _log(path, detected, local_found, rec_status="DONE"):
    page = {"project": "p", "sheet": "a.kicad_sch", "instance": None, "detected_pins": detected, "prims": 10,
            "clusters": 3, "wire_objects": 4, "recovery": {"status": rec_status, "promotable_pins": 1, "candidates": {"UNKNOWN": 2}}}
    tot = {"detected": {"pos": 5, "neg": 7, "FP_V": 0, "FP_I": 0}, "detected_local": {"pins": 9, "pin_recall": local_found / 9},
           "coverage": {"page_coverage": 1.0}, "inventory_consistent": True, "recovery": {"new_tp": 1}}
    path.write_text("GT_EVAL " + json.dumps(page) + "\nGT_TOTAL " + json.dumps(tot) + "\n")
    return str(path)


def test_before_after_requires_identical_prediction(tmp_path):
    import gt_before_after as ba
    a, b = _log(tmp_path / "a.log", 3, 3), _log(tmp_path / "b.log", 3, 2)
    assert ba.gt_mode(a, b, tmp_path / "o") == 0                 # GT differs, prediction identical: reported
    r = json.loads((tmp_path / "o" / "recovery_comparison.json").read_text())
    assert r["prediction_identical"] and r["gt_metrics"]["baseline_gt"]["local_pins_denominator"] == 9
    c = _log(tmp_path / "c.log", 4, 3)
    assert ba.gt_mode(a, c, tmp_path / "o2") == 1                # prediction moved with the GT run: fail
    d = _log(tmp_path / "d.log", 3, 3, "SKIPPED_BUDGET")
    assert ba.gt_mode(a, d, tmp_path / "o3") == 0
    assert json.loads((tmp_path / "o3" / "recovery_comparison.json").read_text())["recovery_budget_dependent_pages"]
