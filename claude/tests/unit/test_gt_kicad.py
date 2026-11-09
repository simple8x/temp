"""Plumbing tests for the ground-truth reader. They check parsing, filtering and selection logic.
They cannot prove the coordinate convention (that would be circular); `calibrate` does that on real
KiCad output in CI and its counts are printed in the gt_eval log."""
import math

from ecci.gt_kicad import MM_TO_PT, _place, calibrate, embedded_library_text, gt_pins, parse_netlist_xml, read_sch
from ecci.kicad_lib import load_library

SCH = """(kicad_sch (version 20250610) (generator "eeschema") (paper "A4")
 (lib_symbols
  (symbol "Device:R" (property "Reference" "R" (at 0 0 0))
   (symbol "R_0_1" (rectangle (start -1 2.5) (end 1 -2.5) (stroke (width 0.2) (type default)) (fill (type none))))
   (symbol "R_1_1"
    (pin passive line (at 1 3.81 270) (length 1.27) (name "~" (effects (font (size 1 1)))) (number "1" (effects (font (size 1 1)))))
    (pin passive line (at 0 -3.81 90) (length 1.27) (hide yes) (name "~" (effects (font (size 1 1)))) (number "2" (effects (font (size 1 1)))))))
  (symbol "power:GND" (symbol "GND_1_1" (pin power_in line (at 0 0 270) (length 0) (name "GND") (number "1")))))
 (symbol (lib_id "Device:R") (at 50 50 90) (mirror x) (unit 1) (property "Reference" "R1" (at 0 0 0)))
 (symbol (lib_id "power:GND") (at 60 60 0) (property "Reference" "#PWR01" (at 0 0 0)))
 (wire (pts (xy 1 1) (xy 2 2))))
"""
XML = """<export><nets>
 <net code="1" name="/A"><node ref="R1" pin="1"/><node ref="#PWR01" pin="1"/></net>
 <net code="2" name="/B"><node ref="R1" pin="2"/></net></nets></export>"""


def files(tmp_path, order="MR", wire=True):
    s, x = tmp_path / "a.kicad_sch", tmp_path / "a.xml"
    wx, wy = _place(1.0, 3.81, (50.0, 50.0, 90.0), "x", order)
    s.write_text(SCH.replace("(wire (pts (xy 1 1) (xy 2 2))))",
                             f"(wire (pts (xy {wx} {wy}) (xy {wx + 5} {wy}))))" if wire else ")"))
    x.write_text(XML)
    return str(s), str(x)


def test_netlist_skips_power_refs(tmp_path):
    _, x = files(tmp_path)
    assert parse_netlist_xml(x) == {("R1", "1"): "/A", ("R1", "2"): "/B"}


def test_pins_hidden_flag_power_skipped_and_net_attached(tmp_path):
    s, x = files(tmp_path)
    pins, info = gt_pins(s, x)
    assert sorted(p.number for p in pins) == ["1", "2"]            # power symbol skipped
    by = {p.number: p for p in pins}
    assert by["1"].net == "/A" and by["2"].net == "/B"
    assert by["2"].hidden and not by["1"].hidden
    assert info["placed"] == 2 and info["order"] in ("MR", "RM")


def test_instance_order_follows_the_wire_that_decides_it(tmp_path):
    for order in ("MR", "RM"):
        s, x = files(tmp_path, order)
        pins, _ = gt_pins(s, x)
        p1 = next(p for p in pins if p.number == "1")
        ex, ey = _place(1.0, 3.81, (50.0, 50.0, 90.0), "x", order)
        assert math.isclose(p1.x, ex * MM_TO_PT) and p1.net == "/A"


def test_instance_with_undecidable_order_carries_no_net(tmp_path):
    s, x = files(tmp_path, wire=False)
    pins, info = gt_pins(s, x)
    assert all(p.net is None for p in pins) and info["ambiguous_instances"] == 1


def test_place_rotation_without_mirror_is_ccw_in_y_up_then_flipped():
    x, y = _place(1.0, 3.81, (50.0, 50.0, 90.0), None, "MR")
    assert math.isclose(x, 50 - 3.81, abs_tol=1e-9) and math.isclose(y, 50.0 - 1.0, abs_tol=1e-9)
    x, y = _place(1.0, 3.81, (50.0, 50.0, 0.0), None, "MR")
    assert math.isclose(x, 51.0) and math.isclose(y, 50 - 3.81)     # y flips: lib up = schematic up


def test_calibration_selects_the_order_that_hits_wire_ends(tmp_path):
    s, x = files(tmp_path)
    sch = read_sch(s)
    x_mr, y_mr = _place(1.0, 3.81, (50.0, 50.0, 90.0), "x", "MR")
    x_rm, y_rm = _place(1.0, 3.81, (50.0, 50.0, 90.0), "x", "RM")
    assert (round(x_mr, 2), round(y_mr, 2)) != (round(x_rm, 2), round(y_rm, 2))
    sch["wires"] = [((x_mr, y_mr), (x_mr + 5, y_mr))]
    assert calibrate(sch)["MR"] > calibrate(sch)["RM"]
    sch["wires"] = [((x_rm, y_rm), (x_rm + 5, y_rm))]
    assert calibrate(sch)["RM"] > calibrate(sch)["MR"]


def test_points_are_converted_from_mm_to_pt(tmp_path):
    s, x = files(tmp_path)
    pins, _ = gt_pins(s, x, order="MR")
    p1 = next(p for p in pins if p.number == "1")
    ex, ey = _place(1.0, 3.81, (50.0, 50.0, 90.0), "x", "MR")
    assert math.isclose(p1.x, ex * MM_TO_PT) and math.isclose(p1.y, ey * MM_TO_PT)


def test_embedded_library_round_trips_through_load_library(tmp_path):
    s, _ = files(tmp_path)
    out = tmp_path / "emb.kicad_sym"
    out.write_text(embedded_library_text(s))
    lib = load_library(str(out))
    assert "Device:R" in lib.symbols
    assert {p.number for p in lib.symbols["Device:R"].pins} == {"1", "2"}


def test_coincident_pins_on_different_nets_drop_the_symbols_from_scoring(tmp_path):
    s, x = files(tmp_path)
    import pathlib
    r1 = '(symbol (lib_id "Device:R") (at 50 50 90) (mirror x) (unit 1) (property "Reference" "R1" (at 0 0 0)))'
    r2 = r1.replace('"R1"', '"R2"')
    sp = pathlib.Path(s)
    txt = sp.read_text()
    assert r1 in txt
    sp.write_text(txt.replace(r1, r1 + "\n " + r2))
    # R2 sits exactly on R1 but the netlist puts its pin 1 on another net than R1 pin 1: physically impossible
    pathlib.Path(x).write_text(XML.replace('<node ref="R1" pin="2"/>', '<node ref="R1" pin="2"/><node ref="R2" pin="1"/>'))
    pins, info = gt_pins(s, x)
    assert info["inconsistent_refs"] == 2
    assert all(p.net is None for p in pins)
