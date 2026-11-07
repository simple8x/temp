import json
import tempfile
import unittest
from knowledge_vault import KnowledgeVault
from benchmarks.run_benchmark import CASES, build_vault, evaluate

class KnowledgeVaultBenchmarkTests(unittest.TestCase):
    def test_fixed_dataset_is_reproducible_and_source_backed(self):
        dataset = json.loads(CASES.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            vault = KnowledgeVault(tmp)
            build_vault(vault, dataset)
            report = evaluate(vault, dataset)
            self.assertEqual(report["query_count"], 7)
            self.assertEqual(report["note_count"], 7)
            self.assertEqual(report["source_count"], 4)
            self.assertTrue(vault.audit()["ok"])
            self.assertEqual(len(report["per_query"]), 7)

    def test_report_exposes_negative_results_and_limitations(self):
        dataset = json.loads(CASES.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            vault = KnowledgeVault(tmp)
            build_vault(vault, dataset)
            report = evaluate(vault, dataset)
            self.assertEqual(len(report["negative_query_results"]), 2)
            self.assertIn("limitations", report)

if __name__ == "__main__":
    unittest.main()
