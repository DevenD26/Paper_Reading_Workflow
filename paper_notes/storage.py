from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import fcntl
import fitz

from PIL import Image, UnidentifiedImageError


SECTION_LABELS = [
    ("one_minute_summary", "1 Minute Summary"),
    ("contribution", "Contribution to the Literature and Broader Context"),
    ("background", "Background Details / Context"),
    ("data_sources", "Data Sources"),
    ("empirical_strategy", "Empirical Strategy and Identification Concerns"),
    ("exhibits", "Tables and Figures"),
    ("concerns_extensions", "Concerns and Extensions"),
    ("additional_notes", "Additional Notes"),
]

MAX_PDF_UPLOAD_BYTES = 100 * 1024 * 1024

TEXT_SECTION_KEYS = [
    "one_minute_summary",
    "contribution",
    "background",
    "empirical_strategy",
    "concerns_extensions",
    "additional_notes",
]

PAPER_ORDER_FILENAME = "paper_order.json"
PAPER_ORDER_SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_slug(value: str, fallback: str = "paper") -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return (slug[:72].strip("-") or fallback)


def safe_display_filename(value: str) -> str:
    name = Path(value).name
    cleaned = re.sub(r"[^a-zA-Z0-9._ -]+", "_", name).strip(" .")
    return cleaned[:120] or "paper.pdf"


def blank_data_source() -> Dict[str, Any]:
    return {"id": uuid.uuid4().hex, "name": "", "description": ""}


def blank_exhibit() -> Dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "identifier_title": "",
        "page_number": 1,
        "convey": "",
        "empirical_strategy": "",
        "interpretation": "",
        "qualifications": "",
        "image": None,
        "crop": None,
        "images": [],
    }


def blank_empirical_strategy() -> Dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "title": "",
        "page_number": 1,
        "description": "",
        "latex_equation": "",
        "image": None,
        "crop": None,
    }


def new_notes(record_id: str, title: str, original_pdf_name: str) -> Dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": 3,
        "revision": 0,
        "record_id": record_id,
        "title": title,
        "original_pdf_name": safe_display_filename(original_pdf_name),
        "categories": [],
        "created_at": now,
        "updated_at": now,
        "sections": {key: "" for key in TEXT_SECTION_KEYS},
        "data_sources": [],
        "empirical_strategies": [],
        "exhibits": [],
    }


def normalize_notes(notes: Dict[str, Any]) -> Dict[str, Any]:
    """Add new optional fields without changing or removing authored content."""
    payload = deepcopy(notes)
    payload["schema_version"] = max(int(payload.get("schema_version", 1)), 3)
    payload["revision"] = max(int(payload.get("revision", 0)), 0)
    sections = payload.setdefault("sections", {})
    for key in TEXT_SECTION_KEYS:
        sections.setdefault(key, "")
    payload.setdefault("data_sources", [])
    payload.setdefault("empirical_strategies", [])
    payload.setdefault("exhibits", [])
    for exhibit in payload["exhibits"]:
        if isinstance(exhibit, dict):
            images = exhibit.setdefault("images", [])
            if not isinstance(images, list):
                exhibit["images"] = []
    categories = payload.setdefault("categories", [])
    if not isinstance(categories, list):
        payload["categories"] = []
    return payload


def _inside(root: Path, candidate: Path) -> bool:
    root = root.resolve()
    candidate = candidate.resolve()
    return candidate == root or root in candidate.parents


def record_dir(data_root: Path, record_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", record_id):
        raise ValueError("Invalid record identifier")
    result = data_root.resolve() / record_id
    if not _inside(data_root, result):
        raise ValueError("Record path is outside the data folder")
    return result


def _atomic_json_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temporary:
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


@contextmanager
def _record_lock(folder: Path):
    """Serialize notes mutations across the organizer and linked extractor."""
    lock_path = folder / ".notes.lock"
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _paper_order_lock(data_root: Path):
    """Serialize paper-order migration and mutations across local app processes."""
    data_root.mkdir(parents=True, exist_ok=True)
    lock_path = data_root / ".paper-order.lock"
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_notes_file(folder: Path, record_id: str) -> Dict[str, Any]:
    with (folder / "notes.json").open("r", encoding="utf-8") as source:
        payload = normalize_notes(json.load(source))
    if payload.get("record_id") != record_id:
        raise ValueError("Record metadata does not match its folder")
    return payload


def _write_notes_file(folder: Path, payload: Dict[str, Any], current_revision: int) -> Dict[str, Any]:
    updated = normalize_notes(payload)
    updated["revision"] = current_revision + 1
    updated["updated_at"] = utc_now()
    _atomic_json_write(folder / "notes.json", updated)
    return updated


def create_record(
    data_root: Path,
    title: str,
    pdf_bytes: bytes,
    original_pdf_name: str,
) -> Dict[str, Any]:
    title = title.strip()
    if not title:
        raise ValueError("A paper title is required")
    validate_pdf_upload(pdf_bytes)

    data_root.mkdir(parents=True, exist_ok=True)
    base = safe_slug(title)
    counter = 1
    while True:
        record_id = base if counter == 1 else f"{base}-{counter}"
        folder = record_dir(data_root, record_id)
        try:
            folder.mkdir(parents=False, exist_ok=False)
            break
        except FileExistsError:
            counter += 1

    try:
        (folder / "exhibits").mkdir()
        (folder / "extraction_proposals").mkdir()
        (folder / "formatted_notes").mkdir()
        (folder / "source.pdf").write_bytes(pdf_bytes)
        notes = new_notes(record_id, title, original_pdf_name)
        _atomic_json_write(folder / "notes.json", notes)
        with _paper_order_lock(data_root):
            _reconcile_paper_order_unlocked(data_root, _discover_records(data_root))
        return notes
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def validate_pdf_upload(pdf_bytes: bytes) -> int:
    """Validate a complete local PDF before any record folder is created."""
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("The uploaded file does not appear to be a PDF.")
    if len(pdf_bytes) > MAX_PDF_UPLOAD_BYTES:
        raise ValueError("The uploaded PDF is larger than 100 MB.")
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            if document.page_count < 1:
                raise ValueError("The PDF contains no pages.")
            return document.page_count
    except (fitz.FileDataError, fitz.EmptyFileError, RuntimeError) as error:
        raise ValueError("The uploaded file is not a readable PDF.") from error


def save_notes(data_root: Path, notes: Dict[str, Any]) -> Dict[str, Any]:
    payload = normalize_notes(notes)
    folder = record_dir(data_root, str(payload["record_id"]))
    if not folder.is_dir():
        raise FileNotFoundError("The paper record folder no longer exists")
    with _record_lock(folder):
        current = _load_notes_file(folder, str(payload["record_id"]))
        if current["revision"] > payload["revision"]:
            incoming_by_id = {
                item.get("id"): item
                for item in payload.get("exhibits", [])
                if isinstance(item, dict) and item.get("id")
            }
            current_ids = {
                item.get("id")
                for item in current.get("exhibits", [])
                if isinstance(item, dict) and item.get("id")
            }
            merged_exhibits = []
            for current_item in current.get("exhibits", []):
                if not isinstance(current_item, dict):
                    continue
                item_id = current_item.get("id")
                if item_id in incoming_by_id:
                    merged_exhibits.append(incoming_by_id[item_id])
                elif current_item.get("extraction_source"):
                    merged_exhibits.append(deepcopy(current_item))
            merged_exhibits.extend(
                item
                for item in payload.get("exhibits", [])
                if not isinstance(item, dict) or item.get("id") not in current_ids
            )
            payload["exhibits"] = merged_exhibits
        return _write_notes_file(folder, payload, current["revision"])


def move_exhibit(
    data_root: Path,
    record_id: str,
    exhibit_id: str,
    direction: int,
) -> Dict[str, Any]:
    """Move one exhibit by one position without altering the exhibit or its image files."""
    if direction not in {-1, 1}:
        raise ValueError("Exhibit direction must be -1 or 1")
    folder = record_dir(data_root, record_id)
    if not folder.is_dir():
        raise FileNotFoundError("The paper record folder no longer exists")
    with _record_lock(folder):
        notes = _load_notes_file(folder, record_id)
        exhibits = notes.get("exhibits", [])
        matches = [
            index
            for index, exhibit in enumerate(exhibits)
            if isinstance(exhibit, dict) and exhibit.get("id") == exhibit_id
        ]
        if len(matches) != 1:
            raise ValueError("Exhibit not found")
        index = matches[0]
        destination = index + direction
        if destination < 0 or destination >= len(exhibits):
            return notes
        exhibits[index], exhibits[destination] = exhibits[destination], exhibits[index]
        return _write_notes_file(folder, notes, notes["revision"])


def mutate_notes(data_root: Path, record_id: str, mutator):
    """Apply one locked mutation to the latest notes and return the mutator result."""
    folder = record_dir(data_root, record_id)
    if not folder.is_dir():
        raise FileNotFoundError("The paper record folder no longer exists")
    with _record_lock(folder):
        notes = _load_notes_file(folder, record_id)
        result = mutator(notes)
        _write_notes_file(folder, notes, notes["revision"])
        return result


def load_notes(data_root: Path, record_id: str) -> Dict[str, Any]:
    folder = record_dir(data_root, record_id)
    return _load_notes_file(folder, record_id)


def update_record_title(data_root: Path, record_id: str, title: str) -> Dict[str, Any]:
    updated_title = title.strip()
    if not updated_title:
        raise ValueError("A paper title cannot be blank")
    notes = load_notes(data_root, record_id)
    notes["title"] = updated_title
    return save_notes(data_root, notes)


def delete_record(data_root: Path, record_id: str) -> Dict[str, Any]:
    """Permanently delete exactly one validated paper record folder."""
    notes = load_notes(data_root, record_id)
    folder = record_dir(data_root, record_id)
    with _paper_order_lock(data_root):
        if not folder.is_dir():
            raise FileNotFoundError("The paper record folder no longer exists")
        shutil.rmtree(folder)
        _reconcile_paper_order_unlocked(data_root, _discover_records(data_root))
    return notes


def _discover_records(data_root: Path) -> List[Dict[str, Any]]:
    if not data_root.exists():
        return []
    records: List[Dict[str, Any]] = []
    for notes_file in data_root.glob("*/notes.json"):
        try:
            with notes_file.open("r", encoding="utf-8") as source:
                payload = normalize_notes(json.load(source))
            record_id = str(payload.get("record_id", ""))
            if record_dir(data_root, record_id) != notes_file.parent.resolve():
                continue
            records.append(payload)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return records


def _legacy_record_order(records: Iterable[Dict[str, Any]]) -> List[str]:
    """Reproduce the former newest-save-first menu order with deterministic ties."""
    return [
        str(item["record_id"])
        for item in sorted(
            records,
            key=lambda item: (str(item.get("updated_at", "")), str(item.get("record_id", ""))),
            reverse=True,
        )
    ]


def _reconcile_paper_order_unlocked(
    data_root: Path, records: Iterable[Dict[str, Any]]
) -> List[str]:
    """Return and persist one complete, valid ordering while the order lock is held."""
    records = list(records)
    legacy_order = _legacy_record_order(records)
    available = set(legacy_order)
    order_path = data_root / PAPER_ORDER_FILENAME
    stored_order: Optional[List[Any]] = None
    raw_payload: Any = None
    try:
        with order_path.open("r", encoding="utf-8") as source:
            raw_payload = json.load(source)
        if (
            isinstance(raw_payload, dict)
            and raw_payload.get("schema_version") == PAPER_ORDER_SCHEMA_VERSION
            and isinstance(raw_payload.get("record_ids"), list)
        ):
            stored_order = raw_payload["record_ids"]
    except (OSError, json.JSONDecodeError):
        stored_order = None

    if stored_order is None:
        ordered_ids = legacy_order
    else:
        retained: List[str] = []
        seen = set()
        for value in stored_order:
            if isinstance(value, str) and value in available and value not in seen:
                retained.append(value)
                seen.add(value)
        # Records absent from existing metadata are new to the ordering system and
        # belong at the top. Their legacy order makes simultaneous additions stable.
        ordered_ids = [record_id for record_id in legacy_order if record_id not in seen]
        ordered_ids.extend(retained)

    normalized_payload = {
        "schema_version": PAPER_ORDER_SCHEMA_VERSION,
        "record_ids": ordered_ids,
    }
    if raw_payload != normalized_payload:
        _atomic_json_write(order_path, normalized_payload)
    return ordered_ids


def list_records(data_root: Path) -> List[Dict[str, Any]]:
    records = _discover_records(data_root)
    if not records and not data_root.exists():
        return []
    with _paper_order_lock(data_root):
        ordered_ids = _reconcile_paper_order_unlocked(data_root, records)
    by_id = {str(item["record_id"]): item for item in records}
    return [by_id[record_id] for record_id in ordered_ids]


def order_records(data_root: Path, records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort any record subset by the persistent main-menu order."""
    selected = list(records)
    with _paper_order_lock(data_root):
        ordered_ids = _reconcile_paper_order_unlocked(data_root, _discover_records(data_root))
    positions = {record_id: index for index, record_id in enumerate(ordered_ids)}
    return sorted(
        selected,
        key=lambda item: positions.get(str(item.get("record_id", "")), len(positions)),
    )


def move_record(data_root: Path, record_id: str, direction: int) -> bool:
    """Move one paper a single position; return whether its position changed."""
    if direction not in (-1, 1):
        raise ValueError("Paper moves must be one position up or down")
    record_dir(data_root, record_id)
    with _paper_order_lock(data_root):
        records = _discover_records(data_root)
        ordered_ids = _reconcile_paper_order_unlocked(data_root, records)
        if record_id not in ordered_ids:
            raise FileNotFoundError("The paper record folder no longer exists")
        current_index = ordered_ids.index(record_id)
        destination = current_index + direction
        if destination < 0 or destination >= len(ordered_ids):
            return False
        ordered_ids[current_index], ordered_ids[destination] = (
            ordered_ids[destination],
            ordered_ids[current_index],
        )
        _atomic_json_write(
            data_root / PAPER_ORDER_FILENAME,
            {
                "schema_version": PAPER_ORDER_SCHEMA_VERSION,
                "record_ids": ordered_ids,
            },
        )
        return True


def section_completion(notes: Dict[str, Any]) -> Dict[str, bool]:
    sections = notes.get("sections", {})
    status = {key: bool(str(sections.get(key, "")).strip()) for key in TEXT_SECTION_KEYS}
    status["data_sources"] = any(
        str(source.get("name", "")).strip() or str(source.get("description", "")).strip()
        for source in notes.get("data_sources", [])
    )
    status["exhibits"] = any(
        str(exhibit.get("identifier_title", "")).strip()
        or str(exhibit.get("interpretation", "")).strip()
        or bool(exhibit.get("image"))
        or bool(exhibit.get("images"))
        for exhibit in notes.get("exhibits", [])
    )
    status["empirical_strategy"] = status["empirical_strategy"] or any(
        str(strategy.get("title", "")).strip()
        or str(strategy.get("description", "")).strip()
        or str(strategy.get("latex_equation", "")).strip()
        or bool(strategy.get("image"))
        for strategy in notes.get("empirical_strategies", [])
    )
    return status


def content_equal(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    ignored = {"updated_at"}
    return (
        {key: value for key, value in left.items() if key not in ignored}
        == {key: value for key, value in right.items() if key not in ignored}
    )


def exhibit_image_path(data_root: Path, record_id: str, exhibit_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", exhibit_id):
        raise ValueError("Invalid exhibit identifier")
    folder = record_dir(data_root, record_id)
    return folder / "exhibits" / f"exhibit-{exhibit_id}.png"


def strategy_image_path(data_root: Path, record_id: str, strategy_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", strategy_id):
        raise ValueError("Invalid empirical strategy identifier")
    folder = record_dir(data_root, record_id)
    return folder / "exhibits" / f"strategy-{strategy_id}.png"


def save_uploaded_image(
    data_root: Path,
    record_id: str,
    item_kind: str,
    item_id: str,
    image_bytes: bytes,
) -> str:
    """Validate an uploaded screenshot and save a normalized PNG inside its paper record."""
    if not image_bytes:
        raise ValueError("Choose an image file to upload.")
    if len(image_bytes) > 25 * 1024 * 1024:
        raise ValueError("The screenshot is larger than 25 MB. Please upload a smaller image.")
    if item_kind == "strategy":
        output = strategy_image_path(data_root, record_id, item_id)
    elif item_kind == "exhibit":
        output = exhibit_image_path(data_root, record_id, item_id)
    else:
        raise ValueError("Invalid image destination.")

    try:
        with Image.open(BytesIO(image_bytes)) as uploaded:
            uploaded.load()
            if uploaded.width < 1 or uploaded.height < 1:
                raise ValueError("The uploaded image is empty.")
            if uploaded.width * uploaded.height > 50_000_000:
                raise ValueError("The screenshot dimensions are too large. Please upload a smaller image.")
            has_transparency = uploaded.mode in {"RGBA", "LA"} or "transparency" in uploaded.info
            normalized = uploaded.convert("RGBA" if has_transparency else "RGB")
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("That file could not be read as an image. Please use PNG, JPG, or WEBP.") from error

    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.stem}.", suffix=".tmp", dir=str(output.parent)
    )
    os.close(handle)
    try:
        normalized.save(temporary_name, format="PNG", optimize=True)
        os.replace(temporary_name, output)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return str(output.relative_to(record_dir(data_root, record_id)))


def remove_exhibit_image(data_root: Path, record_id: str, relative_path: Optional[str]) -> None:
    if not relative_path:
        return
    folder = record_dir(data_root, record_id)
    candidate = (folder / relative_path).resolve()
    exhibits_folder = (folder / "exhibits").resolve()
    if _inside(exhibits_folder, candidate) and candidate.is_file():
        candidate.unlink()


def remove_exhibit_images(data_root: Path, record_id: str, exhibit: Dict[str, Any]) -> None:
    """Remove only image files referenced by one exhibit inside its own record folder."""
    paths = [exhibit.get("image")]
    paths.extend(
        part.get("image")
        for part in exhibit.get("images", [])
        if isinstance(part, dict)
    )
    for relative_path in paths:
        remove_exhibit_image(data_root, record_id, relative_path)
