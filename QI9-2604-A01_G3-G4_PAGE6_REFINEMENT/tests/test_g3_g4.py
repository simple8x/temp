import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
EVIDENCE = ROOT.parent / "g3_g4"
PAGE6 = ROOT.parent / "g3_g4_refinement" / "page6_wire_classification_candidates.json"


class G34(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g3 = json.loads((EVIDENCE / "g3_symbol_pin_candidates.json").read_text())
        cls.summary = json.loads((EVIDENCE / "G3_G4_RESULT.json").read_text())
        cls.page6 = json.loads(PAGE6.read_text())

    def test_reference_candidates_have_source_provenance(self):
        self.assertGreater(self.g3["candidate_reference_count"], 0)
        self.assertTrue(all(
            item["source_page"] in range(5, 12) and item["bbox_px"]
            for item in self.g3["symbol_candidates"]
        ))

    def test_unresolved_symbols_do_not_gain_pin_anchors(self):
        unresolved = [
            item for item in self.g3["symbol_candidates"]
            if item["status"] == "CANDIDATE_REQUIRES_VISUAL_CONFIRMATION"
        ]
        self.assertTrue(unresolved)
        self.assertTrue(all(item["pin_anchors"] == [] for item in unresolved))

    def test_reviewed_pin_net_facts_are_deduplicated(self):
        facts = self.g3["reviewed_pin_net_facts"]
        keys = [(item["page"], item["symbol"], item["pin"], item["net"]) for item in facts]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(facts), self.summary["reviewed_pin_net_facts"])

    def test_g4_summary_remains_blocked_without_net_memberships(self):
        self.assertEqual(self.summary["G4"], "PARTIAL_GRAPH_EVIDENCE_READY_BLOCKED")
        self.assertEqual(self.summary["net_membership_claims"], 0)
        self.assertFalse(self.summary["kicad_netlist_generated"])

    def test_page6_classifier_never_promotes_candidates_to_electrical_facts(self):
        summary = self.page6["summary"]
        self.assertGreater(summary["wire_like_stroke_candidates"], 0)
        self.assertEqual(summary["verified_wire_segments"], 0)
        self.assertEqual(summary["verified_junctions"], 0)
        self.assertEqual(summary["verified_net_memberships"], 0)
        self.assertEqual(
            summary["status"],
            "CANDIDATE_CLASSIFICATION_ONLY_BLOCKED_FOR_ELECTRICAL_CONNECTIVITY",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
