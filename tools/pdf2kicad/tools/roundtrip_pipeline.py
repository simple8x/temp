#!/usr/bin/env python3
"""PDF -> reviewable reference connectivity -> KiCad -> independent read-back gate.

The first pass creates PDF-linked candidate ledgers only. It NEVER promotes
parser output to reference truth. A reviewer must resolve every pin/net against
the source PDF and mark each entry VERIFIED or NC before KiCad generation.
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
    for row in evidence.get("memberships", []):
        terminal = row.get("terminal", {})
        wire = row.get("wire_primitives", [])
        entries.append({
            "ref": row.get("ref"),
            "pin": row.get("pin_identity") if row.get("pin_identity_kind") != "coordinate_fallback" else None,
            "net": row.get("net"),
            "status": "UNKNOWN",
            "evidence": {
                "pdf_page": page,
                "terminal_xy_pdf_pt": [terminal.get("x"), terminal.get("y")],
                "primitive_ids": [str(x.get("primitive_index")) for x in wire],
                "wire_primitives": wire,
                "candidate_lineage": "parser_candidate_only_not_reference_truth",
            },
        })
    for row in evidence.get("unresolved_terminals", []):
        entries.append({
            "ref": row.get("ref"),
            "pin": None,
            "net": None,
            "status": "UNKNOWN",
            "evidence": {
                "pdf_page": page,
                "terminal_xy_pdf_pt": [row.get("x"), row.get("y")],
                "primitive_ids": [],
                "wire_primitives": [],
                "candidate_lineage": "unresolved_terminal_from_pdf_parser",
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
            "verified_entries": 0,
            "unknown_entries": len(entries),
            "status": "BLOCKED_REFERENCE_REVIEW_REQUIRED",
        },
        "policy": {
            "parser_candidates_are_not_ground_truth": True,
            "coordinate_tokens_are_never_pin_numbers": True,
            "no_automatic_promotion_to_verified": True,
            "review_source": "inspect the cited page and evidence coordinates in the original PDF",
        },
        "entries": entries,
    }


def validate_reference(path: Path, page: int, source_sha: str):
    doc = load_json(path)
    errors = []
    if doc.get("schema") != SCHEMA:
        errors.append("wrong or missing schema")
    if doc.get("source_pdf_sha256") != source_sha:
        errors.append("source PDF SHA-256 does not match")
    if int(doc.get("page", -1)) != page:
        errors.append("page number does not match")
    entries = doc.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("entries must be a non-empty array")
        entries = []
    seen = set()
    verified = []
    for i, row in enumerate(entries):
        status = row.get("status")
        ref, pin, net = row.get("ref"), row.get("pin"), row.get("net")
        if status not in ("VERIFIED", "NC"):
            errors.append(f"entry {i}: status {status!r} is not VERIFIED/NC")
            continue
        if not all(isinstance(v, str) and v.strip() for v in (ref, pin)):
            errors.append(f"entry {i}: ref and pin must be non-empty strings")
            continue
        if COORDINATE_TOKEN.fullmatch(pin.strip()):
            errors.append(f"entry {i}: coordinate-like token cannot be a pin number")
            continue
        if status == "VERIFIED" and (not isinstance(net, str) or not net.strip()):
            errors.append(f"entry {i}: VERIFIED entry requires net")
            continue
        ev = row.get("evidence") or {}
        if int(ev.get("pdf_page", -1)) != page:
            errors.append(f"entry {i}: evidence.pdf_page must equal {page}")
        if not ev.get("primitive_ids") or not ev.get("review_note"):
            errors.append(f"entry {i}: requires primitive_ids and a human review_note")
        key = (ref.strip(), pin.strip())
        if key in seen:
            errors.append(f"entry {i}: duplicate physical pin {key}")
        seen.add(key)
        verified.append(row)
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
            "Reference entries require explicit review against the original PDF.",
            "KiCad read-back proves serialization agreement with the reviewed reference, not that an ambiguous PDF has a unique interpretation.",
        ],
    }
    all_verified = True
    for page in pages:
        page_dir = outdir / f"page-{page:03d}"
        extract_page(["pdf2kicad", str(pdf), str(page), str(page_dir)])
        candidate = candidate_ledger(pdf, page, page_dir, source_sha)
        candidate_path = outdir / "reference-candidates" / f"page-{page:03d}.json"
        write_json(candidate_path, candidate)
        page_result = {
            "page": page,
            "candidate_file": str(candidate_path),
            "candidate_entries": candidate["summary"]["candidate_entries"],
            "status": "BLOCKED_REFERENCE_REVIEW_REQUIRED",
        }
        if reference_dir is None:
            all_verified = False
            summary["pages"].append(page_result)
            continue
        ref_path = reference_dir / f"page-{page:03d}.json"
        if not ref_path.is_file():
            page_result["reference_error"] = f"missing reviewed reference: {ref_path}"
            all_verified = False
            summary["pages"].append(page_result)
            continue
        try:
            rows = validate_reference(ref_path, page, source_sha)
            netlist = page_dir / "pdf_reference_netlist.csv"
            write_reference_netlist(rows, netlist)
            sch = page_dir / f"page-{page:03d}.kicad_sch"
            cmd = [sys.executable, str(GENERATOR), str(netlist), str(sch),
                   "--parts", str(page_dir / "parts.csv"),
                   "--title", f"PDF page {page}"]
            generated = subprocess.run(cmd, capture_output=True, text=True)
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
    # Candidate generation is a successful preparation step, not a sign-off.
    # Only a supplied reviewed reference that fails validation/read-back exits non-zero.
    return 0 if reference_dir is None or summary["status"] == "PDF_REFERENCE_TO_KICAD_READBACK_PASS" else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--pages", default="5-11")
    ap.add_argument("--outdir", type=Path, default=Path("pdf2kicad-roundtrip"))
    ap.add_argument("--reference-dir", type=Path,
                    help="directory containing reviewed page-NNN.json references; omit to create candidate ledgers only")
    args = ap.parse_args()
    start, end = (int(x) for x in args.pages.split("-", 1))
    raise SystemExit(run(args.pdf, list(range(start, end + 1)), args.outdir, args.reference_dir))


if __name__ == "__main__":
    main()
