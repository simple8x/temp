import unittest
from g3_g4_geometry import *
class GeometryEvidenceTests(unittest.TestCase):
 def s(self,i,a,b,role="WIRE",ev="SOURCE_REVIEWED",path=None): return Segment(i,6,a,b,role,ev,path)
 def test_shared_endpoint_unknown(self):
  r=analyze_connectivity([self.s("a",(0,0),(1,0)),self.s("b",(1,0),(2,0))]); self.assertEqual(r["candidates"][0]["relation"],"ENDPOINT_ENDPOINT"); self.assertEqual(r["verified_join_count"],0)
 def test_same_pdf_path_continuity(self):
  r=analyze_connectivity([self.s("a",(0,0),(1,0),path="p"),self.s("b",(1,0),(2,0),path="p")]); self.assertEqual(r["verified_join_count"],1)
 def test_t_without_dot(self):
  r=analyze_connectivity([self.s("a",(0,0),(2,0)),self.s("b",(1,0),(1,1))]); self.assertEqual(r["candidates"][0]["relation"],"T"); self.assertEqual(r["verified_join_count"],0)
 def test_t_with_verified_dot(self):
  d=JunctionDot(6,(1,0),.05,"dot",True); r=analyze_connectivity([self.s("a",(0,0),(2,0)),self.s("b",(1,0),(1,1))],[d]); self.assertEqual(r["verified_join_count"],1)
 def test_crossing_without_dot(self):
  r=analyze_connectivity([self.s("a",(0,0),(2,0)),self.s("b",(1,-1),(1,1))]); self.assertEqual(r["candidates"][0]["relation"],"CROSSING"); self.assertEqual(r["verified_join_count"],0)
 def test_crossing_with_dot(self):
  d=JunctionDot(6,(1,0),.05,"dot",True); r=analyze_connectivity([self.s("a",(0,0),(2,0)),self.s("b",(1,-1),(1,1))],[d]); self.assertEqual(r["verified_join_count"],1)
 def test_unclassified_never_join(self):
  r=analyze_connectivity([self.s("a",(0,0),(1,0),"UNKNOWN","NONE","p"),self.s("b",(1,0),(2,0),"UNKNOWN","NONE","p")]); self.assertEqual(r["verified_join_count"],0)
 def test_overlap_ambiguous(self):
  r=analyze_connectivity([self.s("a",(0,0),(2,0)),self.s("b",(1,0),(3,0))]); self.assertEqual(r["candidates"][0]["relation"],"OVERLAP"); self.assertEqual(r["verified_join_count"],0)
 def test_near_parallel_disjoint(self):
  r=analyze_connectivity([self.s("a",(0,0),(2,0)),self.s("b",(0,.001),(2,.001))],tol=1e-5); self.assertEqual(r["candidate_count"],0)
 def test_cross_page_never_join(self):
  r=analyze_connectivity([self.s("a",(0,0),(1,0)),Segment("b",7,(1,0),(2,0),"WIRE","SOURCE_REVIEWED","p")]); self.assertEqual(r["candidate_count"],0)
 def test_degenerate(self):
  r=analyze_connectivity([self.s("a",(0,0),(0,0)),self.s("b",(0,0),(1,0))]); self.assertEqual(r["candidates"][0]["relation"],"DEGENERATE")
 def test_pin_anchor_requires_template(self):
  self.assertEqual(validate_pin_anchor(PinAnchor("U5","1",(0,0),"lib@1",True,"ev"))["status"],"VERIFIED_ANCHOR")
  self.assertEqual(validate_pin_anchor(PinAnchor("U5","1",(0,0),"",False,""))["status"],"UNKNOWN")
 def test_label_attachment_requires_explicit_evidence(self):
  s=self.s("w",(0,0),(2,0)); self.assertEqual(validate_label_attachment(LabelAnchor("RXN",(1,0),"ocr",False),s)["status"],"UNKNOWN")
  self.assertEqual(validate_label_attachment(LabelAnchor("RXN",(1,0),"review",True),s)["status"],"VERIFIED_ATTACHMENT")
 def test_near_label_not_attached(self):
  self.assertEqual(validate_label_attachment(LabelAnchor("RXN",(1,.1),"review",True),self.s("w",(0,0),(2,0)))["status"],"UNKNOWN")
if __name__=="__main__": unittest.main(verbosity=2)
