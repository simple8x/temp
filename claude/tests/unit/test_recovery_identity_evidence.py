"""P0 audit blockers: library-qualified symbol identity (B1) and disjoint evidence roles (B2).

Expected outcome everywhere: an ambiguous identity / transform or a pin line that is not independent of the
candidate's own lineage is UNKNOWN or REJECTED, never PROMOTABLE."""
from __future__ import annotations

from dataclasses import replace

import pytest

from ecci import recovery as rc
from ecci import template as tp
from ecci.kicad_lib import BodyPath, PinDef
from ecci.symbols import _rebuild
from ecci.pins import detect_pins
from test_recovery import BODY_W, H, LIB, XF, _fresh, _xf, draw, line, page, promotable, run, truth


def _gate(c, pi, cls, **kw):
    c2 = _fresh(c, **kw)
    rc.gate(c2, pi, cls, rc._line_index(pi, cls), 0.0)
    return c2


def _audit(c, pi, cls, syms, base, **kw):
    for k, v in kw.items():
        setattr(c, k, v)
    return rc.audit_candidate(c, pi, cls, syms, base)


def _states(rep):
    return sorted((c.operation, c.source_prim_ids, c.state, c.reasons[-1].split(":", 1)[0]) for c in rep.candidates)


# =============================================================================================== B1: identity

def test_same_name_different_lib_token_identical_geometry_is_unknown():
    twin = replace(XF, lib_token="lib:other_lib.kicad_sym@0")          # SAME name, same body, same pins
    pi, cls, base, rep = run(draw(XF), [XF, twin])
    (c,) = rep.candidates
    assert c.identity["symbols"] == ["XF"]                            # a name-only check would see ONE symbol
    assert sorted(map(tuple, c.identity["identities"])) == sorted({(XF.lib_token, "XF"), (twin.lib_token, "XF")})
    assert c.state == rc.UNKNOWN and c.reasons[-1].startswith("multiple_symbol_identities")
    assert not promotable(rep)


def test_same_name_different_pin_definitions_across_libraries_is_unknown():
    pins = tuple(replace(p, electrical_type="output", name="Q") for p in XF.pins)   # same positions, other pins
    other = replace(XF, pins=pins, lib_token="lib:vendor.kicad_sym@0")
    for syms in ([XF, other], [other, XF]):
        pi, cls, base, rep = run(draw(XF), syms)
        (c,) = rep.candidates
        assert c.state == rc.UNKNOWN and c.reasons[-1].startswith("multiple_symbol_identities")


def test_same_identity_with_two_different_definitions_is_a_library_collision():
    clash = replace(XF, pins=tuple(replace(p, electrical_type="power_in") for p in XF.pins))  # same token + name
    pi, cls, base, rep = run(draw(XF), [XF, clash])
    assert not promotable(rep)
    (c,) = rep.candidates
    assert c.state == rc.UNKNOWN
    assert c.reasons[-1].split(":", 1)[0] in ("library_identity_collision", "ambiguous_alternatives")
    assert c.identity["library_collision"] == [[XF.lib_token, "XF"]]


def test_equivalent_transforms_with_different_pin_numbers_are_unknown_and_every_alternative_is_checked():
    sym_body = (BodyPath(((-1.0, -2.0), (1.0, -2.0), (1.0, 2.0), (-1.0, 2.0)), True),
                BodyPath(((2.5, -2.0), (4.5, -2.0), (4.5, 2.0), (2.5, 2.0)), True))
    sym = _xf("XF_SYM", pins=(PinDef("1", "A", -3.54, 0.0, 0.0, 2.54, "passive"),
                              PinDef("2", "B", 7.04, 0.0, 180.0, 2.54, "passive")), body=sym_body)
    pi, cls, base, rep = run(draw(sym), [sym])
    (c,) = rep.candidates
    assert len(c.hits) > 1 and c.state == rc.UNKNOWN                  # numbers 1/2 swap between alternatives
    # forge a CONFIRMED identity taken from the FIRST alternative only: the other alternatives must refute it
    n0 = {tuple(round(v, 6) for v in t): n for n, t, _b in c.hits[0]["pins"]}
    pins = [(t, b, (n0[tuple(round(v, 6) for v in t)],)) for t, b, _n in c.identity["pins"]]
    idn = dict(c.identity, id_state="CONFIRMED", pins=pins)
    c2 = _gate(c, pi, cls, identity=idn)
    assert c2.state == rc.UNKNOWN and c2.reasons[-1].startswith("ambiguous_alternatives")
    assert "pin number" in c2.reasons[-1]
    assert rc.alternative_problems(c2)                                  # the audit uses the same check
    c3 = _fresh(c, identity=idn, hits=c.hits[:1])                      # dropping the refuting alternatives
    assert any("re-match differs" in p for p in rc.audit_candidate(c3, pi, cls, [sym], base))


@pytest.mark.parametrize("order", [0, 1])
def test_reordered_symbols_and_hits_never_change_the_result(order, monkeypatch):
    twin = replace(XF, lib_token="lib:other_lib.kicad_sym@0")
    d = LIB.symbols["D"]
    content = draw(XF) + draw(d, t=(60.0, 60.0))
    ref = _states(run(content, [XF, twin, d])[3])
    syms = [d, twin, XF] if order else [twin, d, XF]
    real = rc.match_cluster
    monkeypatch.setattr(rc, "match_cluster", lambda cl, s: list(reversed(real(cl, s))))
    pi, cls, base, rep = run(content, syms)
    assert _states(rep) == ref
    assert all(c.state != rc.PROMOTABLE for c in rep.candidates if "XF" in c.identity["symbols"])


def test_resolve_qualified_is_order_independent_and_baseline_resolve_is_unchanged():
    twin = replace(XF, lib_token="lib:other_lib.kicad_sym@0")
    pi, cls, base, rep = run(draw(XF), [XF, twin])
    (c,) = rep.candidates
    cl = _rebuild(0, c.matched_prim_ids, {q.id: q for q in pi.prims})
    hits = tp.match_cluster(cl, [XF, twin])
    assert len(hits) >= 2
    a, b = tp.resolve(cl, hits, qualified=True), tp.resolve(cl, list(reversed(hits)), qualified=True)
    assert a == b and len(a.identities) == 2
    legacy = tp.resolve(cl, hits)                                       # baseline call: name-only fields as before
    assert legacy.symbols == ("XF",) and legacy.status == a.status and legacy.id_state == a.id_state


def test_audit_rejects_a_forged_single_identity_and_a_library_without_the_token():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    assert rc.audit_candidate(c, pi, cls, [XF], base) == []
    renamed = replace(XF, lib_token="lib:elsewhere.kicad_sym@0")       # same name, other library
    probs = rc.audit_candidate(c, pi, cls, [renamed], base)
    assert probs and any("re-match differs" in p or "not in library" in p for p in probs)
    twin = replace(XF, lib_token="lib:other_lib.kicad_sym@0")
    probs = rc.audit_candidate(c, pi, cls, [XF, twin], base)          # library now has two identities
    assert any("identity" in p for p in probs)


def test_audit_rejects_recorded_pin_numbers_that_no_alternative_gives():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    c.identity = dict(c.identity, pins=[(t, b, ("9",)) for t, b, _n in c.identity["pins"]])
    rc.reaudit(rep, pi, cls, [XF], base)
    assert c.state == rc.REJECTED and "not recorded for its terminal" in c.reasons[-1]


def test_audit_rejects_tampered_alternative_pins():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    h = dict(c.hits[0])
    h["pins"] = tuple((n, (t[0] + 3.0, t[1]), b) for n, t, b in h["pins"])
    c.hits = (h,)
    rc.reaudit(rep, pi, cls, [XF], base)
    assert c.state == rc.REJECTED and "alternative pins do not follow" in c.reasons[-1]


# =============================================================================================== B2: evidence roles

def _body_style_pin_lines(sym=XF):
    """Body drawn without pin lines; the pin lines are drawn in BODY style touching the outline, so they become
    part of the candidate's own grouping (and are stripped as stubs by the relaxation)."""
    out = draw(sym, skip_pins=tuple(p.number for p in sym.pins))
    for (tx, ty), (bx, by) in truth(sym).values():
        out += line(tx, H - ty, bx, H - by, BODY_W)
    return out


def test_pin_line_that_is_a_stripped_primitive_is_not_independent():
    pi, cls, base, rep = run(_body_style_pin_lines(), [XF])
    assert not promotable(rep)
    (c,) = [c for c in rep.candidates if c.stripped_prim_ids]
    assert "+RELAX" in c.operation and c.state == rc.UNKNOWN
    assert {p.why for p in c.pins} == {"pin_line_reused_from_stripped"}
    # and the audit refuses it too, if a forged report claims the stripped stubs as pin lines
    sup = _fresh(c, state=rc.PROMOTABLE)
    sup.pins = tuple(replace(p, line_prim=s, why=None) for p, s in zip(c.pins, c.stripped_prim_ids))
    assert any("stripped evidence (not independent)" in p for p in rc.audit_candidate(sup, pi, cls, [XF], base))


def test_pin_line_that_is_a_matched_primitive_is_rejected_by_the_audit():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    c.pins = (replace(c.pins[0], line_prim=c.matched_prim_ids[0]),) + c.pins[1:]
    probs = rc.audit_candidate(c, pi, cls, [XF], base)
    assert any("template evidence (not independent)" in p for p in probs)


def _with_line_in(c, role):
    """The promotable candidate with its pin-0 line added to one lineage role (all other roles kept consistent)."""
    ln = c.pins[0].line_prim
    kw = {"discovery_prim_ids": tuple(sorted(set(c.discovery_prim_ids) | {ln}))}
    if role in ("proposal", "excluded"):
        kw["source_prim_ids"] = tuple(sorted(set(c.source_prim_ids) | {ln}))
        kw["excluded_prim_ids"] = tuple(sorted(set(c.excluded_prim_ids) | {ln}))
        kw["operation"] = c.operation + "+RELAX"
    return kw, ln


@pytest.mark.parametrize("role", ["discovery", "excluded"])
def test_pin_line_from_the_candidates_own_grouping_is_not_independent(role):
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    kw, ln = _with_line_in(c, role)
    probs = rc.evidence_role_problems(_fresh(c, pins=c.pins, **kw))
    assert any(f"pin line {ln} is {role} evidence" in p for p in probs)
    if role == "discovery":                                            # source still valid: the gate measures it
        c2 = _gate(c, pi, cls, **kw)
        assert c2.state == rc.UNKNOWN and "pin_line_reused_from_discovery" in c2.reasons[-1]
    for k, v in kw.items():
        setattr(c, k, v)
    rc.reaudit(rep, pi, cls, [XF], base)
    assert c.state == rc.REJECTED and f"is {role} evidence" in c.reasons[-1]


def test_discovery_lineage_must_follow_from_the_baseline():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    kw, _ln = _with_line_in(c, "discovery")
    probs = _audit(c, pi, cls, [XF], base, **kw)
    assert "discovery lineage differs from the baseline" in probs


@pytest.mark.parametrize("field,value,expect", [
    ("discovery_prim_ids", (), "missing discovery lineage"),
    ("source_clusters", (), "no source clusters"),
    ("source_clusters", (10**6,), "not in the baseline"),
])
def test_missing_lineage_fails_closed(field, value, expect):
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    if field == "discovery_prim_ids":
        c2 = _gate(c, pi, cls, **{field: value})
        assert c2.state == rc.REJECTED and expect in c2.reasons[-1]
    setattr(c, field, value)
    rc.reaudit(rep, pi, cls, [XF], base)
    assert c.state == rc.REJECTED and expect in c.reasons[-1]
    assert "discovery lineage not re-derivable (no baseline)" in rc.audit_candidate(c, pi, cls, [XF], None)


def test_incomplete_source_coverage_fails_closed():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    c2 = _gate(c, pi, cls, matched_prim_ids=c.matched_prim_ids[1:])   # one proposal prim now has no role
    assert c2.state == rc.REJECTED and "source coverage incomplete" in c2.reasons[-1]


@pytest.mark.parametrize("kw,expect", [
    (lambda c: {"matched_prim_ids": c.matched_prim_ids + c.matched_prim_ids[:1]}, "duplicate prim ids within template"),
    (lambda c: {"excluded_prim_ids": c.matched_prim_ids[:1], "operation": c.operation + "+RELAX"},
     "prim holds two of template/stripped/excluded"),
    (lambda c: {"excluded_prim_ids": c.matched_prim_ids[:1]}, "without a relaxation"),
    (lambda c: {"stripped_prim_ids": (10**6,), "operation": c.operation + "+RELAX"}, "outside the proposal"),
])
def test_duplicate_or_overlapping_role_ids_fail_closed(kw, expect):
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    assert any(expect in p for p in rc.evidence_role_problems(_fresh(c, pins=c.pins, **kw(c))))
    c2 = _gate(c, pi, cls, **kw(c))
    assert c2.state == rc.REJECTED


def test_one_pin_line_in_two_roles_and_two_pins_fails_closed():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    ln = c.pins[0].line_prim
    dup = _fresh(c, pins=(c.pins[0], replace(c.pins[1], line_prim=ln)))
    assert any("also used by pin" in p for p in rc.evidence_role_problems(dup))


def test_every_promotable_pin_line_is_outside_the_whole_lineage():
    for content in (draw(XF), draw(LIB.symbols["D"]) + draw(LIB.symbols["D"], t=(150.0, 108.12))):
        syms = [XF, LIB.symbols["D"]]
        pi, cls, base, rep = run(content, syms)
        assert promotable(rep)
        for c in promotable(rep):
            lineage = set(c.discovery_prim_ids) | set(c.source_prim_ids)
            assert set(c.matched_prim_ids) | set(c.stripped_prim_ids) | set(c.excluded_prim_ids) == set(c.source_prim_ids)
            assert all(p.line_prim is not None and p.line_prim not in lineage for p in c.pins)
            assert rc.audit_candidate(c, pi, cls, syms, base) == []


def test_pin_line_of_one_candidate_grouped_by_another_is_a_conflict():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    a = _fresh(c, id="A", state=rc.EVIDENCE_SUPPORTED, pins=c.pins)
    b = _fresh(c, id="B", state=rc.EVIDENCE_SUPPORTED, source_prim_ids=(c.pins[0].line_prim,),
               pins=tuple(replace(p, line_prim=10**6 + p.index,
                                  predicted_tip=(p.predicted_tip[0] + 80, p.predicted_tip[1])) for p in c.pins))
    rc.resolve_conflicts([a, b], set(), [])
    assert a.state == b.state == rc.UNKNOWN


def test_pin_exchange_shortcut_needs_one_library_qualified_identity():
    """Two allowlisted resistors with the SAME name from two library files: numbers may permute within ONE
    identity (PIN_EQUIVALENT_CANONICAL), never across two identities."""
    r = LIB.symbols["R"]
    r2 = replace(r, lib_token=r.lib_token.split("@", 1)[0].replace("lib_min.kicad_sym", "x/lib_min.kicad_sym") + "@9")
    assert tp._pin_number_exchange_allowed(r) and tp._pin_number_exchange_allowed(r2) and r2.lib_token != r.lib_token
    pi, cls = page(draw(r))
    base = detect_pins(pi, cls, [r])
    cl = next(cr.cluster for cr in base if cr.resolution.status == "UNIQUE")
    one = tp.resolve(cl, tp.match_cluster(cl, [r]), qualified=True)
    assert one.id_state == "PIN_EQUIVALENT_CANONICAL" and len(one.identities) == 1
    two = tp.resolve(cl, tp.match_cluster(cl, [r, r2]), qualified=True)
    assert len(two.identities) == 2 and two.id_state == "AMBIGUOUS"
