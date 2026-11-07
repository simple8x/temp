from pdf2kicad.proof_and_exceptions import audit_reference_ledger

def row(ref,pin,rnet,cnet,status="VERIFIED"):
    return {"page":5,"ref_candidate":ref,"pin_candidate":pin,"reference_status":status,"reference_pin":pin,"reference_net":rnet,"candidate_net_name_diagnostic_only":cnet,"mapping_decision":"MAPPED","classification":"INFERRED","source_wire_primitive_ids":["wire-1"]}

def test_exact_partition_proves():
    x=audit_reference_ledger({"entries":[row("U1","1","VCC","N1"),row("R1","1","VCC","N1"),row("U1","2","GND","N2")]})
    assert x["proof"]["status"]=="ELECTRICAL_CONNECTIVITY_PROVEN" and not x["exceptions"]

def test_partition_mismatch_blocks():
    x=audit_reference_ledger({"entries":[row("U1","1","VCC","N1"),row("R1","1","VCC","N2")]})
    assert x["proof"]["status"]=="BLOCKED" and "NET_PARTITION_MISMATCH" in x["exception_summary"]

def test_unknown_never_promotes_geometry():
    x=audit_reference_ledger({"entries":[row("U1","1",None,"N1","UNKNOWN")]})
    assert x["proof"]["status"]=="BLOCKED" and x["exception_summary"]["INDEPENDENT_REFERENCE_MISSING"]==1

def test_nc_must_not_have_candidate_net():
    x=audit_reference_ledger({"entries":[row("J1","3",None,"N3","NC")]})
    assert x["proof"]["status"]=="BLOCKED" and x["exception_summary"]["NC_CONNECTED_IN_CANDIDATE"]==1

def test_duplicate_terminal_blocks():
    x=audit_reference_ledger({"entries":[row("U1","1","VCC","N1"),row("U1","1","GND","N2")]})
    assert x["proof"]["status"]=="BLOCKED" and x["exception_summary"]["DUPLICATE_REFERENCE_TERMINAL"]==1

def test_reference_pin_identity_mismatch_blocks():
    item=row("U1","1","VCC","N1")
    item["reference_pin"]="2"
    x=audit_reference_ledger({"entries":[item]})
    assert x["proof"]["status"]=="BLOCKED"
    assert x["exception_summary"]["REFERENCE_PIN_IDENTITY_MISMATCH"]==1
