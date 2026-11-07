"""Independent reference proof gate and actionable exception ledger."""
from __future__ import annotations
import argparse, json
from collections import Counter, defaultdict
from pathlib import Path

def key(r):
    ref=r.get("ref_candidate") or r.get("ref")
    pin=r.get("pin_candidate") or r.get("pin")
    return (str(ref).strip(),str(pin).strip()) if ref and pin else None

def partition(rows, field):
    groups=defaultdict(set)
    for r in rows:
        k=key(r); n=r.get(field)
        if k and n: groups[str(n)].add(k)
    return {frozenset(v) for v in groups.values() if v}

def audit_reference_ledger(ledger):
    entries=ledger.get("entries",[])
    if not isinstance(entries,list): raise ValueError("ledger.entries must be a list")
    exceptions=[]; seen=set(); duplicates=set(); approved=[]
    for i,r in enumerate(entries):
        if not isinstance(r,dict):
            exceptions.append({"entry_index":i,"code":"MALFORMED_ENTRY","action":"Repair structured terminal record."}); continue
        k=key(r); state=str(r.get("reference_status") or "UNKNOWN").upper()
        if k:
            if k in seen: duplicates.add(k)
            seen.add(k)
        code=action=None
        if state not in ("VERIFIED","NC"):
            cls=r.get("classification"); mapping=r.get("mapping_decision")
            if cls=="CONFLICT": code,action="REFERENCE_CONFLICT","Resolve contradictory source evidence."
            elif not k: code,action="REFERENCE_PIN_IDENTITY_UNKNOWN","Resolve exact component reference and pin."
            elif mapping!="MAPPED": code,action="TERMINAL_GEOMETRY_UNMAPPED","Resolve terminal-to-wire primitive mapping."
            elif cls=="OPEN_WIRE_ENDPOINT": code,action="OPEN_WIRE_ENDPOINT","Determine intentional open versus missing connection."
            elif cls=="NC_STUB_BODY_END": code,action="NC_SEMANTICS_UNRESOLVED","Confirm component pin and explicit NC semantics."
            else: code,action="INDEPENDENT_REFERENCE_MISSING","Independently review source PDF and supply ref/pin/net or NC."
        elif not k: code,action="PIN_IDENTITY_MISSING","Verified reference requires exact ref and pin."
        elif state=="VERIFIED" and not r.get("reference_net"): code,action="REFERENCE_NET_MISSING","Supply independently reviewed reference net."
        elif state=="VERIFIED" and not r.get("candidate_net_name_diagnostic_only"): code,action="PARSER_MEMBERSHIP_MISSING","Resolve missing parser net membership."
        elif state=="NC" and r.get("candidate_net_name_diagnostic_only"): code,action="NC_CONNECTED_IN_CANDIDATE","Explain/remove candidate membership on explicit NC pin."
        else: approved.append(r)
        if code:
            exceptions.append({"entry_index":i,"page":r.get("page"),"ref_candidate":k[0] if k else r.get("ref_candidate"),"pin_candidate":k[1] if k else r.get("pin_candidate"),"code":code,"action":action,"source_wire_primitive_ids":r.get("source_wire_primitive_ids",[]),"terminal_xy_pdf_pt":r.get("terminal_xy_pdf_pt")})
    for ref,pin in sorted(duplicates):
        exceptions.append({"ref_candidate":ref,"pin_candidate":pin,"code":"DUPLICATE_REFERENCE_TERMINAL","action":"Resolve duplicate ref/pin identities."})
    accepted=[r for r in entries if isinstance(r,dict) and str(r.get("reference_status") or "").upper() in ("VERIFIED","NC")]
    complete=bool(entries) and len(accepted)==len(entries)
    unique=bool(entries) and len(seen)==len(entries) and not duplicates and all(key(r) for r in entries if isinstance(r,dict))
    verified=[r for r in approved if str(r.get("reference_status")).upper()=="VERIFIED"]
    refp=partition(verified,"reference_net")
    candp=partition(verified,"candidate_net_name_diagnostic_only")
    equal=refp==candp
    if complete and unique and not equal:
        exceptions.append({"code":"NET_PARTITION_MISMATCH","action":"Resolve every split/merge between reviewed PDF nets and parser candidate nets.","reference_net_groups":len(refp),"candidate_net_groups":len(candp)})
    nc_connected=[r for r in accepted if str(r.get("reference_status")).upper()=="NC" and r.get("candidate_net_name_diagnostic_only")]
    passed=complete and unique and equal and not nc_connected and not exceptions
    counts=Counter(e["code"] for e in exceptions)
    return {"schema":"pdf2kicad-electrical-proof-and-exceptions/v1","source_pdf_sha256":ledger.get("source_pdf_sha256"),"pages":ledger.get("pages",[]),"proof":{"status":"ELECTRICAL_CONNECTIVITY_PROVEN" if passed else "BLOCKED","reference_entries":len(entries),"independently_approved_entries":len(accepted),"verified_net_memberships":len(verified),"explicit_nc_pins":sum(str(r.get("reference_status")).upper()=="NC" for r in accepted),"candidate_nc_pins_with_net":len(nc_connected),"reference_net_groups":len(refp),"candidate_net_groups":len(candp),"net_partition_equal":equal,"complete_reference_coverage":complete,"unique_terminal_identity":unique,"proof_basis":["independently reviewed ref/pin/net or explicit NC","complete terminal coverage","unique ref/pin identities","exact electrical-net partition equivalence","no candidate net on explicit NC pins"]},"exceptions":exceptions,"exception_summary":dict(sorted(counts.items())),"exception_count":len(exceptions),"policy":{"geometry_match_is_not_electrical_proof":True,"parser_output_is_not_independent_reference_truth":True,"unknown_is_blocking":True,"no_coordinate_fallback_for_pin_identity":True,"no_automatic_promotion":True}}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--ledger",required=True,type=Path); p.add_argument("--out",required=True,type=Path); a=p.parse_args()
    report=audit_reference_ledger(json.loads(a.ledger.read_text(encoding="utf-8"))); a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"proof_status":report["proof"]["status"],"reference_entries":report["proof"]["reference_entries"],"approved_entries":report["proof"]["independently_approved_entries"],"exception_count":report["exception_count"],"exception_summary":report["exception_summary"]},indent=2))
if __name__=="__main__": main()
