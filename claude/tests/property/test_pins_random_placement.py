from pathlib import Path
"""Random placements of library symbols: detected pins must agree with the placement oracle.

Oracle = the placement itself (render_kicad.expected_tips_norm), not any ecci output.
Claims checked per case:
  * every true pin number has a detected pin whose tip is within tolerance and which lists
    that number among its candidates (never a wrong assignment);
  * positions are CONFIRMED (two independent sources) because stubs are drawn;
  * id_state is CONFIRMED exactly for geometrically asymmetric symbols (D, OSC) and AMBIGUOUS
    for symmetric ones (R, U4): pin numbers are then NOT guessed.
"""

import math

from hypothesis import HealthCheck, assume, given, settings, strategies as st

from ecci.classify import classify_page
from ecci.ingest_vector import ingest_pdf
from ecci.kicad_lib import Library, load_library
from ecci.pins import POS_CONFIRMED, detect_pins
from pdfgen import one_page
from render_kicad import ORIENTS, draw_symbol, expected_tips_norm

ALL = load_library(str(Path(__file__).resolve().parents[1] / "fixtures" / "lib_min.kicad_sym"))
LIB = Library(ALL.path, ALL.sha256, {k: v for k, v in ALL.symbols.items() if k != "R_LONG"})
W, H = 300, 200
WIRES = b"0.45 w 10 190 m 80 190 l S 10 185 m 10 150 l S 90 185 m 90 150 l S\n"
EXPECT_ID = {"R": "AMBIGUOUS", "U4": "AMBIGUOUS", "D": "CONFIRMED", "OSC": "CONFIRMED"}


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.sampled_from(sorted(EXPECT_ID)), st.sampled_from(sorted(ORIENTS)),
       st.floats(2.0, 6.0), st.floats(70.0, 230.0), st.floats(70.0, 130.0))
def test_random_placement_matches_oracle(name, orient, s, tx, ty):
    sym = LIB.symbols[name]
    content = draw_symbol(sym, orient, s, (tx, ty), body_w=0.9, pin_w=0.45)
    pi = ingest_pdf(one_page(WIRES + content, size=(W, H))).pages[0]
    # the fixed wires are scenery: skip placements whose symbol would touch them (a wire crossing a body is
    # a different scene, reported as UNEXPECTED_CONTACT by design), not placements the detector gets wrong
    from ecci.flatten import point_segment_distance
    wsegs = [(a, b) for p in pi.prims[:3] for _, a, b in p.segments()]
    assume(all(point_segment_distance(q, a, b) > 3.0 for p in pi.prims[3:] for q in p.pts for a, b in wsegs))
    results = detect_pins(pi, classify_page(pi), list(LIB.symbols.values()))
    assert len(results) == 1, [r.cluster.bbox for r in results]
    cr = results[0]
    assert cr.resolution.status == "UNIQUE" and not cr.conflicts and not cr.unresolved
    exp = expected_tips_norm(sym, orient, s, (tx, ty), H)
    tol = 0.05 + 0.02 * s * 10
    assert len(cr.pins) == len(sym.pins)
    for pin in cr.pins:
        assert pin.pos_state == POS_CONFIRMED and pin.id_state == EXPECT_ID[name]
    for number, tip in exp.items():
        match = [p for p in cr.pins if math.hypot(p.tip[0] - tip[0], p.tip[1] - tip[1]) <= tol]
        assert len(match) == 1, (name, orient, number)
        assert number in match[0].numbers
        if EXPECT_ID[name] == "CONFIRMED":
            assert match[0].numbers == (number,)
