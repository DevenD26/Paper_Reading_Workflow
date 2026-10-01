from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Tuple

import fitz
from PIL import Image


def page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as document:
        return document.page_count


def render_page(pdf_path: Path, page_number: int, zoom: float = 1.5) -> Image.Image:
    with fitz.open(pdf_path) as document:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError(f"Page must be between 1 and {document.page_count}")
        page = document.load_page(page_number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def crop_page(
    pdf_path: Path,
    page_number: int,
    crop_percent: Tuple[float, float, float, float],
    output_path: Path,
    zoom: float = 2.5,
) -> Path:
    left, top, right, bottom = crop_percent
    if not (0 <= left < right <= 100 and 0 <= top < bottom <= 100):
        raise ValueError("Crop bounds must form a non-empty rectangle within the page")
    image = render_page(pdf_path, page_number, zoom=zoom)
    box = (
        round(image.width * left / 100),
        round(image.height * top / 100),
        round(image.width * right / 100),
        round(image.height * bottom / 100),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}.", suffix=".png", dir=str(output_path.parent)
    )
    os.close(handle)
    try:
        image.crop(box).save(temporary_name, format="PNG", optimize=True)
        os.replace(temporary_name, output_path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return output_path
