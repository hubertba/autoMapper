#!/usr/bin/env python3
"""Detect the small illustrations embedded in the three coloured text blocks.

Each detected illustration becomes one responsive rectangle in a ``.feat2.map.txt``
JSON file.  The output also names the matching audio file.  Audio numbering is
01/02 for German, 03/04 for Hungarian, and 05/06 for Croatian.

Pass either one page image or the ``Mitlesebuch`` directory.  In directory mode
one raster page is selected from every ``Seite N`` folder; PNG is preferred
because the supplied PNGs contain the inline illustrations.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from create_click_map import COLORS, Box, collect_text_clusters, hue_matches, spatial_clusters

IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
LANGUAGE_KEYS = {"blue": "de", "red": "hu", "purple": "hr"}
AUDIO_START = {"blue": 1, "red": 3, "purple": 5}


@dataclass
class Candidate:
    """One likely inline illustration and its compact visual descriptor."""

    color: str
    language: str
    box: Box
    points: int
    descriptor: tuple[float, ...]
    concept: int = 0


def _expanded_hue_matches(hue: int, ranges: tuple[tuple[int, int], ...], margin: int = 3) -> bool:
    """Match a hue range with wraparound and a small JPEG tolerance."""

    for low, high in ranges:
        for value in (hue, hue - 256, hue + 256):
            if low - margin <= value <= high + margin:
                return True
    return False


def _descriptor(hsv: Image.Image, box: Box) -> tuple[float, ...]:
    """Describe shape and colour so repeated copies get the same audio number."""

    left, top, right, bottom = box
    aspect = (right - left + 1) / max(1, bottom - top + 1)
    histogram = [0.0] * 6
    coloured = 0
    # Use every colour in the crop, including the paragraph's hue. Otherwise
    # the same blue vehicle would look different in German and Hungarian text.
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            hue, saturation, value = hsv.getpixel((x, y))
            if saturation >= 45 and value >= 45:
                histogram[min(5, hue * 6 // 256)] += 1
                coloured += 1
    if coloured:
        histogram = [count / coloured for count in histogram]
    # Aspect has a little extra weight; colour bins distinguish similarly shaped art.
    return (math.log(max(0.05, aspect)) * 0.7, *(value * 1.4 for value in histogram))


def _candidate_groups(
    image: Image.Image,
    text_box: Box,
    color_name: str,
    hue_ranges: tuple[tuple[int, int], ...],
) -> list[Candidate]:
    """Find multicolour/grey connected regions inside one coloured paragraph."""

    rgb = image.convert("RGB")
    hsv = rgb.convert("HSV")
    width, height = rgb.size
    left, top, right, bottom = text_box
    coloured_points: list[tuple[int, int]] = []
    all_points: list[tuple[int, int]] = []

    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            hue, saturation, value = hsv.getpixel((x, y))
            # Keep coloured picture pixels and dark/grey outlines, but remove the
            # paragraph's ink. Remaining dark antialiasing is rejected by density.
            paragraph_ink = saturation >= 35 and _expanded_hue_matches(hue, hue_ranges)
            if paragraph_ink or value < 35:
                continue
            if saturation >= 55:
                coloured_points.append((x, y))
                all_points.append((x, y))
            elif value < 175:
                all_points.append((x, y))

    min_width = max(12, round(width * 0.012))
    min_height = max(10, round(height * 0.012))
    max_width = width * 0.18
    max_height = height * 0.14

    def extract(points: list[tuple[int, int]], minimum_density: float) -> list[Candidate]:
        groups, _, _ = spatial_clusters(points, width, height, bin_fraction=0.003)
        found: list[Candidate] = []
        for group in groups:
            if len(group) < 40:
                continue
            xs = [point[0] for point in group]
            ys = [point[1] for point in group]
            box = (min(xs), min(ys), max(xs), max(ys))
            box_width = box[2] - box[0] + 1
            box_height = box[3] - box[1] + 1
            density = len(group) / (box_width * box_height)
            relative_area = box_width * box_height / (width * height)
            if not (min_width <= box_width <= max_width and min_height <= box_height <= max_height):
                continue
            if density < minimum_density or relative_area > 0.018:
                continue
            found.append(Candidate(color_name, LANGUAGE_KEYS[color_name], box, len(group), _descriptor(hsv, box)))
        return found

    # Saturated pixels keep adjacent illustrations separate. The grey-inclusive
    # pass recovers objects such as roads and ladders; only non-overlapping boxes
    # are added so it cannot join two already detected pictures.
    result = extract(coloured_points, 0.13)
    grey_candidates = extract(all_points, 0.13)
    for candidate in grey_candidates:
        overlaps = [
            item for item in result
            if not (candidate.box[2] < item.box[0] or item.box[2] < candidate.box[0]
                    or candidate.box[3] < item.box[1] or item.box[3] < candidate.box[1])
        ]
        if not overlaps:
            result.append(candidate)

    # Two vertically adjacent pictures can occasionally be connected by a few
    # antialiased text pixels. Split the single composite at a clear whitespace
    # valley instead of emitting one box around both pictures.
    if len(result) == 1:
        candidate = result[0]
        c_left, c_top, c_right, c_bottom = candidate.box
        crop_points = [
            point for point in all_points
            if c_left <= point[0] <= c_right and c_top <= point[1] <= c_bottom
        ]
        choices: list[tuple[float, str, int]] = []
        for axis, start, end in (("x", c_left, c_right), ("y", c_top, c_bottom)):
            counts = []
            for position in range(start, end + 1):
                counts.append(sum(1 for x, y in crop_points if (x if axis == "x" else y) == position))
            low = len(counts) // 4
            high = len(counts) * 3 // 4
            if high > low and max(counts) > 0:
                index = min(range(low, high), key=lambda item: counts[item])
                choices.append((counts[index] / max(counts), axis, start + index))
        if choices:
            valley, axis, split = min(choices)
            halves = (
                [point for point in crop_points if (point[0] if axis == "x" else point[1]) < split],
                [point for point in crop_points if (point[0] if axis == "x" else point[1]) > split],
            )
            split_candidates: list[Candidate] = []
            for half in halves:
                if len(half) < 40:
                    continue
                xs, ys = [point[0] for point in half], [point[1] for point in half]
                box = (min(xs), min(ys), max(xs), max(ys))
                if box[2] - box[0] + 1 < min_width or box[3] - box[1] + 1 < min_height:
                    continue
                split_candidates.append(
                    Candidate(color_name, LANGUAGE_KEYS[color_name], box, len(half), _descriptor(hsv, box))
                )
            if valley <= 0.10 and len(split_candidates) == 2:
                result = split_candidates
    return result


def _distance(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    return sum((a - b) ** 2 for a, b in zip(first, second))


def _assign_concepts(candidates: list[Candidate]) -> None:
    """Split each language's occurrences into its two picture concepts."""

    # The paragraph colour can slightly alter antialiased picture edges, so
    # clustering each language independently is more reliable than one page-wide
    # clustering. Repeated copies still receive the same concept/audio number.
    for color in COLORS:
        items = [candidate for candidate in candidates if candidate.color == color.name]
        if not items:
            continue
        descriptors = [candidate.descriptor for candidate in items]
        first = descriptors[0]
        second = max(descriptors, key=lambda item: _distance(item, first))
        centres = [first, second]

        for _ in range(12):
            groups: list[list[tuple[float, ...]]] = [[], []]
            for candidate in items:
                concept = min(range(2), key=lambda index: _distance(candidate.descriptor, centres[index]))
                candidate.concept = concept
                groups[concept].append(candidate.descriptor)
            changed = False
            for index, group in enumerate(groups):
                if not group:
                    continue
                centre = tuple(sum(values) / len(group) for values in zip(*group))
                changed |= centre != centres[index]
                centres[index] = centre
            if not changed:
                break

        reading_order = sorted(items, key=lambda item: (item.box[1], item.box[0]))
        if reading_order[0].concept == 1:
            for candidate in items:
                candidate.concept = 1 - candidate.concept


def _text_boxes(
    image: Image.Image, saturation: int, brightness: int, trim: float
) -> dict[str, Box]:
    """Find paragraphs without merging them into nearby same-coloured artwork."""

    hsv = image.convert("HSV")
    width, height = image.size
    points: dict[str, list[tuple[int, int]]] = {color.name: [] for color in COLORS}
    for y in range(height):
        for x in range(width):
            hue, sat, value = hsv.getpixel((x, y))
            if sat < saturation or value < brightness:
                continue
            for color in COLORS:
                if hue_matches(hue, color.hue_ranges):
                    points[color.name].append((x, y))
                    break
    boxes: dict[str, Box] = {}
    for color in COLORS:
        ranked = collect_text_clusters(points[color.name], width, height, trim)
        if not ranked:
            continue
        # Paragraphs can be split by an inline picture or a large line gap. Join
        # only modest, aligned text-like clusters. The relative-area guard keeps
        # nearby large illustrations (for example a red sail) out of the block.
        selected = ranked[0]
        left, top, right, bottom = selected.box
        for cluster in ranked[1:]:
            if cluster.rel_area >= 0.10:
                continue
            c_left, c_top, c_right, c_bottom = cluster.box
            x_overlap = max(0, min(right, c_right) - max(left, c_left) + 1)
            y_overlap = max(0, min(bottom, c_bottom) - max(top, c_top) + 1)
            x_ratio = x_overlap / max(1, min(right - left + 1, c_right - c_left + 1))
            y_ratio = y_overlap / max(1, min(bottom - top + 1, c_bottom - c_top + 1))
            vertical_gap = max(0, c_top - bottom, top - c_bottom)
            horizontal_gap = max(0, c_left - right, left - c_right)
            if ((x_ratio >= 0.55 and vertical_gap <= height * 0.08)
                    or (y_ratio >= 0.55 and horizontal_gap <= width * 0.08)):
                left, top = min(left, c_left), min(top, c_top)
                right, bottom = max(right, c_right), max(bottom, c_bottom)
        boxes[color.name] = (left, top, right, bottom)
    return boxes


def detect_inline_images(
    image: Image.Image,
    saturation: int = 90,
    brightness: int = 90,
    trim: float = 0.0005,
    padding: float = 0.45,
) -> list[dict[str, object]]:
    """Return responsive map entries for inline illustrations in language order."""

    image = image.convert("RGB")
    width, height = image.size
    text_boxes = _text_boxes(image, saturation, brightness, trim)
    candidates: list[Candidate] = []
    for color in COLORS:
        if color.name in text_boxes:
            candidates.extend(
                _candidate_groups(image, text_boxes[color.name], color.name, color.hue_ranges)
            )

    _assign_concepts(candidates)

    # The feature has exactly two picture concepts per language. Keep the
    # strongest occurrence of each concept; repeated pictures intentionally map
    # to the same audio and do not create extra output rectangles.
    reduced: list[Candidate] = []
    for color in COLORS:
        items = [item for item in candidates if item.color == color.name]
        for concept in (0, 1):
            matches = [item for item in items if item.concept == concept]
            if matches:
                reduced.append(max(matches, key=lambda item: item.points))
    candidates = reduced

    pad_x = round(width * padding / 100)
    pad_y = round(height * padding / 100)
    color_order = {color.name: index for index, color in enumerate(COLORS)}
    candidates.sort(key=lambda item: (color_order[item.color], item.box[1], item.box[0]))

    result: list[dict[str, object]] = []
    for candidate in candidates:
        left, top, right, bottom = candidate.box
        left = max(0, left - pad_x)
        top = max(0, top - pad_y)
        right = min(width - 1, right + pad_x)
        bottom = min(height - 1, bottom + pad_y)
        audio_number = AUDIO_START[candidate.color] + candidate.concept
        result.append(
            {
                "width": f"{(right - left + 1) * 100 / width:.6f}%",
                "height": f"{(bottom - top + 1) * 100 / height:.6f}%",
                "left": f"{left * 100 / width:.6f}%",
                "top": f"{top * 100 / height:.6f}%",
                "language": candidate.language,
                "picture": candidate.concept + 1,
                "audio": f"{audio_number:02d}.mp3",
            }
        )
    return result


def _page_number(path: Path) -> int:
    try:
        return int(path.name.split()[-1])
    except ValueError:
        return sys.maxsize


def find_page_images(root: Path) -> list[Path]:
    """Choose one raster page from each ``Seite N`` subfolder."""

    pages: list[Path] = []
    for folder in sorted((item for item in root.glob("Seite *") if item.is_dir()), key=_page_number):
        images = [
            item for item in folder.iterdir()
            if item.is_file()
            and item.suffix.lower() in IMAGE_SUFFIXES
            and ".feat2.map" not in item.name
        ]
        if not images:
            print(f"Skipping {folder}: no raster page image", file=sys.stderr)
            continue
        # Feature-two edits are in PNG when both an original JPEG and PNG exist.
        page_images = [item for item in images if "misi" in item.stem.lower()]
        if page_images:
            images = page_images
        images.sort(key=lambda item: (item.suffix.lower() != ".png", -item.stat().st_size, item.name.lower()))
        pages.append(images[0])
    return pages


def process(image_path: Path, output: Path | None = None, **options: object) -> int:
    """Detect one image, write its map, and return the number of areas."""

    with Image.open(image_path) as image:
        areas = detect_inline_images(image, **options)
    output = output or image_path.with_suffix(".feat2.map.txt")
    output.write_text(json.dumps(areas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    status = "OK" if len(areas) == 6 else "CHECK"
    print(f"{image_path}: {len(areas)} areas [{status}] -> {output}")
    return len(areas)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect inline pictures and create feature-two audio click maps."
    )
    parser.add_argument("source", type=Path, help="a page image or the Mitlesebuch directory")
    parser.add_argument("--output", type=Path, help="output map path (single-image mode only)")
    parser.add_argument("--padding", type=float, default=0.45, help="area padding as image percent")
    parser.add_argument("--saturation", type=int, default=90, help="text detector HSV saturation")
    parser.add_argument("--brightness", type=int, default=90, help="text detector HSV brightness")
    parser.add_argument("--trim", type=float, default=0.0005, help="text detector outlier trim")
    args = parser.parse_args()

    if not args.source.exists():
        parser.error(f"source does not exist: {args.source}")
    if not 0 <= args.padding <= 10:
        parser.error("--padding must be between 0 and 10")
    options = dict(
        padding=args.padding,
        saturation=args.saturation,
        brightness=args.brightness,
        trim=args.trim,
    )

    if args.source.is_file():
        process(args.source, args.output, **options)
        return
    if args.output:
        parser.error("--output can only be used with one image")
    pages = find_page_images(args.source)
    if not pages:
        parser.error(f"no page images found below {args.source}")
    counts = [process(page, **options) for page in pages]
    print(f"Done: {len(pages)} pages, {sum(counts)} clickable areas")


if __name__ == "__main__":
    main()
