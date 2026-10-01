from __future__ import annotations

from typing import Any, Dict, Iterable, List


TRACKER_COLUMNS = [
    "record_id",
    "title",
    "categories",
    "one_minute_summary",
    "data_sources",
    "background",
    "concerns_extensions",
    "created_at",
    "updated_at",
]


def _text(value: Any) -> str:
    return str(value or "")


def _data_sources_text(notes: Dict[str, Any]) -> str:
    rows = []
    for index, source in enumerate(notes.get("data_sources", []), start=1):
        name = _text(source.get("name")).strip() or f"Source {index}"
        description = _text(source.get("description")).strip()
        if name or description:
            rows.append(f"{name}: {description}" if description else name)
    return "\n".join(rows)


def build_tracker_row(notes: Dict[str, Any]) -> Dict[str, str]:
    """Build one stable, spreadsheet-ready row without creating a spreadsheet."""
    sections = notes.get("sections", {})
    return {
        "record_id": _text(notes.get("record_id")),
        "title": _text(notes.get("title")),
        "categories": ", ".join(
            _text(category).strip()
            for category in notes.get("categories", [])
            if _text(category).strip()
        ),
        "one_minute_summary": _text(sections.get("one_minute_summary")),
        "data_sources": _data_sources_text(notes),
        "background": _text(sections.get("background")),
        "concerns_extensions": _text(sections.get("concerns_extensions")),
        "created_at": _text(notes.get("created_at")),
        "updated_at": _text(notes.get("updated_at")),
    }


def build_tracker_rows(notes_collection: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Return rows keyed by immutable record ID, ready for a future upsert workflow."""
    rows_by_id = {}
    for notes in notes_collection:
        row = build_tracker_row(notes)
        if row["record_id"]:
            rows_by_id[row["record_id"]] = row
    return list(rows_by_id.values())
