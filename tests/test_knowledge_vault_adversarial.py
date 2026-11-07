import tempfile
import unittest
from pathlib import Path

from knowledge_vault import KnowledgeVault, NoteStatus, VaultError


class KnowledgeVaultAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = KnowledgeVault(self.tmp.name)

    def add(self, note_id, title, body, source_text=None, status=NoteStatus.OBSERVED):
        source_text = source_text or body
        source = self.vault.ingest(
            source_text, f"fixture://adversarial/{note_id}",
            "2026-11-07T00:00:00+00:00"
        )
        return self.vault.add_note(
            note_id, title, body, source["raw_path"], status=status
        ), source

    def test_substring_collision_data_does_not_match_database(self):
        self.add("db-note", "Database index", "Database stores records.")
        self.assertEqual(self.vault.answer_evidence("data"), [])

    def test_paraphrase_without_shared_terms_returns_gap_not_guess(self):
        self.add("power-note", "Supply rail", "The board receives power from VIN.")
        self.assertEqual(self.vault.answer_evidence("Where does energy enter?"), [])
        gap = Path(self.tmp.name) / "inbox" / "gaps.md"
        self.assertIn("Where does energy enter?", gap.read_text(encoding="utf-8"))

    def test_conflicting_candidates_are_both_exposed(self):
        self.add("reset-low", "RESET_N polarity", "RESET_N is active low.")
        self.add("reset-high", "RESET_N polarity", "RESET_N is active high.")
        found = self.vault.answer_evidence("What is RESET_N polarity?")
        self.assertEqual({item.note_id for item in found}, {"reset-low", "reset-high"})

    def test_unknown_state_is_never_promoted_by_retrieval(self):
        self.add("unknown-reset", "RESET_N polarity", "RESET_N polarity is not established.")
        self.vault.set_status("unknown-reset", NoteStatus.UNKNOWN, "No primary source confirms polarity")
        found = self.vault.answer_evidence("What is RESET_N polarity?")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].status, "UNKNOWN")

    def test_tampered_source_blocks_retrieval_even_if_note_exists(self):
        _, source = self.add("rail", "Power rail", "Power rail is +3V3.")
        raw = Path(self.tmp.name) / source["raw_path"]
        raw.write_text("Power rail is 5V.", encoding="utf-8")
        with self.assertRaisesRegex(VaultError, "source hash mismatch"):
            self.vault.answer_evidence("What is power rail?")

    def test_missing_source_blocks_retrieval(self):
        _, source = self.add("rail", "Power rail", "Power rail is +3V3.")
        (Path(self.tmp.name) / source["raw_path"]).unlink()
        with self.assertRaisesRegex(VaultError, "source missing"):
            self.vault.answer_evidence("What is power rail?")

    def test_verified_note_downgrade_keeps_current_status_explicit(self):
        _, source = self.add(
            "rail", "Power rail", "Power rail is +3V3.",
            "Power rail is named +3V3."
        )
        verified = self.vault.verify_note("rail", "reviewer", "Power rail is named +3V3.")
        self.assertEqual(verified["status"], "VERIFIED")
        downgraded = self.vault.set_status("rail", NoteStatus.CONFLICT, "Second source disagrees")
        self.assertEqual(downgraded["status"], "CONFLICT")
        found = self.vault.answer_evidence("What is power rail?")
        self.assertEqual(found[0].status, "CONFLICT")
        self.assertEqual(downgraded["verification"]["verifier"], "reviewer")

if __name__ == "__main__":
    unittest.main()
