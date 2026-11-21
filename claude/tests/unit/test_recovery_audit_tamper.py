"""F2: the audit re-derives the FULL pin resolution (positions, base/direction, possible numbers, assignment
state, identities) and refuses any stored candidate that disagrees with it."""
from __future__ import annotations

from dataclasses import replace

import pytest

from ecci import recovery as rc
from test_recovery import XF, _fresh, draw, promotable, run


def _promotable():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    assert rc.audit_candidate(c, pi, cls, [XF], base) == []            # the honest record passes
    return pi, cls, base, rep, c


def _refused(rep, c, pi, cls, syms, base, expect):
    probs = rc.audit_candidate(c, pi, cls, syms, base)
    assert any(expect in p for p in probs), probs
    rc.reaudit(rep, pi, cls, syms, base)
    assert c.state == rc.REJECTED and expect in c.reasons[-1]


def _pins(c, k, tip=None, base=None, nums=None):
    ps = [list(p) for p in c.identity["pins"]]
    t, b, n = ps[k]
    ps[k] = [tip or t, base or b, nums or n]
    return [tuple(p) for p in ps]


def test_1_identity_pins_replaced_hits_kept():
    pi, cls, base, rep, c = _promotable()
    c.identity = dict(c.identity, pins=c.identity["pins"][:1])
    _refused(rep, c, pi, cls, [XF], base, "pin resolution differs: 1 stored vs 2 derived")


def test_1b_identity_pin_numbers_swapped_between_terminals():
    pi, cls, base, rep, c = _promotable()
    (t0, b0, n0), (t1, b1, n1) = c.identity["pins"]
    c.identity = dict(c.identity, pins=[(t0, b0, n1), (t1, b1, n0)])
    _refused(rep, c, pi, cls, [XF], base, "pin resolution differs: numbers of terminal")


def test_2_pin_number_changed_candidate_evidence_kept():
    pi, cls, base, rep, c = _promotable()
    c.identity = dict(c.identity, pins=_pins(c, 0, nums=("1", "2")))
    _refused(rep, c, pi, cls, [XF], base, "pin resolution differs: numbers of terminal")


def test_2b_evidence_numbers_changed_identity_kept():
    pi, cls, base, rep, c = _promotable()
    c.pins = (replace(c.pins[0], numbers=("2",)),) + c.pins[1:]
    _refused(rep, c, pi, cls, [XF], base, "differ from the identity")


def test_3_terminal_position_changed():
    pi, cls, base, rep, c = _promotable()
    t, _b, _n = c.identity["pins"][0]
    assert 0.01 < c.tolerances["template_tau"]                          # inside every geometric tolerance
    c.identity = dict(c.identity, pins=_pins(c, 0, tip=(t[0] + 0.01, t[1])))
    _refused(rep, c, pi, cls, [XF], base, "not re-derived")


@pytest.mark.parametrize("state", ["PIN_EQUIVALENT_CANONICAL", "AMBIGUOUS", "UNKNOWN"])
def test_4_assignment_state_changed(state):
    pi, cls, base, rep, c = _promotable()
    c.identity = dict(c.identity, id_state=state)
    _refused(rep, c, pi, cls, [XF], base, "identity re-derivation differs: id_state")


def test_5_pin_base_direction_changed():
    pi, cls, base, rep, c = _promotable()
    _t, b, _n = c.identity["pins"][1]
    c.identity = dict(c.identity, pins=_pins(c, 1, base=(b[0], b[1] + 0.01)))
    _refused(rep, c, pi, cls, [XF], base, "base/direction of terminal 1")


@pytest.mark.parametrize("field", ["identities", "library_collision", "symbols", "note", "status", "pins"])
def test_missing_identity_field_fails_closed(field):
    pi, cls, base, rep, c = _promotable()
    c.identity = {k: v for k, v in c.identity.items() if k != field}
    probs = rc.audit_candidate(c, pi, cls, [XF], base)
    assert any(field in p for p in probs), probs


@pytest.mark.parametrize("tamper,expect", [
    (lambda c: {"hits": (c.hits[0], dict(c.hits[1], tx=c.hits[1]["tx"] + 0.5))}, "template re-match differs"),
    (lambda c: {"hits": (c.hits[0], dict(c.hits[1], lib_token=c.hits[0]["lib_token"]))}, "re-match differs"),
    (lambda c: {"identity": dict(c.identity, identities=c.identity["identities"][:1])},
     "identity re-derivation differs: identities"),
    (lambda c: {"identity": dict(c.identity, library_collision=[["lib:x@0", "XF"]])},
     "identity re-derivation differs: library_collision"),
])
def test_6_one_alternative_tampered_with_several_qualified_identities(tamper, expect):
    twin = replace(XF, lib_token="lib:other_lib.kicad_sym@0")
    syms = [XF, twin]
    pi, cls, base, rep = run(draw(XF), syms)
    (c,) = rep.candidates
    assert c.state == rc.UNKNOWN and len(c.identity["identities"]) == 2 and len(c.hits) == 2
    forged = _fresh(c, state=rc.PROMOTABLE, pins=c.pins, **tamper(c))
    probs = rc.audit_candidate(forged, pi, cls, syms, base)
    assert any(expect in p for p in probs), probs
    assert any("not unique" in p for p in probs)                         # and it can never pass anyway
    rep.candidates = [forged]
    rc.reaudit(rep, pi, cls, syms, base)
    assert forged.state == rc.REJECTED


def test_audit_and_reaudit_cannot_be_called_without_the_baseline_argument():
    pi, cls, base, rep, c = _promotable()
    with pytest.raises(TypeError):
        rc.audit_candidate(c, pi, cls, [XF])                            # noqa: the omission must not be silent
    with pytest.raises(TypeError):
        rc.reaudit(rep, pi, cls, [XF])
    assert "discovery lineage not re-derivable (no baseline)" in rc.audit_candidate(c, pi, cls, [XF], None)
