"""Fail-closed IngestLab ParseResult adapter; never emits electrical connectivity."""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path
from typing import Any

SCHEMA = "ecci.ingestlab-evidence"
ALLOWED = {"CANDIDATE", "INFERRED", "UNKNOWN", "CONFLICT"}
TEXT_TYPES = {"heading", "paragraph", "aside", "form_section"}
TABLE_TYPES = {"table", "form_grid", "form_matrix", "worksheet", "kv_panel"}

class AdapterError(ValueError):
    pass

def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")

def _bbox(raw: Any, width: float, height: float) -> list[float]:
    if not isinstance(raw, dict):
        raise AdapterError("source bbox is missing")
    vals = [raw.get(k) for k in ("x0", "y0", "x1", "y1")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vals):
        raise AdapterError("bbox coordinates must be finite numbers")
    x0, y0, x1, y1 = map(float, vals)
    if x0 < 0 or y0 < 0 or x1 < x0 or y1 < y0 or x1 > width or y1 > height:
        raise AdapterError("bbox is outside declared page dimensions")
    return [x0 / width, y0 / height, x1 / width, y1 / height]

def _text(block: dict[str, Any]) -> str | None:
    typ = block.get("type")
    if typ in TEXT_TYPES and isinstance(block.get("text"), str):
        return block["text"]
    if typ in TABLE_TYPES and isinstance(block.get("rows"), list):
        cells = []
        for row in block["rows"]:
            if isinstance(row, list):
                cells.extend(str(v) for v in row if v is not None)
            elif isinstance(row, dict):
                cells.extend(str(v) for v in row.values() if v is not None)
        return "\n".join(cells)
    if typ == "figure":
        caption = block.get("caption") or block.get("sourceCaption")
        return caption if isinstance(caption, str) and caption else None
    if typ == "image":
        value = block.get("text")
        return value if isinstance(value, str) and value else None
    return None

def adapt_parse_result(pdf_bytes: bytes, result: dict[str, Any],
                       page_dimensions: list[tuple[float, float]], *,
                       extractor_version: str, extractor_config: dict[str, Any]) -> dict[str, Any]:
    """Convert an IngestLab ParseResult JSON object into candidate evidence only.

    Page dimensions must come from an independent PDF metadata reader. Coordinates
    are not guessed. OCR evidence is UNKNOWN; multi-page RAG chunks are unresolved.
    """
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes:
        raise AdapterError("original PDF bytes are required")
    if not isinstance(result, dict) or result.get("kind", "pdf") not in ("pdf", None):
        raise AdapterError("expected an IngestLab PDF ParseResult object")
    if not isinstance(extractor_version, str) or not extractor_version.strip():
        raise AdapterError("exact extractor version is required")
    if not isinstance(extractor_config, dict):
        raise AdapterError("extractor config must be an object")
    stats = result.get("stats") or {}
    page_count = stats.get("pages")
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count <= 0:
        raise AdapterError("ParseResult.stats.pages must be a positive integer")
    if len(page_dimensions) != page_count:
        raise AdapterError("independent page dimensions required for every page")
    dims = []
    for pair in page_dimensions:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise AdapterError("page dimension must be (width, height)")
        w, h = pair
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
               for v in (w, h)):
            raise AdapterError("page dimensions must be finite positive numbers")
        dims.append((float(w), float(h)))

    source_hash = hashlib.sha256(pdf_bytes).hexdigest()
    config_hash = hashlib.sha256(_canonical(extractor_config)).hexdigest()
    items, unresolved, seen = [], [], set()

    def add(item_id: str, kind: str, page: int, raw_bbox: Any, raw_text: str,
            payload: Any, status: str) -> None:
        if page < 0 or page >= page_count:
            unresolved.append({"id": item_id, "reason": "PAGE_OUT_OF_RANGE"})
            return
        try:
            normalized = _bbox(raw_bbox, *dims[page])
        except AdapterError as exc:
            unresolved.append({"id": item_id, "reason": str(exc)})
            return
        if item_id in seen:
            raise AdapterError(f"duplicate evidence id: {item_id}")
        seen.add(item_id)
        if status not in ALLOWED:
            raise AdapterError(f"forbidden adapter status: {status}")
        raw = {"kind": kind, "page_index": page, "bbox": normalized,
               "raw_text": raw_text, "payload": payload}
        items.append({"id": item_id, "kind": kind, "page_index": page,
                      "bbox": normalized, "raw_text": raw_text,
                      "raw_payload_sha256": hashlib.sha256(_canonical(raw)).hexdigest(),
                      "status": status, "source_sha256": source_hash})

    pages = result.get("pages")
    if isinstance(pages, list):
        for pi, blocks in enumerate(pages):
            if not isinstance(blocks, list):
                unresolved.append({"id": f"page:{pi}", "reason": "MALFORMED_PAGE_BLOCKS"})
                continue
            for bi, block in enumerate(blocks):
                item_id = f"ingestlab:p{pi}:b{bi}"
                if not isinstance(block, dict):
                    unresolved.append({"id": item_id, "reason": "MALFORMED_BLOCK"})
                    continue
                value = _text(block)
                if value is None or not value.strip():
                    continue
                typ = block.get("type")
                kind = ("ocr_text" if block.get("ocr") or block.get("viaVision") else
                        "table_cell" if typ in TABLE_TYPES else
                        "figure" if typ in ("figure", "image") else "text")
                add(item_id, kind, pi, block.get("bbox"), value, block,
                    "UNKNOWN" if kind == "ocr_text" else "CANDIDATE")

    chunks = result.get("ragChunks")
    if isinstance(chunks, list) and not pages:
        for i, chunk in enumerate(chunks):
            item_id = f"ingestlab:rag:{i}"
            if not isinstance(chunk, dict):
                unresolved.append({"id": item_id, "reason": "MALFORMED_CHUNK"})
                continue
            sources = chunk.get("sources")
            if not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict):
                unresolved.append({"id": item_id, "reason": "RAG_SOURCE_NOT_UNIQUE"})
                continue
            src = sources[0]
            page = src.get("page")
            if isinstance(page, bool) or not isinstance(page, int):
                unresolved.append({"id": item_id, "reason": "RAG_PAGE_MISSING"})
                continue
            value = chunk.get("text")
            if isinstance(value, str) and value.strip():
                add(item_id, "text", page - 1, src.get("bbox"), value, chunk, "CANDIDATE")

    return {
        "schema": SCHEMA, "schema_version": 1,
        "document": {"source_sha256": source_hash, "source_bytes": len(pdf_bytes),
                     "page_count": page_count,
                     "extractor": {"name": "ingestlab", "version": extractor_version},
                     "extractor_config_sha256": config_hash},
        "items": items, "unresolved": unresolved,
        "summary": {"candidate_items": sum(x["status"] == "CANDIDATE" for x in items),
                    "unknown_items": sum(x["status"] == "UNKNOWN" for x in items),
                    "unresolved_items": len(unresolved), "connectivity_claims": 0,
                    "verified_items": 0},
        "authority": {"may_create_or_modify_nets": False, "may_promote_verified": False,
                      "promotion_authority": "ECCI independent verifier only"},
    }

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pdf", required=True, type=Path)
    p.add_argument("--result", required=True, type=Path)
    p.add_argument("--page-dimensions", required=True, type=Path,
                   help="JSON array of [width,height] PDF-point pairs from independent metadata")
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--version", required=True)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    try:
        out = adapt_parse_result(a.pdf.read_bytes(), json.loads(a.result.read_text("utf-8")),
            json.loads(a.page_dimensions.read_text("utf-8")),
            extractor_version=a.version, extractor_config=json.loads(a.config.read_text("utf-8")))
        a.out.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(out["summary"], sort_keys=True))
        return 2 if out["unresolved"] else 0
    except (OSError, json.JSONDecodeError, AdapterError) as exc:
        print(f"ADAPTER_BLOCKED: {exc}")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
