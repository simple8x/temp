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


class SpatialIndexTests(unittest.TestCase):
 def test_broadphase_pair_count_and_unknown_policy(self):
  ss=[Segment("a",6,(0,0),(10,0)),Segment("b",6,(5,-5),(5,5)),Segment("far",6,(50,50),(60,50))]
  r=analyze_connectivity(ss)
  self.assertEqual(r["broadphase_pair_count"],1)
  self.assertEqual(r["candidate_count"],1)
  self.assertEqual(r["verified_join_count"],0)
  self.assertTrue(r["policy"]["spatial_index_is_candidate_pruning_only"])
 def test_long_segment_fallback(self):
  ss=[Segment("long",6,(0,0),(1000,1000)),Segment("cross",6,(500,499),(500,501))]
  pairs=candidate_pairs(ss,cell_size=1,max_cells_per_segment=8)
  self.assertIn((0,1),pairs)
 def test_page_scoped_index(self):
  ss=[Segment("a",6,(0,0),(10,0)),Segment("b",7,(5,0),(5,5))]
  self.assertEqual(candidate_pairs(ss),[])
 def test_invalid_grid_size(self):
  with self.assertRaises(ValueError):
   candidate_pairs([Segment("a",6,(0,0),(1,1))],cell_size=0)
 def test_random_intersection_pairs_not_pruned(self):
  import random
  rng=random.Random(17)
  ss=[]
  for i in range(120):
   x,y=rng.uniform(-20,20),rng.uniform(-20,20)
   dx,dy=rng.uniform(-5,5),rng.uniform(-5,5)
   ss.append(Segment(str(i),6,(x,y),(x+dx,y+dy)))
  pairs=set(candidate_pairs(ss,cell_size=2))
  for i in range(len(ss)):
   for j in range(i+1,len(ss)):
    if relation(ss[i],ss[j],1e-4)[0]!="DISJOINT":
     self.assertIn((i,j),pairs)


if __name__=="__main__": unittest.main(verbosity=2)
