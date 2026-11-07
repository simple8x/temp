from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from roundtrip_pipeline import SCHEMA, validate_reference, write_reference_netlist


def reference(tmp_path, entries, sha="abc"):
    path = tmp_path / "page-005.json"
    path.write_text(json.dumps({
        "schema": SCHEMA,
        "source_pdf_sha256": sha,
        "page": 5,
        "entries": entries,
    }), encoding="utf-8")
    return path


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


def test_accepts_reviewed_pdf_reference(tmp_path):
    rows = validate_reference(reference(tmp_path, [row()]), 5, "abc")
    assert len(rows) == 1
    assert rows[0]["ref"] == "U1"


def test_rejects_coordinate_as_pin_number(tmp_path):
    path = reference(tmp_path, [row(pin="123.45_67.89")])
    try:
        validate_reference(path, 5, "abc")
    except ValueError as exc:
        assert "coordinate-like token" in str(exc)
    else:
        raise AssertionError("coordinate fallback was accepted as a pin number")


def test_rejects_unreviewed_unknown(tmp_path):
    path = reference(tmp_path, [row(status="UNKNOWN")])
    try:
        validate_reference(path, 5, "abc")
    except ValueError as exc:
        assert "not VERIFIED/NC" in str(exc)
    else:
        raise AssertionError("UNKNOWN entry was promoted")


def test_rejects_wrong_source_pdf_hash(tmp_path):
    path = reference(tmp_path, [row()], sha="wrong")
    try:
        validate_reference(path, 5, "abc")
    except ValueError as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("reference from another PDF was accepted")


def test_rejects_duplicate_physical_pin(tmp_path):
    path = reference(tmp_path, [row(), row(net="GND")])
    try:
        validate_reference(path, 5, "abc")
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
