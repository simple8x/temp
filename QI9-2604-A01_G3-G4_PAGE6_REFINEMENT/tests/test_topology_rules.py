import unittest, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from topology_rules import near, axis_aligned_intersection, electrical_join_allowed
class GeometryTests(unittest.TestCase):
 def test_endpoint_tolerance(self): self.assertTrue(near((1,1),(1.1,1.1),.15))
 def test_endpoint_outside_tolerance(self): self.assertFalse(near((1,1),(1.3,1.3),.15))
 def test_orthogonal_crossing_detected(self): self.assertEqual(axis_aligned_intersection((0,5),(10,5),(4,0),(4,10)),(4,5))
 def test_crossing_not_joined_without_dot(self): self.assertFalse(electrical_join_allowed((4,5)))
 def test_explicit_junction_joins(self): self.assertTrue(electrical_join_allowed((4,5),explicit_junction=True))
 def test_shared_endpoint_joins_candidate(self): self.assertTrue(electrical_join_allowed((10,0),shared_endpoint=True))
 def test_diagonal_not_classified(self): self.assertIsNone(axis_aligned_intersection((0,0),(10,10),(5,-1),(5,11)))
if __name__=='__main__': unittest.main(verbosity=2)
