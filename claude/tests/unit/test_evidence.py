import pytest

from ecci.evidence import (EvidenceItem, EvidenceRegistry, ROLE_CANDIDATE, ROLE_CONFIRMED,
                           ROLE_UNKNOWN, decide_role, independent, max_independent_subset)


def reg_with(*items):
    r = EvidenceRegistry()
    for it in items:
        r.add(it)
    return r


def ev(i, lineage, src="VECTOR", derived=(), conn=False, kind="K"):
    return EvidenceItem(i, kind, src, frozenset(lineage), frozenset(derived), conn)


def test_empty_lineage_and_unknown_source_rejected():
    with pytest.raises(ValueError):
        ev("a", [])
    with pytest.raises(ValueError):
        ev("a", ["x"], src="MAGIC")


def test_same_data_different_method_is_not_independent():
    r = reg_with(ev("template", {"prim:7", "lib:k"}), ev("stub", {"prim:7"}))
    assert not independent("template", "stub", r)


def test_disjoint_lineage_is_independent_and_symmetric():
    r = reg_with(ev("a", {"prim:1"}), ev("b", {"prim:2"}))
    assert independent("a", "b", r) and independent("b", "a", r)
    assert not independent("a", "a", r)


def test_derived_evidence_inherits_ancestor_lineage():
    r = reg_with(ev("a", {"prim:1"}), ev("b", {"prim:2"}, derived=["a"]), ev("c", {"prim:1x"}))
    assert not independent("a", "b", r)                    # direct ancestry
    r2 = reg_with(ev("a", {"prim:1"}), ev("m", {"prim:9"}, derived=["a"]),
                  ev("z", {"prim:1"}))
    assert not independent("m", "z", r2)                   # shares inherited prim:1


def test_dangling_derivation_never_hides_a_dependency():
    r = reg_with(ev("a", {"prim:1"}, derived=["ghost"]), ev("b", {"prim:2"}, derived=["ghost"]))
    assert not independent("a", "b", r)


def test_derivation_cycle_terminates():
    r = reg_with(ev("a", {"p1"}, derived=["b"]), ev("b", {"p2"}, derived=["a"]))
    assert r.closure("a") == {"a", "b"}


def test_max_independent_subset_exact_and_greedy_lower_bound():
    r = reg_with(*[ev(f"e{i}", {f"p{i % 3}"}) for i in range(6)])
    assert len(max_independent_subset([f"e{i}" for i in range(6)], r)) == 3
    big = reg_with(*[ev(f"e{i}", {f"p{i % 4}"}) for i in range(40)])
    assert len(max_independent_subset([f"e{i}" for i in range(40)], big, exact_limit=8)) >= 1


def test_two_independent_vector_items_confirm_a_role():
    r = reg_with(ev("style", {"prim:1"}), ev("struct", {"prim:2"}))
    d = decide_role(["style", "struct"], r)
    assert d.status == ROLE_CONFIRMED and len(d.independent_items) == 2


def test_two_dependent_items_only_candidate():
    r = reg_with(ev("style", {"prim:1"}), ev("struct", {"prim:1"}))
    assert decide_role(["style", "struct"], r).status == ROLE_CANDIDATE


def test_circular_connectivity_evidence_cannot_confirm_a_wire():
    # "wire because it touches a pin" where the touch itself is a connectivity claim
    r = reg_with(ev("style", {"prim:1"}),
                 ev("touch", {"prim:2"}, conn=True))
    d = decide_role(["style", "touch"], r)
    assert d.status == ROLE_CANDIDATE and ("touch", "CIRCULAR_CONNECTIVITY_BASIS") in d.excluded


def test_transitive_connectivity_dependency_is_also_circular():
    r = reg_with(ev("claim", {"prim:3"}, conn=True), ev("derived", {"prim:4"}, derived=["claim"]),
                 ev("style", {"prim:1"}))
    d = decide_role(["derived", "style"], r)
    assert d.status == ROLE_CANDIDATE and ("derived", "CIRCULAR_CONNECTIVITY_BASIS") in d.excluded


@pytest.mark.parametrize("src", ["RASTER", "OCR"])
def test_weak_sources_never_confirm(src):
    r = reg_with(ev("a", {"p1"}, src=src), ev("b", {"p2"}, src=src), ev("c", {"p3"}, src=src))
    d = decide_role(["a", "b", "c"], r)
    assert d.status == ROLE_UNKNOWN and len(d.excluded) == 3


def test_unknown_evidence_id_is_excluded_not_trusted():
    r = reg_with(ev("a", {"p1"}))
    d = decide_role(["a", "nope"], r)
    assert d.status == ROLE_CANDIDATE and ("nope", "UNKNOWN_EVIDENCE_ID") in d.excluded


def test_duplicate_evidence_id_rejected():
    r = reg_with(ev("a", {"p1"}))
    with pytest.raises(ValueError):
        r.add(ev("a", {"p2"}))
