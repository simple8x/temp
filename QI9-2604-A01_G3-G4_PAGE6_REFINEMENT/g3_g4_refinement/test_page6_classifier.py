import json, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent

class Page6ClassifierEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads((ROOT/'page6_wire_classification_candidates.json').read_text())
        cls.summary=cls.data['summary']

    def test_source_hash_matches_authoritative_pdf(self):
        import hashlib
        p=ROOT.parent/'source/[QI9-2604-A01] Module IO.pdf'
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),self.summary['source_pdf_sha256'])

    def test_page_scope_is_rs232_page6(self):
        self.assertEqual(self.summary['source_page'],6)

    def test_candidates_are_not_promoted_to_wires(self):
        self.assertGreater(self.summary['wire_like_stroke_candidates'],0)
        self.assertEqual(self.summary['verified_wire_segments'],0)
        self.assertTrue(all(x['classification']=='WIRE_LIKE_STROKE_CANDIDATE'
                            for x in self.data['wire_like_candidates']))

    def test_filled_marks_are_not_promoted_to_junctions(self):
        self.assertEqual(self.summary['small_filled_mark_candidates'],
                         len(self.data['filled_mark_candidates']))
        self.assertEqual(self.summary['verified_junctions'],0)
        self.assertTrue(all(x['classification'].endswith('NOT_CONFIRMED_JUNCTION')
                            for x in self.data['filled_mark_candidates']))

    def test_filled_mark_geometry_is_unique(self):
        marks=self.data['filled_mark_candidates']
        keys=[(x['drawing_index'],tuple(x['bbox']),tuple(x['fill'])) for x in marks]
        self.assertEqual(len(keys),len(set(keys)))
        self.assertEqual(self.summary['small_filled_mark_candidates'],17)
        self.assertEqual(self.summary['raw_filled_curve_items'],68)
        self.assertEqual(self.summary['duplicate_curve_items_collapsed'],51)

    def test_no_net_membership_claims(self):
        self.assertEqual(self.summary['verified_net_memberships'],0)

    def test_gate_remains_blocked(self):
        self.assertEqual(self.summary['status'],
                         'CANDIDATE_CLASSIFICATION_ONLY_BLOCKED_FOR_ELECTRICAL_CONNECTIVITY')

if __name__=='__main__':
    unittest.main(verbosity=2)
