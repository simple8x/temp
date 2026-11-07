import io
import math

import pikepdf
import pytest

from ecci.transform import (IDENTITY, apply, det, linear_scales, mat_mul, page_geometry,
                            stroke_width_scale)
from pdfgen import PageSpec, build_pdf


def _geom(**kw):
    data = build_pdf([PageSpec(content=b"", **kw)])
    with pikepdf.open(io.BytesIO(data)) as pdf:
        return page_geometry(pdf.pages[0].obj, 0)


def close(a, b, tol=1e-9):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def test_mat_mul_applies_left_operand_first():
    translate = (1, 0, 0, 1, 10, 0)
    scale = (2, 0, 0, 2, 0, 0)
    assert apply(mat_mul(translate, scale), 1, 1) == (22.0, 2.0)   # translate, then scale
    assert apply(mat_mul(scale, translate), 1, 1) == (12.0, 2.0)   # scale, then translate


def test_identity_and_det():
    assert mat_mul(IDENTITY, (3, 1, 2, 4, 5, 6)) == (3, 1, 2, 4, 5, 6)
    assert det((2, 0, 0, 3, 7, 7)) == 6
    assert det((1, 0, 0, -1, 0, 0)) == -1


def test_stroke_width_scale_uniform_and_anisotropic():
    s, aniso = stroke_width_scale((3, 0, 0, 3, 0, 0))
    assert s == pytest.approx(3.0) and not aniso
    s, aniso = stroke_width_scale((2, 0, 0, 1, 0, 0))
    assert s == pytest.approx(math.sqrt(2)) and aniso
    s, aniso = stroke_width_scale((1, 0, 0, -1, 0, 0))   # pure flip is not anisotropic
    assert s == pytest.approx(1.0) and not aniso
    assert linear_scales((0, 0, 0, 0, 5, 5)) == (0.0, 0.0)


@pytest.mark.parametrize("rot,expected", [
    (0,   {(0, 0): (0, 100), (200, 0): (200, 100), (0, 100): (0, 0), (200, 100): (200, 0)}),
    (90,  {(0, 0): (0, 0), (200, 0): (0, 200), (0, 100): (100, 0), (200, 100): (100, 200)}),
    (180, {(0, 0): (200, 0), (200, 0): (0, 0), (0, 100): (200, 100), (200, 100): (0, 100)}),
    (270, {(0, 0): (100, 200), (200, 0): (100, 0), (0, 100): (0, 200), (200, 100): (0, 0)}),
])
def test_rotation_corner_mapping_hand_derived(rot, expected):
    g = _geom(size=(200, 100), rotate=rot)
    assert g.rotate == rot
    assert (g.width, g.height) == ((200, 100) if rot in (0, 180) else (100, 200))
    for user_pt, want in expected.items():
        assert close(apply(g.base_matrix, *user_pt), want), (rot, user_pt)


def test_negative_rotate_normalised():
    assert _geom(rotate=-90).rotate == 270
    assert _geom(rotate=450).rotate == 90


def test_mediabox_offset_and_reversed_corners():
    for mb in [(100, 200, 300, 300), (300, 300, 100, 200)]:
        g = _geom(mediabox=mb)
        assert close(apply(g.base_matrix, 100, 200), (0, 100))
        assert close(apply(g.base_matrix, 300, 300), (200, 0))
        assert (g.width, g.height) == (200, 100)


def test_cropbox_defines_visible_page():
    g = _geom(mediabox=(0, 0, 200, 100), cropbox=(20, 10, 120, 60))
    assert (g.width, g.height) == (100, 50)
    assert close(apply(g.base_matrix, 20, 10), (0, 50))
    assert close(apply(g.base_matrix, 120, 60), (100, 0))


def test_cropbox_outside_mediabox_falls_back_and_reports():
    g = _geom(mediabox=(0, 0, 200, 100), cropbox=(500, 500, 600, 600))
    assert (g.width, g.height) == (200, 100)
    assert "CROPBOX_OUTSIDE_MEDIABOX_USING_MEDIABOX" in g.issues


def test_cropbox_partially_outside_is_intersected():
    g = _geom(mediabox=(0, 0, 200, 100), cropbox=(150, 50, 400, 400))
    assert (g.width, g.height) == (50, 50)


def test_userunit_scales_everything():
    g = _geom(size=(200, 100), userunit=2.0)
    assert (g.width, g.height) == (400, 200)
    assert close(apply(g.base_matrix, 200, 100), (400, 0))


def test_bad_mediabox_defaults_and_reports():
    g = _geom(mediabox=(0, 0, 0, 0))
    assert (g.width, g.height) == (612, 792)
    assert "BAD_OR_MISSING_MEDIABOX_DEFAULTED" in g.issues


def test_inherited_mediabox_and_rotate_from_pages_node():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(50, 80))
    page = pdf.pages[0]
    del page.obj["/MediaBox"]
    pdf.Root.Pages.MediaBox = pikepdf.Array([0, 0, 200, 100])
    pdf.Root.Pages.Rotate = 90
    buf = io.BytesIO()
    pdf.save(buf)
    with pikepdf.open(io.BytesIO(buf.getvalue())) as p2:
        g = page_geometry(p2.pages[0].obj, 0)
    assert g.rotate == 90 and (g.width, g.height) == (100, 200)
