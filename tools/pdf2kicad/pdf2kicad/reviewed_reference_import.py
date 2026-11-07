"""Import a small, independently reviewed set of pin/net facts from the QI9 PDF.

This step does not infer missing pins or promote parser geometry. It joins only
exact VERIFIED (page, reference, pin, net) facts to one uniquely matching parser
candidate, retaining the parser's candidate-net label for partition comparison.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PIN_RE = re.compile(r"^\s*Pin\s+([^:]+):")


def import_facts(ledger: dict, verification: dict) -> dict:
    if ledger.get("source_pdf_sha256") != verification.get("source_sha256"):
        raise ValueError("source PDF SHA-256 mismatch between candidate ledger and reviewed holdout")
    entries = ledger.get("entries")
    facts = verification.get("items")
    if not isinstance(entries, list) or not isinstance(facts, list):
        raise ValueError("ledger.entries and verification.items must be arrays")

    declared_verified = verification.get("verified_count")
    actual_verified = sum(f.get("resolution_status") == "VERIFIED" for f in facts)
    if declared_verified != actual_verified:
        raise ValueError("holdout verified_count does not match its VERIFIED records")

    grouped: dict[tuple[int, str, str], dict] = {}
    for fact in facts:
        if fact.get("resolution_status") != "VERIFIED":
            continue
        pin_match = PIN_RE.match(str(fact.get("pin_identity") or ""))
        symbol = str(fact.get("symbol_identity") or "")
        ref = symbol.split(" / ", 1)[0].strip()
        pin = pin_match.group(1).strip() if pin_match else ""
        net = str(fact.get("net_identity") or "").strip()
        page = fact.get("source_page")
        if not all((ref, pin, net)) or not isinstance(page, int):
            raise ValueError(f"malformed VERIFIED fact {fact.get('resolution_id')}")
        if fact.get("confidence") != "HIGH" or fact.get("evidence_type") != "VECTOR_GEOMETRY":
            raise ValueError(f"reviewed fact lacks HIGH VECTOR_GEOMETRY evidence: {fact.get('resolution_id')}")
        key = (page, ref, pin)
        prior = grouped.get(key)
        if prior and prior["net"] != net:
            raise ValueError(f"conflicting independently reviewed nets for {key}")
        if prior and prior["normalized_reference"] != fact.get("normalized_reference"):
            raise ValueError(f"conflicting normalized pin identities for {key}")
        if not prior:
            grouped[key] = {"net": net, "normalized_reference": fact.get("normalized_reference"), "facts": []}
        grouped[key]["facts"].append(fact)

    applied = []
    for (page, ref, pin), fact_group in sorted(grouped.items()):
        matches = [r for r in entries if r.get("page") == page and r.get("ref_candidate") == ref
                   and str(r.get("pin_candidate") or "").strip() == pin]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one candidate for page={page} {ref}.{pin}; found {len(matches)}")
        row = matches[0]
        if row.get("mapping_decision") != "MAPPED" or not row.get("source_wire_primitive_ids"):
            raise ValueError(f"reviewed pin {ref}.{pin} lacks mapped source wire primitive evidence")
        if not row.get("candidate_net_name_diagnostic_only"):
            raise ValueError(f"reviewed pin {ref}.{pin} has no parser candidate membership to compare")
        if row.get("reference_status") not in (None, "UNKNOWN", ""):
            if row.get("reference_pin") != pin or row.get("reference_net") != fact_group["net"]:
                raise ValueError(f"refusing to overwrite prior reviewed fact for {ref}.{pin}")

        source_facts = fact_group["facts"]
        row["reference_pin"] = pin
        row["reference_net"] = fact_group["net"]
        row["reference_status"] = "VERIFIED"
        row["review_status"] = "INDEPENDENT_SOURCE_REVIEW_VERIFIED"
        row["reference_evidence"] = {
            "source": "schematic_reference_verification.json",
            "resolution_ids": sorted(str(f.get("resolution_id")) for f in source_facts),
            "source_page": page,
            "source_region": source_facts[0].get("source_region"),
            "symbol_identity": source_facts[0].get("symbol_identity"),
            "pin_identity": source_facts[0].get("pin_identity"),
            "normalized_reference": fact_group["normalized_reference"],
            "net_identity": fact_group["net"],
            "evidence_type": source_facts[0].get("evidence_type"),
            "confidence": source_facts[0].get("confidence"),
            "evidence_descriptions": sorted(set(str(f.get("evidence_description") or "") for f in source_facts)),
        }
        applied.append({
            "page": page, "ref": ref, "pin": pin,
            "reference_net": fact_group["net"],
            "candidate_net": row["candidate_net_name_diagnostic_only"],
            "resolution_ids": row["reference_evidence"]["resolution_ids"],
        })

    result = dict(ledger)
    result["entries"] = entries
    result["summary"] = dict(ledger.get("summary") or {})
    result["summary"].update({
        "independently_reviewed_facts_applied": len(applied),
        "reference_status": "PARTIAL_INDEPENDENT_SOURCE_REVIEW",
        "electrical_signoff": "BLOCKED_UNTIL_COMPLETE_REFERENCE_COVERAGE",
    })
    result["independent_reference_import"] = {
        "source": "QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/reference_holdout/schematic_reference_verification.json",
        "source_sha256": verification.get("source_sha256"),
        "verified_source_records": sum(len(v["facts"]) for v in grouped.values()),
        "unique_verified_pin_net_facts": len(applied),
        "applied_facts": applied,
        "policy": "Only exact HIGH-confidence VERIFIED ref/pin/net facts are imported; unresolved tokens are not guessed; all other candidates remain UNKNOWN.",
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--verification", required=True, type=Path)
    args = parser.parse_args()
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    verification = json.loads(args.verification.read_text(encoding="utf-8"))
    result = import_facts(ledger, verification)
    args.ledger.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "unique_verified_pin_net_facts": result["independent_reference_import"]["unique_verified_pin_net_facts"],
        "verified_source_records": result["independent_reference_import"]["verified_source_records"],
        "remaining_unknown": sum(r.get("reference_status") not in ("VERIFIED", "NC") for r in result["entries"]),
        "electrical_signoff": "BLOCKED_UNTIL_COMPLETE_REFERENCE_COVERAGE",
    }, indent=2))


if __name__ == "__main__":
    main()
