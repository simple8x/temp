"""Resolve a Prov back to the PDF operators it came from (traceability, invariant I8)."""

from __future__ import annotations

import pikepdf

from .ingest_vector import form_resources, lookup_xobject
from .ir import Prov
from .transform import _inherited


class ProvResolveError(Exception):
    pass


def resolve_prov(pdf: pikepdf.Pdf, prov: Prov) -> dict:
    """Return {"paint": (operator, operands), "first": (operator, operands), "stream": label}.

    Walks the chain: every non-final entry must be a `Do` of a form XObject; the final entry
    must be a painting operator. Raises ProvResolveError instead of guessing.
    """
    try:
        page = pdf.pages[prov.page]
    except Exception as e:  # noqa: BLE001
        raise ProvResolveError(f"page {prov.page} not found") from e
    stream = page.obj
    resources = _inherited(page.obj, "/Resources")
    label = "page"
    for depth, (lab, idx) in enumerate(prov.chain):
        if depth == 0 and lab != "page":
            raise ProvResolveError("chain must start at the page content stream")
        instrs = pikepdf.parse_content_stream(stream if depth else page)
        if not (0 <= idx < len(instrs)):
            raise ProvResolveError(f"op index {idx} out of range in stream {lab!r}")
        inst = instrs[idx]
        op = str(inst.operator)
        last = depth == len(prov.chain) - 1
        if not last:
            if op != "Do":
                raise ProvResolveError(f"chain entry {lab}#{idx} is {op!r}, expected 'Do'")
            nxt = lookup_xobject(resources, str(inst.operands[0]))
            if nxt is None:
                raise ProvResolveError(f"XObject {inst.operands[0]} not found")
            resources = form_resources(nxt, resources)
            stream = nxt
            label = prov.chain[depth + 1][0]
        else:
            if op not in ("S", "s", "f", "F", "f*", "B", "B*", "b", "b*"):
                raise ProvResolveError(f"final entry {lab}#{idx} is {op!r}, not a painting operator")
            first = instrs[prov.first_op]
            fop = str(first.operator)
            if fop not in ("m", "re", "l", "c", "v", "y"):
                raise ProvResolveError(f"first_op {prov.first_op} is {fop!r}, not a path operator")
            if prov.first_op > idx:
                raise ProvResolveError("first_op after painting operator")
            return {
                "stream": label,
                "paint": (op, [str(o) for o in inst.operands]),
                "first": (fop, [str(o) for o in first.operands]),
            }
    raise ProvResolveError("empty chain")
