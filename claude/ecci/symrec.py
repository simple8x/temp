"""M4.2 symbol records: `ecci.symbols` schema 1, bound to one `ecci.connectivity` document.

What a record says, and only what the evidence supports:
  cluster      the body geometry (prim ids + their PDF content refs, bbox, verts, segs) that was matched
  candidates   EVERY template hit of the library on that geometry (symbol, lib token, D4 orientation, scale,
               translation, residual, predicted pins), and which of them the pipeline kept (`selected`)
  identity     CONFIRMED only if every selected hit names the same library symbol; AMBIGUOUS if several do;
               UNKNOWN if none matched
  transform    UNIQUE (one selected hit), EQUIVALENT (several hits of one symbol that put every pin number on
               the same tip, e.g. a diode mirrored about its axis), AMBIGUOUS, or UNKNOWN; no transform is
               chosen when the candidates disagree
  pins         the pin objects of the connectivity document for this cluster (tip, base, numbers, states)
  reference    always UNKNOWN in schema 1: no stage of ECCI reads designators or values from the PDF yet, so
  value        a non-UNKNOWN value is rejected on read rather than trusted

The reader (`loads(text, conn_doc, library)`) trusts nothing it can recompute: it rebuilds every cluster,
re-runs template matching over the WHOLE library, and requires the stored candidates, selection, resolution,
identity, transform and pins to be exactly what that gives; it also checks the binding to the connectivity
document (hash, pin objects, primitive refs) and the library hash.

Not re-verifiable from the file alone (stated in every record): that the cluster geometry is the geometry of
the PDF (needs the page), and the pin-line support behind a `PIN_LINES` selection.
"""
from __future__ import annotations

import hashlib
import json
import math

from .pins import _part_stem
from .symbols import Cluster
from .template import D4, MatchParams, match_cluster, resolve

FORMAT = "ecci.symbols"
SCHEMA_VERSION = 1
UNKNOWN = "UNKNOWN"
ID_CONFIRMED, ID_AMBIGUOUS = "CONFIRMED", "AMBIGUOUS"
TF_UNIQUE, TF_EQUIVALENT, TF_AMBIGUOUS = "UNIQUE", "EQUIVALENT", "AMBIGUOUS"
SEL_ALL, SEL_PIN_LINES = "ALL", "PIN_LINES"
NOT_FROM_FILE = ("cluster geometry vs PDF page", "pin-line support of a PIN_LINES selection")

TOP_KEYS = frozenset({"format", "schema_version", "connectivity_sha256", "library", "match_params", "source",
                      "clusters", "not_reverifiable_from_file", "content_sha256"})
CLUSTER_KEYS = frozenset({"id", "primitives", "bbox", "verts", "segs", "resolution", "candidates", "identity",
                          "transform", "pins", "reference", "value", "unresolved_terminals", "pin_conflicts"})
CAND_KEYS = frozenset({"symbol", "lib_token", "orient", "matrix", "scale", "tx", "ty", "residual", "selected", "pins"})
PIN_KEYS = frozenset({"object", "tip", "base", "numbers", "pos_state", "id_state"})
FIELD_KEYS = frozenset({"value", "state", "evidence"})


class SymbolDocError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems[:8]) + (f" (+{len(problems) - 8} more)" if len(problems) > 8 else ""))
        self.problems = problems


def canonical(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"


def content_hash(doc: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in doc.items() if k != "content_sha256"}).encode()).hexdigest()


def _f(x) -> float:
    return float(x)


def _pt(p) -> list:
    return [_f(p[0]), _f(p[1])]


def _cand(h, selected: bool) -> dict:
    return {"symbol": h.symbol.name, "lib_token": h.symbol.lib_token, "orient": int(h.orient),
            "matrix": list(D4[h.orient]), "scale": _f(h.scale), "tx": _f(h.tx), "ty": _f(h.ty),
            "residual": _f(h.residual), "selected": bool(selected),
            "pins": [{"number": p.number, "tip": _pt(h.place(*p.tip)), "base": _pt(h.place(*p.base))}
                     for p in h.symbol.pins]}


def _hit_key(c: dict) -> tuple:
    return (c["symbol"], c["orient"], repr(c["scale"]), repr(c["tx"]), repr(c["ty"]))


def _number_map(c: dict, tau: float) -> list:
    return sorted((p["number"], round(p["tip"][0] / max(tau, 1e-9)), round(p["tip"][1] / max(tau, 1e-9)))
                  for p in c["pins"])


def part_stem(name: str) -> str:
    """The part a symbol name belongs to, by the SAME rule pins.detect_pins used to allow a PIN_LINES choice
    (library nickname, unit suffix '#uN' and KiCad's duplicate-embedding suffixes '_N' ignored)."""
    return _part_stem(name)


def derive_identity(selected: list[dict]) -> dict:
    names = sorted({c["symbol"] for c in selected})
    if not names:
        return {"state": UNKNOWN, "lib_symbol": None, "names": []}
    if len(names) == 1:
        return {"state": ID_CONFIRMED, "lib_symbol": names[0], "names": names}
    return {"state": ID_AMBIGUOUS, "lib_symbol": None, "names": names}


def _equivalent(a: dict, b: dict, tau: float) -> bool:
    """Same symbol and every pin number lands on the same tip (within tau)."""
    if a["symbol"] != b["symbol"] or len(a["pins"]) != len(b["pins"]):
        return False
    pa = sorted(a["pins"], key=lambda p: p["number"])
    pb = sorted(b["pins"], key=lambda p: p["number"])
    return all(x["number"] == y["number"] and math.hypot(x["tip"][0] - y["tip"][0], x["tip"][1] - y["tip"][1]) <= tau
               for x, y in zip(pa, pb))


def derive_transform(selected: list[dict], tau: float) -> dict:
    if not selected:
        return {"state": UNKNOWN, "candidate": None, "n_candidates": 0}
    if len(selected) == 1:
        return {"state": TF_UNIQUE, "candidate": 0, "n_candidates": 1}
    if all(_equivalent(selected[0], c, tau) for c in selected[1:]):
        # electrically equivalent: the export may use candidate 0, and must say that it is a choice
        return {"state": TF_EQUIVALENT, "candidate": 0, "n_candidates": len(selected)}
    return {"state": TF_AMBIGUOUS, "candidate": None, "n_candidates": len(selected)}


def _cluster_of(rec: dict) -> Cluster:
    return Cluster(int(rec["id"]), tuple(int(p["id"]) for p in rec["primitives"]),
                   tuple(_f(v) for v in rec["bbox"]), tuple((_f(a), _f(b)) for a, b in rec["verts"]),
                   tuple(((_f(a[0]), _f(a[1])), (_f(b[0]), _f(b[1]))) for a, b in rec["segs"]))


def _tau(cl: Cluster, mp: MatchParams) -> float:
    w, h = cl.bbox[2] - cl.bbox[0], cl.bbox[3] - cl.bbox[1]
    return mp.tau_rel * math.hypot(w, h) + mp.tau_abs


def _unknown_field() -> dict:
    return {"value": None, "state": UNKNOWN, "evidence": []}


def build_symbols(clusters, page, library, conn_doc: dict, match_params: MatchParams = MatchParams(),
                  check: bool = True) -> dict:
    """Records for every cluster of `pins.detect_pins`. `library` is the `kicad_lib.Library` the pipeline used,
    `conn_doc` the connectivity document (writer.build_document) of the same page."""
    syms = list(library.symbols.values())
    prov = {p.id: p.prov.content_ref for p in getattr(page, "prims", ())}
    pin_objs = {o["id"]: o for o in conn_doc["objects"] if o["kind"] == "PIN"}
    recs = []
    for cr in sorted(clusters, key=lambda c: c.cluster.id):
        cl = cr.cluster
        tau = _tau(cl, match_params)
        allh = match_cluster(cl, syms, match_params) if syms else []
        sel_keys = {(h.symbol.name, h.orient, repr(float(h.scale)), repr(float(h.tx)), repr(float(h.ty)))
                    for h in cr.resolution.hits}
        # kept in match order: `resolve` numbers the pins after its first hit, so the order is part of the record
        cands = [_cand(h, _hit_key(_cand(h, False)) in sel_keys) for h in allh]
        selected = [c for c in cands if c["selected"]]
        if len(selected) != len(cr.resolution.hits):
            raise ValueError(f"cluster {cl.id}: pipeline hits are not reproducible from the cluster geometry")
        pins = []
        for k, pin in enumerate(cr.pins):
            oid = f"p:{cl.id}:{k}"
            if oid not in pin_objs:
                raise ValueError(f"cluster {cl.id}: pin object {oid} missing from the connectivity document")
            pins.append({"object": oid, "tip": _pt(pin.tip), "base": _pt(pin.base), "numbers": list(pin.numbers),
                         "pos_state": pin.pos_state, "id_state": pin.id_state})
        recs.append({
            "id": int(cl.id),
            "primitives": [{"id": int(i), "content_ref": prov.get(i)} for i in cl.prim_ids],
            "bbox": [_f(v) for v in cl.bbox], "verts": [_pt(v) for v in cl.verts],
            "segs": [[_pt(a), _pt(b)] for a, b in cl.segs],
            "resolution": {"status": cr.resolution.status, "id_state": cr.resolution.id_state,
                           "note": cr.resolution.note,
                           "selection": SEL_ALL if len(selected) == len(cands) else SEL_PIN_LINES},
            "candidates": cands,
            "identity": derive_identity(selected),
            "transform": derive_transform(selected, tau),
            "pins": pins,
            "reference": _unknown_field(), "value": _unknown_field(),
            "unresolved_terminals": len(cr.unresolved), "pin_conflicts": len(cr.conflicts),
        })
    doc = {
        "format": FORMAT, "schema_version": SCHEMA_VERSION,
        "connectivity_sha256": conn_doc["content_sha256"],
        "library": {"sha256": library.sha256, "n_symbols": len(syms)},
        "match_params": {"tau_rel": match_params.tau_rel, "tau_abs": match_params.tau_abs,
                         "scale_isotropy": match_params.scale_isotropy, "min_scale": match_params.min_scale},
        "source": {"page_index": conn_doc["source"]["page_index"], "page_size": conn_doc["source"]["page_size"]},
        "clusters": recs,
        "not_reverifiable_from_file": list(NOT_FROM_FILE),
    }
    doc["content_sha256"] = content_hash(doc)
    problems = validate(doc, conn_doc, library) if check else []     # check=False: the caller runs loads()
    if problems:
        raise ValueError("symbol writer produced an invalid document: " + "; ".join(problems[:5]))
    return doc


def dumps(doc: dict) -> str:
    return canonical(doc)


def _no_dup(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise SymbolDocError([f"duplicate key {k!r}"])
        out[k] = v
    return out


def loads(text: str, conn_doc: dict, library) -> dict:
    try:
        doc = json.loads(text, object_pairs_hook=_no_dup,
                         parse_constant=lambda c: (_ for _ in ()).throw(SymbolDocError([f"constant {c}"])))
    except json.JSONDecodeError as e:
        raise SymbolDocError([f"not JSON: {e}"]) from e
    problems = validate(doc, conn_doc, library)
    if not problems and canonical(doc) != text:
        problems = ["text is not the canonical serialization"]
    if problems:
        raise SymbolDocError(problems)
    return doc


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _pt_ok(p) -> bool:
    return isinstance(p, list) and len(p) == 2 and all(_num(v) for v in p)


def validate(doc, conn_doc: dict, library) -> list[str]:
    try:
        return _validate(doc, conn_doc, library)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as e:   # malformed -> fail closed
        return [f"malformed document: {type(e).__name__}: {e}"]


def _validate(doc, conn_doc, library) -> list[str]:
    P: list[str] = []
    if not isinstance(doc, dict) or set(doc) != TOP_KEYS:
        return [f"top-level keys wrong: {sorted(set(doc) ^ TOP_KEYS) if isinstance(doc, dict) else type(doc)}"]
    if doc["format"] != FORMAT or doc["schema_version"] != SCHEMA_VERSION:
        P.append("format / schema_version")
    if doc["content_sha256"] != content_hash(doc):
        P.append("content_sha256 mismatch")
    if doc["connectivity_sha256"] != conn_doc.get("content_sha256"):
        P.append("connectivity_sha256 does not bind to the given connectivity document")
    if doc["library"] != {"sha256": library.sha256, "n_symbols": len(library.symbols)}:
        P.append("library hash / size does not match the given library")
    mp = doc["match_params"]
    if set(mp) != {"tau_rel", "tau_abs", "scale_isotropy", "min_scale"} or not all(_num(v) for v in mp.values()):
        return P + ["match_params malformed"]
    params = MatchParams(**{k: float(v) for k, v in mp.items()})
    if doc["source"] != {"page_index": conn_doc["source"]["page_index"], "page_size": conn_doc["source"]["page_size"]}:
        P.append("source does not match the connectivity document")
    if doc["not_reverifiable_from_file"] != list(NOT_FROM_FILE):
        P.append("not_reverifiable_from_file must state the limits verbatim")
    if not isinstance(doc["clusters"], list):
        return P + ["clusters is not a list"]
    syms = list(library.symbols.values())
    conn_pins = {o["id"]: o for o in conn_doc["objects"] if o["kind"] == "PIN"}
    conn_prims = {p["id"]: p["content_ref"] for p in conn_doc["primitives"]}
    seen_ids, seen_pins = set(), set()
    for rec in doc["clusters"]:
        if not isinstance(rec, dict) or set(rec) != CLUSTER_KEYS:
            P.append(f"cluster record keys wrong: {sorted(set(rec) ^ CLUSTER_KEYS) if isinstance(rec, dict) else rec!r}")
            continue
        cid = rec["id"]
        w = f"cluster {cid}"
        if not isinstance(cid, int) or isinstance(cid, bool) or cid in seen_ids:
            P.append(f"{w}: id not a unique int")
            continue
        seen_ids.add(cid)
        if not (isinstance(rec["primitives"], list) and rec["primitives"] and all(
                isinstance(p, dict) and set(p) == {"id", "content_ref"} and isinstance(p["id"], int) for p in rec["primitives"])):
            P.append(f"{w}: primitives malformed")
            continue
        for p in rec["primitives"]:
            if p["id"] in conn_prims and conn_prims[p["id"]] != p["content_ref"]:
                P.append(f"{w}: primitive {p['id']} content_ref differs from the connectivity document")
        if not (isinstance(rec["bbox"], list) and len(rec["bbox"]) == 4 and all(_num(v) for v in rec["bbox"])
                and all(_pt_ok(v) for v in rec["verts"]) and all(isinstance(s, list) and len(s) == 2 and
                                                                 all(_pt_ok(v) for v in s) for s in rec["segs"])):
            P.append(f"{w}: geometry malformed")
            continue
        cl = _cluster_of(rec)
        tau = _tau(cl, params)
        # 1. candidates: exactly the hits of the whole library on this geometry
        cands = rec["candidates"]
        if not isinstance(cands, list) or not all(isinstance(c, dict) and set(c) == CAND_KEYS for c in cands):
            P.append(f"{w}: candidate records malformed")
            continue
        hits = match_cluster(cl, syms, params) if syms else []
        want = [_cand(h, False) for h in hits]
        got = [{k: v for k, v in c.items() if k != "selected"} for c in cands]
        if got != [{k: v for k, v in c.items() if k != "selected"} for c in want]:
            P.append(f"{w}: candidates are not the library hits of the stored geometry "
                     f"({len(got)} stored, {len(want)} recomputed; forged, dropped or altered transform)")
            continue
        if not all(isinstance(c["selected"], bool) for c in cands):
            P.append(f"{w}: selected flag not bool")
            continue
        selected = [c for c in cands if c["selected"]]
        sel_hits = [h for h, c in zip(hits, cands) if c["selected"]]
        r = rec["resolution"]
        if not isinstance(r, dict) or set(r) != {"status", "id_state", "note", "selection"}:
            P.append(f"{w}: resolution malformed")
            continue
        sel_kind = SEL_ALL if len(selected) == len(cands) else SEL_PIN_LINES
        if r["selection"] != sel_kind:
            P.append(f"{w}: selection {r['selection']!r} but {len(selected)}/{len(cands)} candidates selected")
        if sel_kind == SEL_PIN_LINES:
            stems = {part_stem(c["symbol"]) for c in cands}
            if not selected or len(stems) != 1:
                P.append(f"{w}: a PIN_LINES selection may only choose among units of ONE part")
        res = resolve(cl, sel_hits, params)
        if (r["status"], r["id_state"], r["note"]) != (res.status, res.id_state, res.note):
            P.append(f"{w}: resolution {r['status']}/{r['id_state']} is not what the selected candidates give "
                     f"({res.status}/{res.id_state})")
        if sel_kind == SEL_PIN_LINES and res.status != "UNIQUE":
            P.append(f"{w}: PIN_LINES selection must resolve UNIQUE")
        if rec["identity"] != derive_identity(selected):
            P.append(f"{w}: identity is not derived from the selected candidates")
        if rec["transform"] != derive_transform(selected, tau):
            P.append(f"{w}: transform state is not derived from the selected candidates")
        # 2. pins: the connectivity document's pin objects of this cluster, and those of the resolution
        pins = rec["pins"]
        if not isinstance(pins, list) or not all(isinstance(p, dict) and set(p) == PIN_KEYS for p in pins):
            P.append(f"{w}: pin records malformed")
            continue
        want_objs = sorted(o for o, ob in conn_pins.items() if ob["pin"] and ob["pin"]["cluster"] == cid)
        if sorted(p["object"] for p in pins) != want_objs:
            P.append(f"{w}: pin objects {sorted(p['object'] for p in pins)} != connectivity pins {want_objs}")
            continue
        if res.status == "UNIQUE" and len(pins) != len(res.pins):
            P.append(f"{w}: {len(pins)} pins but the resolution has {len(res.pins)}")
        for p in pins:
            seen_pins.add(p["object"])
            cp = conn_pins[p["object"]]["pin"]
            if [p["tip"], p["base"], p["numbers"], p["pos_state"], p["id_state"]] != \
                    [cp["tip"], cp["base"], cp["numbers"], cp["pos_state"], cp["id_state"]]:
                P.append(f"{w}: pin {p['object']} differs from the connectivity document")
            k = int(p["object"].rsplit(":", 1)[1])
            if res.status == "UNIQUE" and k < len(res.pins):
                rp = res.pins[k]
                if math.hypot(rp.tip[0] - p["tip"][0], rp.tip[1] - p["tip"][1]) > 1e-9 or list(rp.numbers) != p["numbers"]:
                    P.append(f"{w}: pin {p['object']} is not the resolution's pin {k}")
            if p["id_state"] != res.id_state:
                P.append(f"{w}: pin {p['object']} id_state differs from the resolution")
        # 3. reference / value: no evidence kind exists in schema 1, so only UNKNOWN is accepted
        for f in ("reference", "value"):
            if rec[f] != _unknown_field():
                P.append(f"{w}: {f} must be UNKNOWN/null with no evidence in schema 1 (no designator reader exists)")
        for f in ("unresolved_terminals", "pin_conflicts"):
            if not isinstance(rec[f], int) or isinstance(rec[f], bool) or rec[f] < 0:
                P.append(f"{w}: {f} malformed")
    missing = sorted(set(conn_pins) - seen_pins)
    if missing:
        P.append(f"connectivity pin objects without a cluster record: {missing[:5]}")
    return P


def audit_against_page(doc: dict, page) -> list[str]:
    """The check the file alone cannot make: every cluster's geometry and primitive refs are those of the PDF
    page (`ir.PageIngest`), i.e. the record describes drawn primitives and not invented ones."""
    from .symbols import _rebuild
    by_id = {p.id: p for p in page.prims}
    P = []
    for rec in doc["clusters"]:
        ids = tuple(int(p["id"]) for p in rec["primitives"])
        miss = [i for i in ids if i not in by_id]
        if miss:
            P.append(f"cluster {rec['id']}: primitives {miss[:5]} are not on the page")
            continue
        for p in rec["primitives"]:
            if by_id[p["id"]].prov.content_ref != p["content_ref"]:
                P.append(f"cluster {rec['id']}: primitive {p['id']} content_ref differs from the page")
        cl = _rebuild(rec["id"], ids, by_id)
        if [list(cl.bbox), [list(v) for v in cl.verts], [[list(a), list(b)] for a, b in cl.segs]] != \
                [rec["bbox"], rec["verts"], rec["segs"]]:
            P.append(f"cluster {rec['id']}: geometry is not the geometry of its primitives on the page")
    return P


def audit_evidence(conn: dict, syms: dict, page, library) -> list[str]:
    """Evidence lineage is not re-derivable from the connectivity file alone (its reader checks closure, ids
    and independence, not that a lineage names real geometry). Against the page and the library:
      every `geom:N` is a primitive of the page and every `lib:` token is the library's token;
      S1 (template pin) lineage is exactly the selected candidates' library tokens + the cluster's primitives;
      S2 (measured stub) lineage is one primitive; S3 (terminal contact) includes the cluster's primitives."""
    prims = {p.id for p in page.prims}
    tokens = {s.lib_token for s in library.symbols.values()}
    clusters = {c["id"]: c for c in syms["clusters"]}
    P = []
    for ev in conn["evidence"]:
        lin = ev["lineage"]
        for x in lin:
            if x.startswith("geom:") and (not x[5:].isdigit() or int(x[5:]) not in prims):
                P.append(f"evidence {ev['id']}: lineage {x} is not a primitive of the page")
            if x.startswith("lib:") and x not in tokens:
                P.append(f"evidence {ev['id']}: lineage {x} is not this library")
        kind, _, rest = ev["id"].partition(":")
        if kind in ("S1", "S2", "S3"):
            cid = int(rest.split(":")[0]) if rest.split(":")[0].isdigit() else None
            c = clusters.get(cid)
            if c is None:
                P.append(f"evidence {ev['id']}: no cluster {cid}")
                continue
            body = {f"geom:{p['id']}" for p in c["primitives"]}
            if kind == "S1":
                want = {x["lib_token"] for x in c["candidates"] if x["selected"]} | body
                if set(lin) != want:
                    P.append(f"evidence {ev['id']}: lineage is not the template + cluster geometry")
            elif kind == "S2" and (len(lin) != 1 or not lin[0].startswith("geom:")):
                P.append(f"evidence {ev['id']}: a measured stub is one primitive")
            elif kind == "S3" and not body <= set(lin):
                P.append(f"evidence {ev['id']}: a terminal contact names the cluster geometry")
    return P


def build_symbols_like(doc: dict, conn_doc: dict) -> dict:
    """The same records re-bound to another (reader-checked) connectivity document (tests / re-binding)."""
    d = {k: v for k, v in doc.items() if k != "content_sha256"}
    d["connectivity_sha256"] = conn_doc["content_sha256"]
    d["content_sha256"] = content_hash(d)
    return d
