"""Exercise the actual pdf2kicad CLI writer on the pinned real QI9 PDF.

This validates file generation and output invariants only. It deliberately does
not claim that coordinate fallbacks are physical pin numbers or that the
resulting connectivity matches an independent schematic oracle.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

from pdf2kicad.cli import main as cli_main

PDF = Path("QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/source/[QI9-2604-A01] Module IO.pdf")
PAGES = range(5, 12)
EXPECTED_SHA256 = "a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc"
OUT = Path("production_cli_real_pdf")


def read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise AssertionError(f"CLI output missing: {path}")
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = set(reader.fieldnames or [])
        if not required.issubset(fields):
            raise AssertionError(f"{path}: missing columns {sorted(required - fields)}")
        return list(reader)


def main() -> None:
    if not PDF.is_file():
        raise FileNotFoundError(PDF)
    digest = hashlib.sha256(PDF.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(f"PDF SHA256 mismatch: {digest}")

    OUT.mkdir(parents=True, exist_ok=True)
    pages = []
    total_net_rows = total_status_rows = total_unknown = total_coordinate_fallback = 0
    for page in PAGES:
        page_dir = OUT / f"page-{page}"
        # Invoke the same entry point as the installed pdf2kicad command.
        cli_main(["pdf2kicad", str(PDF), str(page), str(page_dir)])
        net_rows = read_csv(page_dir / "netlist.csv", {"net", "ref", "pin"})
        status_rows = read_csv(
            page_dir / "pin_status.csv",
            {"ref", "pin_identity", "identity_kind", "status", "evidence", "component_assignment"},
        )
        read_csv(page_dir / "no_connect.csv", {"ref", "pin_identity", "identity_kind", "evidence"})
        if any(not row["net"] or not row["ref"] or not row["pin"] for row in net_rows):
            raise AssertionError(f"page {page}: netlist.csv contains empty net/ref/pin fields")
        unknown = sum(1 for row in net_rows if row["pin"].replace(".", "", 1).isdigit())
        coordinate = sum(1 for row in net_rows if row["pin"] and row["pin"][0].isdigit())
        total_net_rows += len(net_rows)
        total_status_rows += len(status_rows)
        total_unknown += unknown
        total_coordinate_fallback += coordinate
        pages.append({
            "page": page,
            "netlist_rows": len(net_rows),
            "pin_status_rows": len(status_rows),
            "coordinate_like_pin_tokens_in_netlist": coordinate,
            "coordinate_fallback_candidate_rows": unknown,
            "no_connect_rows": sum(1 for row in read_csv(
                page_dir / "no_connect.csv",
                {"ref", "pin_identity", "identity_kind", "evidence"},
            )),
            "writer_status": "FILES_GENERATED_SCHEMA_VALID",
        })

    report = {
        "schema": "pdf2kicad-production-cli-real-pdf-writer-audit/v1",
        "source_pdf": str(PDF),
        "source_sha256": digest,
        "source_sha256_check": "PASS",
        "pages": pages,
        "summary": {
            "pages_written": len(pages),
            "netlist_rows": total_net_rows,
            "pin_status_rows": total_status_rows,
            "coordinate_like_pin_tokens_in_netlist": total_coordinate_fallback,
            "coordinate_fallback_candidate_rows": total_unknown,
        },
        "status": "PRODUCTION_CLI_WRITER_EXECUTED_OUTPUT_SCHEMA_VALID_ELECTRICAL_SIGNOFF_BLOCKED",
        "limitations": [
            "Successful file generation is not proof of correct electrical connectivity.",
            "Coordinate-like pin tokens are geometric fallbacks, not verified library pin numbers.",
            "This audit does not compare against a complete independent reference netlist.",
            "No KiCad schematic serialization or ERC is performed by this CSV writer.",
        ],
    }
    (OUT / "production_cli_writer_audit.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
