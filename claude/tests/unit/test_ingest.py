import io
import json
import math

import pikepdf
import pytest

from ecci.flatten import point_segment_distance
from ecci.ingest_vector import IngestError, ingest_pdf
from ecci.ir import IngestResult, Prim
from ecci.provenance import ProvResolveError, resolve_prov
from pdfgen import Form, PageSpec, build_pdf, one_page


def prims(content: bytes, **kw):
    res = ingest_pdf(one_page(content, **kw))
    return res.pages[0].prims, res.pages[0].report


def pts_close(got, want, tol=1e-6):
    assert len(got) == len(want), (got, want)
    for g, w in zip(got, want):
        assert math.hypot(g[0] - w[0], g[1] - w[1]) <= tol, (got, want)


# ---------------------------------------------------------------- basic geometry

def test_line_is_y_flipped_and_has_exact_provenance():
    ps, rep = prims(b"1 w 10 10 m 60 10 l S")
    assert len(ps) == 1 and not rep.errors
    p = ps[0]
    pts_close(p.pts, [(10, 90), (60, 90)])
    # instruction list: 0:"1 w" 1:"10 10 m" 2:"60 10 l" 3:"S"
    assert p.prov.chain == (("page", 3),) and p.prov.first_op == 1
    assert p.prov.op_index == 3 and p.prov.content_ref == "p0/page#3"
    assert p.stroke and not p.fill and p.stroke_w == 1.0 and p.kind == "polyline"


def test_cm_and_q_Q_scope_and_width_scaling():
    content = b"q 2 0 0 2 10 5 cm 4 w 0 0 m 10 0 l S Q 1 w 0 0 m 10 0 l S"
    ps, _ = prims(content)
    pts_close(ps[0].pts, [(10, 95), (30, 95)])       # (0,0)->(10,5)->y flip; (10,0)->(30,5)
    assert ps[0].stroke_w == pytest.approx(8.0)       # 4 * scale 2
    pts_close(ps[1].pts, [(0, 100), (10, 100)])       # after Q the CTM is restored
    assert ps[1].stroke_w == pytest.approx(1.0)


def test_cm_rotation_matrix_hand_computed():
    ps, _ = prims(b"0 1 -1 0 50 0 cm 10 0 m 10 20 l S")
    # (10,0): x'=0*10-1*0+50=50, y'=1*10+0+0=10  -> normalized (50,90)
    # (10,20): x'=-20+50=30, y'=10               -> normalized (30,90)
    pts_close(ps[0].pts, [(50, 90), (30, 90)])


def test_consecutive_cm_compose_in_pdf_order():
    # second cm is applied FIRST to the point: (1,0)->scale->(2,0)->translate->(12,0)
    ps, _ = prims(b"1 0 0 1 10 0 cm 2 0 0 2 0 0 cm 1 0 m 2 0 l S")
    pts_close(ps[0].pts, [(12, 100), (14, 100)])


def test_rect_orientation_and_negative_size():
    ps, _ = prims(b"50 50 -20 10 re S")
    assert ps[0].origin == "re" and ps[0].closed
    pts_close(ps[0].pts, [(50, 50), (30, 50), (30, 40), (50, 40)])


def test_close_path_and_painting_variants():
    ps, _ = prims(b"10 10 m 30 10 l 30 30 l h S  50 10 m 70 10 l 70 30 l s  "
                  b"80 10 m 90 10 l 90 20 l f  110 10 m 120 10 l 120 20 l B  "
                  b"130 10 m 140 10 l 140 20 l b")
    flags = [(p.stroke, p.fill, p.closed) for p in ps]
    assert flags == [(True, False, True), (True, False, True), (False, True, False),
                     (True, True, False), (True, True, True)]
    assert ps[2].line_closed and not ps[3].line_closed and ps[4].line_closed
    assert ps[2].stroke_w == 0.0


def test_hairline_zero_width_is_preserved():
    ps, _ = prims(b"0 w 0 0 m 10 0 l S")
    assert ps[0].stroke_w == 0.0


def test_dash_and_colors_captured():
    ps, _ = prims(b"[3 2] 1 d 1 0 0 RG 0.5 g 0 0 m 10 0 l B")
    p = ps[0]
    assert p.dash == ((3.0, 2.0), 1.0)
    assert p.stroke_color == ("rgb", 1.0, 0.0, 0.0) and p.fill_color == ("gray", 0.5)


def test_extgstate_line_width_and_ignored_params_reported():
    data = build_pdf([PageSpec(content=b"/GS1 gs 0 0 m 10 0 l S",
                               extgstate={"GS1": {"/LW": 5, "/CA": 0.5}})])
    res = ingest_pdf(data)
    assert res.pages[0].prims[0].stroke_w == 5.0
    assert res.pages[0].report.unsupported.get("extgstate_param_ignored:/CA") == 1


def test_degenerate_subpaths():
    ps, rep = prims(b"5 5 m 5 5 l S  7 7 m S")
    assert [p.kind for p in ps] == ["point"] and ps[0].pts == [(5.0, 95.0)]
    assert rep.counts.get("empty_subpaths") == 1


def test_anisotropic_ctm_flagged():
    ps, rep = prims(b"2 0 0 1 0 0 cm 3 w 0 0 m 10 0 l S")
    assert ps[0].anisotropic and ps[0].stroke_w == pytest.approx(3 * math.sqrt(2))
    assert rep.counts.get("anisotropic_stroke") == 1


# --------------------------------------------------------------------- beziers

def test_bezier_endpoints_and_error_bound_vs_hand_curve():
    ps, rep = prims(b"0 0 m 0 55.2 44.8 100 100 100 c S")
    p = ps[0]
    assert p.flatten_ok and not rep.errors
    pts_close([p.pts[0], p.pts[-1]], [(0, 100), (100, 0)])
    # true curve in normalized space: control points (0,100),(0,44.8),(44.8,0),(100,0)
    c = [(0, 100), (0, 44.8), (44.8, 0), (100, 0)]
    for i in range(1001):
        t = i / 1000
        u = 1 - t
        x = u**3 * c[0][0] + 3*u*u*t * c[1][0] + 3*u*t*t * c[2][0] + t**3 * c[3][0]
        y = u**3 * c[0][1] + 3*u*u*t * c[1][1] + 3*u*t*t * c[2][1] + t**3 * c[3][1]
        d = min(point_segment_distance((x, y), p.pts[k], p.pts[k + 1]) for k in range(len(p.pts) - 1))
        assert d <= 0.05 + 1e-9


def test_v_and_y_operators_equal_equivalent_c():
    base = ps_c, _ = prims(b"10 10 m 10 10 40 50 60 20 c S")
    ps_v, _ = prims(b"10 10 m 40 50 60 20 v S")
    ps_y, _ = prims(b"10 10 m 10 10 60 20 y S")      # y: second control point = end point
    pts_close(ps_v[0].pts, ps_c[0].pts, 1e-9)
    ps_c2, _ = prims(b"10 10 m 10 10 60 20 60 20 c S")
    pts_close(ps_y[0].pts, ps_c2[0].pts, 1e-9)


def test_flatten_tolerance_parameter_is_respected():
    data = one_page(b"0 0 m 0 55.2 44.8 100 100 100 c S")
    coarse = ingest_pdf(data, flatten_tol=5.0).pages[0].prims[0]
    fine = ingest_pdf(data, flatten_tol=0.001).pages[0].prims[0]
    assert len(fine.pts) > len(coarse.pts) and fine.flatten_tol == 0.001


# ------------------------------------------------------------------ form XObjects

def test_form_matrix_and_cm_compose_and_provenance_chain():
    forms = {"Fm1": Form(content=b"0 0 m 10 0 l S", matrix=(1, 0, 0, 1, 100, 0))}
    content = b"q 1 0 0 1 0 20 cm /Fm1 Do Q"
    # page ops: 0:q 1:cm 2:Do 3:Q ; form ops: 0:m 1:l 2:S
    ps, rep = prims(content, forms=forms)
    assert not rep.errors and len(ps) == 1
    pts_close(ps[0].pts, [(100, 80), (110, 80)])     # form matrix, then cm, then y flip
    assert ps[0].prov.chain == (("page", 2), ("Fm1", 2)) and ps[0].prov.first_op == 0
    assert ps[0].prov.content_ref == "p0/page#2/Fm1#2"


def test_state_inside_form_does_not_leak_out():
    forms = {"Fm1": Form(content=b"5 w 3 0 0 3 0 0 cm 0 0 m 1 0 l S")}
    ps, _ = prims(b"/Fm1 Do 0 0 m 10 0 l S", forms=forms)
    assert ps[0].stroke_w == pytest.approx(15.0)
    assert ps[1].stroke_w == pytest.approx(1.0)
    pts_close(ps[1].pts, [(0, 100), (10, 100)])


def test_nested_forms():
    inner = Form(content=b"0 0 m 1 0 l S", matrix=(2, 0, 0, 2, 0, 0))
    outer = Form(content=b"/In Do", matrix=(1, 0, 0, 1, 10, 10), xobjects={"In": inner})
    ps, rep = prims(b"/Out Do", forms={"Out": outer})
    assert not rep.errors
    pts_close(ps[0].pts, [(10, 90), (12, 90)])
    assert ps[0].prov.chain == (("page", 0), ("Out", 0), ("In", 2))


def test_self_referencing_form_is_reported_not_followed():
    data = build_pdf([PageSpec(content=b"/Fm1 Do", forms={"Fm1": Form(content=b"/Fm1 Do 0 0 m 5 0 l S")})])
    with pikepdf.open(io.BytesIO(data)) as pdf:
        form = pdf.pages[0].obj.Resources.XObject.Fm1
        form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm1=form))
        buf = io.BytesIO()
        pdf.save(buf)
    res = ingest_pdf(buf.getvalue())
    codes = [e["code"] for e in res.pages[0].report.errors]
    assert "FORM_CYCLE" in codes and len(res.pages[0].prims) == 1


def test_form_depth_limit_reported():
    inner = Form(content=b"0 0 m 1 0 l S")
    for i in range(20):
        inner = Form(content=b"/N Do", xobjects={"N": inner})
    res = ingest_pdf(build_pdf([PageSpec(content=b"/Top Do", forms={"Top": inner})]))
    assert "FORM_DEPTH_EXCEEDED" in [e["code"] for e in res.pages[0].report.errors]


def test_missing_xobject_reported():
    _, rep = prims(b"/Nope Do")
    assert [e["code"] for e in rep.errors] == ["XOBJECT_NOT_FOUND"]


# ------------------------------------------- unsupported features are REPORTED

def test_unsupported_features_reported_not_silently_dropped():
    content = (b"BT /F1 12 Tf 10 10 Td (Hi) Tj ET /Im1 Do 0 0 100 50 re W n "
               b"/OC /MC0 BDC 0 0 m 5 0 l S EMC BI /W 1 /H 1 /CS /G /BPC 8 ID \x00 EI "
               b"0 0 m 1 1 l S")
    ps, rep = prims(content, image_names=("Im1",))
    u = rep.unsupported
    assert u.get("text_show_ops") == 1 and u.get("image_xobject") == 1
    assert u.get("clip_not_applied") == 1 and u.get("optional_content_not_filtered") == 1
    assert u.get("inline_image") == 1
    assert rep.counts.get("clip_paths") == 1
    assert len(ps) == 2          # geometry under clip/OC is still emitted, but flagged above


def test_shading_and_pattern_reported():
    _, rep = prims(b"/Sh0 sh /Pattern cs /P1 scn 0 0 m 5 5 l S")
    assert rep.unsupported.get("shading") == 1 and rep.unsupported.get("pattern_color") == 1


# --------------------------------------------------------- defensive behaviour

def test_bad_operands_do_not_kill_the_page():
    ps, rep = prims(b"10 m 10 10 l 0 0 m 5 5 l S")
    codes = [e["code"] for e in rep.errors]
    assert codes.count("BAD_OPERANDS") == 2 and len(ps) == 1
    pts_close(ps[0].pts, [(0, 100), (5, 95)])


def test_q_underflow_unbalanced_and_unknown_operator():
    _, rep = prims(b"Q q q zz 0 0 m 1 0 l S")
    codes = [e["code"] for e in rep.errors]
    assert "Q_UNDERFLOW" in codes and "Q_UNBALANCED" in codes
    assert rep.unknown_ops == {"zz": 1}


def test_prim_limit_truncates_and_reports():
    content = b" ".join(b"0 %d m 5 %d l S" % (i, i) for i in range(10))
    res = ingest_pdf(one_page(content), max_prims=3)
    assert len(res.pages[0].prims) == 3
    assert [e["code"] for e in res.pages[0].report.errors] == ["PRIM_LIMIT"]


@pytest.mark.parametrize("src,code", [
    ("/nonexistent/file.pdf", "FILE_NOT_FOUND"),
    (b"this is not a pdf", "CORRUPT_OR_NOT_PDF"),
])
def test_open_failures_have_codes(src, code):
    with pytest.raises(IngestError) as e:
        ingest_pdf(src)
    assert e.value.code == code


def test_encrypted_pdf_needs_password():
    data = build_pdf([PageSpec(content=b"0 0 m 5 0 l S")], encrypt=("u", "o"))
    with pytest.raises(IngestError) as e:
        ingest_pdf(data)
    assert e.value.code == "ENCRYPTED"
    assert len(ingest_pdf(data, password="u").pages[0].prims) == 1


def test_no_pages_page_index_and_bad_parameter():
    pdf = pikepdf.new()
    buf = io.BytesIO()
    pdf.save(buf)
    with pytest.raises(IngestError) as e:
        ingest_pdf(buf.getvalue())
    assert e.value.code == "NO_PAGES"
    data = one_page(b"0 0 m 1 1 l S")
    with pytest.raises(IngestError) as e:
        ingest_pdf(data, pages=[3])
    assert e.value.code == "PAGE_INDEX"
    with pytest.raises(IngestError) as e:
        ingest_pdf(data, flatten_tol=0)
    assert e.value.code == "BAD_PARAMETER"


def test_multi_page_selection_and_page_in_provenance():
    data = build_pdf([PageSpec(content=b"0 0 m 1 0 l S"), PageSpec(content=b"0 0 m 2 0 l S")])
    res = ingest_pdf(data, pages=[1])
    assert len(res.pages) == 1 and res.pages[0].prims[0].prov.page == 1
    assert res.pages[0].prims[0].prov.content_ref.startswith("p1/")


def test_rotated_page_geometry_end_to_end():
    ps, _ = prims(b"0 0 m 200 0 l S", rotate=90)
    pts_close(ps[0].pts, [(0, 0), (0, 200)])          # bottom edge becomes the left edge


# ------------------------------------------------------------------ traceability

def _case_pdf():
    forms = {"Fm1": Form(content=b"0 0 m 10 0 l 10 5 l S 1 1 2 2 re f", matrix=(1, 0, 0, 1, 100, 0))}
    content = (b"1 w 10 10 m 60 10 l S q 1 0 0 1 0 20 cm /Fm1 Do Q "
               b"20 30 m 20 30 l S 70 20 30 10 re B 0 0 m 0 50 50 100 100 100 c S")
    return build_pdf([PageSpec(content=content, forms=forms)])


def test_every_primitive_resolves_back_to_a_painting_operator():
    data = _case_pdf()
    res = ingest_pdf(data)
    assert len(res.pages[0].prims) == 6
    with pikepdf.open(io.BytesIO(data)) as pdf:
        for p in res.pages[0].prims:
            r = resolve_prov(pdf, p.prov)
            assert r["paint"][0] in {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}
            assert r["first"][0] in {"m", "re"}


def test_provenance_first_operand_matches_primitive_start():
    data = _case_pdf()
    res = ingest_pdf(data)
    by_ref = {p.prov.content_ref: p for p in res.pages[0].prims}
    with pikepdf.open(io.BytesIO(data)) as pdf:
        # top-level 'm' for the first line: operands 10 10 -> normalized (10, 90)
        p0 = res.pages[0].prims[0]
        op, operands = resolve_prov(pdf, p0.prov)["first"]
        assert op == "m" and [float(x) for x in operands] == [10.0, 10.0]
        pts_close([p0.pts[0]], [(10, 90)])
        # form line: user (0,0) -> form matrix (100,0) -> cm (100,20) -> normalized (100,80)
        pf = next(p for p in res.pages[0].prims if p.prov.chain[0][0] == "page" and len(p.prov.chain) == 2
                  and p.stroke)
        op, operands = resolve_prov(pdf, pf.prov)["first"]
        assert op == "m" and [float(x) for x in operands] == [0.0, 0.0]
        pts_close([pf.pts[0]], [(100, 80)])


def test_tampered_provenance_is_rejected():
    data = _case_pdf()
    res = ingest_pdf(data)
    p = res.pages[0].prims[0]
    from dataclasses import replace
    bad = replace(p.prov, chain=(("page", 0),))               # op 0 is `1 w`, not a painting op
    with pikepdf.open(io.BytesIO(data)) as pdf:
        with pytest.raises(ProvResolveError):
            resolve_prov(pdf, bad)
        with pytest.raises(ProvResolveError):
            resolve_prov(pdf, replace(p.prov, chain=(("page", 9999),)))
        with pytest.raises(ProvResolveError):
            resolve_prov(pdf, replace(p.prov, page=7))


# ------------------------------------------------------------- IR serialization

def test_json_round_trip_is_lossless_for_every_primitive():
    res = ingest_pdf(_case_pdf())
    d = json.loads(res.to_json())
    assert d["schema"] == "ecci-ir/1"
    for original, raw in zip(res.pages[0].prims, d["pages"][0]["prims"]):
        assert Prim.from_dict(raw) == original
