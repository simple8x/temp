import os
import tempfile
from pathlib import Path

import fitz
import pytest

from pdf2kicad.electrical_evidence import classify_page, _incident_rays


EXPECTED_SOURCE_SHA256 = "a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc"
EXPECTED_JUNCTIONS = {5: 33, 6: 17, 7: 20, 8: 8, 9: 3, 10: 62, 11: 48}
EXPECTED_CANDIDATES = {5: 33, 6: 17, 7: 23, 8: 8, 9: 3, 10: 62, 11: 48}


def _synthetic_pdf(path, with_dot):
    doc = fitz.open()
    page = doc.new_page(width=200, height=150)
    page.draw_line((50, 50), (100, 50), color=(0, 0, 0), width=0.48)
    page.draw_line((100, 50), (100, 100), color=(0, 0, 0), width=0.48)
    page.draw_line((100, 50), (150, 50), color=(0, 0, 0), width=0.48)
    if with_dot:
        page.draw_circle((100, 50), 1.2, color=(0, 0, 0), fill=(0, 0, 0), width=0.48)
    doc.save(path)
    doc.close()


def test_filled_dot_with_three_orthogonal_rays_is_confirmed(tmp_path):
    path = tmp_path / "junction.pdf"
    _synthetic_pdf(path, with_dot=True)
    result = classify_page(path, 1)
    assert result["summary"]["explicit_junctions_confirmed"] == 1
    assert result["junction_candidates"][0]["incident_directions"] == ["E", "S", "W"]


def test_bare_geometric_crossing_without_dot_is_not_joined(tmp_path):
    path = tmp_path / "crossing.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=150)
    page.draw_line((50, 50), (150, 50), color=(0, 0, 0), width=0.48)
    page.draw_line((100, 20), (100, 80), color=(0, 0, 0), width=0.48)
    doc.save(path)
    doc.close()
    result = classify_page(path, 1)
    assert result["summary"]["filled_circle_mark_candidates"] == 0
    assert result["summary"]["explicit_junctions_confirmed"] == 0


@pytest.mark.skipif(not os.environ.get("QI9_SOURCE_PDF"), reason="source PDF fixture not supplied")
def test_authoritative_qi9_pdf_junction_inventory():
    pdf = Path(os.environ["QI9_SOURCE_PDF"])
    import hashlib
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == EXPECTED_SOURCE_SHA256
    for page, expected in EXPECTED_JUNCTIONS.items():
        result = classify_page(pdf, page)
        assert result["summary"]["explicit_junctions_confirmed"] == expected
        assert result["summary"]["filled_circle_mark_candidates"] == EXPECTED_CANDIDATES[page]
        assert result["summary"]["verified_net_memberships"] == 0
