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
