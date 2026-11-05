#!/usr/bin/env python3
"""Compare pdf2kicad netlist.csv outputs with the six VERIFIED QI9-2604-A01 anchors.

Usage:
  python tools/validate_qi9_2604_anchors.py out/page6 out/page7 out/page8
Each argument is a page output directory containing netlist.csv.
This is a smoke test only; it does not certify full-page connectivity.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

EXPECTED = {
    6: {("U5", "1", "GNDREF"), ("U5", "3", "GND_RS232")},
    7: {("U9", "1", "GNDREF"), ("U9", "3", "GND_RS485")},
    8: {("J5", "6", "RXN"), ("J5", "3", "TXN")},
}


def load_rows(directory: Path):
    path = directory / "netlist.csv"
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"net", "ref", "pin"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{path}: expected columns {sorted(required)}, got {reader.fieldnames}")
        return {
            (row["ref"].strip(), row["pin"].strip(), row["net"].strip())
            for row in reader
        }


def main(argv):
    if len(argv) != 4:
        raise SystemExit("usage: validate_qi9_2604_anchors.py PAGE6_DIR PAGE7_DIR PAGE8_DIR")

    results = []
    for page, arg in zip((6, 7, 8), argv[1:]):
        actual = load_rows(Path(arg))
        expected = EXPECTED[page]
        for ref, pin, net in sorted(expected):
            results.append({
                "page": page,
                "ref": ref,
                "pin": pin,
                "net": net,
                "status": "PASS" if (ref, pin, net) in actual else "FAIL",
            })

    passed = sum(item["status"] == "PASS" for item in results)
    report = {
        "project": "QI9-2604-A01",
        "test": "six_verified_pin_net_anchors",
        "passed": passed,
        "total": len(results),
        "status": "PASS" if passed == len(results) else "FAIL",
        "results": results,
        "scope_limit": "Smoke test only; not complete electrical topology verification.",
    }
    print(json.dumps(report, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
