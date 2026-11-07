import json
import tempfile
import unittest
from pathlib import Path

from knowledge_vault import KnowledgeVault, NoteStatus, VaultError


class KnowledgeVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = KnowledgeVault(self.tmp.name)
        self.source = self.vault.ingest(
            "Power rail is named +3V3.\nConnector J1 pin 1 is VIN.",
            "fixture://schematic/source-1",
            "2026-11-07T06:00:00+00:00",
        )

    def test_ingest_preserves_exact_source_and_hash(self):
        raw = Path(self.tmp.name) / self.source["raw_path"]
        self.assertEqual(raw.read_text(encoding="utf-8"), "Power rail is named +3V3.\nConnector J1 pin 1 is VIN.")
        self.assertEqual(len(self.source["sha256"]), 64)
        self.assertEqual(json.loads(raw.with_suffix(".json").read_text())["source_uri"], "fixture://schematic/source-1")

    def test_note_requires_raw_source_and_explicit_verification(self):
        note = self.vault.add_note("rail", "Power rail", "The rail is +3V3.", self.source["raw_path"])
        self.assertEqual(note["status"], "OBSERVED")
        with self.assertRaises(VaultError):
            self.vault.add_note("bad", "Bad", "Claim", self.source["raw_path"], NoteStatus.VERIFIED)
        verified = self.vault.verify_note("rail", "reviewer-1", "Power rail is named +3V3.")
        self.assertEqual(verified["status"], "VERIFIED")
        self.assertEqual(verified["verification"]["verifier"], "reviewer-1")

    def test_verification_rejects_excerpt_not_in_source(self):
        self.vault.add_note("rail", "Power rail", "The rail is +3V3.", self.source["raw_path"])
        with self.assertRaises(VaultError):
            self.vault.verify_note("rail", "reviewer-1", "The source says 5V.")

    def test_changed_source_fails_closed(self):
        self.vault.add_note("rail", "Power rail", "The rail is +3V3.", self.source["raw_path"])
        raw = Path(self.tmp.name) / self.source["raw_path"]
        raw.write_text("tampered", encoding="utf-8")
        with self.assertRaises(VaultError):
            self.vault.verify_note("rail", "reviewer-1", "tampered")

    def test_linking_and_orphan_audit(self):
        self.vault.add_note("rail", "Power rail", "The rail is +3V3.", self.source["raw_path"])
        self.vault.add_note("connector", "Connector", "J1 pin 1 is VIN.", self.source["raw_path"], links=["rail"])
        report = self.vault.audit()
        self.assertTrue(report["ok"])
        self.assertEqual(report["orphans"], ["rail"])

    def test_missing_answer_is_recorded_as_gap(self):
        self.assertEqual(self.vault.answer_evidence("What is the battery voltage?"), [])
        gaps = Path(self.tmp.name) / "inbox" / "gaps.md"
        self.assertIn("battery voltage", gaps.read_text(encoding="utf-8"))

    def test_duplicate_note_and_bad_id_rejected(self):
        self.vault.add_note("rail", "Power rail", "The rail is +3V3.", self.source["raw_path"])
        with self.assertRaises(VaultError):
            self.vault.add_note("rail", "Duplicate", "Another idea.", self.source["raw_path"])
        with self.assertRaises(VaultError):
            self.vault.add_note("../escape", "Bad", "Body", self.source["raw_path"])


if __name__ == "__main__":
    unittest.main()
