import hashlib
import pytest
from ecci.ingestlab_adapter import AdapterError, adapt_parse_result

PDF = b"%PDF-1.7\nfixture"
DIMS = [(200.0, 100.0)]
CFG = {"imageMode": "none", "ocr": False, "markdownProfile": "faithful"}

def result(blocks, **extra):
    return {"kind": "pdf", "version": "1.1.3", "stats": {"pages": 1},
            "pages": [blocks], **extra}

def run(blocks):
    return adapt_parse_result(PDF, result(blocks), DIMS,
        extractor_version="1.1.3", extractor_config=CFG)

def test_text_block_has_exact_provenance_and_normalized_bbox():
    out = run([{"type": "paragraph", "text": "VIN 5V",
                "bbox": {"x0": 20, "y0": 10, "x1": 100, "y1": 30}}])
    item = out["items"][0]
    assert item["status"] == "CANDIDATE"
    assert item["bbox"] == [0.1, 0.1, 0.5, 0.3]
    assert item["source_sha256"] == hashlib.sha256(PDF).hexdigest()
    assert out["summary"]["verified_items"] == 0
    assert out["authority"]["may_create_or_modify_nets"] is False

def test_missing_bbox_is_unresolved_not_fabricated():
    out = run([{"type": "paragraph", "text": "R1 10k"}])
    assert out["items"] == []
    assert out["unresolved"][0]["reason"] == "source bbox is missing"

def test_ocr_text_is_unknown():
    out = run([{"type": "image", "text": "GND", "ocr": True,
                "bbox": {"x0": 0, "y0": 0, "x1": 20, "y1": 20}}])
    assert out["items"][0]["status"] == "UNKNOWN"

def test_figure_caption_only_no_visual_graph_edges():
    block = {"type": "figure", "caption": "Partial schematic",
             "mermaid": "A --- B", "visualContent": {"nodes": ["A", "B"]},
             "bbox": {"x0": 10, "y0": 10, "x1": 100, "y1": 80}}
    out = run([block])
    assert out["items"][0]["raw_text"] == "Partial schematic"
    assert out["summary"]["connectivity_claims"] == 0

def test_out_of_page_bbox_is_unresolved():
    out = run([{"type": "paragraph", "text": "bad",
                "bbox": {"x0": 0, "y0": 0, "x1": 500, "y1": 10}}])
    assert out["items"] == [] and out["unresolved"]

def test_missing_page_dimensions_fail_closed():
    with pytest.raises(AdapterError, match="independent page dimensions"):
        adapt_parse_result(PDF, result([]), [], extractor_version="1.1.3", extractor_config=CFG)

def test_multi_page_rag_chunk_not_attributed_to_one_page():
    rag = {"id": "abc", "text": "joined text", "sources": [
        {"page": 1, "bbox": {"x0": 0, "y0": 0, "x1": 2, "y1": 2}},
        {"page": 2, "bbox": {"x0": 0, "y0": 0, "x1": 2, "y1": 2}}]}
    data = {"kind": "pdf", "stats": {"pages": 1}, "ragChunks": [rag]}
    out = adapt_parse_result(PDF, data, DIMS, extractor_version="1.1.3", extractor_config=CFG)
    assert out["items"] == []
    assert out["unresolved"][0]["reason"] == "RAG_SOURCE_NOT_UNIQUE"

def test_invalid_page_dimensions_are_rejected():
    with pytest.raises(AdapterError):
        adapt_parse_result(PDF, result([]), [(0, 100)], extractor_version="1.1.3", extractor_config=CFG)

def test_list_blocks_are_retained_and_accounted():
    out = run([{"type": "list", "items": [
        {"text": "Unit: mm", "ordered": False},
        {"text": "Maker design", "ordered": False},
    ], "bbox": {"x0": 10, "y0": 10, "x1": 80, "y1": 40}}])
    assert len(out["items"]) == 1
    assert out["items"][0]["kind"] == "list_item"
    assert out["items"][0]["raw_text"] == "Unit: mm\nMaker design"
    assert out["summary"]["source_blocks_seen"] == 1
    assert out["summary"]["source_blocks_accounted"] == 1
    assert out["summary"]["block_accounting_complete"] is True

def test_code_blocks_are_retained():
    out = run([{"type": "code", "text": "A -> B",
                "bbox": {"x0": 10, "y0": 10, "x1": 80, "y1": 40}}])
    assert out["items"][0]["kind"] == "code"
    assert out["items"][0]["raw_text"] == "A -> B"

def test_form_section_title_and_note_are_retained():
    out = run([{"type": "form_section", "title": "Checkboxes",
                "note": "Select one", "bbox": {"x0": 10, "y0": 10, "x1": 80, "y1": 40}}])
    assert out["items"][0]["kind"] == "form_section"
    assert out["items"][0]["raw_text"] == "Checkboxes\nSelect one"

def test_unsupported_or_empty_blocks_are_explicitly_unresolved():
    out = run([{"type": "unknown_extension", "value": "not silently dropped"},
               {"type": "paragraph", "text": ""}])
    assert out["items"] == []
    assert [x["reason"] for x in out["unresolved"]] == [
        "UNSUPPORTED_BLOCK_TYPE:unknown_extension", "NO_TEXT_CONTENT"
    ]
    assert out["summary"]["source_blocks_seen"] == 2
    assert out["summary"]["source_blocks_accounted"] == 2
    assert out["summary"]["block_accounting_complete"] is True

def test_page_count_must_match_pages_array():
    data = {"kind": "pdf", "stats": {"pages": 2}, "pages": [[]]}
    with pytest.raises(AdapterError, match="one block list per declared page"):
        adapt_parse_result(PDF, data, [(200.0, 100.0), (200.0, 100.0)],
            extractor_version="1.1.3", extractor_config=CFG)
