#!/usr/bin/env python3
"""PDF -> reviewable connectivity reference -> KiCad -> independent read-back gate.

Parser output is only a candidate ledger. A reviewed reference must resolve every
candidate (attached, open, and NC) against the original PDF before KiCad export.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from pdf2kicad.cli import main as extract_page
from pdf2kicad.electrical_evidence import classify_page as independent_pdf_junctions

SCHEMA = "pdf2kicad-pdf-reference-connectivity/v1"
COORDINATE_TOKEN = re.compile(r"^-?\d+(?:\.\d+)?_-?\d+(?:\.\d+)?$")
HERE = Path(__file__).resolve().parent
GENERATOR = HERE / "gen_kicad_sch.py"
VERIFIER = HERE / "verify_kicad_sch.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def candidate_ledger(pdf: Path, page: int, page_dir: Path, source_sha: str):
    evidence = load_json(page_dir / "connectivity_evidence.json")
    entries = []
    inventory = evidence.get("terminal_inventory", [])
    if not inventory:
        raise ValueError(
            "connectivity evidence has no complete terminal_inventory; "
            "refusing to build a partial PDF reference ledger"
        )

    def add(row):
        row["source_candidate_index"] = len(entries)
        entries.append(row)

    for terminal in inventory:
        wire = terminal.get("wire_primitives", [])
        candidate_nets = terminal.get("candidate_net_names_diagnostic_only", [])
        candidate_net = candidate_nets[0] if len(candidate_nets) == 1 else None
        pin = terminal.get("pin_identity")
        add({
            "terminal_index": terminal.get("terminal_index"),
            "ref": terminal.get("ref"),
            "pin": pin if terminal.get("pin_identity_kind") == "parser_pin_token" else None,
            "net": candidate_net,
            "status": "UNKNOWN",
            "evidence": {
                "pdf_page": page,
                "terminal_xy_pdf_pt": [terminal.get("x"), terminal.get("y")],
                "terminal_status": terminal.get("status"),
                "anchor_kind": terminal.get("anchor_kind"),
                "symbol_group": terminal.get("symbol_group"),
                "wire_component": terminal.get("wire_component"),
                "primitive_ids": [str(x.get("primitive_index")) for x in wire],
                "wire_primitives": wire,
                "candidate_net_names_diagnostic_only": candidate_nets,
                "candidate_net_status": terminal.get("candidate_net_status"),
                "candidate_lineage": "complete_topology_terminal_inventory_parser_candidates_not_reference_truth",
            },
        })
    return {
        "schema": SCHEMA,
        "source_pdf": pdf.name,
        "source_pdf_sha256": source_sha,
        "page": page,
        "mode": "REVIEW_CANDIDATES_NOT_VERIFIED_REFERENCE",
        "summary": {
            "candidate_entries": len(entries),
            "topology_terminal_coverage": len(inventory),
            "verified_entries": 0,
            "unknown_entries": len(entries),
            "status": "BLOCKED_REFERENCE_REVIEW_REQUIRED",
        },
        "policy": {
            "parser_candidates_are_not_ground_truth": True,
            "coordinate_tokens_are_never_pin_numbers": True,
            "no_automatic_promotion_to_verified": True,
            "complete_candidate_coverage_required": True,
            "one_entry_per_topology_terminal": True,
            "review_source": "inspect the cited page and evidence coordinates in the original PDF",
        },
        "entries": entries,
    }


def validate_reference(path: Path, page: int, source_sha: str, candidate: dict, candidate_path: Path):
    doc = load_json(path)
    errors = []
    if doc.get("schema") != SCHEMA:
        errors.append("wrong or missing schema")
    if doc.get("source_pdf_sha256") != source_sha:
        errors.append("source PDF SHA-256 does not match")
    if int(doc.get("page", -1)) != page:
        errors.append("page number does not match")

    expected_count = candidate["summary"]["candidate_entries"]
    if doc.get("candidate_ledger_sha256") != digest(candidate_path):
        errors.append("candidate ledger SHA-256 does not match")
    if doc.get("expected_entry_count") != expected_count:
        errors.append("expected_entry_count must equal complete candidate count " + str(expected_count))

    entries = doc.get("entries")
    if not isinstance(entries, list) or len(entries) != expected_count:
        got = len(entries) if isinstance(entries, list) else "non-array"
        errors.append("reference must resolve all " + str(expected_count) + " candidates; got " + str(got))
        entries = entries if isinstance(entries, list) else []

    seen_pins = set()
    covered = set()
    verified = []
    for i, row in enumerate(entries):
        source_index = row.get("source_candidate_index")
        if not isinstance(source_index, int) or source_index < 0 or source_index >= expected_count:
            errors.append("entry " + str(i) + ": invalid source_candidate_index")
        elif source_index in covered:
            errors.append("entry " + str(i) + ": duplicate source_candidate_index " + str(source_index))
        else:
            covered.add(source_index)
            # The review row may resolve an UNKNOWN candidate, but it may not
            # silently rewrite a parser-explicit reference or printed pin token.
            # Such disagreements belong in the resolver/evidence stage first.
            candidate_row = candidate.get("entries", [])[source_index]
            candidate_ref = candidate_row.get("ref")
            candidate_pin = candidate_row.get("pin")
            if candidate_ref and row.get("ref") != candidate_ref:
                errors.append(
                    "entry " + str(i) + ": reviewed ref conflicts with candidate ref "
                    + repr(candidate_ref) + "; fix/resolve the candidate lineage first"
                )
            if candidate_pin and row.get("pin") != candidate_pin:
                errors.append(
                    "entry " + str(i) + ": reviewed pin conflicts with explicit candidate pin "
                    + repr(candidate_pin) + "; fix/resolve the pin resolver first"
                )

        status = row.get("status")
        ref, pin, net = row.get("ref"), row.get("pin"), row.get("net")
        if status not in ("VERIFIED", "NC"):
            errors.append("entry " + str(i) + ": status must be VERIFIED or NC, got " + repr(status))
            continue
        if not all(isinstance(v, str) and v.strip() for v in (ref, pin)):
            errors.append("entry " + str(i) + ": ref and pin must be non-empty strings")
            continue
        if COORDINATE_TOKEN.fullmatch(pin.strip()):
            errors.append("entry " + str(i) + ": coordinate-like token cannot be a pin number")
            continue
        if status == "VERIFIED" and (not isinstance(net, str) or not net.strip()):
            errors.append("entry " + str(i) + ": VERIFIED entry requires net")
            continue
        if status == "NC" and net not in (None, "", "NC"):
            errors.append("entry " + str(i) + ": NC entry cannot carry a net name")
        ev = row.get("evidence") or {}
        if int(ev.get("pdf_page", -1)) != page:
            errors.append("entry " + str(i) + ": evidence.pdf_page must equal " + str(page))
        if not ev.get("primitive_ids") or not ev.get("review_note"):
            errors.append("entry " + str(i) + ": requires PDF primitive/mark evidence and a human review_note")
        key = (ref.strip(), pin.strip())
        if key in seen_pins:
            errors.append("entry " + str(i) + ": duplicate physical pin " + repr(key))
        seen_pins.add(key)
        verified.append(row)

    missing = sorted(set(range(expected_count)) - covered)
    if missing:
        errors.append("reference coverage incomplete; missing candidate indices " + repr(missing[:30]))
    if errors:
        raise ValueError("REFERENCE BLOCKED:\n- " + "\n- ".join(errors))
    return verified


def write_reference_netlist(rows, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["net", "ref", "pin"])
        for row in sorted(rows, key=lambda x: (x["net"] if x["status"] == "VERIFIED" else "NC", x["ref"], x["pin"])):
            writer.writerow(["NC" if row["status"] == "NC" else row["net"], row["ref"], row["pin"]])


def run(pdf: Path, pages: list[int], outdir: Path, reference_dir: Path | None):
    source_sha = digest(pdf)
    summary = {
        "schema": "pdf2kicad-reference-roundtrip-run/v1",
        "source_pdf": str(pdf),
        "source_pdf_sha256": source_sha,
        "pages": [],
        "status": "BLOCKED",
        "limitations": [
            "Candidate generation is not independent verification.",
            "Direct PyMuPDF junction evidence is independent geometry evidence only; it does not establish complete net membership.",
            "Reference entries must cover every candidate index, including explicit NC and unresolved terminals, before KiCad generation.",
            "KiCad read-back proves serialization agreement with the reviewed reference, not that an ambiguous PDF has a unique interpretation.",
        ],
    }
    all_verified = True
    for page in pages:
        page_dir = outdir / ("page-" + str(page).zfill(3))
        extract_page(["pdf2kicad", str(pdf), str(page), str(page_dir)])
        candidate = candidate_ledger(pdf, page, page_dir, source_sha)
        candidate_path = outdir / "reference-candidates" / ("page-" + str(page).zfill(3) + ".json")
        write_json(candidate_path, candidate)
        direct_junctions = independent_pdf_junctions(pdf, page)
        junction_path = outdir / "independent-pdf-evidence" / ("page-" + str(page).zfill(3) + "-junctions.json")
        write_json(junction_path, direct_junctions)

        page_result = {
            "page": page,
            "candidate_file": str(candidate_path),
            "candidate_entries": candidate["summary"]["candidate_entries"],
            "candidate_ledger_sha256": digest(candidate_path),
            "independent_pdf_junction_evidence": str(junction_path),
            "independent_junction_summary": direct_junctions["summary"],
            "status": "BLOCKED_REFERENCE_REVIEW_REQUIRED",
        }
        if reference_dir is None:
            all_verified = False
            summary["pages"].append(page_result)
            continue
        ref_path = reference_dir / ("page-" + str(page).zfill(3) + ".json")
        if not ref_path.is_file():
            page_result["reference_error"] = "missing reviewed reference: " + str(ref_path)
            all_verified = False
            summary["pages"].append(page_result)
            continue
        try:
            rows = validate_reference(ref_path, page, source_sha, candidate, candidate_path)
            netlist = page_dir / "pdf_reference_netlist.csv"
            write_reference_netlist(rows, netlist)
            sch = page_dir / ("page-" + str(page).zfill(3) + ".kicad_sch")
            generated = subprocess.run(
                [sys.executable, str(GENERATOR), str(netlist), str(sch),
                 "--parts", str(page_dir / "parts.csv"), "--title", "PDF page " + str(page)],
                capture_output=True, text=True,
            )
            if generated.returncode:
                raise RuntimeError("KiCad generator failed: " + generated.stdout + generated.stderr)
            checked = subprocess.run(
                [sys.executable, str(VERIFIER), str(sch), str(netlist)],
                capture_output=True, text=True,
            )
            if checked.returncode:
                raise RuntimeError("independent KiCad read-back failed: " + checked.stdout + checked.stderr)
            page_result.update({
                "reference_file": str(ref_path),
                "reference_entries": len(rows),
                "kicad_schematic": str(sch),
                "readback": checked.stdout.strip(),
                "status": "PDF_REFERENCE_TO_KICAD_READBACK_PASS",
            })
        except Exception as exc:
            page_result["reference_error"] = str(exc)
            all_verified = False
        summary["pages"].append(page_result)

    if reference_dir is None:
        summary["status"] = "BLOCKED_REFERENCE_REVIEW_REQUIRED"
    else:
        summary["status"] = (
            "PDF_REFERENCE_TO_KICAD_READBACK_PASS"
            if all_verified and all(x["status"] == "PDF_REFERENCE_TO_KICAD_READBACK_PASS" for x in summary["pages"])
            else "BLOCKED_REFERENCE_REVIEW_OR_READBACK"
        )
    write_json(outdir / "roundtrip_report.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if reference_dir is None or summary["status"] == "PDF_REFERENCE_TO_KICAD_READBACK_PASS" else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--pages", default="5-11")
    ap.add_argument("--outdir", type=Path, default=Path("pdf2kicad-roundtrip"))
    ap.add_argument("--reference-dir", type=Path,
                    help="directory containing complete reviewed page-NNN.json references; omit to create candidate ledgers only")
    args = ap.parse_args()
    start, end = (int(x) for x in args.pages.split("-", 1))
    raise SystemExit(run(args.pdf, list(range(start, end + 1)), args.outdir, args.reference_dir))


if __name__ == "__main__":
    main()
