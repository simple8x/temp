#!/usr/bin/env python3
"""Validate a manually authored QI9 annotation file without inferring facts.

The validator is intentionally standard-library-only and read-only. It never
creates, upgrades, or repairs annotations. Candidate data is not ground truth.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA = "qi9-ground-truth-annotation/v1"
KINDS = {
    "SYMBOL_BOUNDARY", "PIN_IDENTITY", "WIRE_SEGMENT",
    "JUNCTION", "NET_LABEL", "CONNECTIVITY",
}
STATUSES = {
    "NOT_REVIEWED", "REVIEWED_PENDING_SECOND_REVIEW", "VERIFIED",
    "VERIFIED_CONNECTED", "VERIFIED_SEPARATE", "UNKNOWN",
    "CONFLICT", "REJECTED",
}
CONFIRMATION_STATUSES = {"VERIFIED", "VERIFIED_CONNECTED", "VERIFIED_SEPARATE"}


def validate(document: object, expected_sha256: str | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["document must be a JSON object"]
    if document.get("schema") != SCHEMA:
        errors.append(f"schema must equal {SCHEMA}")
    digest = document.get("source_pdf_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        errors.append("source_pdf_sha256 must be 64 lowercase hexadecimal characters")
    elif expected_sha256 and digest != expected_sha256:
        errors.append("source_pdf_sha256 does not match the pinned source PDF")
    if not isinstance(document.get("page"), int) or document["page"] < 1:
        errors.append("page must be a positive 1-based integer")
    if document.get("coordinate_system") != "pdf_points_origin_top_left":
        errors.append("coordinate_system must be pdf_points_origin_top_left")

    rows = document.get("annotations")
    if not isinstance(rows, list):
        errors.append("annotations must be an array")
        return errors

    ids: set[str] = set()
    for i, row in enumerate(rows):
        prefix = f"annotations[{i}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} must be an object")
            continue
        aid = row.get("annotation_id")
        if not isinstance(aid, str) or not aid:
            errors.append(f"{prefix}.annotation_id is required")
        elif aid in ids:
            errors.append(f"duplicate annotation_id: {aid}")
        else:
            ids.add(aid)
        if row.get("kind") not in KINDS:
            errors.append(f"{prefix}.kind is invalid")
        if row.get("status") not in STATUSES:
            errors.append(f"{prefix}.status is invalid")
        subject = row.get("subject")
        if not isinstance(subject, dict) or not all(
            isinstance(subject.get(k), str) and subject.get(k)
            for k in ("type", "id")
        ):
            errors.append(f"{prefix}.subject requires non-empty type and id")
        evidence = row.get("evidence")
        if not isinstance(evidence, list):
            errors.append(f"{prefix}.evidence must be an array")
            evidence = []
        reviewers = row.get("reviewers")
        if not isinstance(reviewers, list):
            errors.append(f"{prefix}.reviewers must be an array")
            reviewers = []

        status = row.get("status")
        if status in CONFIRMATION_STATUSES:
            if not evidence:
                errors.append(f"{prefix}: confirmed fact requires source evidence")
            confirmed = {
                r.get("reviewer_id") for r in reviewers
                if isinstance(r, dict) and r.get("decision") == "CONFIRM"
                and isinstance(r.get("reviewer_id"), str) and r.get("reviewer_id")
            }
            if len(confirmed) < 2:
                errors.append(f"{prefix}: VERIFIED requires two distinct confirming reviewers")

        assertion = row.get("assertion")
        if status in {"VERIFIED_CONNECTED", "VERIFIED_SEPARATE"}:
            if row.get("kind") != "CONNECTIVITY":
                errors.append(f"{prefix}: connected/separate status requires kind CONNECTIVITY")
            if not isinstance(assertion, dict):
                errors.append(f"{prefix}: connected/separate fact requires assertion endpoints")
            else:
                if assertion.get("predicate") != (
                    "CONNECTED_TO" if status == "VERIFIED_CONNECTED" else "SEPARATE_FROM"
                ):
                    errors.append(f"{prefix}: assertion predicate conflicts with status")

    # Validate cross-references after collecting all annotation IDs.
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        assertion = row.get("assertion")
        if assertion is None:
            continue
        if not isinstance(assertion, dict):
            errors.append(f"annotations[{i}].assertion must be an object")
            continue
        for field in ("from_annotation_id", "to_annotation_id"):
            target = assertion.get(field)
            if target not in ids:
                errors.append(f"annotations[{i}].assertion.{field} references unknown annotation")
        if assertion.get("from_annotation_id") == assertion.get("to_annotation_id"):
            errors.append(f"annotations[{i}].assertion endpoints must differ")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("annotation_json")
    parser.add_argument("--expected-source-sha256")
    args = parser.parse_args(argv)
    try:
        document = json.loads(Path(args.annotation_json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INVALID: cannot read JSON: {exc}", file=sys.stderr)
        return 2
    errors = validate(document, args.expected_source_sha256)
    if errors:
        print(json.dumps({"status": "BLOCKED", "errors": errors}, indent=2))
        return 1
    print(json.dumps({
        "status": "VALID_ANNOTATION_STRUCTURE_ONLY",
        "annotation_count": len(document["annotations"]),
        "source_pdf_sha256": document["source_pdf_sha256"],
        "page": document["page"],
        "electrical_signoff": "NOT_EVALUATED",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
