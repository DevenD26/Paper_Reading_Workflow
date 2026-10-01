from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import fitz
from PIL import Image

from .pdf_tools import crop_page
from .storage import blank_exhibit, load_notes, mutate_notes, record_dir


CAPTION_PATTERN = re.compile(
    r"^\s*(Table|Figure|Fig\.?|Chart|Graph|Exhibit)\s+"
    r"([A-Z]\.?(?:\d+(?:\.\d+)?)|\d+(?:\.\d+)*(?:[A-Z])?|[IVXLCDM]+)"
    r"(?:\s*[:.\-–—]\s*|\s+|$)(.*)$",
    re.IGNORECASE,
)
APPENDIX_ID_PATTERN = re.compile(r"^[A-Z]\.?\d", re.IGNORECASE)
APPENDIX_HEADING_PATTERN = re.compile(
    r"^(?:online\s+|web\s+|supplementary\s+)?appendix(?:\s+[A-Z0-9][^.]*)?$",
    re.IGNORECASE,
)
CONTINUED_PATTERN = re.compile(r"\b(?:continued|cont\.)\b", re.IGNORECASE)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _line_records(page: fitz.Page) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_dict = page.get_text("dict", sort=True)
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        block_lines = block.get("lines", [])
        block_bbox = tuple(float(value) for value in block.get("bbox", (0, 0, 0, 0)))
        for line in block_lines:
            spans = line.get("spans", [])
            text = "".join(str(span.get("text", "")) for span in spans).strip()
            if not text:
                continue
            bbox = tuple(float(value) for value in line.get("bbox", (0, 0, 0, 0)))
            sizes = [float(span.get("size", 0)) for span in spans if span.get("text", "").strip()]
            records.append(
                {
                    "text": text,
                    "bbox": bbox,
                    "font_size": max(sizes, default=0.0),
                    "block_bbox": block_bbox,
                    "block_line_count": len(block_lines),
                    "direction": tuple(float(value) for value in line.get("dir", (1, 0))),
                }
            )
    return sorted(records, key=lambda item: (item["bbox"][1], item["bbox"][0]))


def _text_blocks(page: fitz.Page) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in page.get_text("blocks", sort=True):
        if len(block) < 7 or int(block[6]) != 0:
            continue
        text = " ".join(str(block[4]).split())
        if text:
            blocks.append({"text": text, "bbox": tuple(float(value) for value in block[:4])})
    return blocks


def _caption_match(text: str) -> re.Match[str] | None:
    normalized = " ".join(text.split())
    match = CAPTION_PATTERN.match(normalized)
    if match is None:
        return None
    remainder = normalized[match.end(2) :].lstrip()
    if not remainder or remainder[0] in ":.-–—":
        return match
    caption_tail = match.group(3).strip()
    if CONTINUED_PATTERN.search(caption_tail):
        return match
    if caption_tail and caption_tail[0].isupper():
        return match
    return None


def _caption_layout_candidate(line: dict[str, Any]) -> bool:
    """Reject caption-like phrases embedded in ordinary multi-line prose blocks."""
    if int(line.get("block_line_count", 1)) > 3:
        return False
    block_bbox = line.get("block_bbox", line["bbox"])
    direction = line.get("direction", (1, 0))
    if abs(float(direction[1])) > 0.8:
        line_extent = max(float(line["bbox"][2]) - float(line["bbox"][0]), 1.0)
        block_extent = float(block_bbox[2]) - float(block_bbox[0])
    else:
        line_extent = max(float(line["bbox"][3]) - float(line["bbox"][1]), 1.0)
        block_extent = float(block_bbox[3]) - float(block_bbox[1])
    return block_extent <= line_extent * 4.2


def _canonical_kind(value: str) -> str:
    lowered = value.casefold().rstrip(".")
    if lowered == "fig":
        return "Figure"
    return value.rstrip(".").title()


def _identifier(kind: str, number: str) -> str:
    cleaned_number = re.sub(r"\s+", "", number.upper())
    return f"{_canonical_kind(kind)} {cleaned_number}"


def _candidate_id(identifier: str, page_number: int, bbox: Iterable[float]) -> str:
    material = f"{identifier}|{page_number}|" + ",".join(f"{value:.2f}" for value in bbox)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _find_appendix_boundary(
    pages: list[dict[str, Any]], user_start_page: int | None
) -> dict[str, Any]:
    if user_start_page is not None:
        if user_start_page < 1 or user_start_page > len(pages):
            raise ValueError(f"Appendix start must be between 1 and {len(pages)}.")
        return {
            "page_number": user_start_page,
            "confidence": "user-specified",
            "reason": "The user supplied the appendix starting page.",
        }

    uncertain_pages: list[int] = []
    for page in pages:
        height = page["height"]
        font_sizes = sorted(line["font_size"] for line in page["lines"] if line["font_size"] > 0)
        median_size = font_sizes[(len(font_sizes) - 1) // 2] if font_sizes else 0
        for line in page["lines"]:
            normalized = " ".join(line["text"].split()).strip(" :.-")
            if "appendix" not in normalized.casefold():
                continue
            heading_match = APPENDIX_HEADING_PATTERN.fullmatch(normalized)
            near_top = line["bbox"][1] <= height * 0.35
            heading_sized = line["font_size"] >= max(median_size * 1.05, 10)
            if heading_match and near_top and heading_sized:
                return {
                    "page_number": page["page_number"],
                    "confidence": "high",
                    "reason": f'Heading “{line["text"]}” appears near the top of the page.',
                }
            uncertain_pages.append(page["page_number"])
    return {
        "page_number": None,
        "confidence": "uncertain" if uncertain_pages else "none",
        "reason": (
            "Appendix wording was found, but not in a heading-like position."
            if uncertain_pages
            else "No appendix heading was detected."
        ),
        "possible_pages": sorted(set(uncertain_pages)),
    }


def _visual_rectangles(page: fitz.Page) -> list[fitz.Rect]:
    rectangles: list[fitz.Rect] = []
    page_area = max(page.rect.width * page.rect.height, 1)
    for info in page.get_image_info():
        rect = fitz.Rect(info.get("bbox", (0, 0, 0, 0)))
        if rect.width > page.rect.width * 0.12 and rect.width * rect.height > page_area * 0.004:
            rectangles.append(rect)
    try:
        drawings = page.get_drawings()
    except (RuntimeError, ValueError):
        drawings = []
    for drawing in drawings:
        rect = fitz.Rect(drawing.get("rect", (0, 0, 0, 0)))
        if rect.width > page.rect.width * 0.12 and rect.width * rect.height > page_area * 0.002:
            rectangles.append(rect)
    return rectangles


def _union(rectangles: list[fitz.Rect]) -> fitz.Rect | None:
    if not rectangles:
        return None
    result = fitz.Rect(rectangles[0])
    for rectangle in rectangles[1:]:
        result.include_rect(rectangle)
    return result


def _propose_crop(
    page_info: dict[str, Any],
    caption: dict[str, Any],
    previous_caption_bottom: float | None,
    next_caption_top: float | None,
    kind: str,
) -> tuple[dict[str, float], str, list[str]]:
    width = page_info["width"]
    height = page_info["height"]
    x0, y0, x1, y1 = caption["bbox"]
    reasons: list[str] = []
    direction = "below-caption"
    confidence = "review"

    text_direction = caption.get("direction", (1, 0))
    if abs(float(text_direction[1])) > 0.8:
        if float(text_direction[1]) < 0:
            left = max(0.0, x0 - width * 0.025)
            right = width * 0.94
            direction = "right-of-vertical-caption"
        else:
            left = width * 0.06
            right = min(width, x1 + width * 0.025)
            direction = "left-of-vertical-caption"
        top = height * 0.055
        bottom = height * 0.945
        reasons.append("The caption is vertical, so the page region beside it is proposed and rotated for review.")
        return (
            {
                "left": 100 * left / width,
                "top": 100 * top / height,
                "right": 100 * right / width,
                "bottom": 100 * bottom / height,
            },
            confidence,
            reasons + [f"The proposed exhibit region is {direction.replace('-', ' ')}."],
        )

    above = [
        rect
        for rect in page_info["visuals"]
        if rect.y1 <= y1 + height * 0.03 and rect.y1 >= y0 - height * 0.48
        and (previous_caption_bottom is None or rect.y0 >= previous_caption_bottom)
    ]
    below = [
        rect
        for rect in page_info["visuals"]
        if rect.y0 >= y0 - height * 0.02 and rect.y0 <= y1 + height * 0.48
    ]
    above_union = _union(above)
    below_union = _union(below)

    notes_blocks = [
        block
        for block in page_info["blocks"]
        if block["bbox"][1] > y1
        and re.match(r"^Notes?\s*:", block["text"], re.IGNORECASE)
        and (next_caption_top is None or block["bbox"][1] < next_caption_top)
    ]

    if kind == "table" and notes_blocks:
        notes_block = notes_blocks[0]
        region_blocks = [
            block
            for block in page_info["blocks"]
            if block["bbox"][1] >= y0 - height * 0.02
            and block["bbox"][3] <= notes_block["bbox"][3] + height * 0.01
        ]
        left = min([x0, *(block["bbox"][0] for block in region_blocks)]) - width * 0.02
        right = max([x1, *(block["bbox"][2] for block in region_blocks)]) + width * 0.02
        top = y0 - height * 0.012
        bottom = notes_block["bbox"][3] + height * 0.015
        direction = "caption-through-notes"
        confidence = "strong"
        reasons.append("A table notes block provides a clear lower boundary.")
    elif above_union is not None and (
        y0 >= height * 0.30
        or
        below_union is None
        or above_union.width * above_union.height > below_union.width * below_union.height * 1.2
    ):
        direction = "above-caption"
        left = min(x0, above_union.x0) - width * 0.02
        right = max(x1, above_union.x1) + width * 0.02
        top = above_union.y0 - height * 0.015
        caption_following_limit = y1 + height * 0.07
        if next_caption_top is not None:
            caption_following_limit = min(
                caption_following_limit,
                next_caption_top - height * 0.015,
            )
        bottom = caption_following_limit
        confidence = "strong"
        reasons.append("A substantial image or drawing region is immediately above the caption.")
    elif below_union is not None:
        left = min(x0, below_union.x0) - width * 0.02
        right = max(x1, below_union.x1) + width * 0.02
        top = y0 - height * 0.012
        bottom = below_union.y1 + height * 0.015
        confidence = "strong"
        reasons.append("A substantial image or drawing region is immediately below the caption.")
    else:
        left = width * 0.025
        right = width * 0.975
        top = y0 - height * 0.012
        boundary = next_caption_top - height * 0.015 if next_caption_top else height * 0.93
        bottom = max(y1 + height * 0.18, boundary)
        reasons.append("No reliable image boundary was available; a broad page region is proposed.")

    if kind in {"figure", "chart", "graph"} and (above_union is not None or below_union is not None):
        left = width * 0.025
        right = width * 0.975

    left = max(0.0, left)
    top = max(0.0, top)
    right = min(width, right)
    bottom = min(height, bottom)
    if right <= left or bottom <= top:
        left, top, right, bottom = 0.0, max(0.0, y0 - 10), width, min(height, y1 + height * 0.4)
        confidence = "review"
        reasons.append("The initial bounds were invalid, so a safe broad crop was substituted.")

    crop = {
        "left": 100 * left / width,
        "top": 100 * top / height,
        "right": 100 * right / width,
        "bottom": 100 * bottom / height,
    }
    reasons.append(f"The proposed exhibit region is {direction.replace('-', ' ')}.")
    return crop, confidence, reasons


def extract_pdf(
    pdf_path: Path,
    *,
    include_appendix: bool = False,
    appendix_start_page: int | None = None,
) -> dict[str, Any]:
    """Inspect a PDF locally and return deterministic exhibit proposals without writing files."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pages: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as document:
        if document.page_count < 1:
            raise ValueError("The PDF contains no pages.")
        for index in range(document.page_count):
            page = document.load_page(index)
            lines = _line_records(page)
            pages.append(
                {
                    "page_number": index + 1,
                    "width": float(page.rect.width),
                    "height": float(page.rect.height),
                    "lines": lines,
                    "blocks": _text_blocks(page),
                    "visuals": _visual_rectangles(page),
                    "text_length": sum(len(line["text"]) for line in lines),
                }
            )

    appendix = _find_appendix_boundary(pages, appendix_start_page)
    boundary_page = appendix.get("page_number")
    detections: list[dict[str, Any]] = []
    excluded_count = 0

    for page in pages:
        caption_lines = [
            line
            for line in page["lines"]
            if _caption_match(line["text"]) and _caption_layout_candidate(line)
        ]
        for caption_index, line in enumerate(caption_lines):
            match = _caption_match(line["text"])
            assert match is not None
            kind, number, _ = match.groups()
            identifier = _identifier(kind, number)
            appendix_identifier = bool(APPENDIX_ID_PATTERN.match(number.replace(" ", "")))
            after_boundary = bool(boundary_page and page["page_number"] >= boundary_page)
            is_appendix = appendix_identifier or after_boundary
            if is_appendix and not include_appendix:
                excluded_count += 1
                continue
            next_top = (
                caption_lines[caption_index + 1]["bbox"][1]
                if caption_index + 1 < len(caption_lines)
                else None
            )
            previous_bottom = (
                caption_lines[caption_index - 1]["bbox"][3]
                if caption_index > 0
                else None
            )
            crop, confidence, reasons = _propose_crop(
                page,
                line,
                previous_bottom,
                next_top,
                _canonical_kind(kind).casefold(),
            )
            if is_appendix:
                reasons.append("This exhibit is included despite matching an appendix rule.")
            detections.append(
                {
                    "candidate_id": _candidate_id(identifier, page["page_number"], line["bbox"]),
                    "identifier": identifier,
                    "title": " ".join(line["text"].split()),
                    "kind": _canonical_kind(kind).casefold(),
                    "page_number": page["page_number"],
                    "caption_bbox": list(line["bbox"]),
                    "crop": crop,
                    "confidence": confidence,
                    "reasons": reasons,
                    "appendix": is_appendix,
                    "continued": bool(CONTINUED_PATTERN.search(line["text"])),
                    "direction": list(line.get("direction", (1, 0))),
                }
            )

    candidates: list[dict[str, Any]] = []
    for detection in detections:
        previous = candidates[-1] if candidates else None
        can_continue = bool(
            previous
            and previous["identifier"].casefold() == detection["identifier"].casefold()
            and previous["parts"][-1]["page_number"] + 1 == detection["page_number"]
            and (
                detection["continued"]
                or detection["title"].casefold() == previous["title"].casefold()
                or previous["parts"][-1].get("near_page_bottom", False)
            )
        )
        part = {
            "page_number": detection["page_number"],
            "caption_text": detection["title"],
            "crop": detection["crop"],
            "confidence": detection["confidence"],
            "reasons": detection["reasons"],
            "near_page_bottom": detection["crop"]["bottom"] >= 88,
            "rotate_degrees": (
                90
                if detection.get("direction", [1, 0])[1] < -0.8
                else 270
                if detection.get("direction", [1, 0])[1] > 0.8
                else 0
            ),
        }
        if can_continue:
            previous["parts"].append(part)
            previous["confidence"] = "review"
            previous["reasons"].append("An explicitly repeated or continued caption was found on the next page.")
        else:
            candidates.append(
                {
                    "id": detection["candidate_id"],
                    "identifier": detection["identifier"],
                    "title": detection["title"],
                    "kind": detection["kind"],
                    "confidence": detection["confidence"],
                    "reasons": list(detection["reasons"]),
                    "appendix": detection["appendix"],
                    "status": "pending",
                    "parts": [part],
                }
            )

    for candidate in candidates:
        multiple = len(candidate["parts"]) > 1
        for index, part in enumerate(candidate["parts"]):
            part.pop("near_page_bottom", None)
            part["panel_label"] = f"panel_{_panel_letters(index)}" if multiple else None

    image_only_pages = [page["page_number"] for page in pages if page["text_length"] < 20]
    warnings: list[str] = []
    if image_only_pages:
        warnings.append(
            "Little or no extractable text was found on page(s) "
            + ", ".join(map(str, image_only_pages))
            + "; captions on those pages require manual review."
        )
    if appendix.get("confidence") == "uncertain" and not appendix_start_page:
        warnings.append("The appendix boundary is uncertain; review the detected pages or set it manually.")

    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_name": pdf_path.name,
        "source_sha256": _sha256(pdf_path),
        "page_count": len(pages),
        "settings": {
            "include_appendix": include_appendix,
            "appendix_start_page": appendix_start_page,
        },
        "appendix": appendix,
        "excluded_appendix_candidates": excluded_count,
        "warnings": warnings,
        "candidates": candidates,
    }


def _panel_letters(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _rotate_image_clockwise(path: Path, degrees: int) -> None:
    normalized = degrees % 360
    if normalized == 0:
        return
    with Image.open(path) as source:
        rotated = source.rotate(-normalized, expand=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}.", suffix=".png", dir=str(path.parent)
        )
        os.close(handle)
        try:
            rotated.save(temporary_name, format="PNG", optimize=True)
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)


def _render_proposal_previews(pdf_path: Path, manifest: dict[str, Any], folder: Path) -> None:
    for candidate in manifest["candidates"]:
        for index, part in enumerate(candidate["parts"]):
            preview_name = f"{candidate['id']}-{index + 1}.png"
            crop = part["crop"]
            crop_page(
                pdf_path,
                int(part["page_number"]),
                (crop["left"], crop["top"], crop["right"], crop["bottom"]),
                folder / preview_name,
                zoom=1.6,
            )
            _rotate_image_clockwise(folder / preview_name, int(part.get("rotate_degrees", 0)))
            part["preview"] = preview_name


def write_proposal(pdf_path: Path, proposal_root: Path, **settings: Any) -> Path:
    """Create a self-contained proposal folder, leaving accepted notes untouched."""
    pdf_path = Path(pdf_path)
    proposal_root = Path(proposal_root)
    proposal_root.mkdir(parents=True, exist_ok=True)
    manifest = extract_pdf(pdf_path, **settings)
    run_name = f"extraction-{_utc_stamp()}-{uuid.uuid4().hex[:8]}"
    manifest["proposal_id"] = run_name
    temporary = Path(tempfile.mkdtemp(prefix=f".{run_name}-", dir=str(proposal_root)))
    final = proposal_root / run_name
    try:
        _render_proposal_previews(pdf_path, manifest, temporary)
        _atomic_json_write(temporary / "manifest.json", manifest)
        os.replace(temporary, final)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return final / "manifest.json"


def create_record_proposal(
    data_root: Path,
    record_id: str,
    *,
    include_appendix: bool = False,
    appendix_start_page: int | None = None,
) -> Path:
    folder = record_dir(data_root, record_id)
    return write_proposal(
        folder / "source.pdf",
        folder / "extraction_proposals",
        include_appendix=include_appendix,
        appendix_start_page=appendix_start_page,
    )


def load_manifest(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as source:
        return json.load(source)


def latest_record_proposal(data_root: Path, record_id: str) -> Path | None:
    proposal_root = record_dir(data_root, record_id) / "extraction_proposals"
    manifests = sorted(proposal_root.glob("extraction-*/manifest.json"), reverse=True)
    return manifests[0] if manifests else None


def reject_candidate(manifest_path: Path, candidate_id: str) -> None:
    manifest = load_manifest(manifest_path)
    candidate = next((item for item in manifest["candidates"] if item["id"] == candidate_id), None)
    if candidate is None:
        raise ValueError("Extraction candidate not found.")
    if candidate.get("status") == "accepted":
        raise ValueError("An accepted candidate cannot be rejected.")
    candidate["status"] = "rejected"
    _atomic_json_write(Path(manifest_path), manifest)


def _safe_image_stem(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9() _.'\[\]-]+", "_", ascii_value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return cleaned[:90].rstrip(" ._") or "Exhibit"


def _filename_base(title: str) -> str:
    match = _caption_match(title)
    if match:
        return _identifier(match.group(1), match.group(2))
    return title


def _unique_image_path(folder: Path, stem: str) -> Path:
    candidate = folder / f"{stem}.png"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem}_{counter}.png"
        counter += 1
    return candidate


def _validated_crop(crop: dict[str, Any]) -> dict[str, float]:
    values = {key: float(crop[key]) for key in ("left", "top", "right", "bottom")}
    if not (
        0 <= values["left"] < values["right"] <= 100
        and 0 <= values["top"] < values["bottom"] <= 100
    ):
        raise ValueError("Crop bounds must form a non-empty rectangle inside the page.")
    return values


def accept_candidate(
    data_root: Path,
    record_id: str,
    manifest_path: Path,
    candidate_id: str,
    title: str,
    crop_overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Atomically append one accepted proposal and never overwrite an existing image."""
    title = title.strip()
    if not title:
        raise ValueError("Enter an exhibit identifier or title before accepting it.")
    folder = record_dir(data_root, record_id)
    pdf_path = folder / "source.pdf"
    manifest_path = Path(manifest_path).resolve()
    proposal_root = (folder / "extraction_proposals").resolve()
    if proposal_root not in manifest_path.parents:
        raise ValueError("Proposal path is outside this paper record.")
    manifest = load_manifest(manifest_path)
    if manifest.get("source_sha256") != _sha256(pdf_path):
        raise ValueError("The source PDF changed after extraction. Run extraction again.")
    candidate = next((item for item in manifest["candidates"] if item["id"] == candidate_id), None)
    if candidate is None:
        raise ValueError("Extraction candidate not found.")

    notes = load_notes(data_root, record_id)
    proposal_id = manifest.get("proposal_id")
    existing = next(
        (
            item
            for item in notes.get("exhibits", [])
            if item.get("extraction_source", {}).get("candidate_id") == candidate_id
            and item.get("extraction_source", {}).get("source_sha256") == manifest["source_sha256"]
            and item.get("extraction_source", {}).get("proposal_id") == proposal_id
        ),
        None,
    )
    if existing is not None:
        candidate["status"] = "accepted"
        _atomic_json_write(manifest_path, manifest)
        return existing
    if candidate.get("status") == "rejected":
        raise ValueError("Rejecting this candidate was already confirmed.")

    parts = candidate["parts"]
    if crop_overrides is not None and len(crop_overrides) != len(parts):
        raise ValueError("Every image part needs one crop definition.")
    exhibits_folder = folder / "exhibits"
    exhibits_folder.mkdir(parents=True, exist_ok=True)
    multiple = len(parts) > 1
    created: list[Path] = []
    image_parts: list[dict[str, Any]] = []
    try:
        for index, part in enumerate(parts):
            crop = _validated_crop((crop_overrides or [item["crop"] for item in parts])[index])
            panel_label = f"panel_{_panel_letters(index)}" if multiple else None
            base = _safe_image_stem(_filename_base(title))
            stem = f"{base}_[{panel_label}]" if panel_label else base
            destination = _unique_image_path(exhibits_folder, stem)
            handle, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.stem}.", suffix=".png", dir=str(exhibits_folder)
            )
            os.close(handle)
            temporary = Path(temporary_name)
            try:
                crop_page(
                    pdf_path,
                    int(part["page_number"]),
                    (crop["left"], crop["top"], crop["right"], crop["bottom"]),
                    temporary,
                )
                os.replace(temporary, destination)
                _rotate_image_clockwise(destination, int(part.get("rotate_degrees", 0)))
            finally:
                if temporary.exists():
                    temporary.unlink()
            created.append(destination)
            image_parts.append(
                {
                    "label": panel_label,
                    "page_number": int(part["page_number"]),
                    "image": str(destination.relative_to(folder)),
                    "crop": {"page_number": int(part["page_number"]), **crop},
                }
            )

        exhibit = blank_exhibit()
        exhibit.update(
            {
                "identifier_title": title,
                "page_number": int(parts[0]["page_number"]),
                "image": None,
                "crop": None,
                "images": image_parts,
                "extraction_source": {
                    "source_sha256": manifest["source_sha256"],
                    "candidate_id": candidate_id,
                    "caption_text": candidate["title"],
                    "proposal_id": proposal_id,
                },
            }
        )
        def attach(latest_notes: dict[str, Any]) -> dict[str, Any]:
            duplicate = next(
                (
                    item
                    for item in latest_notes.get("exhibits", [])
                    if item.get("extraction_source", {}).get("candidate_id") == candidate_id
                    and item.get("extraction_source", {}).get("source_sha256")
                    == manifest["source_sha256"]
                    and item.get("extraction_source", {}).get("proposal_id") == proposal_id
                ),
                None,
            )
            if duplicate is not None:
                return duplicate
            latest_notes["exhibits"].append(exhibit)
            return exhibit

        attached = mutate_notes(data_root, record_id, attach)
        if attached.get("id") != exhibit["id"]:
            for path in created:
                if path.is_file():
                    path.unlink()
            exhibit = attached
    except Exception:
        for path in created:
            if path.is_file():
                path.unlink()
        raise

    candidate["status"] = "accepted"
    candidate["accepted_title"] = title
    _atomic_json_write(manifest_path, manifest)
    return exhibit


def approve_candidate_files(
    pdf_path: Path,
    manifest_path: Path,
    output_folder: Path,
    candidate_id: str,
    title: str,
    crop_overrides: list[dict[str, Any]] | None = None,
) -> list[Path]:
    """Render one reviewed candidate into a standalone collision-safe output folder."""
    title = title.strip()
    if not title:
        raise ValueError("Enter an exhibit identifier or title before approving it.")
    pdf_path = Path(pdf_path)
    manifest_path = Path(manifest_path)
    output_folder = Path(output_folder)
    manifest = load_manifest(manifest_path)
    if manifest.get("source_sha256") != _sha256(pdf_path):
        raise ValueError("The source PDF changed after extraction. Run extraction again.")
    candidate = next((item for item in manifest["candidates"] if item["id"] == candidate_id), None)
    if candidate is None:
        raise ValueError("Extraction candidate not found.")
    if candidate.get("status") == "rejected":
        raise ValueError("Rejecting this candidate was already confirmed.")
    if candidate.get("status") == "accepted":
        existing = [output_folder / name for name in candidate.get("accepted_files", [])]
        if existing and all(path.is_file() for path in existing):
            return existing

    parts = candidate["parts"]
    if crop_overrides is not None and len(crop_overrides) != len(parts):
        raise ValueError("Every image part needs one crop definition.")
    output_folder.mkdir(parents=True, exist_ok=True)
    multiple = len(parts) > 1
    created: list[Path] = []
    try:
        for index, part in enumerate(parts):
            crop = _validated_crop((crop_overrides or [item["crop"] for item in parts])[index])
            panel_label = f"panel_{_panel_letters(index)}" if multiple else None
            base = _safe_image_stem(_filename_base(title))
            stem = f"{base}_[{panel_label}]" if panel_label else base
            destination = _unique_image_path(output_folder, stem)
            crop_page(
                pdf_path,
                int(part["page_number"]),
                (crop["left"], crop["top"], crop["right"], crop["bottom"]),
                destination,
            )
            _rotate_image_clockwise(destination, int(part.get("rotate_degrees", 0)))
            created.append(destination)
    except Exception:
        for path in created:
            if path.is_file():
                path.unlink()
        raise

    candidate["status"] = "accepted"
    candidate["accepted_title"] = title
    candidate["accepted_files"] = [path.name for path in created]
    _atomic_json_write(manifest_path, manifest)
    return created


def save_manual_crop(
    pdf_path: Path,
    output_folder: Path,
    title: str,
    page_number: int,
    crop: dict[str, Any],
    *,
    rotate_degrees: int = 0,
) -> Path:
    """Save one explicitly named manual crop without overwriting another file."""
    title = title.strip()
    if not title:
        raise ValueError("Enter a filename/title for the manual crop.")
    validated = _validated_crop(crop)
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    destination = _unique_image_path(
        output_folder,
        _safe_image_stem(_filename_base(title)),
    )
    crop_page(
        Path(pdf_path),
        int(page_number),
        (
            validated["left"],
            validated["top"],
            validated["right"],
            validated["bottom"],
        ),
        destination,
    )
    _rotate_image_clockwise(destination, int(rotate_degrees))
    return destination


def save_record_manual_crop(
    data_root: Path,
    record_id: str,
    title: str,
    page_number: int,
    crop: dict[str, Any],
    *,
    rotate_degrees: int = 0,
) -> dict[str, Any]:
    """Save a manual crop in one paper folder and attach it to that paper's notes."""
    folder = record_dir(data_root, record_id)
    destination = save_manual_crop(
        folder / "source.pdf",
        folder / "exhibits",
        title,
        page_number,
        crop,
        rotate_degrees=rotate_degrees,
    )
    exhibit = blank_exhibit()
    exhibit.update(
        {
            "identifier_title": title.strip(),
            "page_number": int(page_number),
            "image": str(destination.relative_to(folder)),
            "crop": {"page_number": int(page_number), **_validated_crop(crop)},
            "extraction_source": {"manual": True},
        }
    )
    try:
        mutate_notes(
            data_root,
            record_id,
            lambda notes: notes["exhibits"].append(exhibit),
        )
    except Exception:
        if destination.is_file():
            destination.unlink()
        raise
    return exhibit
