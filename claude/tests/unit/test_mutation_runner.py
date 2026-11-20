"""P2: the mutation runner classifies every outcome explicitly; only a genuine test failure is KILLED.

These tests drive the real runner (make_copy / run_suite / check_one / main) against a tiny fake package, so
they exercise real pytest exit codes and JUnit reports. They are listed in HARNESS_TESTS (not run in each copy)."""
import importlib
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
mc = importlib.import_module("mutation_check")

TEST_SRC = "import mod\n\n\ndef test_x():\n    assert mod.X == 1\n"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    root = tmp_path / "repo" / "claude"
    (root / "tests").mkdir(parents=True)
    (root / "conftest.py").write_text("# conftest\n")
    (root / "mod.py").write_text("X = 1\nY = 1\n")
    (root / "tests" / "test_m.py").write_text(TEST_SRC)
    monkeypatch.setattr(mc, "ROOT", root)
    return root


def one(rel, old, new):
    return mc.check_one(("MT", rel, old, new))


def test_genuine_assertion_failure_is_killed(fake):
    r = one("mod.py", "X = 1\n", "X = 2\n")
    assert r["status"] == mc.KILLED, r
    assert "test_x" in r["detail"]


def test_exception_raised_by_mutated_code_in_a_test_body_is_killed(fake):
    (fake / "mod.py").write_text("X = 1\nY = 1\n\ndef f():\n    return 1\n")
    (fake / "tests" / "test_m.py").write_text("import mod\n\n\ndef test_x():\n    assert mod.f() == 1\n")
    r = one("mod.py", "    return 1\n", "    return {}['k']\n")
    assert r["status"] == mc.KILLED, r


def test_successful_suite_after_mutation_survived(fake):
    r = one("mod.py", "X = 1\n", "X = 1  # equivalent\n")
    assert r["status"] == mc.SURVIVED, r


def test_syntax_error_is_not_killed(fake):
    r = one("mod.py", "X = 1\n", "X = (\n")
    assert r["status"] == mc.COLLECTION_ERROR and "compile" in r["detail"], r


def test_syntax_error_reached_only_at_import_time_is_not_killed(fake, monkeypatch):
    monkeypatch.setattr(mc, "_compiles", lambda rel, path: "")       # pretend the pre-check missed it
    r = one("mod.py", "X = 1\n", "X = (\n")
    assert r["status"] == mc.COLLECTION_ERROR, r


def test_import_error_at_collection_is_not_killed(fake):
    r = one("mod.py", "X = 1\n", "import no_such_module_zz\nX = 1\n")
    assert r["status"] == mc.COLLECTION_ERROR, r


def test_import_error_inside_a_test_body_is_not_killed(fake):
    r = one("tests/test_m.py", "    assert mod.X == 1\n", "    import no_such_module_zz\n    assert mod.X == 1\n")
    assert r["status"] == mc.COLLECTION_ERROR, r


def test_collection_error_is_not_killed(fake):
    r = one("tests/test_m.py", "import mod\n", "import mod\nraise RuntimeError('boom at import')\n")
    assert r["status"] == mc.COLLECTION_ERROR, r


def test_zero_collected_tests_is_not_killed(fake):
    r = one("tests/test_m.py", "def test_x():", "def check_x():")
    assert r["status"] == mc.COLLECTION_ERROR and "collected" in r["detail"], r


def test_pytest_internal_error_is_not_killed(fake):
    r = one("conftest.py", "# conftest\n", "def pytest_runtestloop(session):\n    raise RuntimeError('internal')\n")
    assert r["status"] == mc.HARNESS_ERROR and "internal" in r["detail"], r


def test_setup_error_only_is_not_killed(fake):
    r = one("tests/test_m.py", "def test_x():\n", "import pytest\n@pytest.fixture\ndef fx():\n    raise RuntimeError('x')\n"
            "def test_x(fx):\n")
    assert r["status"] == mc.HARNESS_ERROR and "setup" in r["detail"], r


def test_timeout_is_not_killed(fake, monkeypatch):
    monkeypatch.setattr(mc, "SUITE_TIMEOUT", 3)
    r = one("tests/test_m.py", "    assert mod.X == 1\n", "    import time\n    time.sleep(60)\n")
    assert r["status"] == mc.TIMEOUT, r


def test_runner_failure_is_a_harness_error(fake, monkeypatch):
    def boom(*a, **k):
        raise OSError("no python")
    monkeypatch.setattr(mc, "run_suite", boom)
    assert one("mod.py", "X = 1\n", "X = 2\n")["status"] == mc.HARNESS_ERROR


def test_pattern_matching_zero_times_is_invalid(fake):
    r = one("mod.py", "Z = 9\n", "Z = 8\n")
    assert r["status"] == mc.INVALID and "0x" in r["detail"], r


def test_pattern_matching_multiple_times_is_invalid(fake):
    r = one("mod.py", " = 1\n", " = 2\n")
    assert r["status"] == mc.INVALID and "2x" in r["detail"], r


@pytest.mark.parametrize("mut", [("MT", "mod.py", "X = 1\n", "X = 1\n"), ("MT", "mod.py", "", "x"),
                                 ("MT", "nope.py", "a", "b"), ("MT", "mod.py"), None])
def test_mutation_that_cannot_apply_is_never_killed(fake, mut):
    assert mc.check_one(mut)["status"] in (mc.INVALID, mc.HARNESS_ERROR)


def test_original_sources_are_never_modified(fake):
    before = {p: p.read_text() for p in fake.rglob("*.py")}
    one("mod.py", "X = 1\n", "X = 2\n")
    assert {p: p.read_text() for p in fake.rglob("*.py")} == before


# ---------------------------------------------------------------- classify() on synthetic reports

def junit(*cases):
    body = "".join(f'<testcase classname="{c}" name="{n}">{x}</testcase>' for c, n, x in cases)
    return f'<testsuites><testsuite tests="{len(cases)}">{body}</testsuite></testsuites>'


@pytest.mark.parametrize("code,report,want", [
    (1, junit(("tests.unit.t", "test_a", '<failure message="assert 1 == 2"/>')), "KILLED"),
    (1, junit(("tests.unit.t", "test_a", '<failure message="KeyError: k"/>')), "KILLED"),
    (1, junit(("tests.unit.t", "test_a", '<failure message="ModuleNotFoundError: No module named x"/>')),
     "COLLECTION_ERROR"),
    (1, junit(("tests.unit.t", "test_a", '<failure message="x">E   SyntaxError: invalid syntax</failure>')),
     "COLLECTION_ERROR"),
    (1, junit(("tests.unit.t", "test_a", '<failure message="ImportError: cannot import name y"/>')),
     "COLLECTION_ERROR"),
    (2, junit(("tests.unit.t", "", '<error message="collection failure"/>')), "COLLECTION_ERROR"),
    (1, junit(("tests.unit.t", "", '<error message="collection failure"/>')), "COLLECTION_ERROR"),
    (1, junit(("tests.unit.t", "test_a", '<error message="failed on setup with x"/>')), "HARNESS_ERROR"),
    (1, junit(), "COLLECTION_ERROR"),
    (0, junit(), "COLLECTION_ERROR"),
    (5, None, "COLLECTION_ERROR"),
    (3, None, "HARNESS_ERROR"),
    (4, None, "HARNESS_ERROR"),
    (137, None, "HARNESS_ERROR"),
    (-9, None, "HARNESS_ERROR"),
    (1, None, "HARNESS_ERROR"),
    (1, "<not xml", "HARNESS_ERROR"),
    (1, junit(("tests.unit.t", "test_a", "")), "HARNESS_ERROR"),
    (0, junit(("tests.unit.t", "test_a", '<failure message="x"/>')), "HARNESS_ERROR"),
    (0, junit(("tests.unit.t", "test_a", "")), "SURVIVED"),
    (1, junit(("tests.unit.test_gt_failclosed",
               "test_mutation_runner_reports_in_a_fixed_order_and_counts_every_mutation", '<failure message="x"/>')),
     "HARNESS_ERROR"),
    (1, junit(("tests.unit.test_mutation_runner", "test_x[1]", '<failure message="x"/>')), "HARNESS_ERROR"),
])
def test_classify(code, report, want):
    assert mc.classify(code, report, "")[0] == want


def test_interrupted_run_without_collection_error_is_a_harness_error():
    assert mc.classify(2, None, "!!! KeyboardInterrupt !!!")[0] == mc.HARNESS_ERROR
    assert mc.classify(2, None, "Interrupted: 1 error during collection")[0] == mc.COLLECTION_ERROR


# ---------------------------------------------------------------- baseline and the overall verdict

def test_failing_baseline_prevents_every_mutation(fake, monkeypatch, capsys):
    (fake / "tests" / "test_m.py").write_text("def test_x():\n    assert False\n")
    called = []
    monkeypatch.setattr(mc, "check_one", lambda m: called.append(m) or {"id": m[0], "status": "KILLED", "detail": ""})
    monkeypatch.setattr(sys, "argv", ["mutation_check.py"])
    assert mc.main() == 1 and not called
    assert "MUTATION_BASELINE_FAIL" in capsys.readouterr().out


@pytest.mark.parametrize("src", ["", "def test_x():\n    pass\n" + "raise RuntimeError('x')\n"])
def test_baseline_with_no_or_uncollectable_tests_is_not_a_pass(fake, src):
    (fake / "tests" / "test_m.py").write_text(src)
    assert mc.baseline()[0] == 1


def test_clean_baseline_passes(fake):
    code, detail = mc.baseline()
    assert code == 0 and "1 tests passed" in detail, detail


@pytest.mark.parametrize("status", [s for s in mc.STATUSES if s != "KILLED"] + ["BOGUS"])
def test_any_non_killed_outcome_fails_the_job(tmp_path, monkeypatch, status):
    monkeypatch.setattr(mc, "baseline", lambda: (0, ""))
    monkeypatch.setattr(mc, "ROOT", tmp_path)
    muts = mc.MUTATIONS[:3]
    monkeypatch.setattr(mc, "MUTATIONS", muts)
    monkeypatch.setattr(mc, "check_one", lambda m: {"id": m[0], "status": status if m is muts[1] else "KILLED",
                                                    "detail": ""})
    monkeypatch.setattr(sys, "argv", ["mutation_check.py"])
    assert mc.main() == 1


def test_crashed_worker_fails_the_job(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "baseline", lambda: (0, ""))
    monkeypatch.setattr(mc, "ROOT", tmp_path)
    monkeypatch.setattr(mc, "MUTATIONS", mc.MUTATIONS[:2])
    def cb(m):
        raise RuntimeError("worker died")
    monkeypatch.setattr(mc, "check_one", cb)
    monkeypatch.setattr(sys, "argv", ["mutation_check.py"])
    assert mc.main() == 1


def test_all_killed_passes_and_counts_are_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mc, "baseline", lambda: (0, ""))
    monkeypatch.setattr(mc, "ROOT", tmp_path)
    monkeypatch.setattr(mc, "check_one", lambda m: {"id": m[0], "status": "KILLED", "detail": ""})
    monkeypatch.setattr(sys, "argv", ["mutation_check.py"])
    assert mc.main() == 0
    assert f'"KILLED": {len(mc.MUTATIONS)}' in capsys.readouterr().out


def test_every_harness_test_is_deselected_in_the_copy_and_exists(tmp_path):
    work = mc.make_copy(tmp_path)
    for t in mc.HARNESS_TESTS:
        mod, _, fn = t.partition("::")
        assert (work / mod).is_file(), t
        if fn:
            assert f"def {fn}(" in (work / mod).read_text()
    assert mc._is_harness("tests.unit.test_mutation_runner", "test_classify[1]")
    assert not mc._is_harness("tests.unit.test_rules", "test_tr1_end_to_end_two_wires_verified")
