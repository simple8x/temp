from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from ecci.classify import classify_page
from ecci.ingest_vector import ingest_pdf
from ecci.kicad_lib import Library, load_library
from ecci.pins import PinParams, detect_pins
from ecci.symbols import _gap, body_clusters
from pdfgen import one_page
from render_kicad import draw_symbol, expected_tips_norm

ALL = load_library(str(Path(__file__).resolve().parents[1] / "fixtures" / "lib_min.kicad_sym"))
LIB = Library(ALL.path, ALL.sha256, {k: v for k, v in ALL.symbols.items() if k != "R_LONG"})
W, H = 300, 200
WIRES = b"0.45 w 10 190 m 80 190 l S 10 185 m 10 150 l S 90 185 m 90 150 l S\n"


def page(content: bytes):
    pi = ingest_pdf(one_page(WIRES + content, size=(W, H))).pages[0]
    return pi, classify_page(pi)


def sym(name, orient="r0", s=3.0, t=(150.0, 100.0)):
    return draw_symbol(LIB.symbols[name], orient, s, t, body_w=0.9, pin_w=0.45)


def pins(content, gap_frac=0.5, symbols=None):
    pi, cls = page(content)
    return detect_pins(pi, cls, list((symbols or LIB.symbols).values()), PinParams(cluster_gap_frac=gap_frac))


def test_gap_function():
    assert _gap((0, 0, 10, 10), (5, 5, 6, 6)) == 0.0                 # containment
    assert _gap((0, 0, 10, 10), (10, 0, 12, 10)) == 0.0              # touching
    assert _gap((0, 0, 10, 10), (13, 14, 20, 20)) == pytest.approx(5.0)   # 3-4-5


def test_m1_led_arrows_merge_into_the_led_cluster_and_match_led_not_d():
    (cr,) = pins(sym("LED"))
    assert cr.resolution.symbols == ("LED",) and len(cr.cluster.prim_ids) == 6
    assert all(p.pos_state == "CONFIRMED" for p in cr.pins)
    exp = expected_tips_norm(LIB.symbols["LED"], "r0", 3.0, (150.0, 100.0), H)
    for num, tip in exp.items():
        assert any(abs(p.tip[0] - tip[0]) < 0.3 and abs(p.tip[1] - tip[1]) < 0.3 and num in p.numbers
                   for p in cr.pins)


def test_m1b_plain_diode_still_matches_d_and_not_led():
    (cr,) = pins(sym("D"))
    assert cr.resolution.symbols == ("D",) and len(cr.cluster.prim_ids) == 2


def test_m1c_without_gap_merging_led_arrows_become_separate_clusters_and_d_matches_wrongly():
    # documents the failure that gap merging fixes (old M2 behaviour)
    crs = pins(sym("LED"), gap_frac=0.0)
    assert len(crs) > 1 and any(cr.resolution.symbols == ("D",) for cr in crs)


def test_m2_capacitor_plates_merge_and_match():
    (cr,) = pins(sym("C"))
    assert cr.resolution.symbols == ("C",) and len(cr.cluster.prim_ids) == 2
    assert [p.pos_state for p in cr.pins] == ["CONFIRMED", "CONFIRMED"]
    assert all(p.id_state == "AMBIGUOUS" for p in cr.pins)


def test_m2b_without_gap_merging_the_plates_stay_apart_and_nothing_matches():
    crs = pins(sym("C"), gap_frac=0.0)
    assert len(crs) == 2 and all(cr.pins == [] for cr in crs)


def test_m3_neighbouring_symbols_are_not_merged():
    content = sym("R", t=(100.0, 100.0)) + sym("R", t=(125.0, 100.0))     # bodies 12.2 pt apart
    crs = pins(content)
    assert len(crs) == 2 and all(len(cr.cluster.prim_ids) == 1 for cr in crs)


def test_m4_tiny_part_near_big_body_is_not_swallowed():
    big = b"0.9 w 100 100 40 40 re S"
    tiny = b"0.9 w 146 100 m 147 100 l 147 101 l S"       # 1 pt part, 6 pt from the body
    pi, cls = page(big + tiny)
    clusters = body_clusters(pi, cls)
    assert len(clusters) == 2
    near = b"0.9 w 140.3 100 m 141.3 100 l 141.3 101 l S"  # 0.3 pt from the body
    pi2, cls2 = page(big + near)
    assert len(body_clusters(pi2, cls2)) == 2              # parts below min_gap_part_frac merge only by touching
    assert len(body_clusters(pi2, cls2, min_gap_part_frac=0.0)) == 1
    touch = b"0.9 w 140 100 m 141 100 l 141 101 l S"       # touching: merged whatever the size
    pi3, cls3 = page(big + touch)
    assert len(body_clusters(pi3, cls3)) == 1


def test_glyph_sized_parts_do_not_chain_into_one_cluster():
    strokes = b"".join(b"0.45 w %.1f 100 m %.1f 100 l S\n" % (20 + 1.2 * i, 21 + 1.2 * i) for i in range(30))
    pi, cls = page(strokes)
    assert len(body_clusters(pi, cls)) == 30               # 1 pt strokes 0.2 pt apart stay separate


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.tuples(st.integers(20, 240), st.integers(20, 120), st.integers(3, 25), st.integers(3, 25)),
                min_size=1, max_size=7))
def test_cluster_count_never_increases_with_gap_frac(rects):
    content = b"".join(b"0.9 w %d %d %d %d re S\n" % r for r in rects)
    pi, cls = page(content)
    counts = [len(body_clusters(pi, cls, gap_frac=g)) for g in (0.0, 0.25, 0.5, 1.0, 2.0)]
    assert counts == sorted(counts, reverse=True)
