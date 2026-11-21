"""P0: isolated grouped-recovery candidates. Expected geometry comes from the drawing code (render_kicad.place),
never from the code under test."""
from __future__ import annotations

import copy
import math
from dataclasses import replace
from pathlib import Path

import pytest

from ecci import recovery as rc
from ecci.classify import classify_page
from ecci.ingest_vector import ingest_pdf
from ecci.kicad_lib import BodyPath, PinDef, SymbolDef, load_library
from ecci.pins import detect_pins, s2_tolerance
from ecci.rules import analyze_page
from pdfgen import one_page
from render_kicad import ORIENTS, draw_symbol, place

LIB = load_library(str(Path(__file__).resolve().parents[1] / "fixtures" / "lib_min.kicad_sym"))
W, H = 300, 200
BODY_W, PIN_W = 0.9, 0.45
S = 3.0
WIRES = b"0.45 w 10 190 m 80 190 l S 10 185 m 10 150 l S 90 185 m 90 150 l S\n"


def _xf(name="XF", pins=None, body=None):
    """A part whose body is TWO separate outlines (split primitives): the baseline never merges them."""
    pins = pins or (PinDef("1", "A", -3.54, 1.0, 0.0, 2.54, "passive"),
                    PinDef("2", "B", 7.04, -1.0, 180.0, 2.54, "passive"))
    body = body or (BodyPath(((-1.0, -2.0), (1.0, -2.0), (1.0, 2.0), (-1.0, 2.0)), True),
                    BodyPath(((2.5, -2.0), (4.5, -2.0), (4.5, 1.0), (2.5, 2.0)), True))
    return SymbolDef(name, tuple(pins), tuple(body), f"lib:test_{name}.kicad_sym@0")


XF = _xf()
T0 = (150.0, 100.0)


def draw(sym, o="r0", t=T0, **kw):
    return draw_symbol(sym, o, S, t, body_w=BODY_W, pin_w=PIN_W, **kw)


def line(x0, y0, x1, y1, w=PIN_W):
    return f"{w} w {x0} {y0} m {x1} {y1} l S\n".encode()


def truth(sym, o="r0", t=T0):
    """pin number -> (tip, base) in ingest space (y down)."""
    out = {}
    for p in sym.pins:
        tx, ty = place(ORIENTS[o], S, t, *p.tip)
        bx, by = place(ORIENTS[o], S, t, *p.base)
        out[p.number] = ((tx, H - ty), (bx, H - by))
    return out


def page(content):
    pi = ingest_pdf(one_page(WIRES + content, size=(W, H))).pages[0]
    return pi, classify_page(pi)


def run(content, symbols):
    pi, cls = page(content)
    syms = list(symbols)
    base = detect_pins(pi, cls, syms)
    return pi, cls, base, rc.recover(pi, cls, base, syms, eps=0.02)


def promotable(rep):
    return [c for c in rep.candidates if c.state == rc.PROMOTABLE]


def near(a, b, tol=1e-6):
    return math.dist(a, b) <= tol


def assert_pins_match_truth(cand, sym, o="r0", t=T0):
    exp = truth(sym, o, t)
    got = {p.numbers[0]: (p.predicted_tip, p.predicted_base) for p in cand.pins}
    assert set(got) == set(exp)
    for k, (tip, base) in exp.items():
        assert near(got[k][0], tip, 1e-3) and near(got[k][1], base, 1e-3)


# ----------------------------------------------------------------------------------------------- gains

def test_split_primitive_body_is_merged_and_promotable_with_exact_pins():
    pi, cls, base, rep = run(draw(XF), [XF])
    assert all(cr.resolution.status == "NONE" for cr in base)          # baseline cannot see it
    (c,) = promotable(rep)
    assert c.operation == rc.OP_MERGE and c.identity["symbols"] == ["XF"]
    assert_pins_match_truth(c, XF)
    for p in c.pins:
        assert p.line_prim is not None and p.line_prim not in c.matched_prim_ids and p.residual <= 1e-6
    assert [h[1] for h in c.history] == [rc.PROPOSED, rc.EVIDENCE_SUPPORTED, rc.PROMOTABLE]


def test_adjacent_bodies_chained_by_the_baseline_are_recovered_individually():
    d = LIB.symbols["D"]
    t2 = (150.0, 100.0 + 7.62 + 0.5)
    pi, cls, base, rep = run(draw(d) + draw(d, t=t2), [d])
    assert all(cr.resolution.status == "NONE" for cr in base)
    ps = promotable(rep)
    assert len(ps) == 2
    tips = sorted(p.predicted_tip for c in ps for p in c.pins)
    exp = sorted(v[0] for t in (T0, t2) for v in truth(d, t=t).values())
    assert all(near(a, b, 1e-3) for a, b in zip(tips, exp))


@pytest.mark.parametrize("o", sorted(ORIENTS))
def test_every_transform_of_an_asymmetric_part_resolves_to_the_drawn_pins(o):
    pi, cls, base, rep = run(draw(XF, o), [XF])
    (c,) = promotable(rep)
    assert_pins_match_truth(c, XF, o)


# ----------------------------------------------------------------------------------------------- adversarial

def test_missing_pin_lines_stay_unknown():
    pi, cls, base, rep = run(draw(XF, pins=False), [XF])
    assert not promotable(rep)
    (c,) = rep.candidates
    assert c.state == rc.UNKNOWN and "missing_pin_line" in c.reasons[-1]


def test_one_missing_pin_line_is_enough_to_stay_unknown():
    pi, cls, base, rep = run(draw(XF, skip_pins=("2",)), [XF])
    (c,) = rep.candidates
    assert c.state == rc.UNKNOWN
    assert [p.why for p in c.pins if p.numbers == ("2",)] == ["missing_pin_line"]


def test_overlapping_pin_lines_are_ambiguous():
    tp = truth(XF)["1"]
    # the same pin line plotted twice (two primitives): which one is the pin is not decidable
    dup = line(tp[1][0], H - tp[1][1], tp[0][0], H - tp[0][1])
    pi, cls, base, rep = run(draw(XF) + dup, [XF])
    (c,) = rep.candidates
    assert c.state == rc.UNKNOWN
    assert any(p.why and p.why.startswith("overlapping_pin_lines") for p in c.pins)


def _tol1():
    tip, b = truth(XF)["1"]
    return s2_tolerance(math.dist(tip, b))


@pytest.mark.parametrize("frac,ok", [(0.0, True), (0.95, True), (1.05, False), (2.0, False)])
def test_tolerance_boundary_on_the_measured_tip(frac, ok):
    tip, b = truth(XF)["1"]
    d = frac * _tol1()
    # pin 1 points along -x (tip left of base); the drawn line ends d further left than predicted
    content = draw(XF, skip_pins=("1",)) + line(b[0], H - b[1], tip[0] - d, H - tip[1])
    pi, cls, base, rep = run(content, [XF])
    (c,) = rep.candidates
    assert (c.state == rc.PROMOTABLE) is ok, c.reasons


@pytest.mark.parametrize("frac,ok", [(0.95, True), (1.05, False)])
def test_tolerance_boundary_on_the_measured_base(frac, ok):
    tip, b = truth(XF)["1"]
    d = frac * _tol1()
    content = draw(XF, skip_pins=("1",)) + line(b[0] - d, H - b[1], tip[0], H - tip[1])
    pi, cls, base, rep = run(content, [XF])
    (c,) = rep.candidates
    assert (c.state == rc.PROMOTABLE) is ok, c.reasons


def test_unrelated_nearby_wires_are_not_pin_evidence():
    tip, b = truth(XF)["1"]
    content = draw(XF, skip_pins=("1",))
    content += line(tip[0], H - tip[1], tip[0] - 20, H - tip[1])            # wire leaving the tip outward
    content += line(tip[0], H - tip[1], tip[0], H - tip[1] - 15)            # perpendicular wire at the tip
    content += line(tip[0], H - tip[1] + 0.3, b[0] + 30, H - tip[1] + 0.3)  # long inward wire along the axis
    pi, cls, base, rep = run(content, [XF])
    (c,) = rep.candidates
    assert c.state == rc.UNKNOWN
    assert [p.why for p in c.pins if p.numbers == ("1",)] == ["missing_pin_line"]


def test_normal_wire_connections_do_not_disturb_a_valid_candidate():
    t = truth(XF)
    content = draw(XF)
    for tip, b in t.values():
        far = tip[0] + (tip[0] - b[0]) * 3
        content += line(tip[0], H - tip[1], far, H - tip[1])
    pi, cls, base, rep = run(content, [XF])
    (c,) = promotable(rep)
    assert_pins_match_truth(c, XF)


def test_multiple_candidate_symbols_with_the_same_body_are_unknown():
    twin = replace(XF, name="XF_TWIN", lib_token="lib:test_twin.kicad_sym@0")
    pi, cls, base, rep = run(draw(XF), [XF, twin])
    (c,) = rep.candidates
    assert c.state == rc.UNKNOWN and c.reasons[-1].startswith("multiple_symbols")


def test_same_body_different_pins_is_an_ambiguous_transform():
    other = _xf("XF_OTHER", pins=(PinDef("1", "A", -3.54, -1.0, 0.0, 2.54, "passive"),
                                  PinDef("2", "B", 7.04, 1.0, 180.0, 2.54, "passive")))
    pi, cls, base, rep = run(draw(XF), [XF, other])
    (c,) = rep.candidates
    assert c.state == rc.UNKNOWN and c.reasons[-1].startswith("ambiguous_transform")
    assert {h["symbol"] for h in c.hits} == {"XF", "XF_OTHER"}               # alternatives are recorded


def test_mirror_symmetric_body_with_distinct_pins_is_ambiguous_identity():
    sym_body = (BodyPath(((-1.0, -2.0), (1.0, -2.0), (1.0, 2.0), (-1.0, 2.0)), True),
                BodyPath(((2.5, -2.0), (4.5, -2.0), (4.5, 2.0), (2.5, 2.0)), True))
    sym = _xf("XF_SYM", pins=(PinDef("1", "A", -3.54, 0.0, 0.0, 2.54, "input"),
                              PinDef("2", "B", 7.04, 0.0, 180.0, 2.54, "output")), body=sym_body)
    pi, cls, base, rep = run(draw(sym), [sym])
    (c,) = rep.candidates
    assert len(c.hits) > 1                                                  # equivalent transforms recorded
    assert c.state == rc.UNKNOWN and c.reasons[-1].startswith("ambiguous_identity")


def test_stacked_pins_are_not_promoted():
    st = _xf("XF_STACK", pins=(PinDef("1", "A", -3.54, 1.0, 0.0, 2.54, "passive"),
                               PinDef("3", "C", -3.54, 1.0, 0.0, 2.54, "passive"),
                               PinDef("2", "B", 7.04, -1.0, 180.0, 2.54, "passive")))
    pi, cls, base, rep = run(draw(st), [st])
    (c,) = rep.candidates
    assert c.state == rc.UNKNOWN


def test_pin_line_that_is_template_geometry_is_not_independent():
    tip, b = (-3.54, 1.0), (-1.0, 1.0)
    reuse = _xf("XF_REUSE", body=XF.body + (BodyPath((tip, b), False),))
    # the body itself draws the pin-1 line (body colour, touching the outline); no separate pin line for pin 1
    pi, cls, base, rep = run(draw(reuse, skip_pins=("1",)), [reuse])
    c = next(c for c in rep.candidates if c.identity["symbols"] == ["XF_REUSE"])
    assert c.state == rc.UNKNOWN
    assert [p.why for p in c.pins if p.numbers == ("1",)] == ["pin_line_reused_from_template"]


def test_short_text_like_strokes_never_become_candidates():
    # a glyph-sized copy of the part (strokes far below the page's minimum part size), pin lines included
    small = draw_symbol(XF, "r0", 0.05, (30.0, 40.0), body_w=0.05, pin_w=0.02)
    pi, cls, base, rep = run(small, [XF])
    assert not promotable(rep)
    assert all(c.state == rc.REJECTED for c in rep.candidates)


def test_too_small_gate_rejects_even_a_well_formed_candidate():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    c2 = _fresh(c)
    rc.gate(c2, pi, cls, rc._line_index(pi, cls), min_part=1e9)
    assert c2.state == rc.REJECTED and c2.reasons[-1].startswith("too_small")


def test_degenerate_point_geometry_does_not_crash_or_propose():
    pts = b"".join(f"0.9 w {x} 60 m {x} 60 l S\n".encode() for x in (200, 200.01, 200.5))
    pi, cls, base, rep = run(pts + b"0.9 w 220 60 m 220 60 l S 220 60 m 220 60 l S\n", [XF])
    assert rep.status == rc.STATUS_OK and not promotable(rep)


def test_degenerate_candidate_is_rejected_by_the_gate():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    c2 = _fresh(c)
    pt = next(q for q in pi.prims if q.id == c2.matched_prim_ids[0])
    pi2 = copy.deepcopy(pi)
    for q in pi2.prims:
        if q.id in c2.matched_prim_ids:
            q.pts = [pt.pts[0]] * len(q.pts)
    rc.gate(c2, pi2, cls, rc._line_index(pi2, cls), min_part=0.0)
    assert c2.state == rc.REJECTED and c2.reasons[-1].startswith("degenerate")


# ----------------------------------------------------------------------------------------------- conflicts

def _fresh(c, **kw):
    """The same proposal, back in PROPOSED (as built by propose), for re-running stages."""
    d = dict(c.__dict__)
    d.update(state=rc.PROPOSED, history=list(c.history[:1]), reasons=[], pins=())
    d.update(kw)
    return rc.RecoveryCandidate(**d)


def _supported(c, pi, cls, **kw):
    c2 = _fresh(c, **kw)
    rc.gate(c2, pi, cls, rc._line_index(pi, cls), min_part=0.0)
    assert c2.state == rc.EVIDENCE_SUPPORTED
    return c2


def test_two_candidates_claiming_the_same_terminal_are_both_unknown():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    a, b = _supported(c, pi, cls, id="A"), _supported(c, pi, cls, id="B")
    b.source_prim_ids = (10**6,)                    # disjoint groups, same physical terminals
    rc.resolve_conflicts([a, b], set(), [])
    assert a.state == b.state == rc.UNKNOWN
    assert a.reasons[-1].startswith("candidate_conflict") and b.reasons[-1].startswith("candidate_conflict")


def test_candidates_sharing_only_a_pin_line_are_both_unknown():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    a = _supported(c, pi, cls, id="A")
    b = _supported(c, pi, cls, id="B")
    b.source_prim_ids = (10**6,)
    b.pins = tuple(replace(p, predicted_tip=(p.predicted_tip[0] + 50, p.predicted_tip[1])) for p in b.pins)
    rc.resolve_conflicts([a, b], set(), [])
    assert a.state == b.state == rc.UNKNOWN


def test_terminal_already_claimed_by_a_baseline_pin_is_rejected():
    r = LIB.symbols["R"]
    tip1 = truth(XF)["1"][0]
    # place a resistor (resolved by the baseline) so that its pin-1 tip sits exactly on XF's pin-1 tip
    rp = next(p for p in r.pins if p.number == "1")
    m = ORIENTS["r0"]
    off = place(m, S, (0.0, 0.0), *rp.tip)
    t_r = (tip1[0] - off[0], (H - tip1[1]) - off[1])
    pi, cls, base, rep = run(draw(XF) + draw(r, t=t_r), [XF, r])
    assert any(cr.resolution.status == "UNIQUE" for cr in base)
    c = next(c for c in rep.candidates if c.identity["symbols"] == ["XF"])
    assert c.state == rc.REJECTED and c.reasons[-1].startswith("baseline_claim")


def test_baseline_claimed_primitives_reject_the_candidate():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    a = _supported(c, pi, cls)
    rc.resolve_conflicts([a], {a.pins[0].line_prim}, [])
    assert a.state == rc.REJECTED and "pin_line" in a.reasons[-1]


# ----------------------------------------------------------------------------------------------- provenance

def test_corrupted_geometry_is_rejected_by_the_audit():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    pid, pts = c.geometry[0]
    c.geometry = ((pid, ((pts[0][0] + 1.0, pts[0][1]),) + pts[1:]),) + c.geometry[1:]
    rc.reaudit(rep, pi, cls, [XF], base)
    assert c.state == rc.REJECTED and "geometry/hash mismatch" in c.reasons[-1]


@pytest.mark.parametrize("field,bad,expect", [
    ("geometry_sha256", "0" * 64, "geometry/hash mismatch"),
    ("matched_prim_ids", (10**6,), "matched prims not within source"),
    ("source_prim_ids", (10**6,), "source prim missing"),
    ("hits", (), "template re-match differs"),
])
def test_corrupted_lineage_fields_are_rejected(field, bad, expect):
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    setattr(c, field, bad)
    rc.reaudit(rep, pi, cls, [XF], base)
    assert c.state == rc.REJECTED and expect in c.reasons[-1]


@pytest.mark.parametrize("change,expect", [
    (lambda p: replace(p, line_prim=None), "no measured line"),
    (lambda p: replace(p, line_pts=((0.0, 0.0), (1.0, 1.0))), "recorded line geometry differs"),
    (lambda p: replace(p, orientation="REVERSED" if p.orientation == "FORWARD" else "FORWARD"), "orientation"),
    (lambda p: replace(p, tol=p.tol * 3), "tolerance wider than policy"),
    (lambda p: replace(p, predicted_tip=(p.predicted_tip[0] + 5, p.predicted_tip[1])), "predicted pins"),
])
def test_corrupted_pin_evidence_is_rejected(change, expect):
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    c.pins = (change(c.pins[0]),) + c.pins[1:]
    rc.reaudit(rep, pi, cls, [XF], base)
    assert c.state == rc.REJECTED and expect in c.reasons[-1]


def test_line_prim_pointing_at_a_template_primitive_is_rejected():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    c.pins = (replace(c.pins[0], line_prim=c.matched_prim_ids[0]),) + c.pins[1:]
    rc.reaudit(rep, pi, cls, [XF], base)
    assert c.state == rc.REJECTED


def test_lineage_gate_rejects_non_body_and_missing_prims():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    wire = next(q.id for q in pi.prims if cls.records[q.id].cls == "WIRE")
    for bad in ((wire,) + c.source_prim_ids, (10**6,) + c.source_prim_ids):
        c2 = _fresh(c, source_prim_ids=tuple(sorted(bad)))
        rc.gate(c2, pi, cls, rc._line_index(pi, cls), 0.0)
        assert c2.state == rc.REJECTED and c2.reasons[-1].startswith("lineage")
    # a WIRE prim with an otherwise consistent role lineage: only the BODY check can refuse it
    c4 = _fresh(c, source_prim_ids=tuple(sorted((wire,) + c.source_prim_ids)),
                discovery_prim_ids=tuple(sorted({wire} | set(c.discovery_prim_ids))),
                excluded_prim_ids=(wire,), operation=c.operation + "+RELAX")
    assert rc.evidence_role_problems(c4, pins=False) == []
    rc.gate(c4, pi, cls, rc._line_index(pi, cls), 0.0)
    assert c4.state == rc.REJECTED and "is not a BODY polyline" in c4.reasons[-1]
    c3 = _fresh(c, stripped_prim_ids=c.matched_prim_ids[:1])
    rc.gate(c3, pi, cls, rc._line_index(pi, cls), 0.0)
    assert c3.state == rc.REJECTED and c3.reasons[-1].startswith("lineage")


def test_baseline_that_is_not_the_recomputed_partition_blocks_recovery():
    pi, cls = page(draw(XF))
    base = detect_pins(pi, cls, [XF])
    forged = [replace_cluster(base[0], base[0].cluster.prim_ids + base[1].cluster.prim_ids)] + base[1:]
    rep = rc.recover(pi, cls, forged, [XF], eps=0.02)
    assert rep.status == rc.STATUS_BLOCKED and not rep.candidates and rep.problems
    rep2 = rc.recover(pi, cls, base[:1], [XF], eps=0.02)
    assert rep2.status == rc.STATUS_BLOCKED


def replace_cluster(cr, ids):
    cr2 = copy.copy(cr)
    cr2.cluster = replace(cr.cluster, prim_ids=tuple(ids))
    return cr2


# ----------------------------------------------------------------------------------------------- state machine

def test_state_machine_has_no_verified_and_rejects_illegal_transitions():
    assert "VERIFIED" not in rc.STATES and all("VERIFIED" not in v for v in rc.ALLOWED.values())
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    for bad in ("VERIFIED", rc.PROPOSED, rc.EVIDENCE_SUPPORTED):
        with pytest.raises(rc.IllegalTransition):
            c.to(bad, "x")
    c.to(rc.UNKNOWN, "demote")
    with pytest.raises(rc.IllegalTransition):
        c.to(rc.PROMOTABLE, "x")
    c.to(rc.REJECTED, "x")
    for s in rc.STATES:
        with pytest.raises(rc.IllegalTransition):
            c.to(s, "x")
    f = _fresh(c)
    with pytest.raises(rc.IllegalTransition):
        f.to(rc.PROMOTABLE, "skip the gate")


def test_every_candidate_ends_in_a_terminal_reported_state_with_reasons():
    d = LIB.symbols["D"]
    content = draw(XF) + draw(d, t=(60.0, 60.0)) + draw(d, t=(60.0, 60.0 + 7.62 + 0.5)) + draw(XF, t=(230.0, 60.0), pins=False)
    pi, cls, base, rep = run(content, [XF, d])
    assert rep.candidates
    for c in rep.candidates:
        assert c.state in (rc.PROMOTABLE, rc.UNKNOWN, rc.REJECTED)
        assert c.history[0][1] == rc.PROPOSED and c.reasons
        assert c.source_prim_ids and c.geometry and c.hits and c.frame == rc.FRAME and c.tolerances
    assert sum(rep.by_state().values()) == len(rep.candidates)


# ----------------------------------------------------------------------------------------------- invariance

def _scenes():
    d, r = LIB.symbols["D"], LIB.symbols["R"]
    syms = list(LIB.symbols.values()) + [XF]
    return {
        "split_body": (draw(XF), syms),
        "chained_diodes": (draw(d) + draw(d, t=(150.0, 100.0 + 7.62 + 0.5)), syms),
        "resolved_and_recovered": (draw(r, t=(60.0, 100.0)) + draw(XF), syms),
        "no_pin_lines": (draw(XF, pins=False) + draw(r, t=(60.0, 60.0)), syms),
        "baseline_only": (draw(r, t=(60.0, 100.0)) + draw(LIB.symbols["U4"], t=(200.0, 100.0)), syms),
    }


def _snapshot(pi, cls, clusters, built, res):
    pins = [(cr.cluster.id, cr.cluster.prim_ids, cr.resolution.status, cr.resolution.id_state,
             [(p.tip, p.base, p.numbers, p.pos_state, p.id_state, p.evidence, p.stub) for p in cr.pins],
             cr.unresolved, cr.conflicts) for cr in clusters]
    objs = sorted(o.id for o in built.graph.objects)
    pairs = {(a, b): res.pair_status(a, b) for a in objs for b in objs if a < b}
    return {"pins": pins, "registry": sorted(cls.registry.items.items()), "pairs": pairs,
            "edges": sorted(map(repr, res.final_edges)) if hasattr(res, "final_edges") else None,
            "fp": rc.baseline_fingerprint(pi, cls, clusters, built, res)}


@pytest.mark.parametrize("name", sorted(_scenes()))
def test_recovery_on_and_off_leave_the_baseline_byte_identical(name):
    content, syms = _scenes()[name]
    pi_off, _ = page(content)
    off = _snapshot(pi_off, *analyze_page(pi_off, syms))
    pi_on, _ = page(content)
    cls, clusters, built, res = analyze_page(pi_on, syms)
    before = _snapshot(pi_on, cls, clusters, built, res)
    rep = rc.recover_page(pi_on, cls, clusters, syms, guard_objs=(built, res))
    after = _snapshot(pi_on, cls, clusters, built, res)
    assert rep.status == rc.STATUS_OK
    assert before == after == off                 # clusters, pins, pin states, registry, connectivity, bytes


def test_recovered_candidates_never_enter_baseline_outputs():
    content, syms = _scenes()["resolved_and_recovered"]
    pi, _ = page(content)
    cls, clusters, built, res = analyze_page(pi, syms)
    rep = rc.recover_page(pi, cls, clusters, syms, guard_objs=(built, res))
    assert promotable(rep)
    cand_tips = {p.predicted_tip for c in promotable(rep) for p in c.pins}
    assert not any(near(t, tuple(bt), 1e-6) for t in cand_tips for bt in built.pin_tips.values())
    assert all(not str(k).startswith("RC") for k in cls.registry.items)


def test_guard_detects_a_recovery_that_mutates_the_baseline(monkeypatch):
    pi, cls = page(draw(XF))
    base = detect_pins(pi, cls, [XF])
    real = rc.propose

    def evil(pi_, cls_, baseline, *a, **k):
        baseline[0].pin_lines.append([])
        return real(pi_, cls_, baseline, *a, **k)

    monkeypatch.setattr(rc, "propose", evil)
    with pytest.raises(AssertionError, match="mutated the baseline"):
        rc.recover(pi, cls, base, [XF], eps=0.02)


def test_guard_detects_mutated_page_geometry(monkeypatch):
    pi, cls = page(draw(XF))
    base = detect_pins(pi, cls, [XF])
    real = rc.gate

    def evil(c, pi_, *a, **k):
        pi_.prims[0].pts[0] = (pi_.prims[0].pts[0][0] + 1e-9, pi_.prims[0].pts[0][1])
        return real(c, pi_, *a, **k)

    monkeypatch.setattr(rc, "gate", evil)
    with pytest.raises(AssertionError, match="mutated the baseline"):
        rc.recover(pi, cls, base, [XF], eps=0.02)


def test_recovery_is_deterministic():
    content, syms = _scenes()["chained_diodes"]
    outs = []
    for _ in range(2):
        pi, cls = page(content)
        base = detect_pins(pi, cls, syms)
        rep = rc.recover(pi, cls, base, syms, eps=0.02)
        outs.append([(c.id, c.state, c.source_prim_ids, c.pins, c.reasons) for c in rep.candidates])
    assert outs[0] == outs[1]


def test_touch_components_and_segment_distance():
    assert rc.seg_seg_distance((0, 0), (2, 2), (0, 2), (2, 0)) == 0.0
    assert math.isclose(rc.seg_seg_distance((0, 0), (1, 0), (0, 1), (1, 1)), 1.0)
    assert math.isclose(rc.seg_seg_distance((0, 0), (0, 0), (3, 4), (3, 4)), 5.0)


def test_candidates_sharing_only_a_terminal_position_are_both_unknown():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    a, b = _supported(c, pi, cls, id="A"), _supported(c, pi, cls, id="B")
    b.source_prim_ids = (10**6,)
    b.pins = tuple(replace(p, line_prim=10**7 + p.index) for p in b.pins)     # different lines, same terminals
    rc.resolve_conflicts([a, b], set(), [])
    assert a.state == b.state == rc.UNKNOWN


def test_recover_rejects_a_candidate_whose_audit_fails(monkeypatch):
    monkeypatch.setattr(rc, "audit_candidate", lambda *a, **k: ["forced disagreement"])
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = rep.candidates
    assert c.state == rc.REJECTED and c.reasons[-1] == "audit: forced disagreement"


def _tampered_identity(c, pins):
    idn = dict(c.identity)
    idn.update(id_state="CONFIRMED", pins=pins)
    # the retained alternatives are tampered consistently, so the gate stage under test is the one that decides
    hits = tuple(dict(h, pins=tuple((n[0], tip, base) for tip, base, n in pins)) for h in c.hits)
    return _fresh(c, identity=idn, hits=hits)


def test_stacked_terminals_are_unknown_even_with_a_confirmed_identity():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    tip, b, _ = c.identity["pins"][0]
    c2 = _tampered_identity(c, [(tip, b, ("1",)), (tip, b, ("3",))] + list(c.identity["pins"][1:]))
    rc.gate(c2, pi, cls, rc._line_index(pi, cls), 0.0)
    assert c2.state == rc.UNKNOWN and c2.reasons[-1].startswith("stacked_pins")


def test_one_line_supporting_two_terminals_is_unknown():
    pi, cls, base, rep = run(draw(XF), [XF])
    (c,) = promotable(rep)
    tip, b, _ = c.identity["pins"][0]
    d = 0.6                                        # > template tau (not stacked), < pin-line tol (same line fits)
    assert c.tolerances["template_tau"] < d < s2_tolerance(math.dist(tip, b))
    shifted = ((tip[0], tip[1] + d), (b[0], b[1] + d), ("3",))
    c2 = _tampered_identity(c, [(tip, b, ("1",)), shifted] + list(c.identity["pins"][1:]))
    rc.gate(c2, pi, cls, rc._line_index(pi, cls), 0.0)
    assert c2.state == rc.UNKNOWN and c2.reasons[-1].startswith("duplicate_pin_line")


def test_deadline_counts_dropped_proposals_instead_of_losing_them():
    pi, cls = page(draw(XF))
    base = detect_pins(pi, cls, [XF])
    rep = rc.recover(pi, cls, base, [XF], eps=0.02, deadline=0.0)
    assert rep.status == rc.STATUS_OK and not rep.candidates
    assert rep.stats["budget_deadline_dropped"] == rep.stats["proposals"] > 0
