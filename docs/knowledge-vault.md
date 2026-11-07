# AIOS Knowledge Vault (isolated prototype)

This is an independent, standard-library-only Markdown knowledge module. It is
not an AIOS authority store and does not grant execution or commit authority.

## Data layout

- `raw/YYYY-MM-DD/<sha256>.txt`: exact ingested source text; never summarized.
- Matching `.json`: source URI, capture time, byte length and SHA-256.
- `notes/<id>.md`: one atomic Markdown note.
- Matching `.json`: status, source lineage, links and verification receipt.
- `daily/YYYY-MM-DD.md`: generated daily index.
- `inbox/gaps.md`: unanswered questions; no fabricated answers.
- `archive/`: reserved for non-destructive archival.

## Trust model

- New notes start as `OBSERVED`.
- `VERIFIED` cannot be assigned through note creation or a generic status setter.
  It requires `verify_note()`, an explicit verifier, an exact source excerpt and
  a matching source hash.
- The receipt proves that a named verifier inspected a source excerpt. It does
  **not** mathematically prove the note's semantic interpretation.
- `INFERRED`, `UNKNOWN` and `CONFLICT` remain explicit states.
- Source hash changes fail closed. Missing sources and dangling links appear in
  `audit()`.
- Answers are retrieval evidence with Markdown paths, not generated conclusions.
  No match creates a gap record.

## Minimal use

```python
from knowledge_vault import KnowledgeVault

vault = KnowledgeVault("./VAULT")
source = vault.ingest("Exact source text", "https://example.invalid/source")
note = vault.add_note(
    "power-rail",
    "Power rail",
    "The source labels the rail +3V3.",
    source["raw_path"],
)
vault.verify_note(
    "power-rail",
    verifier="reviewer-id",
    checked_excerpt="Exact source text",
)
print(vault.audit())
```

## Run tests

```sh
python -m unittest discover -s tests -v
```

## Explicit non-goals

No database, plugin, embedding service, autonomous semantic verification,
automatic promotion to authority, source deletion, or AI-generated answer.
Integration with AIOS Authority Store requires a separate contract and review.
