"""M4.1 writer: status preservation, provenance, round trip, determinism, fail-closed reading, F2 arm records."""

import dataclasses
import itertools
import json
import random

import pytest

from ecci.constraints import Constraints
from ecci.rules import ArmRecord, audit_site_arms
from ecci.solver import ConnGraph, Obj, Option, Site, audit_result, fourway_site, solve
from ecci.status import CONFLICT, INFERRED, UNKNOWN, VERIFIED, Edge, RoleRef
from ecci.writer import (DocumentError, WriterError, _link_provenance, build_document, canonical, content_hash,
                         dumps, loads, pair_status, reserialize, validate)

from test_rules import TIP_DN, TIP_UP, disc, four_ends, line, r_content, run, wid


def role(o, kind="WIRE", conf=True):
    return RoleRef(o, kind, "CONFIRMED" if conf else "CANDIDATE", (f"A:{o}", f"B:{o}") if conf else (f"A:{o}",))


def edge(eid, a, b, rule="TR1", conf=True, prov=("p0/page#1",), negative=(), n=2):
    return Edge(eid, a, b, rule, (role(a, conf=conf), role(b, conf=conf)), "node:1@1.000,2.000", n, negative, prov)


def W(*ids):
    return [Obj(i, "WIRE") for i in ids]


def doc_of(G):
    res = solve(G)
    return G, res, build_document(G, res)


def edge_rec(doc, eid):
    return next(e for e in doc["edges"] if e["id"] == eid)


def tamper(doc, f, rehash=True):
    """Apply f to a deep copy; optionally recompute the content hash so only the semantic checks can catch it."""
    d = json.loads(canonical(doc))
    f(d)
    if rehash:
        d["content_sha256"] = content_hash(d)
    return canonical(d)


def all_pairs_equal(G, res, doc):
    ids = sorted(o.id for o in G.objects)
    for p, q in itertools.combinations(ids, 2):
        assert pair_status(doc, p, q) == res.pair_status(p, q), (p, q)


# ------------------------------------------------------------------------- 1-4 status preservation
def test_verified_record_serialized_and_read_back_as_verified():
    G, res, doc = doc_of(ConnGraph(W("a", "b"), [edge("e", "a", "b")]))
    back = loads(canonical(doc))
    r = edge_rec(back, "e")
    assert (r["status"], r["classified"], r["provenance"]["refs"]) == ("VERIFIED", ["VERIFIED", "PREMISES_COMPLETE"],
                                                                        ["p0/page#1"])
    assert r["roles"][0]["evidence"] == ["A:a", "B:a"] and r["origin"] == "rules.build_conn_graph"
    assert pair_status(back, "a", "b") == VERIFIED == res.pair_status("a", "b")
    # the evidence ids are kept and, with no registry, listed as unresolved, not invented
    assert back["unresolved_references"]["evidence"] == ["A:a", "A:b", "B:a", "B:b"]
    assert r["provenance"]["complete"] is False


def test_inferred_record_stays_inferred_and_cannot_be_promoted():
    G, res, doc = doc_of(ConnGraph(W("a", "b"), [edge("e", "a", "b", conf=False)]))
    assert edge_rec(loads(canonical(doc)), "e")["status"] == INFERRED
    assert pair_status(doc, "a", "b") == INFERRED
    def up(d):
        r = edge_rec(d, "e")
        r["status"], r["reason"] = "VERIFIED", "PREMISES_COMPLETE"
    with pytest.raises(DocumentError, match="promotion or tampering"):
        loads(tamper(doc, up))
    def up2(d):                                      # forge the premise record too: the classifier disagrees
        edge_rec(d, "e")["classified"] = ["VERIFIED", "PREMISES_COMPLETE"]
        up(d)
    with pytest.raises(DocumentError, match="premises give"):
        loads(tamper(doc, up2))
    def merge(d):                                    # claim the pair VERIFIED at the connectivity layer only
        for r in d["connectivity"]:
            r["verified_group"] = "a"
    with pytest.raises(DocumentError, match="verified group does not follow"):
        loads(tamper(doc, merge))


def test_unknown_record_stays_unknown():
    G, res, doc = doc_of(ConnGraph(W("a", "b"), [edge("e", "a", "b", negative=("neg:1",))]))
    r = edge_rec(loads(canonical(doc)), "e")
    assert (r["status"], r["reason"]) == ("UNKNOWN", "NEGATIVE_EVIDENCE") and r["negative"] == ["neg:1"]
    assert pair_status(doc, "a", "b") == UNKNOWN
    assert {n["closure"] for n in doc["nets"]} == {"OPEN"}
    def up(d):
        r = edge_rec(d, "e")
        r["status"], r["reason"] = "VERIFIED", "PREMISES_COMPLETE"
    with pytest.raises(DocumentError):
        loads(tamper(doc, up))


def test_conflict_record_stays_conflict():
    G = ConnGraph(W("a", "b"), [edge("e", "a", "b")], constraints=Constraints(cannot_link=(("a", "b", "C4"),)))
    G, res, doc = doc_of(G)
    r = edge_rec(loads(canonical(doc)), "e")
    assert (r["status"], r["reason"], r["classified"][0]) == ("CONFLICT", "QUARANTINED", "VERIFIED")
    assert pair_status(doc, "a", "b") == CONFLICT == res.pair_status("a", "b")
    assert doc["quarantined"] and doc["nets"][0]["status"] == "CONFLICT"
    def up(d):
        r = edge_rec(d, "e")
        r["status"], r["reason"] = "VERIFIED", "PREMISES_COMPLETE"
    with pytest.raises(DocumentError, match="promotion or tampering"):
        loads(tamper(doc, up))
    with pytest.raises(DocumentError):                # dropping the quarantine record is caught too
        loads(tamper(doc, lambda d: d.__setitem__("quarantined", [])))


# ------------------------------------------------------------------------- 5 missing provenance
def test_missing_provenance_is_marked_and_never_promotes():
    G, res, doc = doc_of(ConnGraph(W("a", "b"), [edge("e", "a", "b", prov=())]))
    r = edge_rec(doc, "e")
    assert (r["status"], r["reason"]) == ("INFERRED", "MISSING_PROVENANCE")
    assert r["provenance"]["complete"] is False and "NO_PROV_RECORD" in r["provenance"]["missing"]
    loads(canonical(doc))
    def forge(d):                                    # claim VERIFIED while the provenance is still missing
        x = edge_rec(d, "e")
        x["status"], x["reason"], x["classified"] = "VERIFIED", "PREMISES_COMPLETE", ["VERIFIED", "PREMISES_COMPLETE"]
    with pytest.raises(DocumentError):
        loads(tamper(doc, forge))
    def hide(d):                                     # claim the missing provenance is complete
        edge_rec(d, "e")["provenance"] = {"complete": True, "missing": [], "refs": []}
    with pytest.raises(DocumentError, match="provenance block"):
        loads(tamper(doc, hide))
    # dropping the prov of a VERIFIED edge drops it to what classify gives, so the file is rejected
    G2, res2, doc2 = doc_of(ConnGraph(W("a", "b"), [edge("e", "a", "b")]))
    def strip(d):
        x = edge_rec(d, "e")
        x["prov"] = []
        x["provenance"] = {"complete": False, "missing": ["NO_PROV_RECORD", "UNRESOLVED_EVIDENCE:A:a",
                                                          "UNRESOLVED_EVIDENCE:A:b", "UNRESOLVED_EVIDENCE:B:a",
                                                          "UNRESOLVED_EVIDENCE:B:b"], "refs": []}
    with pytest.raises(DocumentError, match="premises give"):
        loads(tamper(doc2, strip))


# ------------------------------------------------------------------------- 6-7 references and ids
def test_dangling_references_are_rejected():
    G, res, doc = doc_of(ConnGraph(W("a", "b"), [edge("e", "a", "b")]))
    with pytest.raises(DocumentError, match="dangling endpoint"):
        loads(tamper(doc, lambda d: edge_rec(d, "e").__setitem__("b", "zz")))
    def unlisted(d):                                 # an evidence id that is neither resolved nor listed
        d["unresolved_references"]["evidence"].remove("A:a")
    with pytest.raises(DocumentError, match="dangling evidence"):
        loads(tamper(doc, unlisted))
    def ghost(d):
        d["unresolved_references"]["provenance"].remove("p0/page#1")
    with pytest.raises(DocumentError, match="dangling provenance"):
        loads(tamper(doc, ghost))


def test_duplicate_ids_are_rejected():
    G, res, doc = doc_of(ConnGraph(W("a", "b", "c"), [edge("e", "a", "b"), edge("f", "b", "c")]))
    def dup_obj(d):
        d["objects"][1]["id"] = d["objects"][0]["id"]
    with pytest.raises(DocumentError):
        loads(tamper(doc, dup_obj))
    def dup_edge(d):
        d["edges"][1]["id"] = d["edges"][0]["id"]
    with pytest.raises(DocumentError):
        loads(tamper(doc, dup_edge))
    text = canonical(doc)
    with pytest.raises(DocumentError, match="duplicate JSON key"):
        loads(text.replace('{"attachments":', '{"format":"x","attachments":', 1))


# ------------------------------------------------------------------------- 8-9 determinism, round trip
def test_output_is_deterministic_and_independent_of_input_order():
    objs = W("a", "b", "c", "d")
    es = [edge("e1", "a", "b"), edge("e2", "b", "c", conf=False), edge("e3", "c", "d", negative=("n",))]
    ref = dumps(ConnGraph(objs, es), solve(ConnGraph(objs, es)))
    rng = random.Random(7)
    for _ in range(5):
        o2, e2 = objs[:], es[:]
        rng.shuffle(o2)
        rng.shuffle(e2)
        G = ConnGraph(o2, e2)
        assert dumps(G, solve(G)) == ref
    assert '"timestamp"' not in ref and ref.endswith("\n")


def test_write_read_write_is_identical_and_semantically_equal():
    pi, cls, cl, built, res = run(r_content() + line(*TIP_UP, 150, 160) + line(*TIP_DN, 150, 40))
    t1 = dumps(built.graph, res, built=built, page=pi, clusters=cl, registry=cls.registry)
    d = loads(t1)
    assert reserialize(d) == t1
    assert d["solver_digest"] == res.digest()
    assert {e["id"]: (e["status"], e["reason"]) for e in d["edges"]} == \
        {k: tuple(v) for k, v in res.edge_status.items()}
    all_pairs_equal(built.graph, res, d)
    with pytest.raises(DocumentError, match="canonical"):
        loads(json.dumps(d, indent=1))


# ------------------------------------------------------------------------- 10 multi-arm sites, F2
def test_two_wire_crossing_keeps_both_arms_per_wire_with_distinct_geometry():
    pi, cls, cl, built, res = run(line(100, 100, 160, 100) + line(130, 70, 130, 130))
    a, b = wid(pi, 100, 100), wid(pi, 130, 70)
    (site,) = built.graph.sites
    recs = built.site_arms[site.id]
    assert [r.obj for r in recs].count(a) == 2 and [r.obj for r in recs].count(b) == 2
    assert len({r.graph_edge for r in recs}) == 4 and [r.label for r in recs] == [r.direction for r in recs]
    assert audit_site_arms(built.graph, built.site_arms, built.obj_prims) == []
    d = loads(dumps(built.graph, res, built=built, page=pi, clusters=cl, registry=cls.registry))
    s = d["sites"][0]
    assert s["result"]["status"] == "UNKNOWN" and all(arm["geometry"]["prov"] for arm in s["arms"])
    assert d["site_links"] == [] and pair_status(d, a, b) == UNKNOWN
    assert {tuple(map(tuple, o["links"])) for o in s["options"]} == {(), ((min(a, b), max(a, b)),)}
    def swap(d):                                     # contradictory direction for one arm
        g = d["sites"][0]["arms"][0]["geometry"]
        g["vector"] = [-g["vector"][0], -g["vector"][1]]
    with pytest.raises(DocumentError, match="direction"):
        loads(tamper(d, swap))
    def dup(d):                                      # one graph edge claimed by two arms
        arms = d["sites"][0]["arms"]
        arms[1]["geometry"]["graph_edge"] = arms[0]["geometry"]["graph_edge"]
    with pytest.raises(DocumentError, match="duplicate arm evidence"):
        loads(tamper(d, dup))
    def foreign(d):                                  # arm names an object that is not its edge's primitive
        d["sites"][0]["arms"][0]["geometry"]["chosen_prim"] = 999
    with pytest.raises(DocumentError, match="not one of its edge"):
        loads(tamper(d, foreign))


def test_audit_site_arms_detects_missing_contradictory_and_unrelated_arms():
    pi, cls, cl, built, res = run(four_ends())
    (site,) = built.graph.sites
    recs = built.site_arms[site.id]
    assert audit_site_arms(built.graph, built.site_arms, built.obj_prims) == []
    assert any("missing arm provenance" in p for p in audit_site_arms(built.graph, {}, built.obj_prims))
    bad = dataclasses.replace(recs[0], direction="N" if recs[0].direction != "N" else "S")
    assert audit_site_arms(built.graph, {site.id: (bad,) + recs[1:]}, built.obj_prims)
    dup = dataclasses.replace(recs[1], graph_edge=recs[0].graph_edge)
    assert any("duplicate" in p for p in audit_site_arms(built.graph, {site.id: (recs[0], dup) + recs[2:]}))
    alien = dataclasses.replace(recs[0], chosen_prim=999, prim_ids=(999,), obj="w:999")
    assert audit_site_arms(built.graph, {site.id: (alien,) + recs[1:]}, built.obj_prims)
    noprov = dataclasses.replace(recs[0], prov=())
    assert any("missing arm provenance" in p
               for p in audit_site_arms(built.graph, {site.id: (noprov,) + recs[1:]}, built.obj_prims))


def test_link_provenance_requires_geometry_for_every_arm_of_each_endpoint():
    site = {"arms": [
        {"label": "E", "obj": "w:3", "geometry": {"prov": ["pdf#3"]}},
        {"label": "W", "obj": "w:3", "geometry": None},
        {"label": "N", "obj": "w:7", "geometry": {"prov": ["pdf#7"]}},
    ]}
    result = _link_provenance(site, "w:3", "w:7", {"pdf#3", "pdf#7"})
    assert result == {
        "complete": False,
        "missing": ["MISSING_ARM_GEOMETRY:W:w:3"],
        "refs": ["pdf#3", "pdf#7"],
    }


def test_writer_rejects_duplicate_or_missing_arm_records_before_serialization():
    pi, cls, cl, built, res = run(four_ends())
    (site,) = built.graph.sites
    recs = built.site_arms[site.id]

    duplicate = dict(built.site_arms)
    duplicate[site.id] = recs + (recs[0],)
    bad_duplicate = dataclasses.replace(built, site_arms=duplicate)
    with pytest.raises(WriterError, match="duplicate"):
        build_document(built.graph, res, built=bad_duplicate, page=pi, clusters=cl, registry=cls.registry)

    missing = dict(built.site_arms)
    missing[site.id] = recs[:-1]
    bad_missing = dataclasses.replace(built, site_arms=missing)
    with pytest.raises(WriterError, match="site arm audit failed"):
        build_document(built.graph, res, built=bad_missing, page=pi, clusters=cl, registry=cls.registry)


def test_reader_rejects_site_link_claiming_complete_with_one_missing_arm():
    pi, cls, cl, built, res = run(line(100, 100, 160, 100) + line(130, 70, 130, 130))
    (site,) = built.graph.sites
    only_all4 = dataclasses.replace(site, options=tuple(op for op in site.options if op.name == "ALL4"))
    G = dataclasses.replace(built.graph, sites=[only_all4])
    res = solve(G)
    doc = build_document(G, res, built=built, page=pi, clusters=cl, registry=cls.registry)
    link = doc["site_links"][0]
    target_obj = link["a"]
    def remove_one_arm_geometry(d):
        arms = [arm for arm in d["sites"][0]["arms"] if arm["obj"] == target_obj]
        assert len(arms) >= 2
        arms[0]["geometry"] = None
    with pytest.raises(DocumentError, match="provenance block"):
        loads(tamper(doc, remove_one_arm_geometry))


def test_reader_recomputes_site_resolution_and_rejects_forged_assumptions():
    pi, cls, cl, built, res = run(line(100, 100, 160, 100) + line(130, 70, 130, 130))
    doc = build_document(built.graph, res, built=built, page=pi, clusters=cl, registry=cls.registry)
    sid = doc["sites"][0]["id"]

    def forge_result(d):
        d["sites"][0]["result"] = {"status": "INFERRED", "option": "ALL4", "reason": "FIXED_ASSUMPTION"}
    with pytest.raises(DocumentError, match="result does not follow from solver premises"):
        loads(tamper(doc, forge_result))

    def forge_fixed(d):
        d["sites"][0]["fixed"] = "ALL4"
        d["sites"][0]["result"] = {"status": "INFERRED", "option": "ALL4", "reason": "FIXED_ASSUMPTION"}
    with pytest.raises(DocumentError, match="fixed assumption"):
        loads(tamper(doc, forge_fixed))

    def forge_link(d):
        site = d["sites"][0]
        a, b = sorted({arm["obj"] for arm in site["arms"]})
        prov = _link_provenance(site, a, b, {p["content_ref"] for p in d["primitives"]})
        d["site_links"].append({"id": f"site:{sid}:{a}|{b}", "site": sid, "option": "ALL4",
                                "a": a, "b": b, "status": "INFERRED",
                                "origin": "solver.site_resolution", "provenance": prov})
        d["site_links"].sort(key=lambda x: x["id"])
    with pytest.raises(DocumentError, match="site_links are not exactly"):
        loads(tamper(doc, forge_link))

    fixed = dataclasses.replace(built.graph.sites[0], fixed="ALL4")
    fixed_graph = dataclasses.replace(built.graph, sites=[fixed])
    with pytest.raises(WriterError, match="fixed site assumptions"):
        build_document(fixed_graph, solve(fixed_graph), built=built, page=pi,
                       clusters=cl, registry=cls.registry)


def test_solver_digest_is_documented_as_opaque_and_content_hash_bound():
    G, res, doc = doc_of(ConnGraph(W("a", "b"), [edge("e", "a", "b")]))
    changed = tamper(doc, lambda d: d.__setitem__("solver_digest", "not-the-original"), rehash=False)
    with pytest.raises(DocumentError, match="content_sha256"):
        loads(changed)
    # The reader has no solver inputs with which to recompute this opaque digest. Rehashing
    # therefore remains possible; callers must not treat solver_digest as a verified signature.
    accepted = tamper(doc, lambda d: d.__setitem__("solver_digest", "not-the-original"), rehash=True)
    assert loads(accepted)["solver_digest"] == "not-the-original"


def test_resolved_site_links_are_inferred_with_arm_provenance():
    pi, cls, cl, built, res = run(line(100, 100, 160, 100) + line(130, 70, 130, 130))
    (site,) = built.graph.sites
    only_all4 = dataclasses.replace(site, options=tuple(op for op in site.options if op.name == "ALL4"))
    G = dataclasses.replace(built.graph, sites=[only_all4])
    res = solve(G)
    d = loads(dumps(G, res, built=built, page=pi, clusters=cl, registry=cls.registry))
    (lk,) = d["site_links"]
    assert (lk["status"], lk["option"], lk["provenance"]["complete"]) == ("INFERRED", "ALL4", True)
    a, b = lk["a"], lk["b"]
    assert pair_status(d, a, b) == INFERRED == res.pair_status(a, b)
    with pytest.raises(DocumentError, match="must be INFERRED"):
        loads(tamper(d, lambda x: x["site_links"][0].__setitem__("status", "VERIFIED")))
    with pytest.raises(DocumentError):                # a link nobody committed
        loads(tamper(d, lambda x: x.__setitem__("site_links", [])))
    # without arm geometry the link is kept, INFERRED, and marked incomplete
    G2 = ConnGraph(W("a", "b"), [], [
        Site("site:1", "MULTI", (("N", "a"), ("S", "b")),
             (Option("PAIR", frozenset({("a", "b")})),))
    ])
    d2 = build_document(G2, solve(G2))
    (lk2,) = d2["site_links"]
    assert lk2["status"] == "INFERRED" and lk2["provenance"]["complete"] is False
    assert d2["unresolved_references"]["sites_without_arm_geometry"] == ["site:1"]


# ------------------------------------------------------------------------- 11 nothing silently dropped
def test_unsupported_quarantined_and_structural_records_are_kept():
    G = ConnGraph([Obj("w", "WIRE"), Obj("u", "UNRESOLVED", attach="w"), Obj("x", "WIRE"), Obj("y", "WIRE")],
                  [edge("e", "x", "y")], constraints=Constraints(cannot_link=(("x", "y", "C4"),)))
    res = solve(G)
    d = loads(dumps(G, res))
    assert d["attachments"] == [{"id": "attach:u", "object": "u", "attach": "w", "electrical": False,
                                 "origin": "rules.build_conn_graph:attach"}]
    assert pair_status(d, "u", "w") == UNKNOWN == res.pair_status("u", "w")
    assert d["quarantined"][0][0] == ["x", "y"]
    assert sorted(d["unresolved_references"]["objects_without_primitives"]) == ["u", "w", "x", "y"]
    pi, cls, cl, built, res2 = run(line(100, 100, 160, 100))
    d2 = loads(dumps(built.graph, res2, built=built, page=pi))
    assert d2["unsupported"] == sorted(built.unsupported) and d2["unsupported"]
    # dropping an UNKNOWN edge (which leaves no trace in the partition) is still detected
    G3 = ConnGraph(W("a", "b"), [edge("e", "a", "b", negative=("n",))])
    d3 = build_document(G3, solve(G3))
    with pytest.raises(DocumentError, match="open_reasons"):
        loads(tamper(d3, lambda x: x.__setitem__("edges", [])))
    with pytest.raises(DocumentError):                # dropping an attachment
        loads(tamper(d, lambda x: x.__setitem__("attachments", [])))


def test_writer_refuses_a_result_it_cannot_represent_completely():
    G = ConnGraph(W("a", "b"), [edge("e", "a", "b")])
    res = solve(G)
    res.edge_status["ghost"] = (VERIFIED, "PREMISES_COMPLETE")
    with pytest.raises(WriterError):
        build_document(G, res)
    res2 = solve(G)
    res2.edge_status.pop("e")
    with pytest.raises(WriterError):
        build_document(G, res2)


# ------------------------------------------------------------------------- 12 malformed / tampered
def test_malformed_or_tampered_documents_fail_closed():
    G, res, doc = doc_of(ConnGraph(W("a", "b"), [edge("e", "a", "b")]))
    text = canonical(doc)
    bad = [
        text[:-20],                                                      # truncated
        text.replace('"schema_version":1', '"schema_version":2'),        # version
        tamper(doc, lambda d: d.__setitem__("notes", ["x"]), rehash=False),   # hash mismatch
        tamper(doc, lambda d: d.__setitem__("extra", 1)),                # unknown key
        tamper(doc, lambda d: d.pop("nets")),                            # missing key
        tamper(doc, lambda d: edge_rec(d, "e").__setitem__("status", "PROBABLY")),
        tamper(doc, lambda d: d["status_vocabulary"].__setitem__("INFERRED", "VERIFIED")),
        tamper(doc, lambda d: d["coordinates"].__setitem__("y_axis", "up")),
        tamper(doc, lambda d: edge_rec(d, "e").__setitem__("n_prims", 2.0)),
        tamper(doc, lambda d: edge_rec(d, "e")["roles"][0].__setitem__("weak", 0)),
        tamper(doc, lambda d: d["nets"].pop()),
        text.replace('"n_prims":2', '"n_prims":NaN'),
        "[]", "", 42,
    ]
    for t in bad:
        with pytest.raises(DocumentError):
            loads(t)
    assert validate({"format": "nope"})
    assert validate(None)


# ------------------------------------------------------------------------- end to end on the repo fixture
@pytest.mark.parametrize("scene", ["tr1", "tr2_dot", "tr3_pins", "crossing", "t_junction"])
def test_end_to_end_pipeline_to_document_and_back(scene):
    content = {"tr1": line(100, 100, 160, 100) + line(160, 100, 160, 140),
               "tr2_dot": four_ends() + disc(100, 100, 1.0),
               "tr3_pins": r_content() + line(*TIP_UP, 150, 160) + line(*TIP_DN, 150, 40),
               "crossing": line(100, 100, 160, 100) + line(130, 70, 130, 130),
               "t_junction": line(100, 100, 160, 100) + line(130, 100, 130, 140)}[scene]
    pi, cls, cl, built, res = run(content)
    assert audit_result(built.graph, res) == []
    kw = dict(built=built, page=pi, clusters=cl, registry=cls.registry, source={"name": f"{scene}.pdf"})
    t1 = dumps(built.graph, res, **kw)
    assert dumps(built.graph, res, **kw) == t1                          # repeated run
    d = loads(t1)
    assert reserialize(d) == t1
    all_pairs_equal(built.graph, res, d)
    assert d["source"]["page_index"] == 0 and d["source"]["page_size"] == [300.0, 200.0]
    ur = d["unresolved_references"]
    assert ur["provenance"] == [] and ur["primitives"] == [] and ur["objects_without_primitives"] == []
    for e in d["edges"]:
        if e["status"] == "VERIFIED":
            assert e["provenance"]["complete"] and e["rule_kind"] == "SOUND"
            assert all(any(p["content_ref"] == r for p in d["primitives"]) for r in e["prov"])
        assert e["status"] == res.edge_status[e["id"]][0]
    n_ver = sum(e["status"] == "VERIFIED" for e in d["edges"])
    assert n_ver == sum(v[0] == VERIFIED for v in res.edge_status.values())
    if scene in ("tr1", "tr2_dot", "tr3_pins"):
        assert n_ver > 0
    if scene == "t_junction":
        assert n_ver == 0 and {e["status"] for e in d["edges"]} == {"INFERRED"}
