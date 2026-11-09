import unittest

from validate_annotations import validate

SHA = "a" * 64


def base_document():
    return {
        "schema": "qi9-ground-truth-annotation/v1",
        "source_pdf_sha256": SHA,
        "page": 6,
        "coordinate_system": "pdf_points_origin_top_left",
        "annotations": [
            {
                "annotation_id": "symbol-u5",
                "kind": "SYMBOL_BOUNDARY",
                "status": "VERIFIED",
                "subject": {"type": "symbol", "id": "path-4419", "reference": "U5"},
                "evidence": [{"source_type": "PDF_VECTOR", "source_id": "path-4419", "page": 6}],
                "reviewers": [
                    {"reviewer_id": "reviewer-a", "decision": "CONFIRM"},
                    {"reviewer_id": "reviewer-b", "decision": "CONFIRM"}
                ]
            },
            {
                "annotation_id": "symbol-u6",
                "kind": "SYMBOL_BOUNDARY",
                "status": "UNKNOWN",
                "subject": {"type": "symbol", "id": "path-5355", "reference": "U6"},
                "evidence": [{"source_type": "PDF_VECTOR", "source_id": "path-5355", "page": 6}],
                "reviewers": []
            }
        ]
    }


class AnnotationValidationTests(unittest.TestCase):
    def test_two_reviewers_and_evidence_are_accepted(self):
        self.assertEqual(validate(base_document(), SHA), [])

    def test_single_reviewer_cannot_promote_verified(self):
        doc = base_document()
        doc["annotations"][0]["reviewers"].pop()
        self.assertTrue(any("two distinct confirming reviewers" in e for e in validate(doc)))

    def test_verified_without_evidence_is_blocked(self):
        doc = base_document()
        doc["annotations"][0]["evidence"] = []
        self.assertTrue(any("requires source evidence" in e for e in validate(doc)))

    def test_source_hash_mismatch_is_blocked(self):
        self.assertTrue(any("does not match" in e for e in validate(base_document(), "b" * 64)))

    def test_connectivity_requires_explicit_endpoints(self):
        doc = base_document()
        doc["annotations"][0]["kind"] = "CONNECTIVITY"
        doc["annotations"][0]["status"] = "VERIFIED_CONNECTED"
        doc["annotations"][0]["assertion"] = {
            "from_annotation_id": "symbol-u5",
            "to_annotation_id": "missing",
            "predicate": "CONNECTED_TO"
        }
        errors = validate(doc)
        self.assertTrue(any("unknown annotation" in e for e in errors))

    def test_unknown_is_not_promoted_or_rejected(self):
        doc = base_document()
        doc["annotations"][1]["status"] = "UNKNOWN"
        self.assertEqual(validate(doc), [])

    def test_mixed_reviewer_decisions_cannot_be_verified(self):
        doc = base_document()
        doc["annotations"][0]["reviewers"][1]["decision"] = "DISAGREE"
        self.assertTrue(any("unanimous CONFIRM" in e for e in validate(doc)))

    def test_duplicate_reviewer_id_is_blocked(self):
        doc = base_document()
        doc["annotations"][0]["reviewers"][1]["reviewer_id"] = "reviewer-a"
        self.assertTrue(any("reviewer IDs must be unique" in e for e in validate(doc)))

    def test_non_finite_geometry_is_blocked(self):
        doc = base_document()
        doc["annotations"][0]["geometry"] = {"point": [float("nan"), 2]}
        self.assertTrue(any("finite numbers" in e for e in validate(doc)))


if __name__ == "__main__":
    unittest.main()
