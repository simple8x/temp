import os, sys, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
PDF = os.environ.get("PDF2KICAD_TEST_PDF")

@pytest.mark.skipif(not PDF, reason="set PDF2KICAD_TEST_PDF to a KiCad-plotted PDF")
def test_extract_runs():
    from pdf2kicad.cli import extract
    comps, nets = extract(PDF, 5)
    assert len(comps) > 10 and len(nets) > 10
