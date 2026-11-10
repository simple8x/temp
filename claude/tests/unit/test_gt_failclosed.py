"""Fail-closed ground-truth evaluation (external review P1): an incomplete corpus is never a result."""
import hashlib
import importlib
import pathlib
import subprocess
import sys

import pytest

TOOLS = pathlib.Path(__file__).resolve().parents[2] / "tools"


@pytest.fixture()
def gt_eval(monkeypatch):
    monkeypatch.syspath_prepend(str(TOOLS))
    monkeypatch.delenv("GT_ONLY", raising=False)
    sys.modules.pop("gt_eval", None)
    return importlib.import_module("gt_eval")


def _dirs(tmp_path, n):
    out = []
    for k in range(n):
        d = tmp_path / f"p{k}"
        d.mkdir()
        (d / "a.xml").write_text("x")
        (d / "a.pdf").write_text("x")
        out.append(d)
    return out


def test_page_error_is_a_problem(gt_eval, tmp_path):
    cov = gt_eval.coverage([[{"project": "a"}], [{"project": "b#p0", "error": "TimeoutError: page exceeded 150s"}]],
                           _dirs(tmp_path, 2))
    assert cov["errors"] == ["b#p0"] and cov["problems"] and cov["evaluated"] == 1


def test_clean_run_has_no_problems_and_skips_are_not_errors(gt_eval, tmp_path):
    cov = gt_eval.coverage([[{"project": "a"}], [{"project": "b", "skipped": "too large"}]], _dirs(tmp_path, 2))
    assert cov["problems"] == [] and cov["evaluated"] == 1 and cov["skipped_by_design"] == 1


def test_full_run_below_pinned_page_or_project_count_is_a_problem(gt_eval, tmp_path, monkeypatch):
    monkeypatch.setenv("GT_MIN_PAGES", "3")
    monkeypatch.setenv("GT_EXPECT_PROJECTS", "5")
    cov = gt_eval.coverage([[{"project": "a"}]], _dirs(tmp_path, 2))
    assert len(cov["problems"]) == 2 and cov["full_run"]


def test_main_exits_nonzero_when_a_project_raises(gt_eval, tmp_path, monkeypatch, capsys):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "broken.xml").write_text("<not a netlist")
    (d / "broken.pdf").write_bytes(b"not a pdf")
    monkeypatch.setenv("GT_WORKERS", "1")
    assert gt_eval.main(str(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "GT_INCOMPLETE" in out and '"complete": false' in out


def _fake_gt(tmp_path):
    out = tmp_path / "gt_out"
    (out / "proj").mkdir(parents=True)
    (out / "proj" / "proj.xml").write_text("net")
    (out / "proj" / "proj.pdf").write_text("pdf")
    pins = dict(l.split("=", 1) for l in (TOOLS / "gt_expected.txt").read_text().splitlines() if "=" in l and not l.startswith("#"))
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()   # noqa: E731
    (out / "PROVENANCE.txt").write_text(
        f"KICAD_SOURCE_SHA={pins['KICAD_SRC_SHA']}\nKICAD_CLI={pins['KICAD_CLI_VERSION']}\n"
        f"EXPORT_SCRIPT_SHA256={sha(TOOLS / 'gt_export.sh')}\nEXPECTED_SHA256={sha(TOOLS / 'gt_expected.txt')}\n")
    files = sorted(p for p in out.rglob("*") if p.is_file())
    (out / "MANIFEST.sha256").write_text("".join(f"{sha(p)}  ./{p.relative_to(out)}\n" for p in files))
    return out


def _verify(out):
    return subprocess.run(["bash", str(TOOLS / "gt_verify.sh"), str(out)], capture_output=True, text=True)


def test_verify_rejects_tampered_file_wrong_pin_and_missing_provenance(tmp_path):
    out = _fake_gt(tmp_path)
    r = _verify(out)       # intact manifest and pins: only the project count differs from the pinned corpus
    assert r.returncode == 1 and "manifest" not in r.stdout and "source sha" not in r.stdout and "netlists, expected" in r.stdout
    (out / "proj" / "proj.xml").write_text("tampered")
    assert "GT_VERIFY FAIL: manifest" in _verify(out).stdout
    prov = (out / "PROVENANCE.txt").read_text()
    (out / "PROVENANCE.txt").write_text(prov.replace("KICAD_SOURCE_SHA=", "KICAD_SOURCE_SHA=0"))
    assert "GT_VERIFY FAIL: source sha" in _verify(out).stdout
    (out / "PROVENANCE.txt").unlink()
    r = _verify(out)
    assert r.returncode == 1 and "no provenance" in r.stdout
