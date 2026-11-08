import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf2kicad.independent_symbol_pin_resolver import resolve_document


def _ir(primitives):
    return {
        "source_sha256": "abc",
        "page_results": [{
            "page": 5,
            "text_words": [{
                "text": "1", "bbox": [9.5, 9.5, 10.5, 10.5],
                "pin_token_candidate": True, "source": "raw_pdf_vector_strokes",
            }],
            "primitive_inventory": primitives,
        }],
    }


def test_pin_text_endpoint_association_is_candidate_only():
    result = resolve_document(_ir([
        {"id": 1, "a": [10.0, 10.0], "b": [20.0, 10.0],
         "axis": "H", "length_pt": 10.0},
        {"id": 2, "a": [40.0, 40.0], "b": [50.0, 40.0],
         "axis": "H", "length_pt": 10.0},
    ]))
    page = result["pages"][0]
    row = page["candidates"][0]
    assert row["decision"] == "UNIQUE_ENDPOINT_ATTACHMENT_CANDIDATE"
    assert row["nearest_endpoint"]["primitive_ids"] == [1]
    assert row["symbol_ownership"] == "UNKNOWN"
    assert row["library_pin_identity"] == "UNKNOWN"
    assert row["electrical_membership"] == "UNKNOWN"
    assert result["verified_pin_identity"] == 0
    assert result["verified_electrical_memberships"] == 0


def test_ambiguous_distinct_physical_endpoints_remain_unknown():
    result = resolve_document(_ir([
        {"id": 1, "a": [10.0, 10.0], "b": [20.0, 10.0]},
        {"id": 2, "a": [10.2, 10.0], "b": [10.2, 20.0]},
    ]))
    row = result["pages"][0]["candidates"][0]
    assert row["decision"] == "AMBIGUOUS_ENDPOINT_ATTACHMENT"
    assert row["library_pin_identity"] == "UNKNOWN"


def test_coincident_segment_endpoints_are_one_physical_candidate():
    result = resolve_document(_ir([
        {"id": 1, "a": [10.0, 10.0], "b": [20.0, 10.0]},
        {"id": 2, "a": [10.0, 10.0], "b": [10.0, 20.0]},
        {"id": 3, "a": [10.0, 10.0], "b": [5.0, 10.0]},
    ]))
    page = result["pages"][0]
    row = page["candidates"][0]
    assert page["raw_endpoint_record_count"] == 6
    assert page["distinct_physical_endpoint_count"] == 5
    assert row["decision"] == "UNIQUE_ENDPOINT_ATTACHMENT_CANDIDATE"
    assert row["nearest_endpoint"]["primitive_ids"] == [1, 2, 3]
    assert row["symbol_ownership"] == "UNKNOWN"
    assert result["verified_pin_identity"] == 0


def test_close_but_noncoincident_endpoints_are_not_collapsed():
    result = resolve_document(_ir([
        {"id": 1, "a": [10.0, 10.0], "b": [20.0, 10.0]},
        {"id": 2, "a": [10.2, 10.0], "b": [10.2, 20.0]},
    ]), endpoint_merge_tolerance_pt=0.05)
    assert result["pages"][0]["distinct_physical_endpoint_count"] == 4
    assert result["pages"][0]["candidates"][0]["decision"] == "AMBIGUOUS_ENDPOINT_ATTACHMENT"
