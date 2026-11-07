"""Regression tests for exact KiCad schematic assignment read-back."""
import importlib.util
from pathlib import Path

VERIFIER = Path(__file__).resolve().parents[1] / "tools" / "verify_kicad_sch.py"
SPEC = importlib.util.spec_from_file_location("verify_kicad_sch", VERIFIER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_wrong_net_label_fails_exact_readback():
    expected = {("R1", "1"): "VIN"}
    actual = {("R1", "1"): "GND"}
    problems = MODULE.compare_assignments(expected, actual)
    assert len(problems) == 1
    assert "net mismatch" in problems[0]
    assert "'VIN'" in problems[0]
    assert "'GND'" in problems[0]


def test_missing_expected_assignment_fails_readback():
    problems = MODULE.compare_assignments({("C1", "2"): "VCC"}, {})
    assert len(problems) == 1
    assert "no electrical assignment" in problems[0]


def test_unexpected_schematic_assignment_fails_readback():
    problems = MODULE.compare_assignments({}, {("U1", "7"): "SDA"})
    assert len(problems) == 1
    assert "unexpected electrical assignment" in problems[0]


def test_exact_net_and_explicit_nc_assignments_pass():
    expected = {("R1", "1"): "VIN", ("R1", "2"): "NC"}
    actual = {("R1", "1"): "VIN", ("R1", "2"): "NC"}
    assert MODULE.compare_assignments(expected, actual) == []


def test_unmapped_physical_pin_is_not_an_assignment_and_does_not_create_nc():
    # Draft-mode physical pins omitted from the CSV are intentionally absent
    # from both maps. This check must not manufacture an NC assignment.
    expected = {("C1", "1"): "VCC"}
    actual = {("C1", "1"): "VCC"}
    assert MODULE.compare_assignments(expected, actual) == []
    assert ("C1", "2") not in actual
