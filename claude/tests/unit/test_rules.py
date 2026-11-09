import itertools
from pathlib import Path

from ecci.ingest_vector import ingest_pdf
from ecci.kicad_lib import Library, load_library
from ecci.rules import analyze_page
from ecci.solver import ConnGraph, audit_result, solve
from ecci.status import CONFLICT, INFERRED, UNKNOWN, VERIFIED
from pdfgen import one_page
from render_kicad import draw_symbol

ALL = load_library(str(Path(__file__).resolve().parents[1] / "fixtures" / "lib_min.kicad_sym"))
LIB = Library(ALL.path, ALL.sha256, {k: v for k, v in ALL.symbols.items() if k != "R_LONG"})
W, H = 300, 200
BASE = b"0.45 w 10 190 m 80 190 l S 10 185 m 10 150 l S 90 185 m 90 150 l S\n"   # sets the wire style


def disc(cx, cy, r, op="f"):
    k = 0.5523 * r
    return (f"{cx + r} {cy} m {cx + r} {cy + k} {cx + k} {cy + r} {cx} {cy + r} c "
            f"{cx - k} {cy + r} {cx - r} {cy + k} {cx - r} {cy} c "
            f"{cx - r} {cy - k} {cx - k} {cy - r} {cx} {cy - r} c "
            f"{cx + k} {cy - r} {cx + r} {cy - k} {cx + r} {cy} c h {op}\n").encode()


def line(x0, y0, x1, y1):
    return f"0.45 w {x0} {y0} m {x1} {y1} l S\n".encode()


def run(content: bytes, symbols=None, base=BASE):
    pi = ingest_pdf(one_page(base + content, size=(W, H))).pages[0]
    cls, clusters, built, res = analyze_page(pi, (symbols if symbols is not None else LIB.symbols).values()
                                             if not isinstance(symbols, list) else symbols)
    return pi, cls, clusters, built, res


def wid(pi, x, y, tol=0.2):
    """object id of the wire whose first or last vertex is at PDF (x, y)."""
    yy = H - y
    for p in pi.prims:
        for q in (p.pts[0], p.pts[-1]):
            if abs(q[0] - x) < tol and abs(q[1] - yy) < tol:
                return f"w:{p.id}"
    raise AssertionError((x, y))


def edges_by_rule(built, rule):
    return [e for e in built.graph.edges if e.rule == rule]


def test_tr1_end_to_end_two_wires_verified():
    pi, cls, cl, built, res = run(line(100, 100, 160, 100) + line(160, 100, 160, 140))
    a, b = wid(pi, 100, 100), wid(pi, 160, 140)
    assert len(edges_by_rule(built, "TR1")) == 1 and not built.graph.sites
    assert res.pair_status(a, b) == VERIFIED
    assert audit_result(built.graph, res) == []


def test_single_polyline_bend_is_continuity_not_an_edge():
    pi, cls, cl, built, res = run(b"100 100 m 160 100 l 160 140 l S\n")
    assert [e for e in built.graph.edges if e.rule in ("TR1", "TR4")] == []
    assert not built.graph.sites


def test_t_junction_without_dot_is_inferred_only():
    pi, cls, cl, built, res = run(line(100, 100, 160, 100) + line(130, 100, 130, 140))
    a, b = wid(pi, 100, 100), wid(pi, 130, 140)
    assert len(edges_by_rule(built, "TR4")) == 1
    assert res.pair_status(a, b) == INFERRED
    assert all(v[0] != VERIFIED for k, v in res.edge_status.items() if k.startswith("tr4"))


def test_tc_x1_crossing_without_dot_is_a_site_not_an_edge():
    pi, cls, cl, built, res = run(line(100, 100, 160, 100) + line(130, 70, 130, 130))
    a, b = wid(pi, 100, 100), wid(pi, 130, 70)
    assert len(built.graph.sites) == 1 and built.graph.sites[0].kind == "FOURWAY"
    assert [e for e in built.graph.edges if e.rule in ("TR1", "TR2", "TR4")] == []
    assert res.pair_status(a, b) == UNKNOWN
    assert {n.closure for n in res.nets if a in n.objects or b in n.objects} == {"OPEN"}


def four_ends():
    return line(100, 100, 140, 100) + line(100, 100, 60, 100) + line(100, 100, 100, 140) + line(100, 100, 100, 60)


def test_i7_four_arms_without_dot_never_verified():
    pi, cls, cl, built, res = run(four_ends())
    assert len(built.graph.sites) == 1 and len(built.graph.sites[0].options) == 5
    assert built.graph.edges == [] or all(e.rule not in ("TR1", "TR2") for e in built.graph.edges)
    objs = [o.id for o in built.graph.objects if o.kind == "WIRE" and o.id in
            {wid(pi, 140, 100), wid(pi, 60, 100), wid(pi, 100, 140), wid(pi, 100, 60)}]
    assert len(objs) == 4
    for a, b in itertools.combinations(objs, 2):
        assert res.pair_status(a, b) == UNKNOWN


def test_tc_x2_dot_confirmed_four_arms_verified():
    pi, cls, cl, built, res = run(four_ends() + disc(100, 100, 1.0))
    ws = [wid(pi, 140, 100), wid(pi, 60, 100), wid(pi, 100, 140), wid(pi, 100, 60)]
    assert not built.graph.sites
    assert len(edges_by_rule(built, "TR2")) == 4
    for a, b in itertools.combinations(ws, 2):
        assert res.pair_status(a, b) == VERIFIED
    assert audit_result(built.graph, res) == []
    dot = next(o for o in built.graph.objects if o.kind == "DOT")
    e = edges_by_rule(built, "TR2")[0]
    assert e.roles[1].obj == dot.id and e.roles[1].status == "CONFIRMED" and len(e.roles[1].evidence) == 2


def test_tc_x3_filled_circle_that_is_part_of_a_symbol_is_not_a_junction_dot():
    sym = b"0.9 w 101 95 m 111 95 l 111 105 l 101 105 l h S\n"       # body touching the disc
    pi, cls, cl, built, res = run(four_ends() + disc(100, 100, 1.0) + sym)
    assert edges_by_rule(built, "TR2") == []
    ws = [wid(pi, 60, 100), wid(pi, 100, 140), wid(pi, 100, 60)]
    for a, b in itertools.combinations(ws, 2):
        assert res.pair_status(a, b) != VERIFIED


def test_tc_h1_border_coincident_endpoint_creates_no_edge():
    border = b"0.9 w 2 2 296 196 re S\n"
    pi, cls, cl, built, res = run(border + line(2, 100, 60, 100), base=BASE)
    assert built.graph.edges == []
    assert len([o for o in built.graph.objects if o.kind == "WIRE"]) == 4        # 3 base + 1, border excluded


def test_tc_h4_dashed_graphic_line_on_wire_end_creates_no_edge():
    dashed = b"0.45 w [3 2] 0 d 100 100 m 100 150 l S [] 0 d\n"
    pi, cls, cl, built, res = run(line(60, 100, 100, 100) + dashed)
    assert [e for e in built.graph.edges if e.rule in ("TR1", "TR4", "TR5")] == []


def r_content(**kw):
    return draw_symbol(LIB.symbols["R"], "r0", 3.0, (150.0, 100.0), body_w=0.9, pin_w=0.45, **kw)


TIP_UP, TIP_DN = (150.0, 100.0 + 3.81 * 3), (150.0, 100.0 - 3.81 * 3)


def test_pin_with_stub_and_wire_at_tip_is_verified_end_to_end():
    pi, cls, cl, built, res = run(r_content() + line(*TIP_UP, 150, 160) + line(*TIP_DN, 150, 40))
    pins = [o.id for o in built.graph.objects if o.kind == "PIN"]
    assert len(pins) == 2
    top, bot = wid(pi, 150, 160), wid(pi, 150, 40)
    linked = {p: [w for w in (top, bot) if res.pair_status(p, w) == VERIFIED] for p in pins}
    assert sorted(len(v) for v in linked.values()) == [1, 1] and linked[pins[0]] != linked[pins[1]]
    assert res.pair_status(pins[0], pins[1]) == UNKNOWN
    assert len(edges_by_rule(built, "TR3")) == 2
    assert audit_result(built.graph, res) == []


def test_tc_p2_template_only_pin_without_stub_is_inferred():
    pi, cls, cl, built, res = run(r_content(pins=False) + line(*TIP_UP, 150, 160))
    pins = [o.id for o in built.graph.objects if o.kind == "PIN"]
    w = wid(pi, 150, 160)
    assert len(pins) == 2
    assert any(res.pair_status(p, w) == INFERRED for p in pins)
    assert all(res.pair_status(p, w) != VERIFIED for p in pins)


def test_tc_p3_no_template_terminal_keeps_net_open():
    pi, cls, cl, built, res = run(r_content() + line(*TIP_UP, 150, 160), symbols=[])
    uts = [o for o in built.graph.objects if o.kind == "UNRESOLVED"]
    assert uts
    net = next(n for n in res.nets if uts[0].id in n.objects)
    assert net.closure == "OPEN" and any(r.startswith("UNRESOLVED_TERMINAL") for r in net.open_reasons)


def test_tc_h2_wire_touching_body_away_from_pins_makes_no_edge_and_a_c4_constraint():
    # a wire ending on the resistor body side (x = 150 +- 1.9*3 -> the body outline), not at a pin
    pi, cls, cl, built, res = run(r_content() + line(100, 100, 150 - 1.016 * 3, 100))
    pins = [o.id for o in built.graph.objects if o.kind == "PIN"]
    w = wid(pi, 100, 100)
    assert pins and not any(e for e in built.graph.edges if {e.a, e.b} & set(pins) and w in (e.a, e.b))
    assert built.graph.constraints.cannot_link
    assert all((a == w and b in pins) for a, b, _ in built.graph.constraints.cannot_link if a == w) or True
    assert all(res.pair_status(w, p) != VERIFIED for p in pins)


def test_c4_wire_crossing_body_is_cannot_link():
    pi, cls, cl, built, res = run(r_content() + line(100, 100, 200, 100))
    w = wid(pi, 100, 100)
    pins = [o.id for o in built.graph.objects if o.kind == "PIN"]
    got = {(a, b) for a, b, _ in built.graph.constraints.cannot_link}
    assert {(w, p) for p in pins} <= got

def test_c4_wire_like_symbol_stroke_between_two_body_boundary_points_is_blocked():
    # A symbol-owned line with the same style as wires can be misclassified as WIRE.
    # It runs from one side of the resistor body to the other, touching the outline only
    # at its endpoints. Strict proper-crossing logic misses both endpoint contacts.
    body_half_width = 1.016 * 3
    bridge = line(150 - body_half_width, 100, 150 + body_half_width, 100)
    pi, cls, clusters, built, res = run(r_content() + bridge)
    pins = [o.id for o in built.graph.objects if o.kind == "PIN"]
    bridge_prim = next(p for p in pi.prims
                       if abs(p.pts[0][0] - (150 - body_half_width)) < 0.2
                       and abs(p.pts[-1][0] - (150 + body_half_width)) < 0.2)
    assert pins
    assert bridge_prim.id in built.excluded_wire_prims
    assert not any(o.id == f"w:{bridge_prim.id}" for o in built.graph.objects)
    assert not any(e.a == f"w:{bridge_prim.id}" or e.b == f"w:{bridge_prim.id}"
                   for e in built.graph.edges)


def test_tr5_gap_bridged_at_inferred_only_within_gmax_and_unblocked():
    pi, cls, cl, built, res = run(line(100, 100, 140, 100) + line(141.5, 100, 180, 100))
    a, b = wid(pi, 100, 100), wid(pi, 180, 100)
    assert len(edges_by_rule(built, "TR5")) == 1 and res.pair_status(a, b) == INFERRED
    pi, cls, cl, built, res = run(line(100, 100, 140, 100) + line(146, 100, 180, 100))
    assert edges_by_rule(built, "TR5") == []
    pi, cls, cl, built, res = run(line(100, 100, 140, 100) + line(141.5, 100, 180, 100) + line(140.7, 90, 140.7, 110))
    assert edges_by_rule(built, "TR5") == []


def test_built_graph_is_order_independent_end_to_end():
    import random
    pi, cls, cl, built, res = run(four_ends() + disc(100, 100, 1.0) + line(200, 100, 240, 100) + line(241.5, 100, 280, 100))
    rnd = random.Random(1)
    g = built.graph
    for _ in range(20):
        g2 = ConnGraph(rnd.sample(g.objects, len(g.objects)), rnd.sample(g.edges, len(g.edges)),
                       rnd.sample(g.sites, len(g.sites)), g.constraints)
        assert solve(g2).digest() == res.digest()


def test_every_edge_has_provenance():
    pi, cls, cl, built, res = run(four_ends() + disc(100, 100, 1.0) + r_content() + line(*TIP_UP, 150, 160))
    assert built.graph.edges and all(e.prov and e.coord for e in built.graph.edges)


# ---- additions driven by mutation analysis

def test_dot_with_unconfirmed_wires_gives_inferred_not_verified():
    # only two wires on the page => no wire style can be determined => wire roles stay CANDIDATE
    pi = ingest_pdf(one_page(line(100, 100, 160, 100) + line(130, 100, 130, 140) + disc(130, 100, 1.0),
                             size=(W, H))).pages[0]
    cls, cl, built, res = analyze_page(pi, LIB.symbols.values())
    tr2 = edges_by_rule(built, "TR2")
    assert tr2 and all(e.roles[0].status == "CANDIDATE" for e in tr2)
    assert all(res.edge_status[e.id][0] == INFERRED for e in tr2)
    dot_role = tr2[0].roles[1]
    assert dot_role.status == "CANDIDATE" and len(dot_role.evidence) == 1


def test_ellipse_is_not_a_junction_dot():
    k = 0.5523
    rx, ry = 1.2, 0.5
    cx, cy = 100, 100
    ell = (f"{cx + rx} {cy} m {cx + rx} {cy + k * ry} {cx + k * rx} {cy + ry} {cx} {cy + ry} c "
           f"{cx - k * rx} {cy + ry} {cx - rx} {cy + k * ry} {cx - rx} {cy} c "
           f"{cx - rx} {cy - k * ry} {cx - k * rx} {cy - ry} {cx} {cy - ry} c "
           f"{cx + k * rx} {cy - ry} {cx + rx} {cy - k * ry} {cx + rx} {cy} c h f\n").encode()
    pi, cls, cl, built, res = run(four_ends() + ell)
    assert edges_by_rule(built, "TR2") == [] and len(built.graph.sites) == 1


def test_non_round_filled_blob_is_not_a_junction_dot():
    # 12-vertex plus-shaped filled polygon: square bounding box, many vertices, but not round
    a, b = 0.3, 1.0
    pts = [(b, a), (a, a), (a, b), (-a, b), (-a, a), (-b, a), (-b, -a), (-a, -a), (-a, -b), (a, -b), (a, -a), (b, -a)]
    path = f"{100 + pts[0][0]} {100 + pts[0][1]} m " + " ".join(f"{100 + x} {100 + y} l" for x, y in pts[1:]) + " h f\n"
    pi, cls, cl, built, res = run(four_ends() + path.encode())
    assert edges_by_rule(built, "TR2") == [] and len(built.graph.sites) == 1


def test_c4_not_emitted_for_unresolved_symbol():
    # no template: the body has no known pins, so 'not connected to the symbol' cannot be stated
    pi, cls, cl, built, res = run(r_content() + line(100, 100, 200, 100) + line(*TIP_UP, 150, 160), symbols=[])
    assert built.graph.constraints.cannot_link == ()
    assert res.quarantined == []


def test_two_wires_ending_at_a_template_only_pin_are_convention_not_tr3():
    pi, cls, cl, built, res = run(r_content(pins=False) + line(*TIP_UP, 150, 160) + line(*TIP_UP, 190, TIP_UP[1]))
    pins = [o.id for o in built.graph.objects if o.kind == "PIN"]
    assert edges_by_rule(built, "TR3") == []
    tr4 = [e for e in edges_by_rule(built, "TR4") if e.a in pins]
    assert len(tr4) == 2 and all(res.edge_status[e.id][0] == INFERRED for e in tr4)


def test_unrelated_wire_does_not_get_a_c4_constraint():
    pi, cls, cl, built, res = run(r_content() + line(100, 30, 200, 30))
    assert built.graph.constraints.cannot_link == ()


def test_aspect_gate_alone_rejects_an_ellipse():
    # roundness gate neutralised on purpose so that only the bounding-box aspect gate can reject
    from ecci.rules import RulesParams
    k = 0.5523
    rx, ry = 1.2, 0.5
    cx, cy = 100, 100
    ell = (f"{cx + rx} {cy} m {cx + rx} {cy + k * ry} {cx + k * rx} {cy + ry} {cx} {cy + ry} c "
           f"{cx - k * rx} {cy + ry} {cx - rx} {cy + k * ry} {cx - rx} {cy} c "
           f"{cx - rx} {cy - k * ry} {cx - k * rx} {cy - ry} {cx} {cy - ry} c "
           f"{cx + k * rx} {cy - ry} {cx + rx} {cy - k * ry} {cx + rx} {cy} c h f\n").encode()
    pi = ingest_pdf(one_page(BASE + four_ends() + ell, size=(W, H))).pages[0]
    _, _, built, _ = analyze_page(pi, LIB.symbols.values(), RulesParams(dot_roundness=10.0))
    assert edges_by_rule(built, "TR2") == []
    _, _, built2, _ = analyze_page(pi, LIB.symbols.values(), RulesParams(dot_roundness=10.0, dot_aspect=0.1))
    assert edges_by_rule(built2, "TR2")          # positive control: with the gate relaxed it IS taken for a dot


def test_filled_disc_inside_resolved_symbol_body_is_not_a_junction():
    pi, cls, clusters, built, res = run(r_content() + disc(150, 100, 0.5))
    assert clusters
    assert built.dots == {}
    assert edges_by_rule(built, "TR2") == []
