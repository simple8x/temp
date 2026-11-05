"""Normalisation tests: same page re-encoded as other PDF forms must give the same result.
Set PDF2KICAD_TEST_PDF to a KiCad-plotted schematic PDF (page 9 is used)."""
import os, sys, pymupdf, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
PDF = os.environ.get("PDF2KICAD_TEST_PDF")
pytestmark = pytest.mark.skipif(not PDF, reason="set PDF2KICAD_TEST_PDF")


def _variant(tmp_path, name, f=1.0, rot=0):
    src = pymupdf.open(PDF)
    o = pymupdf.open()
    W, H = src[8].rect.width * f, src[8].rect.height * f
    if rot:
        W, H = H, W
    q = o.new_page(width=W, height=H)
    q.show_pdf_page(q.rect, src, 8, rotate=rot)
    p = str(tmp_path / (name + ".pdf"))
    o.save(p)
    return p


def _sig(path, page):
    from pdf2kicad.cli import extract
    comps, nets = extract(path, page)
    return len({g.ref for g in comps}), len(nets)


@pytest.mark.parametrize("name,f,rot", [("half", 0.5, 0), ("double", 2.0, 0), ("sideways", 1.0, 90), ("half_sideways", 0.5, 90)])
def test_scale_and_orientation(tmp_path, name, f, rot):
    assert _sig(_variant(tmp_path, name, f, rot), 1) == _sig(PDF, 9)


def test_raster_is_reported_not_guessed(tmp_path):
    from pdf2kicad.cli import extract, Unsupported
    src = pymupdf.open(PDF)
    o = pymupdf.open()
    q = o.new_page(width=src[8].rect.width, height=src[8].rect.height)
    q.insert_image(q.rect, pixmap=src[8].get_pixmap(dpi=100))
    p = str(tmp_path / "r.pdf")
    o.save(p)
    with pytest.raises(Unsupported):
        extract(p, 1)
