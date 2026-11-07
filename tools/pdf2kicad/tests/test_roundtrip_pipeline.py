from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from roundtrip_pipeline import SCHEMA, validate_reference, write_reference_netlist


def setup_reference(tmp_path, entries, sha="abc"):
    candidate_path = tmp_path / "candidate.json"
    candidate = {
        "schema": SCHEMA,
        "source_pdf_sha256": sha,
        "page": 5,
        "summary": {"candidate_entries": len(entries)},
        "entries": [{"source_candidate_index": i} for i in range(len(entries))],
    }
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    rows = []
    for i, item in enumerate(entries):
        row = dict(item)
        row["source_candidate_index"] = i
        rows.append(row)
    ref_path = tmp_path / "page-005.json"
    ref_path.write_text(json.dumps({
        "schema": SCHEMA,
        "source_pdf_sha256": sha,
        "page": 5,
        "candidate_ledger_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "expected_entry_count": len(entries),
        "entries": rows,
    }), encoding="utf-8")
    return ref_path, candidate, candidate_path


def row(pin="1", status="VERIFIED", net="VCC"):
    return {
        "ref": "U1",
        "pin": pin,
        "net": net,
        "status": status,
        "evidence": {
            "pdf_page": 5,
            "primitive_ids": ["wire-17"],
            "review_note": "Checked against visible pin number and continuous vector wire on PDF page 5.",
        },
    }


def validate(tmp_path, entries, sha="abc"):
    path, candidate, candidate_path = setup_reference(tmp_path, entries, sha)
    return validate_reference(path, 5, sha, candidate, candidate_path)


def test_accepts_complete_reviewed_pdf_reference(tmp_path):
    rows = validate(tmp_path, [row()])
    assert len(rows) == 1
    assert rows[0]["ref"] == "U1"


def test_rejects_coordinate_as_pin_number(tmp_path):
    try:
        validate(tmp_path, [row(pin="123.45_67.89")])
    except ValueError as exc:
        assert "coordinate-like token" in str(exc)
    else:
        raise AssertionError("coordinate fallback was accepted as a pin number")


def test_rejects_unreviewed_unknown(tmp_path):
    try:
        validate(tmp_path, [row(status="UNKNOWN")])
    except ValueError as exc:
        assert "VERIFIED or NC" in str(exc)
    else:
        raise AssertionError("UNKNOWN entry was promoted")


def test_rejects_wrong_source_pdf_hash(tmp_path):
    path, candidate, candidate_path = setup_reference(tmp_path, [row()], sha="wrong")
    try:
        validate_reference(path, 5, "abc", candidate, candidate_path)
    except ValueError as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("reference from another PDF was accepted")


def test_rejects_duplicate_physical_pin(tmp_path):
    try:
        validate(tmp_path, [row(), row(net="GND")])
    except ValueError as exc:
        assert "duplicate physical pin" in str(exc)
    else:
        raise AssertionError("same physical pin was assigned twice")


def test_reference_netlist_preserves_nc(tmp_path):
    path = tmp_path / "netlist.csv"
    write_reference_netlist([row(), row(pin="2", status="NC", net=None)], path)
    text = path.read_text(encoding="utf-8")
    assert "VCC,U1,1" in text
    assert "NC,U1,2" in text


def test_rejects_incomplete_candidate_coverage(tmp_path):
    path, candidate, candidate_path = setup_reference(tmp_path, [row(), row(pin="2", net="GND")])
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["entries"] = doc["entries"][:1]
    path.write_text(json.dumps(doc), encoding="utf-8")
    try:
        validate_reference(path, 5, "abc", candidate, candidate_path)
    except ValueError as exc:
        assert "resolve all 2 candidates" in str(exc)
    else:
        raise AssertionError("partial reference was incorrectly accepted")


def test_rejects_missing_candidate_index(tmp_path):
    path, candidate, candidate_path = setup_reference(tmp_path, [row(), row(pin="2", net="GND")])
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["entries"][1]["source_candidate_index"] = 0
    path.write_text(json.dumps(doc), encoding="utf-8")
    try:
        validate_reference(path, 5, "abc", candidate, candidate_path)
    except ValueError as exc:
        assert "duplicate source_candidate_index" in str(exc)
    else:
        raise AssertionError("duplicate candidate index was accepted")
