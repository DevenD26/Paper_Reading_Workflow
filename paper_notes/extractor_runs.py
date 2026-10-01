from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .storage import safe_display_filename, safe_slug


RUN_METADATA_NAME = "run.json"
RUN_SUFFIX_PATTERN = re.compile(r"-(\d{8}-\d{6}-[0-9a-f]{6})$")
APPROVED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
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


def _validated_run_folder(output_root: Path, run_folder: Path) -> Path:
    root = output_root.resolve()
    candidate = run_folder.resolve()
    if candidate.parent != root or candidate.name.startswith("."):
        raise ValueError("The extraction run is outside the extractor output folder.")
    if not candidate.is_dir():
        raise ValueError("The extraction run no longer exists.")
    return candidate


def _legacy_title(folder_name: str) -> str:
    stem = RUN_SUFFIX_PATTERN.sub("", folder_name)
    words = stem.replace("-", " ").replace("_", " ").strip()
    return words or "Untitled paper"


def _available_run_folder(
    output_root: Path, title: str, current: Optional[Path] = None
) -> Path:
    root = output_root.resolve()
    base = safe_slug(title, fallback="paper")
    candidate = root / base
    suffix = 2
    while candidate.exists() and (current is None or candidate.resolve() != current.resolve()):
        candidate = root / f"{base}-{suffix}"
        suffix += 1
    return candidate


def _load_metadata(run_folder: Path) -> dict[str, Any]:
    metadata_path = run_folder / RUN_METADATA_NAME
    if not metadata_path.is_file():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _run_summary(run_folder: Path) -> dict[str, Any]:
    metadata = _load_metadata(run_folder)
    approved_folder = run_folder / "approved"
    work_folder = run_folder / "work"
    approved_files = sorted(
        path
        for path in approved_folder.iterdir()
        if path.is_file() and path.suffix.casefold() in APPROVED_IMAGE_SUFFIXES
    ) if approved_folder.is_dir() else []
    manifests = sorted(work_folder.glob("extraction-*/manifest.json"), reverse=True)
    try:
        modified_at = run_folder.stat().st_mtime
    except OSError:
        modified_at = 0.0
    return {
        "path": run_folder,
        "folder_name": run_folder.name,
        "title": str(metadata.get("title") or _legacy_title(run_folder.name)),
        "original_pdf_name": str(metadata.get("original_pdf_name") or "source.pdf"),
        "created_at": metadata.get("created_at"),
        "approved_folder": approved_folder,
        "work_folder": work_folder,
        "approved_files": approved_files,
        "approved_count": len(approved_files),
        "proposal_count": len(manifests),
        "latest_manifest": manifests[0] if manifests else None,
        "modified_at": modified_at,
    }


def list_extraction_runs(output_root: Path) -> list[dict[str, Any]]:
    output_root.mkdir(parents=True, exist_ok=True)
    runs = [
        _run_summary(path)
        for path in output_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    return sorted(runs, key=lambda item: item["modified_at"], reverse=True)


def create_extraction_run(
    output_root: Path,
    title: str,
    original_pdf_name: str,
) -> Path:
    clean_title = " ".join(title.split())
    if not clean_title:
        raise ValueError("Enter a paper title before analyzing the PDF.")
    output_root.mkdir(parents=True, exist_ok=True)
    created = _now()
    run_id = uuid.uuid4().hex[:6]
    run_folder = _available_run_folder(output_root, clean_title)
    run_folder.mkdir(parents=False, exist_ok=False)
    (run_folder / "approved").mkdir()
    (run_folder / "work").mkdir()
    _atomic_json_write(
        run_folder / RUN_METADATA_NAME,
        {
            "schema_version": 1,
            "title": clean_title,
            "original_pdf_name": safe_display_filename(original_pdf_name),
            "created_at": created.replace(microsecond=0).isoformat(),
            "run_id": run_id,
        },
    )
    return run_folder


def rename_extraction_run(output_root: Path, run_folder: Path, title: str) -> Path:
    current = _validated_run_folder(output_root, run_folder)
    clean_title = " ".join(title.split())
    if not clean_title:
        raise ValueError("The paper title cannot be empty.")

    metadata = _load_metadata(current)
    desired = _available_run_folder(output_root, clean_title, current=current)
    if desired != current:
        current.rename(desired)
        current = desired

    metadata.update(
        {
            "schema_version": 1,
            "title": clean_title,
            "original_pdf_name": safe_display_filename(
                str(metadata.get("original_pdf_name") or "source.pdf")
            ),
            "created_at": metadata.get("created_at") or _now().replace(microsecond=0).isoformat(),
            "run_id": metadata.get("run_id") or uuid.uuid4().hex[:6],
        }
    )
    _atomic_json_write(current / RUN_METADATA_NAME, metadata)
    return current


def move_extraction_run_to_trash(output_root: Path, run_folder: Path) -> Path:
    current = _validated_run_folder(output_root, run_folder)
    trash_folder = output_root.resolve() / ".trash"
    trash_folder.mkdir(parents=True, exist_ok=True)
    destination = trash_folder / (
        f"{current.name}-deleted-{_now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    )
    current.rename(destination)
    return destination


def move_approved_images_to_trash(
    output_root: Path,
    run_folder: Path,
    filenames: list[str],
) -> list[Path]:
    current = _validated_run_folder(output_root, run_folder)
    approved_folder = current / "approved"
    trash_folder = current / ".trash" / "approved"
    moved: list[Path] = []
    for filename in filenames:
        if Path(filename).name != filename:
            raise ValueError("An approved screenshot name is invalid.")
        source = (approved_folder / filename).resolve()
        if source.parent != approved_folder.resolve():
            raise ValueError("An approved screenshot is outside the approved folder.")
        if not source.is_file() or source.suffix.casefold() not in APPROVED_IMAGE_SUFFIXES:
            raise ValueError(f"Approved screenshot not found: {filename}")
        trash_folder.mkdir(parents=True, exist_ok=True)
        destination = trash_folder / source.name
        if destination.exists():
            destination = trash_folder / f"{source.stem}-{uuid.uuid4().hex[:4]}{source.suffix}"
        source.rename(destination)
        moved.append(destination)
    return moved
