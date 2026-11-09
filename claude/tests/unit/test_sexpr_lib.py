from pathlib import Path
import pytest

from ecci.kicad_lib import LibraryError, load_library
from ecci.sexpr import SexprError, num, parse

LIB = str(Path(__file__).resolve().parents[1] / "fixtures" / "lib_min.kicad_sym")


def test_parse_nested_quotes_and_escapes():
    t = parse('(a (b "x y" 1.5) (c "q\\"z") d)')
    assert t == ["a", ["b", "x y", "1.5"], ["c", 'q"z'], "d"]


@pytest.mark.parametrize("bad", ["", "(a", "a)", "(a))", '(a "x', "(a) (b)", "x", "(" * 300 + ")" * 300])
def test_parse_rejects_malformed(bad):
    with pytest.raises(SexprError):
        parse(bad)


@pytest.mark.parametrize("v", ["nan", "inf", "x", None])
def test_num_rejects_non_finite_and_garbage(v):
    with pytest.raises(SexprError):
        num(v)


def test_library_symbols_pins_and_token():
    lib = load_library(LIB)
    assert set(lib.symbols) == {"R", "R_LONG", "D", "U4", "OSC", "LED", "C"}
    assert len(lib.sha256) == 64 and not lib.warnings
    r = lib.symbols["R"]
    assert r.lib_token == f"lib:lib_min.kicad_sym@{lib.sha256[:12]}"
    p1 = next(p for p in r.pins if p.number == "1")
    assert p1.tip == (0.0, 3.81) and p1.length == 1.27
    bx, by = p1.base                        # angle 270 => from tip toward body (down)
    assert abs(bx) < 1e-9 and abs(by - 2.54) < 1e-9
    u4 = {p.number: p for p in lib.symbols["U4"].pins}
    assert u4["3"].tip == (7.62, -2.54) and abs(u4["3"].base[0] - 5.08) < 1e-9


def test_library_circle_and_arc_are_smooth_paths():
    osc = load_library(LIB).symbols["OSC"]
    assert any(b.smooth and b.closed for b in osc.body) and any(b.smooth and not b.closed for b in osc.body)
    assert len(osc.corners()) == 2          # only the arc end points count as corners


def test_missing_and_garbage_library(tmp_path):
    with pytest.raises(LibraryError):
        load_library(str(tmp_path / "nope.kicad_sym"))
    bad = tmp_path / "bad.kicad_sym"
    bad.write_text("(not_a_library)")
    with pytest.raises(LibraryError):
        load_library(str(bad))
    bad.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(LibraryError):
        load_library(str(bad))


def test_broken_symbol_is_skipped_with_warning_not_crash(tmp_path):
    f = tmp_path / "l.kicad_sym"
    f.write_text('(kicad_symbol_lib (symbol "BAD" (symbol "BAD_0_1" (rectangle (start x 0) (end 1 1))))'
                 ' (symbol "OK" (symbol "OK_0_1" (rectangle (start 0 0) (end 1 1)))))')
    lib = load_library(str(f))
    assert set(lib.symbols) == {"OK"} and any("BAD" in w for w in lib.warnings)


MULTI = ('(kicad_symbol_lib (symbol "M" (symbol "M_0_1" (rectangle (start 0 0) (end 1 1)))'
         ' (symbol "M_1_1" (pin passive line (at 0 0 0) (length 1) (name "a") (number "1")))'
         ' (symbol "M_2_1" (pin passive line (at 0 2 0) (length 1) (name "b") (number "9")))'
         ' (symbol "M_2_2" (pin passive line (at 5 5 0) (length 1) (name "c") (number "7")))))')


def test_each_unit_is_its_own_template_sharing_the_common_graphics(tmp_path):
    f = tmp_path / "l.kicad_sym"
    f.write_text(MULTI)
    lib = load_library(str(f))
    assert set(lib.symbols) == {"M", "M#u2"}
    assert [p.number for p in lib.symbols["M"].pins] == ["1"]
    assert [p.number for p in lib.symbols["M#u2"].pins] == ["9"]       # De Morgan style 2 pin "7" not included
    assert len(lib.symbols["M"].body) == len(lib.symbols["M#u2"].body) == 1
    assert any("De Morgan" in w for w in lib.warnings)
