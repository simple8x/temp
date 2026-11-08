import hashlib
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf2kicad.independent_reference_audit import (
    extract_pdf_text_inventory, resolve_ledger_candidates, build_exception_ledger,
)


def test_raw_pdf_pin_text_is_candidate_not_verified(tmp_path):
    path = tmp_path / "source.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=100)
    page.insert_text((42, 51), "3", fontsize=8)
    doc.save(path)
    doc.close()

    inventory = extract_pdf_text_inventory(str(path), [1])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    ledger = {
        "source_pdf_sha256": digest,
        "entries": [{
            "page": 1, "ref_candidate": "U1", "pin_candidate": None,
            "terminal_xy_pdf_pt": [43, 48], "reference_status": "UNKNOWN",
        }],
        "summary": {"independently_reviewed_facts_applied": 0},
    }
    resolution = resolve_ledger_candidates(ledger, inventory, radius_pt=8)
    assert resolution["printed_pin_text_candidates"] == 1
    assert resolution["independently_verified_pin_facts_from_existing_review"] == 0
    assert resolution["symbol_identity_resolved"] == 0
    assert resolution["rows"][0]["electrical_status"] == "NOT_VERIFIED"

    exceptions = build_exception_ledger(ledger, resolution, [])
    assert exceptions["pin_identity"]["candidate_only"] == 1
    assert exceptions["electrical_connectivity"]["signoff"] == "BLOCKED"


def test_pdf_hash_mismatch_fails_closed(tmp_path):
    path = tmp_path / "source.pdf"
    doc = pymupdf.open()
    doc.new_page(width=100, height=100)
    doc.save(path)
    doc.close()
    inventory = extract_pdf_text_inventory(str(path), [1])
    try:
        resolve_ledger_candidates({"source_pdf_sha256": "wrong", "entries": []}, inventory)
    except ValueError as exc:
        assert "SHA-256 mismatch" in str(exc)
    else:
        raise AssertionError("expected source hash mismatch to fail closed")


def test_evidence_fusion_uses_one_to_one_candidates_and_never_promotes(tmp_path):
    import json
    from pdf2kicad.independent_reference_audit import build_independent_evidence_fusion

    digest = "a" * 64
    ledger = {
        "source_pdf_sha256": digest,
        "entries": [
            {"page": 1, "ref_candidate": "U1", "terminal_xy_pdf_pt": [10.0, 10.0],
             "candidate_net_name_diagnostic_only": "N1", "reference_status": "UNKNOWN"},
            {"page": 1, "ref_candidate": "U1", "terminal_xy_pdf_pt": [20.0, 10.0],
             "candidate_net_name_diagnostic_only": "N1", "reference_status": "UNKNOWN"},
        ],
    }
    vector = {
        "source_sha256": digest, "terminal_count": 2,
        "words": [
            {"page": 1, "text": "1", "bbox": [9.5, 9.5, 10.5, 10.5],
             "pin_token_candidate": True, "reference_token_candidate": False},
            {"page": 1, "text": "2", "bbox": [19.5, 9.5, 20.5, 10.5],
             "pin_token_candidate": True, "reference_token_candidate": False},
            {"page": 1, "text": "U1", "bbox": [14.0, 2.0, 18.0, 4.0],
             "pin_token_candidate": False, "reference_token_candidate": True},
        ],
    }
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "page-1.pdf-reference-graph.json").write_text(json.dumps({
        "source_sha256": digest,
        "segments": [
            {"id": 0, "a": [10.0, 10.0], "b": [15.0, 10.0]},
            {"id": 1, "a": [20.0, 10.0], "b": [25.0, 10.0]},
        ],
        "components": [[0], [1]],
    }))
    report = build_independent_evidence_fusion(ledger, vector, graph_dir)
    assert report["stages"]["pin_identity"]["mutual_nearest_one_to_one_text_candidates"] == 2
    assert report["stages"]["symbol_ownership"]["newly_verified"] == 0
    assert report["stages"]["wire_vs_symbol_vs_graphic"]["verified_wire_classifications"] == 0
    assert report["stages"]["net_membership"]["newly_verified_memberships"] == 0
    assert all(row["pin_identity"] == "UNKNOWN" for row in report["review_rows"])
    assert report["electrical_signoff"] == "BLOCKED"


def test_evidence_fusion_rejects_graph_from_different_pdf(tmp_path):
    import json
    from pdf2kicad.independent_reference_audit import build_independent_evidence_fusion

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "page-1.pdf-reference-graph.json").write_text(json.dumps({
        "source_sha256": "wrong", "segments": [], "components": [],
    }))
    ledger = {"source_pdf_sha256": "a" * 64, "entries": [
        {"page": 1, "terminal_xy_pdf_pt": [1, 1], "ref_candidate": "R1"},
    ]}
    vector = {"source_sha256": "a" * 64, "terminal_count": 1, "words": []}
    try:
        build_independent_evidence_fusion(ledger, vector, graph_dir)
    except ValueError as exc:
        assert "SHA-256 mismatch" in str(exc)
    else:
        raise AssertionError("expected graph source hash mismatch to fail closed")
