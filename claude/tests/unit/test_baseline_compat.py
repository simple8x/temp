"""F1: the BASELINE pipeline output is unchanged versus the parent commit 873bfbb on the same fixtures.

The golden file was produced by tools/baseline_compat.py run against a worktree of 873bfbb (normalized JSON of
classification, ClusterResults incl. Resolution, conn graph and solve result; interpreter- and hash-seed-
independent). Any difference fails: a deliberate baseline change needs a new, explained and approved golden."""
from __future__ import annotations

import dataclasses
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLD = json.loads((ROOT / "tests" / "fixtures" / "baseline_compat_873bfbb.json").read_text())
sys.path.insert(0, str(ROOT / "tools"))
import baseline_compat  # noqa: E402


def test_baseline_outputs_identical_to_parent_873bfbb():
    got = baseline_compat.fingerprint()["pages"]
    assert sorted(got) == sorted(GOLD["pages"]) and len(got) >= 20
    for name, g in GOLD["pages"].items():
        assert got[name]["resolutions"] == g["resolutions"], name
        assert got[name]["resolution_fields"] == g["resolution_fields"], name
        assert got[name]["norm_sha256"] == g["norm_sha256"], name


def test_legacy_resolution_contract_unchanged():
    from ecci import template as tp
    assert [f.name for f in dataclasses.fields(tp.Resolution)] == ["hits", "status", "id_state", "pins", "symbols",
                                                                   "note"]
    assert list(inspect.signature(tp.resolve).parameters) == ["cl", "hits", "params"]


def test_qualified_resolution_never_upgrades_the_legacy_verdict():
    from ecci import template as tp
    from ecci.ingest_vector import ingest_pdf
    from ecci.rules import analyze_page
    from pdfgen import one_page
    downgraded = set()
    for name, (content, syms) in sorted(baseline_compat.scenes().items()):
        pi = ingest_pdf(one_page(content, size=(300, 200))).pages[0]
        _cls, clusters, _b, _r = analyze_page(pi, syms)
        for cr in clusters:
            hits = list(cr.resolution.hits)
            q = tp.resolve_qualified(cr.cluster, hits)
            legacy = tp.resolve(cr.cluster, sorted(hits, key=tp.hit_sort_key))
            assert q.resolution == legacy and q.status == legacy.status, name
            if q.id_state != legacy.id_state:     # the ONLY allowed difference: a downgrade with >1 identity
                assert (legacy.id_state, q.id_state) == ("PIN_EQUIVALENT_CANONICAL", "AMBIGUOUS"), name
                assert len(q.identities) > 1, name
                downgraded.add(name)
    assert downgraded == {"same_name_two_libs_R"}        # the legacy name-only shortcut, refused when qualified
