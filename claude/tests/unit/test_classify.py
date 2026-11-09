import pytest

from ecci.classify import (LC_BODY, LC_BORDER, LC_OTHER, LC_WIRE, ClassifyParams, classify_page,
                           is_manhattan)
from ecci.ingest_vector import ingest_pdf
from pdfgen import one_page

W, H = 300, 200


def classify(content: bytes, **kw):
    pi = ingest_pdf(one_page(content, size=(W, H), **kw)).pages[0]
    return pi, classify_page(pi)


WIRES = b"0.45 w 10 10 m 80 10 l S 10 20 m 10 60 l S 90 20 m 90 60 l S"


def test_border_frame_is_border_and_never_wire():                 # TC-H1
    pi, c = classify(WIRES + b" 1.2 w 5 5 290 190 re S")
    frame = next(p for p in pi.prims if p.origin == "re")
    assert c.records[frame.id].cls == LC_BORDER
    assert c.records[frame.id].role_status == "CONFIRMED"         # geometry + style (differs from wires)
    wires = [p.id for p in pi.prims if p.id != frame.id]
    assert all(c.records[i].cls == LC_WIRE for i in wires)


def test_border_without_style_evidence_is_only_candidate():
    pi, c = classify(b"0.45 w 5 5 290 190 re S")                    # no wire population, no style evidence
    assert c.records[0].cls == LC_BORDER and c.records[0].role_status == "CANDIDATE"


def test_wire_role_confirmed_by_shape_plus_style_independent_items():
    pi, c = classify(WIRES)
    rec = c.records[0]
    assert rec.cls == LC_WIRE and rec.role_status == "CONFIRMED"
    reg = c.registry
    shape, style = reg.items[f"WIRE_SHAPE:0"], reg.items[f"WIRE_STYLE:0"]
    assert shape.lineage == {"geom:0"} and style.lineage == {"style:0", "pagestat:0"}


def test_dashed_and_differently_styled_lines_are_not_wires():     # TC-H4 (class level)
    pi, c = classify(WIRES + b" [3 2] 0 d 0.45 w 100 100 m 160 100 l S"
                     b" [] 0 d 0.9 w 100 120 m 160 120 l S")
    dashed, thick = pi.prims[3], pi.prims[4]
    assert c.records[dashed.id].cls == LC_BODY and c.records[thick.id].cls == LC_BODY
    assert "style differs from wire style" in " ".join(c.records[thick.id].reasons)


def test_filled_shape_and_diagonal_are_not_wires():
    pi, c = classify(WIRES + b" 100 100 20 20 re f 150 100 m 190 140 l S")
    assert all(c.records[p.id].cls != LC_WIRE for p in pi.prims[3:])


def test_closed_rect_with_wire_style_is_body_not_wire():
    pi, c = classify(WIRES + b" 100 100 20 30 re S")
    assert c.records[pi.prims[3].id].cls == LC_BODY


def test_undetermined_wire_style_is_reported_and_downgrades_role():
    pi, c = classify(b"0.2 w 10 10 m 80 10 l S 0.4 w 10 20 m 80 20 l S 0.6 w 10 30 m 80 30 l S")
    assert c.wire_style is None and any("undetermined" in n for n in c.notes)
    assert all(r.cls == LC_WIRE and r.role_status == "CANDIDATE" for r in c.records.values())


def test_too_few_lines_for_a_style_population():
    pi, c = classify(b"0.45 w 10 10 m 80 10 l S 10 20 m 80 20 l S")
    assert c.wire_style is None and all(r.role_status == "CANDIDATE" for r in c.records.values())


def test_large_non_frame_element_is_other():
    pi, c = classify(WIRES + b" 0.9 w 20 20 m 280 180 l S")
    assert c.records[pi.prims[3].id].cls == LC_OTHER


def test_point_primitive_is_other_unknown():
    pi, c = classify(WIRES + b" 50 50 m 50 50 l S")
    r = c.records[pi.prims[3].id]
    assert pi.prims[3].kind == "point" and r.cls == LC_OTHER and r.role_status == "UNKNOWN"


def test_manhattan_tolerance_boundary():
    pi = ingest_pdf(one_page(b"1 w 0 0 m 100 0.8 l S 0 10 m 100 10.9 l S", size=(W, H))).pages[0]
    assert is_manhattan(pi.prims[0], 0.5) and not is_manhattan(pi.prims[1], 0.5)   # 0.46 deg vs 0.52 deg


def test_no_evidence_item_uses_connectivity_or_weak_sources():
    pi, c = classify(WIRES + b" 1.2 w 5 5 290 190 re S 0.9 w 100 100 m 120 100 l 120 120 l h S")
    for it in c.registry.items.values():
        assert not it.uses_connectivity and it.source == "VECTOR"


# ---- wire style by structure, length tie-break, and "never guess" on ambiguity
def _seg(x0, y0, x1, y1, rgb):
    return f"{rgb} RG 0.45 w {x0} {y0} m {x1} {y1} l S\n".encode()


def _style_of(content: bytes):
    pi = ingest_pdf(one_page(content, size=(600, 400))).pages[0]
    return classify_page(pi)


def test_long_wires_beat_equal_width_short_pin_lines_of_other_colour():
    g, r = "0 0.6 0", "0.5 0 0"
    c = b""
    for i in range(6):                       # green: long wires, chained end to end
        c += _seg(20 + 80 * i, 100, 100 + 80 * i, 100, g)
    for i in range(12):                      # dark red: short, uniform pin-like lines, partly touching
        c += _seg(30 + 40 * i, 200, 30 + 40 * i, 207, r)
        c += _seg(30 + 40 * i, 207, 37 + 40 * i, 207, r)
    res = _style_of(c)
    assert res.wire_style is not None and res.wire_style[1] == ("rgb", 0.0, 0.6, 0.0)


def test_tiny_perfectly_connected_group_cannot_win():
    c = b""
    for i in range(5):
        c += _seg(20 + 90 * i, 100, 110 + 90 * i, 100, "0 0.6 0")
    for i in range(4):                       # magenta crosses: every end touches another magenta end
        c += _seg(300, 300 + 3 * i, 303, 300 + 3 * i, "0.5 0 0.5")
        c += _seg(303, 300 + 3 * i, 303, 303 + 3 * i, "0.5 0 0.5")
    res = _style_of(c)
    assert res.wire_style is None or res.wire_style[1] == ("rgb", 0.0, 0.6, 0.0)


def test_ambiguous_structure_yields_no_wire_at_all():
    c = b""
    for i in range(8):                       # two similar styles, similar lengths, similar structure
        c += _seg(20 + 60 * i, 100, 70 + 60 * i, 100, "0 0.6 0")
        c += _seg(20 + 60 * i, 200, 70 + 60 * i, 200, "0.5 0 0")
    res = _style_of(c)
    if res.wire_style is None:
        assert not any(r.cls == LC_WIRE for r in res.records.values())


def test_lone_glyph_of_many_tiny_strokes_is_text_but_a_rectangle_is_not():
    # an isolated 'R'-like blob of 6 short diagonal strokes (no row of neighbours) and a 14 pt rectangle
    glyph = b"".join(f"0 0.4 0.4 RG 0.43 w {200 + 0.5 * i} {100 + 0.6 * (i % 2)} m "
                     f"{200.5 + 0.5 * i} {100 + 0.6 * ((i + 1) % 2)} l S\n".encode() for i in range(6))
    body = b"0.5 0 0 RG 0.72 w 300 100 14 20 re S\n"
    res = _style_of(glyph + body)
    cls = {r.cls for r in res.records.values()}
    assert sum(r.cls == "TEXT" for r in res.records.values()) == 6
    assert "BODY" in cls or "OTHER" in cls


def test_colour_that_draws_closed_outlines_is_a_body_colour_not_the_wire_colour():
    g, r = "0 0.6 0", "0.5 0 0"
    c = b""
    for i in range(6):                                   # red: pin-like lines chained, very well connected
        c += _seg(20 + 40 * i, 100, 52 + 40 * i, 100, r)
    c += f"{r} RG 0.72 w 100 200 14 20 re S 150 200 14 20 re S\n".encode()   # red closed outlines
    for i in range(5):                                   # green: wires, less connected
        c += _seg(20 + 60 * i, 300, 70 + 60 * i, 300, g)
    res = _style_of(c)
    assert res.wire_style is not None and res.wire_style[1] == ("rgb", 0.0, 0.6, 0.0)
