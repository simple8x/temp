import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from independent_electrical_core import run_corpus, solve

def test_adversarial_oracle_corpus_passes():
    fixture=Path(__file__).resolve().parents[1]/"fixtures"/"electrical_topology_oracle.json"
    result=run_corpus(fixture)
    assert result["case_count"] == 7
    assert result["failed"] == 0

def test_pdf_like_unknown_role_is_not_silently_wire():
    case={"segments":[{"id":"x","role":"unknown","a":[0,0],"b":[5,0]}]}
    result=solve(case)
    assert result["partitions"] == []
    assert result["authority"] == "CANDIDATE_ONLY"
