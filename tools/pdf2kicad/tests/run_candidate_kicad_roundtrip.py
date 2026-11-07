#!/usr/bin/env python3
"""Create explicitly UNVERIFIED KiCad draft sheets from non-coordinate PDF parser candidates.

This is a production-writer/read-back exercise, not electrical sign-off. Coordinate
fallbacks and unresolved terminals are excluded rather than emitted as pin numbers.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "tools" / "gen_kicad_sch.py"
VERIFIER = ROOT / "tools" / "verify_kicad_sch.py"
COORD = re.compile(r"^-?\d+(?:\.\d+)?_-?\d+(?:\.\d+)?$")


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["net", "ref", "pin"])
        for row in rows:
            w.writerow([row["net"], row["ref"], row["pin"]])


def build_page(page_dir: Path, out_dir: Path):
    evidence_path = page_dir / "connectivity_evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    candidates = []
    rejected = {"coordinate_fallback": 0, "unknown_pin_identity": 0, "conflicting_pin": 0}
    assignments = {}
    for row in evidence.get("memberships", []):
        kind = row.get("pin_identity_kind")
        pin = str(row.get("pin_identity") or "").strip()
        ref = str(row.get("ref") or "").strip()
        net = str(row.get("net") or "").strip()
        if kind == "coordinate_fallback" or COORD.fullmatch(pin):
            rejected["coordinate_fallback"] += 1
            continue
        # A small, explicit class of geometry-derived identities is safe for
        # an UNVERIFIED draft: pin 1/2 on a two-terminal passive when the parser
        # found the complementary endpoint from an explicit pin token or polarity.
        # This is not accepted as independent electrical reference truth.
        inferred_two_pin = kind == "geometry_inferred" and pin in ("1", "2")
        if kind != "parser_pin_token" and not inferred_two_pin:
            rejected["unknown_pin_identity"] += 1
            continue
        if not (pin and ref and net):
            rejected["unknown_pin_identity"] += 1
            continue
        key = (ref, pin)
        if key in assignments and assignments[key] != net:
            rejected["conflicting_pin"] += 1
            assignments[key] = None
            continue
        assignments[key] = net
    # Explicit NC marks are retained only when the parser identified a real pin token.
    for row in evidence.get("no_connect_marks", []):
        kind = row.get("pin_identity_kind")
        pin = str(row.get("pin_identity") or "").strip()
        ref = str(row.get("ref") or "").strip()
        if kind == "coordinate_fallback" or COORD.fullmatch(pin):
            rejected["coordinate_fallback"] += 1
            continue
        if kind != "parser_pin_token" or not (pin and ref):
            rejected["unknown_pin_identity"] += 1
            continue
        key = (ref, pin)
        if key in assignments and assignments[key] != "NC":
            rejected["conflicting_pin"] += 1
            assignments[key] = None
        else:
            assignments[key] = "NC"
    rows = [{"ref": ref, "pin": pin, "net": net}
            for (ref, pin), net in sorted(assignments.items()) if net is not None]
    # Remove any key that conflicted at least once; never choose one net arbitrarily.
    netlist = out_dir / "candidate_netlist.csv"
    write_csv(netlist, rows)
    parts = page_dir / "parts.csv"
    sch = out_dir / f"{page_dir.name}.candidate.kicad_sch"
    if not rows:
        return {
            "page": page_dir.name, "status": "BLOCKED_NO_SAFE_PIN_CANDIDATES",
            "candidate_rows": 0, "rejected": rejected,
        }
    generated = subprocess.run(
        [sys.executable, str(GENERATOR), str(netlist), str(sch),
         "--parts", str(parts), "--title", f"{page_dir.name} — UNVERIFIED PDF reconstruction"],
        capture_output=True, text=True,
    )
    if generated.returncode:
        return {
            "page": page_dir.name, "status": "DRAFT_GENERATION_FAILED",
            "candidate_rows": len(rows), "rejected": rejected,
            "error": generated.stdout + generated.stderr,
        }
    checked = subprocess.run(
        [sys.executable, str(VERIFIER), str(sch), str(netlist)],
        capture_output=True, text=True,
    )
    result = {
        "page": page_dir.name,
        "status": "DRAFT_READBACK_PASS_NOT_ELECTRICAL_SIGNOFF" if checked.returncode == 0 else "DRAFT_READBACK_FAILED",
        "candidate_rows": len(rows),
        "excluded_candidate_rows": rejected,
        "schematic": str(sch),
        "readback_exit_code": checked.returncode,
        "readback_output": checked.stdout + checked.stderr,
        "electrical_signoff": "BLOCKED_NO_INDEPENDENT_PDF_REFERENCE",
        "layout_fidelity": "NOT_CLAIMED_GENERATOR_PLACES_SYMBOLS_BY_BLOCK",
    }
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=Path("production_cli_real_pdf"))
    ap.add_argument("--outdir", type=Path, default=Path("pdf2kicad_candidate_schematics"))
    ap.add_argument("--pages", default="5-11")
    a = ap.parse_args()
    start, end = (int(x) for x in a.pages.split("-", 1))
    results = []
    for page in range(start, end + 1):
        src = a.input / f"page-{page}"
        if not (src / "connectivity_evidence.json").is_file():
            results.append({"page": page, "status": "BLOCKED_MISSING_PRODUCTION_EVIDENCE"})
            continue
        results.append(build_page(src, a.outdir / f"page-{page}"))
    passed = sum(x["status"] == "DRAFT_READBACK_PASS_NOT_ELECTRICAL_SIGNOFF" for x in results)
    report = {
        "schema": "pdf2kicad-candidate-schematic-roundtrip/v1",
        "status": "DRAFT_ROUNDTRIP_COMPLETE_ELECTRICAL_SIGNOFF_BLOCKED" if passed == len(results) else "DRAFT_ROUNDTRIP_PARTIAL_OR_FAILED",
        "pages_total": len(results),
        "pages_readback_pass": passed,
        "pages": results,
        "policy": {
            "coordinate_fallbacks": "excluded, never serialized as pin numbers",
            "parser_candidates": "unverified candidates, not PDF reference truth",
            "geometry_inferred_pin_1_2": "included only for conservative two-terminal passive convention inference; never promoted to verified reference",
            "KiCad_readback": "checks serialization round-trip only",
            "electrical_signoff": "requires independently reviewed PDF-derived reference connectivity",
        },
    }
    a.outdir.mkdir(parents=True, exist_ok=True)
    (a.outdir / "candidate_roundtrip_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if passed == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
