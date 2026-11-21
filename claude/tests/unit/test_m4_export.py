"""M4.2: symbol records with provenance, fail-closed native KiCad export, manifest, round-trip/GT comparison
logic (the real kicad-cli round trip is in tests/kicad)."""
import copy
import dataclasses
import hashlib

import pytest

from ecci import kicad_export as kx, kicad_roundtrip as kr, symrec, writer
from ecci.kicad_lib import Library
from m4_helpers import LIB, LIB_TEXT, by_cluster_symbol, docs, export, rehash_conn, rehash_syms
from m4_scenes import GtPin, Scene, ambiguous_r, chain, crossing, t_junction
from test_rules import H


# ------------------------------------------------------------------ symbol records: candidates / identity
def test_multiple_candidates_are_recorded_and_electrically_equivalent_pins_get_canonical_export():
    pi, conn, syms, sch, man = export(ambiguous_r())
    r = next(c for c in syms["clusters"] if any(x["symbol"] == "R" for x in c["candidates"]))
    assert len(r["candidates"]) >= 2 and all(x["selected"] for x in r["candidates"])
    assert r["transform"] == {"state": "AMBIGUOUS", "candidate": None, "n_candidates": len(r["candidates"])}
    d = next(d for d in man["symbols"] if d["cluster"] == r["id"])
    assert d["decision"] == "PLACED" and d["lib_id"].endswith(":R")
    assert d["transform_choice"].startswith("SYNTHETIC_CANONICAL:")
    assert all(p["number_assignment"] == "EQUIVALENT_CANONICAL" for p in d["pins"])
    assert len(d["candidates"]) == len(r["candidates"])            # every candidate is in the manifest
    assert "R" in sch.split("(lib_symbols")[1].split("(symbol (lib_id")[0]


def test_two_library_symbols_with_the_same_geometry_make_identity_ambiguous():
    twin = dataclasses.replace(LIB.symbols["D"], name="D_TWIN")
    lib2 = Library(LIB.path, LIB.sha256, {**LIB.symbols, "D_TWIN": twin})
    pi, conn, syms = docs(chain("r0"), lib2)
    c = next(c for c in syms["clusters"] if "D" in c["identity"]["names"])
    assert c["identity"] == {"state": "AMBIGUOUS", "lib_symbol": None, "names": ["D", "D_TWIN"]}
    man = kx.plan(conn, syms, LIB_TEXT)
    d = next(d for d in man["symbols"] if d["cluster"] == c["id"])
    assert d["decision"] == "NOT_PLACED" and any("identity AMBIGUOUS" in x for x in d["reasons"])


def test_symmetric_pin_number_permutation_with_different_electrical_signatures_is_not_placed():
    base = LIB.symbols["R"]
    pins = tuple(dataclasses.replace(p, name=("A" if p.number == "1" else "B"),
                                     electrical_type=("input" if p.number == "1" else "output"))
                 for p in base.pins)
    polarized = dataclasses.replace(base, name="R_POLARIZED", pins=pins)
    lib_text = LIB_TEXT.replace('(symbol "R"', '(symbol "R_POLARIZED"', 1)
    lib = Library(LIB.path, hashlib.sha256(lib_text.encode()).hexdigest(),
                  {k: v for k, v in LIB.symbols.items() if k != "R"} | {"R_POLARIZED": polarized})
    pi, conn, syms = docs(ambiguous_r(), lib)
    c = next(c for c in syms["clusters"] if c["identity"]["lib_symbol"] == "R_POLARIZED")
    assert c["resolution"]["id_state"] == "AMBIGUOUS"
    man = kx.plan(conn, syms, lib_text)
    d = next(d for d in man["symbols"] if d["cluster"] == c["id"])
    assert d["decision"] == "NOT_PLACED"
    assert any("resolution UNIQUE/AMBIGUOUS" in reason for reason in d["reasons"])


def test_symbol_identity_unknown_is_never_placed():
    lib2 = Library(LIB.path, LIB.sha256, {k: v for k, v in LIB.symbols.items() if k != "LED"})
    pi, conn, syms = docs(chain("r0"), lib2)
    led = [c for c in syms["clusters"] if c["identity"]["state"] == "UNKNOWN" and c["candidates"] == []]
    assert led and all(c["transform"]["state"] == "UNKNOWN" for c in led)
    man = kx.plan(conn, syms, LIB_TEXT)
    for c in led:
        d = next(d for d in man["symbols"] if d["cluster"] == c["id"])
        assert d["decision"] == "NOT_PLACED" and any("identity UNKNOWN" in x for x in d["reasons"])
    assert man["nets"] == []                                      # the D alone cannot make a net


def test_reference_designator_is_unknown_and_the_kicad_id_is_marked_synthetic():
    pi, conn, syms, sch, man = export(chain("r90"))
    assert all(c["reference"] == {"value": None, "state": "UNKNOWN", "evidence": []} for c in syms["clusters"])
    assert all(c["value"]["state"] == "UNKNOWN" for c in syms["clusters"])
    placed = [d for d in man["symbols"] if d["decision"] == "PLACED"]
    assert placed
    for d in placed:
        assert d["synthetic_reference"]["kind"] == "SYNTHETIC" and d["source_reference"]["state"] == "UNKNOWN"
        assert d["synthetic_reference"]["value"] == f"ECCI{d['cluster']}"
    assert man["claims"]["source_references"].startswith("UNKNOWN")
    # a designator typed into the file (and rehashed) is refused: no evidence kind for it exists in schema 1
    bad = copy.deepcopy(syms)
    bad["clusters"][0]["reference"] = {"value": "D1", "state": "CONFIRMED", "evidence": ["S1:0:0"]}
    with pytest.raises(symrec.SymbolDocError, match="reference must be UNKNOWN"):
        symrec.loads(rehash_syms(bad), conn, LIB)


@pytest.mark.parametrize("field,delta", [("tx", 0.5), ("ty", -0.5), ("scale", 0.01), ("residual", 0.001),
                                         ("orient", 1), ("matrix", None)])
def test_transform_altered_after_serialization_is_rejected(field, delta):
    pi, conn, syms = docs(chain("r0"))
    bad = copy.deepcopy(syms)
    c = next(c for c in bad["clusters"] if c["identity"]["lib_symbol"] == "LED")["candidates"][0]
    if field == "matrix":
        c["matrix"] = [-v for v in c["matrix"]]
    else:
        c[field] = c[field] + delta
    with pytest.raises(symrec.SymbolDocError, match="candidates are not the library hits"):
        symrec.loads(rehash_syms(bad), conn, LIB)


def test_dropped_or_injected_candidate_and_forged_transform_state_are_rejected():
    pi, conn, syms = docs(ambiguous_r())
    r = next(i for i, c in enumerate(syms["clusters"]) if len(c["candidates"]) > 1)
    dropped = copy.deepcopy(syms)
    dropped["clusters"][r]["candidates"] = dropped["clusters"][r]["candidates"][:1]
    with pytest.raises(symrec.SymbolDocError):
        symrec.loads(rehash_syms(dropped), conn, LIB)
    forged = copy.deepcopy(syms)
    forged["clusters"][r]["transform"] = {"state": "UNIQUE", "candidate": 0, "n_candidates": 1}
    with pytest.raises(symrec.SymbolDocError, match="transform state is not derived"):
        symrec.loads(rehash_syms(forged), conn, LIB)
    desel = copy.deepcopy(syms)
    for x in desel["clusters"][r]["candidates"][1:]:
        x["selected"] = False                       # "choose" one candidate by hand
    with pytest.raises(symrec.SymbolDocError):
        symrec.loads(rehash_syms(desel), conn, LIB)


def test_electrically_equivalent_pin_numbers_use_a_declared_canonical_mapping():
    pi, conn, syms, sch, man = export(ambiguous_r())
    r = next(c for c in syms["clusters"] if any(x["symbol"] == "R" for x in c["candidates"]))
    assert {p["id_state"] for p in r["pins"]} == {"EQUIVALENT"} and all(len(p["numbers"]) == 2 for p in r["pins"])
    placed_r = by_cluster_symbol(man, "R")[0]
    assert placed_r["decision"] == "PLACED"
    assert all(p["number_assignment"] == "EQUIVALENT_CANONICAL" and len(p["numbers"]) == 1
               and len(p["source_number_candidates"]) == 2 for p in placed_r["pins"])
    for mut in ("id_state", "numbers"):
        bad = copy.deepcopy(syms)
        p = next(c for c in bad["clusters"] if c["id"] == r["id"])["pins"][0]
        if mut == "id_state":
            p["id_state"] = "CONFIRMED"
        else:
            p["numbers"] = ["1"]
        with pytest.raises(symrec.SymbolDocError, match="differs from the connectivity document"):
            symrec.loads(rehash_syms(bad), conn, LIB)


# ------------------------------------------------------------------ connectivity: fail closed
def test_ambiguous_crossing_site_is_not_exported_as_a_net():
    pi, conn, syms, sch, man = export(crossing())
    assert conn["sites"] and all(s["result"]["status"] == "UNKNOWN" for s in conn["sites"])
    assert man["not_exported"]["open_sites"] == sorted(s["id"] for s in conn["sites"])
    assert len(man["nets"]) == 2 and all(len(n["pins"]) == 2 for n in man["nets"])
    refs = [{p[0] for p in n["pins"]} for n in man["nets"]]
    assert refs[0].isdisjoint(refs[1])


def test_inferred_t_junction_is_listed_and_not_exported():
    pi, conn, syms, sch, man = export(t_junction(False))
    assert man["not_exported"]["edges"]["INFERRED"]
    assert len(man["nets"]) == 1 and len(man["nets"][0]["pins"]) == 2         # the T arm's pin is not joined
    pi, conn, syms, sch, man = export(t_junction(True))
    assert len(man["nets"]) == 1 and len(man["nets"][0]["pins"]) == 3


def test_every_exported_net_carries_its_verified_edges_evidence_and_provenance():
    pi, conn, syms, sch, man = export(crossing())
    ver = {e["id"]: e for e in conn["edges"] if e["status"] == "VERIFIED"}
    for n in man["nets"]:
        assert n["edges"] and all(e in ver for e in n["edges"])
        assert n["evidence"] and n["prov"]
        assert set(n["prov"]) <= {p["content_ref"] for p in conn["primitives"]}
        assert {lb["object"] for lb in n["labels"]} == {p[2] for p in n["pins"]}


def test_forged_wire_or_net_in_rehashed_connectivity_json_is_rejected():
    pi, conn, syms = docs(crossing())
    # 1. promote the crossing: add a VERIFIED edge between the two crossing wires
    bad = copy.deepcopy(conn)
    e = copy.deepcopy(next(e for e in bad["edges"] if e["status"] == "VERIFIED" and e["rule"] == "TR3"))
    wires = sorted({o["id"] for o in bad["objects"] if o["kind"] == "WIRE"})
    e.update({"id": "forged:1", "a": wires[0], "b": wires[-1]})
    bad["edges"].append(e)
    bad["edges"].sort(key=lambda x: x["id"])
    with pytest.raises(writer.DocumentError):
        writer.loads(rehash_conn(bad))
    # 2. merge two verified groups by editing the partition
    bad = copy.deepcopy(conn)
    groups = sorted({c["verified_group"] for c in bad["connectivity"] if c["verified_group"]})
    for c in bad["connectivity"]:
        if c["verified_group"] == groups[-1]:
            c["verified_group"] = groups[0]
    with pytest.raises(writer.DocumentError):
        writer.loads(rehash_conn(bad))
    # 3. a net injected into the (rehashed) manifest is not what the inputs give
    sch, man = kx.export(conn, syms, LIB_TEXT)
    man2 = copy.deepcopy(man)
    man2["nets"].append(copy.deepcopy(man2["nets"][0]) | {"name": "ECCI_V99"})
    man2.pop("content_sha256")
    man2["content_sha256"] = kx.hashlib.sha256(kx.canonical(man2).encode()).hexdigest()
    assert any("not what the inputs give" in p for p in kx.verify_manifest(man2, conn, syms, LIB_TEXT, sch))
    # 4. a wire added to the schematic text is caught against the manifest
    sch2 = sch.replace("  (sheet_instances", "  (wire (pts (xy 0 0) (xy 10 0)) (stroke (width 0) (type default)) "
                                             "(uuid \"00000000-0000-0000-0000-000000000001\"))\n  (sheet_instances")
    assert kx.verify_manifest(man, conn, syms, LIB_TEXT, sch2) == ["schematic text is not what the inputs give"]


def test_tampered_provenance_or_evidence_is_rejected():
    pi, conn, syms = docs(chain("r0"))
    assert symrec.audit_evidence(conn, syms, pi, LIB) == []
    for forged in (["geom:999999"], ["geom:3"]):          # a primitive not on the page / the wrong primitive
        bad = copy.deepcopy(conn)
        i = next(i for i, e in enumerate(bad["evidence"]) if e["id"].startswith("S1:"))
        bad["evidence"][i]["lineage"] = forged
        try:
            bad = writer.loads(rehash_conn(bad))    # the file reader cannot see a lineage edit ...
        except writer.DocumentError:
            continue
        s_ = symrec.loads(symrec.dumps(symrec.build_symbols_like(syms, bad)), bad, LIB)
        assert any("lineage" in p for p in symrec.audit_evidence(bad, s_, pi, LIB))    # ... the page audit does
    bad = copy.deepcopy(conn)
    e = next(e for e in bad["edges"] if e["status"] == "VERIFIED")
    e["prov"] = ["p0/forged#1"]
    with pytest.raises(writer.DocumentError):
        writer.loads(rehash_conn(bad))
    # symbol record: primitive refs and geometry must be those of the page
    s2 = copy.deepcopy(syms)
    s2["clusters"][0]["primitives"][0]["content_ref"] = "p0/forged#7"
    assert any("content_ref differs from the page" in p for p in symrec.audit_against_page(s2, pi))
    with pytest.raises(symrec.SymbolDocError, match="content_ref differs from the connectivity"):
        symrec.loads(rehash_syms(s2), conn, LIB)          # this primitive is also named by the connectivity doc
    s3 = copy.deepcopy(syms)
    s3["clusters"][0]["primitives"] = s3["clusters"][0]["primitives"][:-1]
    try:
        s3 = symrec.loads(rehash_syms(s3), conn, LIB)
        assert any("geometry is not the geometry" in p for p in symrec.audit_against_page(s3, pi))
    except symrec.SymbolDocError:
        pass
    assert symrec.audit_against_page(syms, pi) == []
    # a symbol document bound to another connectivity document
    other = docs(crossing())[1]
    with pytest.raises(symrec.SymbolDocError, match="does not bind"):
        symrec.loads(symrec.dumps(syms), other, LIB)
    with pytest.raises(kx.ExportError):
        kx.plan(other, syms, LIB_TEXT)
    with pytest.raises(kx.ExportError, match="library text"):
        kx.plan(conn, syms, LIB_TEXT + " ")


def test_symbol_that_kicad_would_connect_by_itself_is_not_placed():
    lib_text = LIB_TEXT.replace('(pin passive line (at 3.81 0 180) (length 2.54) (name "K") (number "1"))',
                                '(pin power_in line (at 3.81 0 180) (length 2.54) hide (name "K") (number "1"))', 1)
    assert lib_text != LIB_TEXT
    pi, conn, syms = docs(chain("r0"))
    syms2 = copy.deepcopy(syms)
    syms2["library"]["sha256"] = kx.hashlib.sha256(lib_text.encode()).hexdigest()
    man = kx.plan(conn, syms2, lib_text)
    d = by_cluster_symbol(man, "D")[0]
    assert d["decision"] == "NOT_PLACED" and any("hidden power_in" in r for r in d["reasons"])


def _dec(cid, pins):
    return {"cluster": cid, "decision": "PLACED", "reasons": [], "lib_id": "x", "synthetic_reference": {},
            "pins": [{"object": o, "kicad_iu": list(xy), "verified_group": g, "numbers": ["1"]} for o, xy, g in pins]}


def test_coincident_pin_ends_of_different_nets_withdraw_the_symbols():
    ds = [_dec(0, [("p:0:0", (0, 0), "g1"), ("p:0:1", (5, 0), None)]),
          _dec(1, [("p:1:0", (5, 0), None)]),                         # on p:0:1, but in no verified net
          _dec(2, [("p:2:0", (0, 0), "g1")]),                         # on p:0:0, same verified net: allowed
          _dec(3, [("p:3:0", (9, 9), None)])]
    kx.withdraw_coincident(ds)
    assert [d["decision"] for d in ds] == ["NOT_PLACED", "NOT_PLACED", "PLACED", "PLACED"]
    assert all("coincides" in d["reasons"][0] and "lib_id" not in d and d["pins"] == [] for d in ds[:2])
    ok = [_dec(0, [("p:0:0", (0, 0), "g1")]), _dec(2, [("p:2:0", (0, 0), "g1")])]
    kx.withdraw_coincident(ok)
    assert [d["decision"] for d in ok] == ["PLACED", "PLACED"]


def test_library_pin_that_does_not_land_on_the_detected_tip_blocks_placement():
    moved = LIB_TEXT.replace('(pin passive line (at 3.81 0 180) (length 2.54) (name "K") (number "1"))',
                             '(pin passive line (at 6.35 0 180) (length 2.54) (name "K") (number "1"))', 1)
    assert moved != LIB_TEXT
    pi, conn, syms = docs(chain("r0"))
    s2 = copy.deepcopy(syms)
    s2["library"]["sha256"] = kx.hashlib.sha256(moved.encode()).hexdigest()
    d = by_cluster_symbol(kx.plan(conn, s2, moved), "D")[0]
    assert d["decision"] == "NOT_PLACED" and any("is not the detected tip" in r for r in d["reasons"])


def test_export_is_deterministic_and_manifest_rederives():
    a = export(chain("mx"))
    b = export(chain("mx"))
    assert a[3] == b[3] and a[4] == b[4]
    pi, conn, syms, sch, man = a
    assert kx.manifest_hash_ok(man) and kx.verify_manifest(man, conn, syms, LIB_TEXT, sch) == []
    assert man["claims"]["electrical_correctness"] == "NOT_CLAIMED" and man["wires"] == []


def test_orientation_table_is_complete_under_the_measured_order_and_refuses_without_it():
    from ecci.template import D4
    assert all(kx.orientation_for(m, "RM") is not None for m in D4)
    assert all(kx.orientation_for(m, "MR") is not None for m in D4)
    unresolved = [m for m in D4 if kx.orientation_for(m) is None]
    assert len(unresolved) == 2                      # the two diagonal reflections depend on the order
    for m in D4:
        o = kx.orientation_for(m, "RM")
        assert kx.kicad_matrix(o[0], o[1], "RM") == m


# ------------------------------------------------------------------ comparison logic (no CLI)
def _netlist_from(man, merge=False, drop=False, join_solo=False):
    nets = {}
    for n in man["nets"]:
        nets[n["name"]] = sorted((r, num) for r, num, _ in n["pins"])
    exported = {(d["synthetic_reference"]["value"], x) for d in man["symbols"] if d["decision"] == "PLACED"
                for p in d["pins"] for x in p["numbers"]}
    for i, x in enumerate(sorted(exported - {tuple(p) for v in nets.values() for p in v})):
        nets[f"unconnected-{i}"] = [x]
    if merge:
        ks = sorted(k for k in nets if k.startswith("ECCI_V"))
        nets[ks[0]] = sorted(nets[ks[0]] + nets.pop(ks[1]))
    if join_solo:                                    # two pins the manifest leaves alone end up in one KiCad net
        solo = sorted(k for k in nets if k.startswith("unconnected-"))[:2]
        nets[solo[0]] = sorted(nets[solo[0]] + nets.pop(solo[1]))
    if drop:
        k = sorted(k for k in nets if k.startswith("ECCI_V"))[0]
        nets[k] = nets[k][:1]
    comps = {d["synthetic_reference"]["value"]: d["lib_id"] for d in man["symbols"] if d["decision"] == "PLACED"}
    return {"components": comps, "nets": nets}


def test_roundtrip_comparison_classifies_merged_and_missing_nets():
    pi, conn, syms, sch, man = export(crossing())
    assert kr.compare_roundtrip(man, sch, _netlist_from(man))["problems"] == []
    m = kr.compare_roundtrip(man, sch, _netlist_from(man, merge=True))
    assert m["net_states"]["MERGED"] == 2 and m["problems"]
    x = kr.compare_roundtrip(man, sch, _netlist_from(man, join_solo=True))
    assert x["net_states"]["EXTRA"] == 2 and any("connected in KiCad but not in the manifest" in p for p in x["problems"])
    d = kr.compare_roundtrip(man, sch, _netlist_from(man, drop=True))
    assert d["net_states"]["MISSING"] == 1 and any("absent from the netlist" in p for p in d["problems"])


def test_ground_truth_mismatch_is_reported_as_fp_and_fn():
    sc = crossing()
    pi, conn, syms, sch, man = export(sc)
    nl = _netlist_from(man)
    good = kr.compare_ground_truth(man, nl, sc.gt_pins())
    assert good["pairs"]["FP"] == 0 and good["pairs"]["TP"] == 2 and good["pairs"]["FN"] == 0
    # a GT that says the crossing IS connected: our (correct, fail-closed) export now misses pairs
    joined = [GtPin(g.ref, g.number, g.x, g.y, "N0" if g.net in ("N0", "N1") else g.net, False, g.lib_id)
              for g in sc.gt_pins()]
    fn = kr.compare_ground_truth(man, nl, joined)
    assert fn["pairs"]["FN"] == 4 and fn["pairs"]["FP"] == 0
    # a GT that says nothing is connected: every exported pair is a false positive
    apart = [GtPin(g.ref, g.number, g.x, g.y, f"solo{i}", False, g.lib_id) for i, g in enumerate(sc.gt_pins())]
    fp = kr.compare_ground_truth(man, nl, apart)
    assert fp["pairs"]["FP"] == 2 and fp["precision"] == 0.0 and fp["fp_examples"]
    # a GT pin moved away is not matched: it is UNKNOWN, not scored
    moved = [GtPin(g.ref, g.number, g.x + 50, H - 0, g.net, False, g.lib_id) for g in sc.gt_pins()]
    assert kr.compare_ground_truth(man, nl, moved)["exported_pins_mapped"] == 0


def test_m4_gate_table_never_passes_blocked_or_failed_stages():
    from ecci.m4_pipeline import gate_table
    ok = {"stages": {s: "PASS" for s in ("connectivity_schema_roundtrip", "symbols_schema_roundtrip", "provenance",
                                         "manifest_rederived", "kicad_read", "netlist_roundtrip", "ground_truth")}}
    assert gate_table([ok], "kicad-cli")["overall"] == "PASS"
    blk = copy.deepcopy(ok)
    blk["stages"]["kicad_read"] = "BLOCKED"
    assert gate_table([ok, blk], None)["overall"] == "BLOCKED_OR_PARTIAL"
    bad = copy.deepcopy(ok)
    bad["stages"]["ground_truth"] = "FAIL"
    assert gate_table([ok, bad], "x")["overall"] == "FAIL"
    assert gate_table([], "x")["overall"] != "PASS"


def test_pin_lines_selection_uses_the_pipelines_part_rule():
    # KiCad embeds duplicates of one part as "lib:SIM4X32", "SIM4X32_1", "SIM4X32#u2": one part, so a PIN_LINES
    # unit choice among them is legal on read (found by the GT run: the reader had its own, narrower rule)
    assert len({symrec.part_stem(n) for n in ("video_schlib:SIM4X32", "SIM4X32_1", "SIM4X32_7", "SIM4X32#u2")}) == 1
    assert symrec.part_stem("R") != symrec.part_stem("C")