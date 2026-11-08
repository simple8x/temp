import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf2kicad.independent_symbol_pin_resolver import resolve_document


def test_pin_text_endpoint_association_is_candidate_only():
    ir = {
        "source_sha256": "abc",
        "page_results": [{
            "page": 5,
            "text_words": [{
                "text": "1", "bbox": [9.5, 9.5, 10.5, 10.5],
                "pin_token_candidate": True, "source": "raw_pdf_vector_strokes",
            }],
            "primitive_inventory": [
                {"id": 1, "a": [10.0, 10.0], "b": [20.0, 10.0],
                 "axis": "H", "length_pt": 10.0},
                {"id": 2, "a": [40.0, 40.0], "b": [50.0, 40.0],
                 "axis": "H", "length_pt": 10.0},
            ],
        }],
    }
    result = resolve_document(ir)
    page = result["pages"][0]
    row = page["candidates"][0]
    assert row["decision"] == "UNIQUE_ENDPOINT_ATTACHMENT_CANDIDATE"
    assert row["nearest_endpoint"]["primitive_id"] == 1
    assert row["symbol_ownership"] == "UNKNOWN"
    assert row["library_pin_identity"] == "UNKNOWN"
    assert row["electrical_membership"] == "UNKNOWN"
    assert result["verified_pin_identity"] == 0
    assert result["verified_electrical_memberships"] == 0


def test_ambiguous_endpoint_attachment_remains_unknown():
    ir = {
        "page_results": [{
            "page": 6,
            "text_words": [{
                "text": "2", "bbox": [9.5, 9.5, 10.5, 10.5],
                "pin_token_candidate": True,
            }],
            "primitive_inventory": [
                {"id": 1, "a": [10.0, 10.0], "b": [20.0, 10.0]},
                {"id": 2, "a": [10.2, 10.0], "b": [10.2, 20.0]},
            ],
        }],
    }
    row = resolve_document(ir)["pages"][0]["candidates"][0]
    assert row["decision"] == "AMBIGUOUS_ENDPOINT_ATTACHMENT"
    assert row["library_pin_identity"] == "UNKNOWN"
