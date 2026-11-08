import hashlib
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf2kicad.independent_pdf_reconstruction import extract_independent_pdf_reconstruction


def test_independent_reconstruction_discovers_refs_without_ledger(tmp_path):
    source = tmp_path / "schematic.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=160)
    page.insert_text((40, 40), "R1", fontsize=8)
    shape = page.new_shape()
    shape.draw_line((60, 38), (90, 38))
    shape.draw_line((90, 38), (90, 60))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()
    doc.save(source)
    doc.close()

    result = extract_independent_pdf_reconstruction(str(source), [1])
    page_result = result["page_results"][0]

    assert result["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["independence_contract"]["reads_production_terminal_ledger"] is False
    assert [item["text"] for item in page_result["references"]] == ["R1"]
    assert page_result["summary"]["symbol_association_candidates"] == 1
    assert page_result["summary"]["vector_primitives"] >= 2
    assert page_result["summary"]["verified_symbol_ownership"] == 0
    assert page_result["summary"]["verified_pin_identity"] == 0
    assert page_result["summary"]["verified_electrical_memberships"] == 0
    assert all(item["pin_number"] is None for item in page_result["terminal_candidates"])


def test_unknown_vector_geometry_never_becomes_electrical_truth(tmp_path):
    source = tmp_path / "artwork.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=100)
    shape = page.new_shape()
    shape.draw_line((10, 50), (90, 50))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()
    doc.save(source)
    doc.close()

    result = extract_independent_pdf_reconstruction(str(source), [1])
    primitive = result["page_results"][0]["primitive_inventory"][0]
    assert primitive["classification"] == "WIRE_OR_GRAPHIC_CANDIDATE"
    assert primitive["electrical_authority"] == "NOT_ESTABLISHED"
    assert result["electrical_signoff"].startswith("BLOCKED")
