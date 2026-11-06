"""Fail-closed contract tests for the multi-page electrical audit."""
import csv
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "tools" / "audit_electrical.py"
SPEC = importlib.util.spec_from_file_location("audit_electrical", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def _csv(path, header, rows=()):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def _complete_tree(root, identity_kind="printed_text"):
    for page in range(5, 12):
        folder = root / ("page%02d" % page)
        folder.mkdir()
        _csv(folder / "netlist.csv", ["net", "ref", "pin"], [["N1", "U1", "1"]])
        _csv(folder / "pin_identity.csv", ["ref", "pin_identity", "identity_kind", "net"],
             [["U1", "1", identity_kind, "N1"]])
        _csv(folder / "pins.csv", ["ref", "pin", "name", "side"], [["U1", "1", "VIN", "left"]])
        _csv(folder / "pin_conflicts.csv", ["ref", "pin_identity", "nets", "net_count"])


def test_missing_required_page_blocks(tmp_path):
    _complete_tree(tmp_path)
    (tmp_path / "page11").rename(tmp_path / "page12")
    assert AUDIT.main(str(tmp_path)) == 2
    report = json.loads((tmp_path / "electrical-audit.json").read_text())
    assert report["status"] == "BLOCKED"
    assert report["missing_pages"] == ["page11"]


def test_coordinate_fallback_never_passes(tmp_path):
    _complete_tree(tmp_path, "coordinate_fallback")
    assert AUDIT.main(str(tmp_path)) == 2
    report = json.loads((tmp_path / "electrical-audit.json").read_text())
    assert report["status"] == "REVIEW_REQUIRED"
    assert report["release_eligible"] is False


def test_missing_csv_blocks(tmp_path):
    _complete_tree(tmp_path)
    (tmp_path / "page07" / "pin_identity.csv").unlink()
    assert AUDIT.main(str(tmp_path)) == 2
    report = json.loads((tmp_path / "electrical-audit.json").read_text())
    assert report["status"] == "BLOCKED"
    assert {"page": "page07", "file": "pin_identity.csv"} in report["missing_outputs"]
