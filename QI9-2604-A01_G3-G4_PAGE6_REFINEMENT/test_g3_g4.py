import json,unittest
from pathlib import Path
ROOT=Path(__file__).parent; OUT=ROOT/'run_g3_g4'
class G34(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.g3=json.loads((OUT/'g3_symbol_pin_candidates.json').read_text())
  cls.g4=json.loads((OUT/'g4_connectivity_candidates.json').read_text())
 def test_candidates_have_provenance(self):
  self.assertGreater(self.g3['candidate_reference_count'],0)
  self.assertTrue(all(x['source_page'] in range(5,12) and x['bbox_px'] for x in self.g3['symbol_candidates']))
 def test_unverified_candidates_have_no_pin_anchors(self):
  self.assertTrue(all(x['pin_anchors']==[] for x in self.g3['symbol_candidates']))
 def test_verified_facts_are_deduplicated(self):
  facts=self.g3['reviewed_pin_net_facts']; keys=[(x['page'],x['symbol'],x['pin'],x['net']) for x in facts]
  self.assertEqual(len(keys),len(set(keys))); self.assertEqual(len(facts),6)
 def test_g4_does_not_claim_nets(self):
  self.assertEqual(self.g4['net_membership_claims'],[])
  self.assertEqual(self.g4['status'],'PARTIAL_GEOMETRY_GRAPH_ONLY_BLOCKED_FOR_ELECTRICAL_NETLIST')
 def test_crossings_and_labels_fail_closed(self):
  p=self.g4['policy']; self.assertEqual(p['geometric_crossings'],'UNJOINED'); self.assertEqual(p['net_labels'],'NOT_ATTACHED_BY_PROXIMITY')
 def test_line_primitives_are_not_mislabeled_as_wires(self):
  self.assertGreater(self.g4['line_primitive_count'],0)
  self.assertIn('has been performed',self.g4['warning'])
if __name__=='__main__': unittest.main(verbosity=2)
