"""Run the fixed, hand-labelled Knowledge Vault retrieval benchmark.

Usage: python benchmarks/run_benchmark.py
Metrics are retrieval-only; they do not measure semantic truth or prove correctness.
"""
from __future__ import annotations
from pathlib import Path
import json
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from knowledge_vault import KnowledgeVault

CASES = ROOT / "benchmarks" / "knowledge_vault_cases.json"

def build_vault(vault: KnowledgeVault, dataset: dict) -> None:
    sources = {}
    for item in dataset["sources"]:
        sources[item["id"]] = vault.ingest(
            item["text"], item["uri"], "2026-11-07T00:00:00+00:00"
        )
    for item in dataset["notes"]:
        vault.add_note(
            item["id"], item["title"], item["body"],
            sources[item["source"]]["raw_path"], item["status"]
        )

def evaluate(vault: KnowledgeVault, dataset: dict) -> dict:
    reciprocal_ranks, recalls_at_5, precisions_at_3, rows = [], [], [], []
    for case in dataset["queries"]:
        actual = [item.note_id for item in vault.answer_evidence(case["question"])]
        expected = case["expected"]
        rank = next((i + 1 for i, note_id in enumerate(actual) if note_id in expected), None)
        reciprocal_ranks.append(1 / rank if rank else 0.0)
        recalls_at_5.append(len(set(actual[:5]).intersection(expected)) / len(expected))
        top3 = actual[:3]
        precisions_at_3.append(
            len(set(top3).intersection(expected)) / len(top3) if top3 else 0.0
        )
        rows.append({
            "query_id": case["id"], "expected": expected, "returned": actual,
            "first_relevant_rank": rank, "hit": bool(rank)
        })
    negative_results = {}
    for question in dataset["negative_queries"]:
        negative_results[question] = [
            item.note_id for item in vault.answer_evidence(question)
        ]
    return {
        "dataset_id": dataset["dataset_id"],
        "query_count": len(rows), "note_count": len(dataset["notes"]),
        "source_count": len(dataset["sources"]),
        "recall_at_5": round(sum(recalls_at_5) / len(recalls_at_5), 4),
        "precision_at_3": round(sum(precisions_at_3) / len(precisions_at_3), 4),
        "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4),
        "negative_query_empty_rate": round(
            sum(not value for value in negative_results.values()) / len(negative_results), 4
        ),
        "negative_query_results": negative_results,
        "per_query": rows,
        "limitations": [
            "Small synthetic fixture, not a representative production corpus.",
            "Ground truth is manually authored and only covers these seven queries.",
            "Lexical retrieval metrics do not establish semantic correctness.",
            "No comparison to another agent/model is performed by this runner."
        ]
    }

def main() -> int:
    dataset = json.loads(CASES.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="knowledge-vault-benchmark-") as tmp:
        vault = KnowledgeVault(tmp)
        build_vault(vault, dataset)
        report = evaluate(vault, dataset)
        report["audit"] = vault.audit()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["audit"]["ok"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
