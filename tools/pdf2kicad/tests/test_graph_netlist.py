import unittest
from types import SimpleNamespace

from pdf2kicad.graph_netlist import GraphConnectivityError, graph_nets


class Segment:
    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.kind = "line"


class Terminal:
    def __init__(self, x, y, comp):
        self.x, self.y, self.comp = x, y, comp
        self.kind = "attach"


def fixture(segments, terminal_specs):
    topo = SimpleNamespace(
        segs=[Segment(*coords) for coords in segments],
        W=list(range(len(segments))),
        comps=[[i] for i in range(len(segments))],
        wire_comps=list(range(len(segments))),
        dots=[],
    )
    groups = []
    for ref, x, y, comp in terminal_specs:
        terminal = Terminal(x, y, comp)
        groups.append(SimpleNamespace(ref=ref, terminals=[terminal]))
    return topo, groups, [g.terminals[0] for g in groups]


class GraphNetlistTests(unittest.TestCase):
    def test_graph_connects_endpoint_contacts_from_separate_legacy_components(self):
        topo, groups, terms = fixture(
            [(0, 0, 5, 0), (5, 0, 10, 0)],
            [("R1", 0, 0, 0), ("R2", 10, 0, 1)],
        )
        result = graph_nets(topo, groups, {})
        self.assertEqual(len(result), 1)
        self.assertEqual({ref for ref, _ in next(iter(result.values()))}, {"R1", "R2"})

    def test_unmarked_t_junction_stays_split(self):
        topo, groups, _ = fixture(
            [(0, 0, 10, 0), (5, 0, 5, 5)],
            [("R1", 0, 0, 0), ("R2", 5, 5, 1)],
        )
        result = graph_nets(topo, groups, {})
        self.assertEqual(len(result), 2)

    def test_explicit_dot_connects_t_junction(self):
        topo, groups, _ = fixture(
            [(0, 0, 10, 0), (5, 0, 5, 5)],
            [("R1", 0, 0, 0), ("R2", 5, 5, 1)],
        )
        topo.dots = [(5, 0)]
        result = graph_nets(topo, groups, {})
        self.assertEqual(len(result), 1)

    def test_same_explicit_label_merges_disconnected_graph_components(self):
        topo, groups, terms = fixture(
            [(0, 0, 5, 0), (20, 0, 25, 0)],
            [("R1", 0, 0, 0), ("R2", 25, 0, 1)],
        )
        result = graph_nets(topo, groups, {"DATA": [("R1", terms[0]), ("R2", terms[1])]})
        self.assertEqual(set(result), {"DATA"})
        self.assertEqual({ref for ref, _ in result["DATA"]}, {"R1", "R2"})

    def test_conflicting_labels_on_one_graph_component_fail_closed(self):
        topo, groups, terms = fixture(
            [(0, 0, 5, 0), (5, 0, 10, 0)],
            [("R1", 0, 0, 0), ("R2", 10, 0, 1)],
        )
        with self.assertRaises(GraphConnectivityError):
            graph_nets(topo, groups, {
                "DATA_A": [("R1", terms[0])],
                "DATA_B": [("R2", terms[1])],
            })

    def test_unmappable_referenced_terminal_fails_closed(self):
        topo, groups, _ = fixture(
            [(0, 0, 5, 0)],
            [("R1", 30, 30, 0)],
        )
        with self.assertRaises(GraphConnectivityError):
            graph_nets(topo, groups, {})


if __name__ == "__main__":
    unittest.main()
