import os
import re
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path

from PIL import Image
from PyPDF2 import PdfReader, PdfWriter


IMAGE_NAME_RE = re.compile(
    r"(?:chapter|capter)_(\d+)_page_(\d+)\.jpg$", re.IGNORECASE
)


def image_sort_key(filename: str) -> tuple[int, int]:
    match = IMAGE_NAME_RE.fullmatch(filename)
    if not match:
        raise ValueError(f"Unsupported image filename: {filename}")
    return tuple(map(int, match.groups()))


def _ordered_image_files(image_dir: Path) -> list[tuple[int, int, Path]]:
    """Return one deterministic image for every numeric chapter/page position."""
    images_by_position = {}
    for path in image_dir.iterdir():
        if not path.is_file() or not IMAGE_NAME_RE.fullmatch(path.name):
            continue

        position = image_sort_key(path.name)
        existing = images_by_position.get(position)
        if existing is None:
            images_by_position[position] = path
            continue

        # Prefer the corrected `chapter_` spelling over a legacy `capter_`
        # duplicate, then use the filename as a deterministic tie-breaker.
        images_by_position[position] = min(
            (existing, path),
            key=lambda candidate: (
                not candidate.name.lower().startswith("chapter_"),
                candidate.name.lower(),
            ),
        )

    return [
        (chapter, page, images_by_position[(chapter, page)])
        for chapter, page in sorted(images_by_position)
    ]


def images_to_pdf(
    img_dir: str | os.PathLike[str],
    pdf_path: str | os.PathLike[str],
    chapter_names: Mapping[int | str, str] | None = None,
) -> str:
    """Create a compact, ordered PDF with hierarchical chapter bookmarks."""
    image_dir = Path(img_dir)
    destination = Path(pdf_path)
    ordered_images = _ordered_image_files(image_dir)
    if not ordered_images:
        raise ValueError(f"No downloaded book images found in {image_dir}")

    normalized_chapter_names = {}
    for chapter, name in (chapter_names or {}).items():
        try:
            chapter_number = int(chapter)
        except (TypeError, ValueError):
            continue
        clean_name = str(name).strip()
        if clean_name:
            normalized_chapter_names[chapter_number] = clean_name

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial_output = destination.with_suffix(destination.suffix + ".part")
    writer = PdfWriter()
    page_buffers = []
    page_readers = []
    first_page_by_chapter = {}

    try:
        for pdf_page, (chapter, _page, image_path) in enumerate(ordered_images):
            with Image.open(image_path) as source:
                converted = source.convert("RGB")
                try:
                    page_buffer = BytesIO()
                    # Pillow's PDF encoder recompresses scan images efficiently
                    # without reducing their pixel dimensions.
                    converted.save(page_buffer, format="PDF", quality=75)
                finally:
                    converted.close()

            page_buffer.seek(0)
            page_reader = PdfReader(page_buffer)
            writer.add_page(page_reader.pages[0])
            page_buffers.append(page_buffer)
            page_readers.append(page_reader)
            first_page_by_chapter.setdefault(chapter, pdf_page)

        first_downloaded_chapter = ordered_images[0][0]
        root_chapter = 0 if 0 in normalized_chapter_names else first_downloaded_chapter
        root_title = normalized_chapter_names.get(
            root_chapter, f"Chapter {root_chapter}"
        )
        root_page = first_page_by_chapter.get(root_chapter, 0)
        root_bookmark = writer.add_outline_item(root_title, root_page)

        for chapter, page_number in sorted(first_page_by_chapter.items()):
            if chapter == root_chapter:
                continue
            writer.add_outline_item(
                normalized_chapter_names.get(chapter, f"Chapter {chapter}"),
                page_number,
                parent=root_bookmark,
            )

        with partial_output.open("wb") as output_file:
            writer.write(output_file)
        os.replace(partial_output, destination)
    finally:
        for page_buffer in page_buffers:
            page_buffer.close()
        if partial_output.exists():
            partial_output.unlink()

    return str(destination)
