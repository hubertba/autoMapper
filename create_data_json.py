#!/usr/bin/env python3
"""Build a book ``data.json`` from images, click-maps, and language audio.

The output matches ``exmlpe-data.json``: one object per page, keyed by a
0-based string index. Each page records the click-map rectangles plus the
image and audio filenames that belong together.

Filenames in ``Buch`` follow this convention:

* image  ``{book}-{title}-Seite-{page}.jpg`` or ``Seite-{page}.jpg``
* map    ``{image stem}.map.txt``
* audio  ``{k|u|d}{book}-seite-{page}.mp3``

``k`` is Croatian, ``u`` is Hungarian, and ``d`` is German. Page and book
numbers are matched numerically, so padding and ``Seite``/``seite`` spelling
do not have to be identical across file types. Images named only
``Seite-{page}`` are paired with audio by page number.

Audio keys are omitted when no matching file exists. Pages without a map file
get an empty ``map`` array.

Click-maps are generated first via ``create_click_maps.py`` so ``data.json``
picks up fresh rectangles. Pass ``--skip-maps`` to reuse existing map files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from create_click_maps import run_click_maps


IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}

# ``26-Benedikt-Seite-01.jpg`` → book 26, page 1.
IMAGE_NAME = re.compile(
    r"^(?P<book>\d+)-(?P<title>.+)-Seite-(?P<page>\d+)$",
    re.IGNORECASE,
)

# ``Seite-01.jpg`` when the book number is only in the audio filenames.
IMAGE_NAME_SIMPLE = re.compile(r"^Seite-(?P<page>\d+)$", re.IGNORECASE)

# ``k26-seite-01.mp3``, ``u1-Seite0.mp3``, ``d01-seite0.mp3``, ``u38-seite03.mp3``.
AUDIO_NAME = re.compile(
    r"^(?P<lang>[kud])(?P<book>\d+)-[Ss]eite-?(?P<page>\d+)$",
)


def find_images(folder: Path) -> list[tuple[int | None, int, Path]]:
    """Return ``(book, page, path)`` for every recognized page image.

    ``book`` is ``None`` when the filename is only ``Seite-{page}``.
    """

    pages: list[tuple[int | None, int, Path]] = []
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        match = IMAGE_NAME.fullmatch(path.stem)
        if match is not None:
            pages.append((int(match["book"]), int(match["page"]), path))
            continue
        match = IMAGE_NAME_SIMPLE.fullmatch(path.stem)
        if match is not None:
            pages.append((None, int(match["page"]), path))
            continue
        print(f"Skipping unrecognized image name: {path.name}", file=sys.stderr)
    pages.sort(key=lambda item: ((item[0] is None, item[0] or 0), item[1], item[2].name.lower()))
    return pages


def index_audio(folder: Path) -> dict[tuple[str, int, int], str]:
    """Map ``(language, book, page)`` to the audio filename as stored on disk."""

    audio: dict[tuple[str, int, int], str] = {}
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() != ".mp3":
            continue
        match = AUDIO_NAME.fullmatch(path.stem)
        if match is None:
            print(f"Skipping unrecognized audio name: {path.name}", file=sys.stderr)
            continue
        key = (match["lang"], int(match["book"]), int(match["page"]))
        audio[key] = path.name
    return audio


def lookup_audio(
    audio: dict[tuple[str, int, int], str],
    lang: str,
    book_number: int | None,
    page_number: int,
) -> str | None:
    """Return the audio filename for a language/page, even without a book number."""

    if book_number is not None:
        return audio.get((lang, book_number, page_number))
    matches = [
        name
        for (item_lang, _book, item_page), name in audio.items()
        if item_lang == lang and item_page == page_number
    ]
    return matches[0] if matches else None


def load_map(image: Path) -> list[dict[str, str]]:
    """Return the click-map next to ``image``, or an empty list if it is missing."""

    map_path = image.with_suffix(".map.txt")
    if not map_path.is_file():
        return []
    data = json.loads(map_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"map file is not a JSON array: {map_path}")
    return data


def build_book_data(
    folder: Path,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    """Assemble the page objects and a list of missing-asset notes."""

    pages = find_images(folder)
    if not pages:
        raise FileNotFoundError(f"no page images found in {folder}")

    audio = index_audio(folder)
    book: dict[str, dict[str, object]] = {}
    notes: list[str] = []

    for index, (book_number, page_number, image) in enumerate(pages):
        croatian = lookup_audio(audio, "k", book_number, page_number)
        hungarian = lookup_audio(audio, "u", book_number, page_number)
        german = lookup_audio(audio, "d", book_number, page_number)
        regions = load_map(image)

        # Field order follows the example: map, k, u, img, d.
        entry: dict[str, object] = {"map": regions}
        if croatian is not None:
            entry["k"] = croatian
        if hungarian is not None:
            entry["u"] = hungarian
        entry["img"] = image.name
        if german is not None:
            entry["d"] = german

        if not regions:
            notes.append(f"{image.name}: no map file")
        if croatian is None:
            notes.append(f"{image.name}: no k audio")
        if hungarian is None:
            notes.append(f"{image.name}: no u audio")
        if german is None:
            notes.append(f"{image.name}: no d audio")

        book[str(index)] = entry

    return book, notes


def main() -> None:
    """Parse CLI options, write data.json, and print a short summary."""

    parser = argparse.ArgumentParser(
        description="Generate click-maps, then write data.json from page files."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        default=Path("Buch"),
        help="folder containing page files (default: Buch)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="destination JSON path (default: <folder>/data.json)",
    )
    parser.add_argument(
        "--skip-maps",
        action="store_true",
        help="reuse existing map files instead of regenerating click-maps",
    )
    args = parser.parse_args()

    if not args.folder.is_dir():
        parser.error(f"folder does not exist: {args.folder}")

    if not args.skip_maps:
        print(f"Generating click-maps in {args.folder}")
        try:
            failed_maps = run_click_maps(args.folder)
        except FileNotFoundError as error:
            parser.error(str(error))
        if failed_maps:
            print(
                f"Click-map generation failed for {len(failed_maps)} image(s); "
                "those pages will have an empty map.",
                file=sys.stderr,
            )
        print()

    output = args.output or args.folder / "data.json"
    try:
        book, notes = build_book_data(args.folder)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))

    output.write_text(json.dumps(book, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(book)} pages to {output}")
    if notes:
        print(f"{len(notes)} missing assets:", file=sys.stderr)
        for note in notes:
            print(f"  {note}", file=sys.stderr)


if __name__ == "__main__":
    main()
