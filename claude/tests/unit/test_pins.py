from pathlib import Path
import math

import pytest

from ecci.classify import classify_page
from ecci.evidence import independent
from ecci.ingest_vector import ingest_pdf
from ecci.kicad_lib import Library, load_library
from ecci.pins import POS_CANDIDATE, POS_CONFIRMED, detect_pins
from pdfgen import one_page
from render_kicad import ORIENTS, draw_symbol, expected_tips_norm

LIB_ALL = load_library(str(Path(__file__).resolve().parents[1] / "fixtures" / "lib_min.kicad_sym"))
# R_LONG has the same body as R but different pins: it is only used by the ambiguity tests.
LIB = Library(LIB_ALL.path, LIB_ALL.sha256, {k: v for k, v in LIB_ALL.symbols.items() if k != "R_LONG"})
W, H = 300, 200
BODY_W, PIN_W = 0.9, 0.45
WIRES = b"0.45 w 10 190 m 80 190 l S 10 185 m 10 150 l S 90 185 m 90 150 l S\n"


def run(content: bytes, symbols=None):
    pi = ingest_pdf(one_page(WIRES + content, size=(W, H))).pages[0]
    cls = classify_page(pi)
    return pi, cls, detect_pins(pi, cls, list((symbols or LIB.symbols).values()))


def sym(name, orient="r0", s=3.0, t=(150.0, 100.0), **kw):
    return draw_symbol(LIB.symbols[name], orient, s, t, body_w=BODY_W, pin_w=PIN_W, **kw)


def near(a, b, tol=0.05):
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


def tip_of(cr, number):
    return next(p for p in cr.pins if number in p.numbers)


def test_p1_resistor_with_stubs_positions_confirmed_by_independent_sources():
    _, cls, (cr,) = run(sym("R"))
    exp = expected_tips_norm(LIB.symbols["R"], "r0", 3.0, (150.0, 100.0), H)
    assert len(cr.pins) == 2 and not cr.conflicts and not cr.unresolved
    for pin in cr.pins:
        assert pin.pos_state == POS_CONFIRMED and len(pin.independent_items) == 2
        assert any(near(pin.tip, e) for e in exp.values())
        s1, s2 = pin.evidence
        assert independent(s1, s2, cls.registry)


def test_p4_symmetric_resistor_has_ambiguous_numbers_not_guessed():
    _, _, (cr,) = run(sym("R"))
    for pin in cr.pins:
        assert pin.id_state == "AMBIGUOUS" and pin.numbers == ("1", "2")


def test_p2_no_stub_drawn_gives_candidate_only():
    _, _, (cr,) = run(sym("R", pins=False))
    assert [p.pos_state for p in cr.pins] == [POS_CANDIDATE, POS_CANDIDATE]
    assert all(len(p.evidence) == 1 and p.independent_items == () or len(p.independent_items) == 1
               for p in cr.pins)


def test_p3_unknown_symbol_gives_unresolved_terminals_not_pins():
    content = b"0.9 w 120 90 60 20 re S 0.45 w 120 100 m 100 100 l S 180 100 m 200 100 l S"
    _, _, (cr,) = run(content)
    assert cr.resolution.status == "NONE" and cr.pins == []
    assert len(cr.unresolved) == 2 and all(u.evidence.startswith("S3:") for u in cr.unresolved)
    xs = sorted(round(u.contact[0]) for u in cr.unresolved)
    assert xs == [120, 180]
    ys = {round(u.far_end[0]) for u in cr.unresolved}
    assert ys == {100, 200}                 # far ends are reported but not claimed as tips


def test_p5_holdout_symbol_missing_from_library_does_not_crash():
    sub = Library("x", "0" * 64, {k: v for k, v in LIB.symbols.items() if k != "R"})
    _, _, (cr,) = run(sym("R"), symbols=sub.symbols)
    assert cr.resolution.status == "NONE" and cr.pins == [] and len(cr.unresolved) == 2


def test_p6_near_identical_templates_with_different_pins_is_ambiguous():
    # R and R_LONG share the same body but predict different pin tips: template source unusable
    pi = ingest_pdf(one_page(WIRES + sym("R"), size=(W, H))).pages[0]
    (cr,) = detect_pins(pi, classify_page(pi), list(LIB_ALL.symbols.values()))
    assert {h.symbol.name for h in cr.resolution.hits} >= {"R", "R_LONG"}
    assert cr.resolution.status == "DIFFERENT_TIP_SETS"
    assert cr.pins == [] and len(cr.unresolved) == 2          # stubs seen, but not claimed as pins
    assert cr.resolution.id_state == "AMBIGUOUS"


def test_p6b_unique_when_only_one_template_is_in_the_library():
    _, _, (cr,) = run(sym("R"))
    assert cr.resolution.status == "UNIQUE" and len(cr.pins) == 2


def test_p7_distorted_body_is_rejected():
    content = (b"0.9 w 140 90 m 164 90 l 164 110 l 140 110 l h S")     # 24 x 20, R body is 2.032 x 5.08
    _, _, (cr,) = run(content)
    assert cr.resolution.status == "NONE" and cr.pins == []


@pytest.mark.parametrize("orient", sorted(ORIENTS))
def test_p7b_asymmetric_symbol_found_in_every_orientation_with_correct_numbers(orient):
    s, t = 4.0, (150.0, 100.0)
    _, _, (cr,) = run(sym("D", orient, s, t))
    exp = expected_tips_norm(LIB.symbols["D"], orient, s, t, H)
    assert cr.resolution.status == "UNIQUE" and len(cr.pins) == 2
    for pin in cr.pins:
        assert pin.id_state == "CONFIRMED" and pin.pos_state == POS_CONFIRMED
        (num,) = pin.numbers
        assert near(pin.tip, exp[num], 0.05 + 0.02 * s)


def test_p8_unexpected_contact_is_flagged_as_conflict():
    extra = b"0.45 w 146.95 100 m 120 100 l S"               # touches the left side of the R body
    _, _, (cr,) = run(sym("R") + extra)
    assert len(cr.conflicts) == 1 and cr.conflicts[0].kind == "UNEXPECTED_CONTACT"
    assert near(cr.conflicts[0].point, (146.95, 100.0), 0.2)


def test_lineage_tokens_record_the_dependency_structure():
    _, cls, (cr,) = run(sym("R"))
    pin = cr.pins[0]
    s1, s2 = (cls.registry.items[i] for i in pin.evidence)
    body = {f"geom:{i}" for i in cr.cluster.prim_ids}
    assert s1.lineage == body | {LIB.symbols["R"].lib_token}
    assert len(s2.lineage) == 1 and not (s2.lineage & s1.lineage)
    assert not s1.uses_connectivity and not s2.uses_connectivity


def test_multiple_symbols_are_separate_clusters():
    content = sym("R", t=(60.0, 100.0)) + sym("D", "r90", 3.0, (200.0, 100.0))
    _, _, crs = run(content)
    assert len(crs) == 2 and sorted(len(c.pins) for c in crs) == [2, 2]


def test_circle_and_arc_symbol_matches():
    _, _, (cr,) = run(sym("OSC", "r0", 3.0, (150.0, 100.0)))
    assert cr.resolution.status == "UNIQUE" and all(p.pos_state == POS_CONFIRMED for p in cr.pins)
    assert all(p.id_state == "CONFIRMED" for p in cr.pins)


def test_no_symbols_in_library_and_empty_page():
    pi = ingest_pdf(one_page(WIRES, size=(W, H))).pages[0]
    cls = classify_page(pi)
    assert detect_pins(pi, cls, list(LIB.symbols.values())) == []
    pi2 = ingest_pdf(one_page(WIRES + sym("R"), size=(W, H))).pages[0]
    (cr,) = detect_pins(pi2, classify_page(pi2), [])
    assert cr.pins == [] and cr.resolution.status == "NONE"


def test_p7c_extra_segment_inside_body_is_rejected_even_though_all_vertices_fit():
    # R body plus a diagonal between two corners: every vertex lies on the template outline and
    # the whole template outline is covered, but the diagonal is not part of the R symbol.
    body = sym("R", pins=False)
    diag = b"0.9 w 146.952 107.62 m 153.048 92.38 l S"      # corner to opposite corner (user space)
    _, _, crs = run(body + diag)
    assert all(cr.resolution.status == "NONE" and cr.pins == [] for cr in crs)


def test_regression_wire_with_both_ends_on_one_body_does_not_crash():
    # found on a real PDF: both ends of one wire segment touch the same cluster
    content = b"0.9 w 120 90 60 20 re S 0.45 w 130 90 m 130 110 l S"
    _, _, (cr,) = run(content)
    ids = [u.evidence for u in cr.unresolved]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_detect_pins_is_repeatable_on_the_same_classification():
    pi = ingest_pdf(one_page(WIRES + sym("R"), size=(W, H))).pages[0]
    cls = classify_page(pi)
    syms = list(LIB.symbols.values())
    a = detect_pins(pi, cls, syms)
    b = detect_pins(pi, cls, syms)
    assert [p.tip for cr in a for p in cr.pins] == [p.tip for cr in b for p in cr.pins]


MULTI_UNIT = """(kicad_symbol_lib (version 20231120) (generator test)
  (symbol "MU" (pin_names (offset 0))
    (symbol "MU_0_1"
      (rectangle (start -5.08 -7.62) (end 5.08 7.62) (stroke (width 0.254) (type default)) (fill (type background))))
    (symbol "MU_1_1"
      (pin input line (at -7.62 5.08 0) (length 2.54) (name "A") (number "1"))
      (pin input line (at -7.62 2.54 0) (length 2.54) (name "B") (number "2")))
    (symbol "MU_2_1"
      (pin input line (at -7.62 -2.54 0) (length 2.54) (name "C") (number "3"))
      (pin input line (at -7.62 -5.08 0) (length 2.54) (name "D") (number "4")))))
"""


def _multi_unit_lib(tmp_path):
    f = tmp_path / "mu.kicad_sym"
    f.write_text(MULTI_UNIT)
    return load_library(str(f))


def _draw_unit(lib, unit_name, with_pins=True):
    from render_kicad import draw_symbol
    return b"0 0.6 0 RG 0.45 w 10 190 m 40 190 l S\n0.5 0 0 RG\n" + draw_symbol(
        lib.symbols[unit_name], "r0", 6.0, (150.0, 100.0), body_w=0.45, pin_w=0.45, pins=with_pins)


def test_p8_units_of_one_part_are_told_apart_by_the_drawn_pin_lines(tmp_path):
    lib = _multi_unit_lib(tmp_path)
    assert set(lib.symbols) == {"MU", "MU#u2"}
    for unit in ("MU", "MU#u2"):
        pi = ingest_pdf(one_page(_draw_unit(lib, unit), size=(W, H))).pages[0]
        res = [r for r in detect_pins(pi, classify_page(pi), list(lib.symbols.values())) if r.cluster.bbox[2] - r.cluster.bbox[0] > 40]
        assert len(res) == 1
        cr = res[0]
        assert cr.resolution.status == "UNIQUE" and unit in cr.resolution.symbols
        # a vertically mirrored other unit puts its pins at the same places: the numbers stay ambiguous (never guessed)
        assert cr.resolution.id_state == "AMBIGUOUS" and all(len(p.numbers) == 2 for p in cr.pins)
        assert len(cr.pins) == 2
        # the lines picked the unit, so they are NOT a second independent source: pins stay candidates
        assert all(p.pos_state == POS_CANDIDATE and len(p.evidence) == 1 and p.evidence[0].startswith("S1:") for p in cr.pins)


def test_p8b_without_pin_lines_the_units_stay_ambiguous(tmp_path):
    lib = _multi_unit_lib(tmp_path)
    pi = ingest_pdf(one_page(_draw_unit(lib, "MU", with_pins=False), size=(W, H))).pages[0]
    res = [r for r in detect_pins(pi, classify_page(pi), list(lib.symbols.values())) if r.cluster.bbox[2] - r.cluster.bbox[0] > 40]
    assert len(res) == 1 and res[0].resolution.status == "DIFFERENT_TIP_SETS" and res[0].pins == []


def test_p8c_pin_lines_of_the_wrong_unit_do_not_select_the_other_unit(tmp_path):
    # body of MU drawn with the pin lines of NO unit (lines at unrelated places): still ambiguous, never guessed
    lib = _multi_unit_lib(tmp_path)
    ops = _draw_unit(lib, "MU", with_pins=False) + b"0.5 0 0 RG 0.45 w 120 140 m 100 140 l S 120 90 m 100 90 l S\n"
    pi = ingest_pdf(one_page(ops, size=(W, H))).pages[0]
    res = [r for r in detect_pins(pi, classify_page(pi), list(lib.symbols.values())) if r.cluster.bbox[2] - r.cluster.bbox[0] > 40]
    assert res and all(r.resolution.status != "UNIQUE" for r in res)
