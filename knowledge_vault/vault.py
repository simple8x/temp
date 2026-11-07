"""Small, filesystem-backed, evidence-first Markdown knowledge vault.

The vault deliberately does not generate or verify semantic claims. It preserves
raw sources, records provenance, requires an explicit human verification receipt,
and returns source-backed notes rather than inventing answers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, date
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Iterable
import json
import re


class VaultError(ValueError):
    """Invalid vault operation or inconsistent evidence."""


class NoteStatus(str, Enum):
    OBSERVED = "OBSERVED"
    VERIFIED = "VERIFIED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    value = value.strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise VaultError("id must be 1-100 safe ASCII characters")
    return value


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


@dataclass(frozen=True)
class AnswerEvidence:
    note_id: str
    title: str
    excerpt: str
    path: str
    status: str


class KnowledgeVault:
    """Markdown files plus JSON provenance sidecars; no database or plugin."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.raw_dir = self.root / "raw"
        self.notes_dir = self.root / "notes"
        self.archive_dir = self.root / "archive"
        self.inbox_dir = self.root / "inbox"
        for directory in (self.raw_dir, self.notes_dir, self.archive_dir, self.inbox_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def _inside(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise VaultError("path escapes vault root") from exc

    def ingest(self, text: str, source_uri: str, captured_at: str | None = None) -> dict:
        """Persist exact source text before any interpretation; never overwrite."""
        if not isinstance(text, str) or not text:
            raise VaultError("source text must be a non-empty string")
        if not isinstance(source_uri, str) or not source_uri.strip():
            raise VaultError("source_uri is required")
        captured_at = captured_at or _utc_now()
        try:
            captured_date = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise VaultError("captured_at must be ISO-8601") from exc
        digest = sha256(text.encode("utf-8")).hexdigest()
        target_dir = self.raw_dir / captured_date.isoformat()
        target_dir.mkdir(parents=True, exist_ok=True)
        raw_path = target_dir / f"{digest}.txt"
        meta_path = target_dir / f"{digest}.json"
        if raw_path.exists() and raw_path.read_text(encoding="utf-8") != text:
            raise VaultError("hash collision or corrupted raw source")
        if not raw_path.exists():
            raw_path.write_text(text, encoding="utf-8", newline="")
        metadata = {
            "sha256": digest,
            "source_uri": source_uri.strip(),
            "captured_at": captured_at,
            "raw_path": self._inside(raw_path),
            "byte_length": len(text.encode("utf-8")),
        }
        if meta_path.exists():
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
            if existing != metadata:
                raise VaultError("same source hash already has different provenance metadata")
        else:
            _write_json(meta_path, metadata)
        return metadata

    def add_note(
        self,
        note_id: str,
        title: str,
        body: str,
        source_path: str,
        status: NoteStatus | str = NoteStatus.OBSERVED,
        links: Iterable[str] = (),
    ) -> dict:
        """Create one atomic note tied to an ingested raw source."""
        note_id = _safe_id(note_id)
        title = title.strip()
        if not title or not body.strip():
            raise VaultError("title and body are required")
        status = NoteStatus(status)
        if status == NoteStatus.VERIFIED:
            raise VaultError("use verify_note() with an explicit verification receipt")
        source = (self.root / source_path).resolve()
        source_rel = self._inside(source)
        if not source.is_file() or source.parent != source.parent.parent and self.raw_dir not in source.parents:
            raise VaultError("source_path must reference an ingested raw source")
        sidecar = source.with_suffix(".json")
        if not sidecar.is_file():
            raise VaultError("raw source provenance sidecar is missing")
        source_meta = json.loads(sidecar.read_text(encoding="utf-8"))
        actual_hash = sha256(source.read_bytes()).hexdigest()
        if actual_hash != source_meta.get("sha256"):
            raise VaultError("raw source hash mismatch")
        linked = sorted({_safe_id(item) for item in links})
        if note_id in linked:
            raise VaultError("a note cannot link to itself")
        for linked_id in linked:
            if not (self.notes_dir / f"{linked_id}.json").is_file():
                raise VaultError(f"linked note does not exist: {linked_id}")
        note_path = self.notes_dir / f"{note_id}.md"
        meta_path = self.notes_dir / f"{note_id}.json"
        if note_path.exists() or meta_path.exists():
            raise VaultError(f"note already exists: {note_id}")
        note_path.write_text(f"# {title}\n\n{body.rstrip()}\n", encoding="utf-8")
        metadata = {
            "id": note_id,
            "title": title,
            "status": status.value,
            "created_at": _utc_now(),
            "source_path": source_rel,
            "source_sha256": actual_hash,
            "source_uri": source_meta["source_uri"],
            "links": linked,
            "verification": None,
        }
        _write_json(meta_path, metadata)
        return metadata

    def verify_note(self, note_id: str, verifier: str, checked_excerpt: str) -> dict:
        """Record an explicit verification action; excerpt must exist in raw source.

        This records who checked the source and what exact text they inspected.
        It does not claim to prove that the note's interpretation is correct.
        """
        note_id = _safe_id(note_id)
        verifier = verifier.strip()
        if not verifier or not checked_excerpt:
            raise VaultError("verifier and checked_excerpt are required")
        meta_path = self.notes_dir / f"{note_id}.json"
        if not meta_path.is_file():
            raise VaultError(f"unknown note: {note_id}")
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        source = self.root / metadata["source_path"]
        raw_text = source.read_text(encoding="utf-8")
        digest = sha256(source.read_bytes()).hexdigest()
        if digest != metadata["source_sha256"]:
            raise VaultError("source changed since note creation")
        if checked_excerpt not in raw_text:
            raise VaultError("checked_excerpt is not present verbatim in the source")
        metadata["status"] = NoteStatus.VERIFIED.value
        metadata["verification"] = {
            "verifier": verifier,
            "checked_at": _utc_now(),
            "checked_excerpt": checked_excerpt,
            "source_sha256": digest,
            "meaning": "explicit source inspection receipt; semantic correctness remains a human judgment",
        }
        _write_json(meta_path, metadata)
        return metadata

    def set_status(self, note_id: str, status: NoteStatus | str, reason: str) -> dict:
        """Set a non-VERIFIED state, including explicit downgrades/conflicts."""
        note_id = _safe_id(note_id)
        status = NoteStatus(status)
        if status == NoteStatus.VERIFIED:
            raise VaultError("VERIFIED requires verify_note()")
        if not reason.strip():
            raise VaultError("a reason is required for status changes")
        meta_path = self.notes_dir / f"{note_id}.json"
        if not meta_path.is_file():
            raise VaultError(f"unknown note: {note_id}")
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata["status"] = status.value
        metadata.setdefault("status_history", []).append(
            {"status": status.value, "reason": reason.strip(), "changed_at": _utc_now()}
        )
        _write_json(meta_path, metadata)
        return metadata

    def link_notes(self, source_id: str, target_id: str) -> dict:
        source_id, target_id = _safe_id(source_id), _safe_id(target_id)
        if source_id == target_id:
            raise VaultError("self-links are not allowed")
        source_meta = self.notes_dir / f"{source_id}.json"
        target_meta = self.notes_dir / f"{target_id}.json"
        if not source_meta.is_file() or not target_meta.is_file():
            raise VaultError("both notes must exist before linking")
        metadata = json.loads(source_meta.read_text(encoding="utf-8"))
        metadata["links"] = sorted(set(metadata.get("links", [])) | {target_id})
        _write_json(source_meta, metadata)
        return metadata

    def answer_evidence(self, question: str) -> list[AnswerEvidence]:
        """Return matching vault evidence only; record a gap if nothing matches."""
        terms = {t.lower() for t in re.findall(r"[A-Za-z0-9_]{3,}", question)}
        if not terms:
            raise VaultError("question must contain searchable terms")
        matches: list[tuple[int, AnswerEvidence]] = []
        for meta_path in sorted(self.notes_dir.glob("*.json")):
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            note_path = self.notes_dir / f"{metadata['id']}.md"
            content = note_path.read_text(encoding="utf-8")
            haystack = (metadata["title"] + " " + content).lower()
            score = sum(term in haystack for term in terms)
            if score:
                matches.append((score, AnswerEvidence(
                    note_id=metadata["id"],
                    title=metadata["title"],
                    excerpt=content[:500].strip(),
                    path=self._inside(note_path),
                    status=metadata["status"],
                )))
        matches.sort(key=lambda item: (-item[0], item[1].note_id))
        if not matches:
            self.record_gap(question, "No matching note found; no answer inferred.")
        return [item[1] for item in matches]

    def record_gap(self, question: str, reason: str) -> Path:
        gap_path = self.inbox_dir / "gaps.md"
        with gap_path.open("a", encoding="utf-8") as stream:
            stream.write(f"\n## { _utc_now() }\n\n- Question: {question.strip()}\n- Gap: {reason.strip()}\n")
        return gap_path

    def daily_note(self, day: date | None = None) -> Path:
        """Write a concise daily index; source notes remain untouched."""
        day = day or datetime.now(timezone.utc).date()
        rows = []
        for meta_path in sorted(self.notes_dir.glob("*.json")):
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            if metadata["created_at"][:10] == day.isoformat():
                rows.append(f"- [{metadata['status']}] [[{metadata['id']}]] — {metadata['title']}")
        path = self.root / "daily" / f"{day.isoformat()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Daily note — {day.isoformat()}\n\n"
            + ("\n".join(rows) if rows else "No notes created today.")
            + "\n",
            encoding="utf-8",
        )
        return path

    def audit(self) -> dict:
        """Check raw hashes, source lineage, note links, and orphan notes."""
        errors, orphans = [], []
        metadata_by_id = {}
        for meta_path in sorted(self.notes_dir.glob("*.json")):
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            metadata_by_id[metadata["id"]] = metadata
            source = self.root / metadata["source_path"]
            if not source.is_file():
                errors.append(f"{metadata['id']}: missing source {metadata['source_path']}")
            elif sha256(source.read_bytes()).hexdigest() != metadata["source_sha256"]:
                errors.append(f"{metadata['id']}: source hash mismatch")
        for note_id, metadata in metadata_by_id.items():
            links = metadata.get("links", [])
            if not links:
                orphans.append(note_id)
            for target in links:
                if target not in metadata_by_id:
                    errors.append(f"{note_id}: dangling link {target}")
        return {"ok": not errors, "errors": errors, "orphans": orphans, "note_count": len(metadata_by_id)}
