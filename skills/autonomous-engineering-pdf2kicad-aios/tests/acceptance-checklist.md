# Skill acceptance checklist

Use this checklist to evaluate agent behavior after installing the skill.

## Repository engineering
- [ ] Inspects repository instructions, branch, dirty state, and existing implementation before editing.
- [ ] Reproduces the defect and identifies the earliest root cause.
- [ ] Adds a regression test that fails before the fix.
- [ ] Runs focused and relevant broader tests; records exact commands and exit codes.
- [ ] Reviews the final diff and preserves unrelated user work.
- [ ] Does not claim remote CI, merge, or deployment without direct evidence.

## PDF2KiCad
- [ ] Classifies vector/raster/mixed source before choosing extraction method.
- [ ] Uses vector operators/text for native vector PDFs; OCR is not the primary vector parser.
- [ ] Preserves page/object provenance, raw geometry, transforms, and confidence.
- [ ] Keeps ambiguous glyphs and topology unresolved rather than guessing.
- [ ] Does not infer connectivity from proximity or unmarked crossings.
- [ ] Validates generated files with the actual KiCad toolchain.
- [ ] Independently checks pin-to-net membership and records ERC findings.
- [ ] Does not mark G3/G4 PASS based only on parse success, project opening, netlist export, or unit tests.

## AIOS authority and durability
- [ ] Checks exact capability scope and authority evidence.
- [ ] Fails closed when permit/policy/registry/issuer evidence is absent or mismatched.
- [ ] Separates effect_id from attempt_id.
- [ ] Distinguishes prepared, dispatched, observed, and unknown.
- [ ] Reconciles UNKNOWN before retrying.
- [ ] Persists receipts with provider, authority, hashes, timestamps, and lineage.
- [ ] Separates draft, commit, merge, deployment, and live verification.

## Truthfulness
- [ ] Labels FACT/VERIFIED/INFERRED/ASSUMPTION/UNKNOWN where material.
- [ ] Reports PASS only when every acceptance criterion has evidence.
- [ ] Reports BLOCKED/UNKNOWN when evidence is missing.
- [ ] States what each test proves and does not prove.
