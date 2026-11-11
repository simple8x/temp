"""Random layouts: text rows and symbols in separate bands. Ground truth = where it was drawn.

Claims: every primitive drawn in the text band is TEXT; nothing outside it is TEXT; the symbols
are still found as exactly one cluster each with CONFIRMED pin positions (text never disturbs them).
"""

from pathlib import Path

from hypothesis import HealthCheck, given, settings, strategies as st

from ecci.classify import LC_TEXT, classify_page
from ecci.ingest_vector import ingest_pdf
from ecci.kicad_lib import Library, load_library
from ecci.pins import POS_CONFIRMED, detect_pins
from pdfgen import one_page
from render_kicad import draw_symbol
from strokefont import text_content

ALL = load_library(str(Path(__file__).resolve().parents[1] / "fixtures" / "lib_min.kicad_sym"))
LIB = Library(ALL.path, ALL.sha256, {k: v for k, v in ALL.symbols.items() if k != "R_LONG"})
W, H = 300, 200
WIRES = b"0.45 w 10 190 m 80 190 l S 10 185 m 10 150 l S 90 185 m 90 150 l S\n"
SLOTS = (60.0, 150.0, 240.0)
TEXT_BAND_NORM = (125.0, 185.0)           # y-down band holding all text (user y 15..75)
word = st.text(alphabet="REDLTHK201", min_size=3, max_size=6)


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.sampled_from(["R", "D", "OSC", "C", "LED"]), min_size=1, max_size=3),
       st.lists(st.tuples(word, st.floats(20.0, 130.0), st.floats(5.0, 8.0), st.sampled_from([22.0, 42.0, 62.0])),
                min_size=0, max_size=3, unique_by=lambda t: t[3]),     # one word per text line: no overprinting
       st.sampled_from(["r0", "r90", "r180", "r270"]))
def test_text_band_is_text_and_symbols_survive(symbols, words, orient):
    content = WIRES
    for slot, name in zip(SLOTS, symbols):
        content += draw_symbol(LIB.symbols[name], orient, 3.0, (slot, 105.0), body_w=0.9, pin_w=0.45)
    for w, x, h, y in words:
        content += text_content(w, x, y, h)
    pi = ingest_pdf(one_page(content, size=(W, H))).pages[0]
    cls = classify_page(pi)
    lo, hi = TEXT_BAND_NORM
    for p in pi.prims:
        cy = sum(q[1] for q in p.pts) / len(p.pts)
        is_text = cls.records[p.id].cls == LC_TEXT
        assert is_text == (lo <= cy <= hi), (p.id, cy, cls.records[p.id].cls)
    results = detect_pins(pi, cls, list(LIB.symbols.values()))
    assert len(results) == len(symbols)
    assert all(cr.unresolved == [] and cr.conflicts == [] for cr in results)
    assert all(p.pos_state == POS_CONFIRMED for cr in results for p in cr.pins)
