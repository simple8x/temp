import importlib.util
from pathlib import Path
import pytest

MODULE_PATH = Path(__file__).parents[1] / "pdf2kicad" / "reviewed_reference_import.py"
spec = importlib.util.spec_from_file_location("reviewed_reference_import", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SHA = "a" * 64


def fact(rid, page=6, ref="U5", pin="1", net="GNDREF"):
    return {"resolution_id": rid, "resolution_status": "VERIFIED", "source_page": page,
            "symbol_identity": f"{ref} / part", "pin_identity": f"Pin {pin}: label",
            "net_identity": net, "normalized_reference": f"{ref} pin {pin}",
            "confidence": "HIGH", "evidence_type": "VECTOR_GEOMETRY",
            "source_region": "region", "evidence_description": "reviewed on source"}


def ledger():
    return {"source_pdf_sha256": SHA, "entries": [
        {"page": 6, "ref_candidate": "U5", "pin_candidate": "1", "candidate_net_name_diagnostic_only": "GNDREF",
         "mapping_decision": "MAPPED", "source_wire_primitive_ids": ["p6:w1"], "reference_status": "UNKNOWN"},
        {"page": 6, "ref_candidate": "U5", "pin_candidate": "2", "candidate_net_name_diagnostic_only": "N2",
         "mapping_decision": "MAPPED", "source_wire_primitive_ids": ["p6:w2"], "reference_status": "UNKNOWN"},
    ]}


def test_imports_exact_verified_facts_and_deduplicates_alias_records():
    verification = {"source_sha256": SHA, "verified_count": 2,
                    "items": [fact("REF-1"), fact("REF-2")]}
    out = module.import_facts(ledger(), verification)
    assert out["independent_reference_import"]["verified_source_records"] == 2
    assert out["independent_reference_import"]["unique_verified_pin_net_facts"] == 1
    assert out["entries"][0]["reference_status"] == "VERIFIED"
    assert out["entries"][1]["reference_status"] == "UNKNOWN"


def test_hash_mismatch_fails_closed():
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        module.import_facts(ledger(), {"source_sha256": "b" * 64, "verified_count": 0, "items": []})


def test_ambiguous_candidate_fails_closed():
    l = ledger()
    l["entries"].append(dict(l["entries"][0]))
    with pytest.raises(ValueError, match="exactly one candidate"):
        module.import_facts(l, {"source_sha256": SHA, "verified_count": 1, "items": [fact("REF-1")]})


def test_conflicting_holdout_facts_fail_closed():
    with pytest.raises(ValueError, match="conflicting independently reviewed nets"):
        module.import_facts(ledger(), {"source_sha256": SHA, "verified_count": 2,
            "items": [fact("REF-1"), fact("REF-2", net="OTHER")]})


def test_missing_source_wire_mapping_fails_closed():
    l = ledger()
    l["entries"][0]["source_wire_primitive_ids"] = []
    with pytest.raises(ValueError, match="lacks mapped source wire"):
        module.import_facts(l, {"source_sha256": SHA, "verified_count": 1, "items": [fact("REF-1")]})
