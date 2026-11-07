import unittest

from pdf2kicad.planar_graph import Primitive, build_planar_graph


class PlanarGraphTests(unittest.TestCase):
    def graph(self, segments, dots=()):
        return build_planar_graph(
            [Primitive(str(i), *coords) for i, coords in enumerate(segments)],
            junction_dots=dots,
        )

    def test_endpoint_endpoint_connects(self):
        g = self.graph([(0, 0, 5, 0), (5, 0, 5, 4)])
        self.assertTrue(any(c.decision == "CONNECTED" and c.kind == "endpoint_endpoint"
                            for c in g.contacts))

    def test_t_junction_without_dot_is_unknown_and_split(self):
        g = self.graph([(0, 0, 10, 0), (5, 0, 5, 4)])
        self.assertTrue(any(c.decision == "UNKNOWN" and c.kind == "endpoint_on_segment"
                            for c in g.contacts))
        self.assertEqual(len(g.connected_components()), 2)
        self.assertEqual(sum(1 for n in g.nodes if abs(n.x - 5) < 1e-6 and abs(n.y) < 1e-6), 2)

    def test_t_junction_with_dot_connects(self):
        g = self.graph([(0, 0, 10, 0), (5, 0, 5, 4)], [(5, 0)])
        self.assertTrue(any(c.decision == "CONNECTED" and c.reason == "explicit_junction_dot"
                            for c in g.contacts))
        self.assertEqual(len(g.connected_components()), 1)
        self.assertEqual(len(g.connected_components()), 1)

    def test_x_crossing_without_dot_is_disconnected(self):
        g = self.graph([(0, 0, 10, 10), (0, 10, 10, 0)])
        self.assertTrue(any(c.decision == "DISCONNECTED" and c.kind == "proper_crossing"
                            for c in g.contacts))
        self.assertEqual(len(g.connected_components()), 2)

    def test_x_crossing_with_dot_connects(self):
        g = self.graph([(0, 0, 10, 10), (0, 10, 10, 0)], [(5, 5)])
        self.assertTrue(any(c.decision == "CONNECTED" and c.reason == "explicit_junction_dot"
                            for c in g.contacts))

    def test_collinear_overlap_splits_and_keeps_both_sources(self):
        g = self.graph([(0, 0, 10, 0), (4, 0, 8, 0)])
        self.assertTrue(any(c.kind == "collinear_overlap" and c.decision == "CONNECTED"
                            for c in g.contacts))
        self.assertTrue(any(e.primitive_ids == {"0"} for e in g.edges))
        self.assertTrue(any(e.primitive_ids == {"1"} for e in g.edges))

    def test_near_miss_does_not_merge(self):
        g = self.graph([(0, 0, 5, 0), (5.2, 0, 5.2, 4)])
        self.assertFalse(g.contacts)

    def test_duplicate_geometry_retains_provenance(self):
        g = self.graph([(0, 0, 5, 0), (0, 0, 5, 0)])
        self.assertTrue(any(e.primitive_ids == {"0"} for e in g.edges))
        self.assertTrue(any(e.primitive_ids == {"1"} for e in g.edges))
        self.assertTrue(any(c.kind == "collinear_overlap" for c in g.contacts))


if __name__ == "__main__":
    unittest.main()
