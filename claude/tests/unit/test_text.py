from pathlib import Path

import pytest

from ecci.classify import LC_BODY, LC_TEXT, LC_WIRE, classify_page
from ecci.ingest_vector import ingest_pdf
from ecci.kicad_lib import Library, load_library
from ecci.pins import detect_pins
from pdfgen import one_page
from render_kicad import draw_symbol
from strokefont import text_content, text_width

ALL = load_library(str(Path(__file__).resolve().parents[1] / "fixtures" / "lib_min.kicad_sym"))
LIB = Library(ALL.path, ALL.sha256, {k: v for k, v in ALL.symbols.items() if k != "R_LONG"})
W, H = 300, 200
WIRES = b"0.45 w 10 190 m 80 190 l S 10 185 m 10 150 l S 90 185 m 90 150 l S\n"


def page(content: bytes, wires: bytes = WIRES):
    pi = ingest_pdf(one_page(wires + content, size=(W, H))).pages[0]
    return pi, classify_page(pi)


def classes_in_box(pi, cls, box):
    x0, y0, x1, y1 = box     # normalized page space (y down)
    out = []
    for p in pi.prims:
        cx = sum(q[0] for q in p.pts) / len(p.pts)
        cy = sum(q[1] for q in p.pts) / len(p.pts)
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            out.append(cls.records[p.id].cls)
    return out


def test_t1_text_row_in_wire_style_is_text_not_wire():
    pi, cls = page(text_content("RED", 100, 100, 7))       # letters R, E, D in wire style
    cs = classes_in_box(pi, cls, (95, 85, 130, 105))
    assert cs and set(cs) == {LC_TEXT}
    assert any("text:" in n for n in cls.notes)


def test_t2_manhattan_only_letters_are_not_wires():
    pi, cls = page(text_content("ELHTI", 100, 100, 7))     # E L H T I are made of axis-aligned strokes
    assert set(classes_in_box(pi, cls, (95, 85, 150, 105))) == {LC_TEXT}


def test_t3_lone_isolated_segment_stays_wire_and_is_not_text():
    pi, cls = page(b"0.45 w 200 100 m 206 100 l S")
    seg = pi.prims[-1]
    assert cls.records[seg.id].cls == LC_WIRE


def test_t3b_two_letters_need_strict_equal_height_and_baseline():
    same, c1 = page(text_content("RD", 100, 100, 7))
    assert set(classes_in_box(same, c1, (95, 85, 120, 105))) == {LC_TEXT}
    pi = ingest_pdf(one_page(WIRES + text_content("R", 100, 100, 7) + text_content("D", 106, 100, 12),
                             size=(W, H))).pages[0]
    c2 = classify_page(pi)
    assert LC_TEXT not in classes_in_box(pi, c2, (95, 80, 130, 105))


def test_t4_text_touching_a_wire_is_not_text():
    wires = WIRES + b"0.45 w 100 100 m 125 100 l S"
    pi, cls = page(text_content("RED", 100, 100, 7), wires)    # baseline lies on the wire
    cs = classes_in_box(pi, cls, (95, 85, 130, 105))
    assert LC_TEXT not in cs


def test_t5_large_isolated_component_and_vertical_dashes_are_not_text():
    pi, cls = page(text_content("RED", 20, 20, 60))        # glyphs 60 pt tall: far above the glyph limit
    assert LC_TEXT not in classes_in_box(pi, cls, (0, 100, 300, 200))
    dashes = b"".join(b"0.45 w 250 %d m 250 %d l S\n" % (20 + 6 * i, 24 + 6 * i) for i in range(6))
    pi2, cls2 = page(dashes)
    assert all(cls2.records[p.id].cls != LC_TEXT for p in pi2.prims)


def test_t6_evidence_lineage_and_independence():
    pi, cls = page(text_content("RED", 100, 100, 7))
    p = next(q for q in pi.prims if cls.records[q.id].cls == LC_TEXT)
    rec = cls.records[p.id]
    assert rec.role_status == "CONFIRMED" and len(rec.evidence) == 2
    shape, row = (cls.registry.items[e] for e in rec.evidence)
    assert any(t.startswith("size:") for t in shape.lineage) and any(t.startswith("contact:") for t in shape.lineage)
    assert all(t.startswith("row:") for t in row.lineage)
    assert not shape.uses_connectivity and not row.uses_connectivity


def test_t7_text_does_not_change_pin_results_and_creates_no_terminals():
    sym = draw_symbol(LIB.symbols["R"], "r0", 3.0, (150.0, 100.0), body_w=0.9, pin_w=0.45)
    label = text_content("R1", 160, 105, 7) + text_content("100", 160, 92, 7)
    pi0, c0 = page(sym)
    pi1, c1 = page(sym + label)
    a = detect_pins(pi0, c0, list(LIB.symbols.values()))
    b = detect_pins(pi1, c1, list(LIB.symbols.values()))
    assert [(p.tip, p.pos_state) for cr in a for p in cr.pins] == [(p.tip, p.pos_state) for cr in b for p in cr.pins]
    assert sum(len(cr.unresolved) for cr in b) == 0 and len(b) == len(a) == 1


def test_t8_text_inside_an_ic_body_is_excluded_so_the_template_still_matches():
    sym = draw_symbol(LIB.symbols["U4"], "r0", 3.0, (150.0, 100.0), body_w=0.9, pin_w=0.45)
    names = text_content("IN1", 137, 102, 5) + text_content("RED", 137, 92, 5)   # fully inside the body (y 88.6..111.4)
    pi, cls = page(sym + names)
    (cr,) = detect_pins(pi, cls, list(LIB.symbols.values()))
    assert cr.resolution.symbols == ("U4",) and all(p.pos_state == "CONFIRMED" for p in cr.pins)


def test_t9_led_arrows_are_not_text():
    sym = draw_symbol(LIB.symbols["LED"], "r0", 3.0, (150.0, 100.0), body_w=0.9, pin_w=0.45)
    pi, cls = page(sym)
    assert LC_TEXT not in {r.cls for r in cls.records.values()}


def test_t10_known_limit_two_glyph_label_with_a_one_dimensional_glyph_is_not_text():
    # documented limitation: "I" is a single straight segment, so "IN" cannot form a valid row
    pi, cls = page(text_content("IN", 200, 100, 7))
    assert LC_TEXT not in classes_in_box(pi, cls, (195, 85, 220, 105))


# ---- M2.1 mutation-driven additions (added because M25/M29 survived; no assertion relaxed)

def test_t11_two_glyph_pair_with_different_heights_is_not_text():
    pi, cls = page(text_content("E", 100, 100, 7) + text_content("D", 110, 100, 3.5))
    cs = classes_in_box(pi, cls, (95, 85, 125, 105))
    assert cs and LC_TEXT not in cs


def test_t12_two_glyph_pair_with_shifted_baseline_is_not_text():
    pi, cls = page(text_content("E", 100, 100, 7) + text_content("D", 110, 101.4, 7))
    cs = classes_in_box(pi, cls, (95, 85, 125, 106))
    assert cs and LC_TEXT not in cs


def test_t13_aligned_equal_pair_is_text():
    # positive control for t11/t12: same height, same baseline
    pi, cls = page(text_content("ED", 100, 100, 7))
    cs = classes_in_box(pi, cls, (95, 85, 125, 106))
    assert cs and set(cs) == {LC_TEXT}


def test_t14_text_strokes_do_not_define_wire_style():
    # Many text strokes in a different style (width 1.0) must not outvote the 3 real wires.
    big = text_content("HIHIHIHI", 100, 100, 7, width=1.0)
    pi, cls = page(big)
    wires = [p for p in pi.prims if p.pts[0][1] < 20 or p.pts[0][0] in (10.0, 90.0)]
    assert wires
    assert all(cls.records[p.id].cls == LC_WIRE for p in wires), \
        [cls.records[p.id].cls for p in wires]


def test_t15_vertical_text_row_is_text_too():
    # a column of glyph-like stroke clusters rotated 90 degrees: transposed rows must be recognised
    from ecci.classify import LC_TEXT
    content = b""
    for k in range(6):
        y = 100 + 9 * k
        content += (f"0.4 w 100 {y} m 104 {y} l S 104 {y} m 104 {y + 6} l S 100 {y + 3} m 104 {y + 3} l S\n").encode()
    pi = ingest_pdf(one_page(content, size=(300, 300))).pages[0]
    cls = classify_page(pi)
    assert any(r.cls == LC_TEXT for r in cls.records.values())


LIB_FOR_T16 = load_library(str(Path(__file__).resolve().parents[1] / "fixtures" / "lib_min.kicad_sym"))


def test_t16_pin_lines_in_body_style_still_match_and_confirm_pins():
    # KiCad draws pin lines in the body colour and width: the pin lines land in the body cluster
    sym = LIB_FOR_T16.symbols["R"]
    wires = "".join(f"{x} 40 m {x + 60} 40 l S\n" for x in range(10, 280, 70))
    content = (b"0 0.6 0 RG 0.45 w " + wires.encode() + b"0.5 0 0 RG\n"
               + draw_symbol(sym, "r0", 3.0, (100.0, 105.0), body_w=0.45, pin_w=0.45))
    pi = ingest_pdf(one_page(content, size=(300, 200))).pages[0]
    syms = [v for k, v in LIB_FOR_T16.symbols.items() if k != "R_LONG"]
    results = detect_pins(pi, classify_page(pi), syms)
    assert len(results) == 1 and results[0].resolution.status == "UNIQUE"
    assert len(results[0].pins) == 2 and all(p.pos_state == "CONFIRMED" for p in results[0].pins)


def test_t17_duplicated_pin_lines_do_not_hide_the_stubs():
    # KiCad plots some pin lines twice (identical primitives): a duplicate must not make the free end look touched
    sym = LIB_FOR_T16.symbols["R"]
    wires = "".join(f"{x} 40 m {x + 60} 40 l S\n" for x in range(10, 280, 70))
    body = draw_symbol(sym, "r0", 3.0, (100.0, 105.0), body_w=0.45, pin_w=0.45)
    lines = body.decode().splitlines()
    dups = [ln for ln in lines if ln.strip().endswith("S") and ln.count(" m ") == 1 and ln.count(" l ") == 1]
    content = (b"0 0.6 0 RG 0.45 w " + wires.encode() + b"0.5 0 0 RG\n" + body + ("\n".join(dups) + "\n").encode())
    pi = ingest_pdf(one_page(content, size=(300, 200))).pages[0]
    syms = [v for k, v in LIB_FOR_T16.symbols.items() if k != "R_LONG"]
    results = detect_pins(pi, classify_page(pi), syms)
    assert len(results) == 1 and results[0].resolution.status == "UNIQUE" and len(results[0].pins) == 2


def _scene_with_r(extra_ops: bytes = b""):
    sym = LIB_FOR_T16.symbols["R"]
    wires = "".join(f"{x} 40 m {x + 60} 40 l S\n" for x in range(10, 280, 70))
    return (b"0 0.6 0 RG 0.45 w " + wires.encode() + b"0.5 0 0 RG\n"
            + draw_symbol(sym, "r0", 3.0, (100.0, 105.0), body_w=0.45, pin_w=0.45) + extra_ops)


def test_t18_symbol_hanging_on_a_pin_tip_does_not_hide_the_stub_or_join_the_cluster():
    # a power-flag like arrow (3 strokes) touches the free end of one pin line: the pin line is still a stub
    syms = [v for k, v in LIB_FOR_T16.symbols.items() if k != "R_LONG"]
    pi0 = ingest_pdf(one_page(_scene_with_r(), size=(300, 200))).pages[0]
    r0 = detect_pins(pi0, classify_page(pi0), syms)
    assert len(r0) == 1 and r0[0].resolution.status == "UNIQUE"
    tx, ty_page = min(r0[0].pins, key=lambda p: p.tip[1]).tip
    ty = 200.0 - ty_page                                   # page coordinates are y-down, the stream is y-up
    arrow = f"{tx - 2} {ty + 2} m {tx} {ty} l S {tx + 2} {ty + 2} m {tx} {ty} l S {tx} {ty} m {tx} {ty + 6} l S\n".encode()
    pi = ingest_pdf(one_page(_scene_with_r(arrow), size=(300, 200))).pages[0]
    results = detect_pins(pi, classify_page(pi), syms)
    ok = [r for r in results if r.resolution.status == "UNIQUE"]
    assert len(ok) == 1 and len(ok[0].pins) == 2


def test_t19b_double_plotted_capacitor_plates_still_merge_as_plates():
    from ecci.symbols import body_clusters
    plate = lambda y: f"0.5 0 0 RG 1.0 w 100 {y} m 112 {y + 3} l S\n"  # noqa: E731  (slanted: BODY, not a wire)
    wires = "0 0.6 0 RG 0.45 w " + "".join(f"{x} 40 m {x + 60} 40 l S\n" for x in range(10, 280, 70))
    ops = (wires + plate(100) * 2 + plate(108) * 2).encode()           # each plate plotted twice, 5pt apart
    pi = ingest_pdf(one_page(ops, size=(300, 200))).pages[0]
    cl = body_clusters(pi, classify_page(pi), gap_frac=0.1)   # the value the pipeline uses
    assert len(cl) == 1 and len(cl[0].prim_ids) == 4


def test_t19_circle_with_an_inner_stroke_is_one_cluster_even_when_plotted_twice():
    # fill + stroke plot the same circle twice; the inner bar floats inside it (containment merges, bare line allowed)
    import math as _m
    pts = [(150 + 7 * _m.cos(2 * _m.pi * k / 32), 100 + 7 * _m.sin(2 * _m.pi * k / 32)) for k in range(32)]
    ring = ("0.5 0 0 RG 0.72 w " + f"{pts[0][0]} {pts[0][1]} m " + " ".join(f"{x} {y} l" for x, y in pts[1:]) + " h S\n")
    bar = "0.43 w 146 98 m 154 102 l S\n"
    from ecci.symbols import body_clusters
    pi = ingest_pdf(one_page((ring + ring + bar).encode(), size=(300, 200))).pages[0]
    cl = body_clusters(pi, classify_page(pi))
    assert len(cl) == 1 and len(cl[0].prim_ids) == 3


def _cap(x, y):
    # capacitor: two plates + two pin lines, all separate short strokes in the wire style, vertical axis
    return (f"{x - 4} {y + 1.5} m {x + 4} {y + 1.5} l S {x - 4} {y - 1.5} m {x + 4} {y - 1.5} l S "
            f"{x} {y + 1.5} m {x} {y + 7} l S {x} {y - 1.5} m {x} {y - 7} l S\n")


def test_t20_row_of_identical_capacitors_is_not_text():
    caps = "".join(_cap(40 + 22 * k, 100) for k in range(5)).encode()
    label = text_content("RED", 20, 60, 3) + text_content("ELHTI", 20, 50, 3) + text_content("DETH", 20, 40, 3)
    pi, cls = page(caps + label)
    assert LC_TEXT not in classes_in_box(pi, cls, (30, 85, 160, 115))      # y-down: caps sit around y=100
    assert any(r.cls == LC_TEXT for r in cls.records.values())             # genuine text is still text


def test_t20b_two_identical_capacitors_are_below_the_repeat_threshold():
    # only 2 copies: the repeat rule must not fire (documented ASSUMPTION threshold = 3)
    caps = (_cap(40, 100) + _cap(62, 100)).encode()
    label = text_content("RED", 20, 60, 3) + text_content("ELHTI", 20, 50, 3) + text_content("DETH", 20, 40, 3)
    pi, cls = page(caps + label)
    assert not any("identical copies" in n for n in cls.notes)
    five = page("".join(_cap(40 + 22 * k, 100) for k in range(5)).encode() + label)[1]
    assert any("identical copies" in n for n in five.notes)      # same scene with 5 copies: the rule is active


def test_t21_big_closed_outline_below_frame_size_is_body_not_other():
    # a large IC unit rectangle (about 60% of the page diagonal, not a frame) must be BODY so pin lines can cluster with it
    from ecci.classify import LC_OTHER
    ops = b"0.5 0 0 RG 0.45 w 60 20 150 150 re S\n"
    pi = ingest_pdf(one_page(WIRES + ops, size=(W, H))).pages[0]
    cls = classify_page(pi)
    rect = [p for p in pi.prims if p.line_closed][0]
    assert cls.records[rect.id].cls == LC_BODY and cls.records[rect.id].cls != LC_OTHER


def test_t21b_big_open_line_stays_other():
    from ecci.classify import LC_OTHER
    pi = ingest_pdf(one_page(WIRES + b"0.5 0 0 RG 0.45 w 10 20 m 150 150 l S\n", size=(W, H))).pages[0]
    cls = classify_page(pi)
    seg = [p for p in pi.prims if not p.line_closed and len(p.pts) == 2 and abs(p.pts[0][0] - p.pts[1][0]) > 100][0]
    assert cls.records[seg.id].cls == LC_OTHER


def test_t22_foreign_colour_marks_inside_the_cluster_do_not_block_the_template():
    # a pin-name mark in ANOTHER colour touching the body must not stop the template match; pins stay UNIQUE
    syms = [v for k, v in LIB_FOR_T16.symbols.items() if k != "R_LONG"]
    pi0 = ingest_pdf(one_page(_scene_with_r(), size=(300, 200))).pages[0]
    r0 = detect_pins(pi0, classify_page(pi0), syms)
    assert len(r0) == 1 and r0[0].resolution.status == "UNIQUE"
    x0, y0, x1, y1 = r0[0].cluster.bbox
    mark = f"0.7 0.2 0 RG {x0 + 0.5} {200 - y0 - 1.0} m {x1 - 0.5} {200 - y0 - 1.0} l S\n".encode()   # inside the body, other colour
    pi = ingest_pdf(one_page(_scene_with_r(mark), size=(300, 200))).pages[0]
    results = detect_pins(pi, classify_page(pi), syms)
    ok = [r for r in results if r.resolution.status == "UNIQUE"]
    assert len(ok) == 1 and len(ok[0].pins) == 2


def test_t22b_restrict_needs_a_closed_outline():
    from ecci.symbols import _rebuild, restrict_to_outline_style
    pi = ingest_pdf(one_page(b"0.5 0 0 RG 0.45 w 10 10 m 40 10 l S 0 0.5 0 RG 10 20 m 40 20 l S\n", size=(100, 100))).pages[0]
    by_id = {p.id: p for p in pi.prims}
    cl = _rebuild(0, tuple(by_id), by_id)
    assert restrict_to_outline_style(cl, by_id) is None
