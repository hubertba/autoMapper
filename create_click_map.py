#!/usr/bin/env python3
"""Generate image-map files from color-coded multilingual page images.

The source pages used by this project contain one text block per language. Text
color identifies the language: blue is German, red is Hungarian, and purple is
Croatian. This script finds pixels in those hue ranges, groups nearby pixels,
and keeps only clusters that look like colored text (modest fill, compact
size). The three rectangles are then separated so they never overlap.

Two files are produced:

* ``<image>.map.txt`` stores responsive percentage coordinates as JSON.
* ``<image>.clickmap.html`` demonstrates a native HTML ``<map>`` whose pixel
  coordinates are rescaled by JavaScript whenever the image changes size.

This is color segmentation, not OCR. Images containing illustrations in the
same colors may still need threshold tuning or a manual correction.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image


@dataclass(frozen=True)
class TextColor:
    """Configuration tying a pixel hue to an output language/area."""

    # ``name`` is used by command-line options such as ``--link blue=...``.
    name: str
    # ``language`` is used for accessible labels in the generated HTML.
    language: str
    # Pillow stores hue on 0..255 rather than the more familiar 0..360 scale.
    # Multiple ranges are needed for red because red wraps around both ends.
    hue_ranges: tuple[tuple[int, int], ...]


# Output order is significant: map entries are German, Hungarian, Croatian.
# Change these ranges when the source material uses noticeably different inks.
COLORS = (
    TextColor("blue", "German", ((155, 184),)),       # about 219-260 degrees
    TextColor("red", "Hungarian", ((0, 12), (247, 255))),
    TextColor("purple", "Croatian", ((202, 244),)),   # about 285-345 degrees
)


def hue_matches(hue: int, ranges: Iterable[tuple[int, int]]) -> bool:
    """Return whether a Pillow HSV hue falls inside any inclusive range."""

    return any(low <= hue <= high for low, high in ranges)


def percentile(values: list[int], fraction: float) -> int:
    """Return a simple nearest-rank percentile from a non-empty integer list."""

    # Sorting in place is safe here: callers construct temporary x/y lists.
    values.sort()
    index = min(len(values) - 1, int((len(values) - 1) * fraction))
    return values[index]


Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class PixelCluster:
    """One spatially connected same-hue region and its text-likeness score."""

    points: tuple[tuple[int, int], ...]
    box: Box
    fill: float
    rel_area: float
    score: float


def box_area(box: Box) -> int:
    """Return the inclusive pixel area of a rectangle."""

    left, top, right, bottom = box
    return max(0, right - left + 1) * max(0, bottom - top + 1)


def overlap_box(first: Box, second: Box) -> Box | None:
    """Return the inclusive overlap rectangle, or None if the boxes are disjoint."""

    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    if right < left or bottom < top:
        return None
    return (left, top, right, bottom)


def contains_box(outer: Box, inner: Box) -> bool:
    """Return whether ``inner`` lies fully inside ``outer``."""

    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def count_in_box(points: Iterable[tuple[int, int]], box: Box) -> int:
    """Count points that fall inside an inclusive rectangle."""

    left, top, right, bottom = box
    return sum(1 for x, y in points if left <= x <= right and top <= y <= bottom)


def spatial_clusters(
    points: list[tuple[int, int]],
    width: int,
    height: int,
    bin_fraction: float = 0.02,
) -> tuple[list[list[tuple[int, int]]], int, int]:
    """Group same-hue pixels into spatially connected clusters.

    Individual letters are disconnected pixel islands, so normal connected
    component analysis would return one component per letter. Instead, pixels
    are placed into coarse bins covering a fraction of the image. Adjacent
    occupied bins become one cluster, which joins letters and lines while
    leaving distant logos or colored marks separate.
    """

    # Never use bins smaller than eight pixels on very small input images.
    bin_width = max(8, round(width * bin_fraction))
    bin_height = max(8, round(height * bin_fraction))
    bins: dict[tuple[int, int], list[tuple[int, int]]] = {}
    # Keep original points in each bin because the final box should retain
    # source-image precision rather than snapping to the coarse grid.
    for x, y in points:
        bins.setdefault((x // bin_width, y // bin_height), []).append((x, y))

    # One noisy pixel should not bridge two otherwise separate regions.
    occupied = {key for key, pixels in bins.items() if len(pixels) >= 2}
    groups: list[set[tuple[int, int]]] = []
    # Flood-fill every eight-connected group of occupied bins. Diagonal
    # adjacency helps connect slanted characters and neighboring text lines.
    while occupied:
        seed = occupied.pop()
        cluster = {seed}
        pending = [seed]
        while pending:
            bx, by = pending.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbor = (bx + dx, by + dy)
                    if neighbor in occupied:
                        occupied.remove(neighbor)
                        cluster.add(neighbor)
                        pending.append(neighbor)
        groups.append(cluster)

    if not groups:
        return ([points] if points else []), bin_width, bin_height

    clustered = [[point for key in group for point in bins[key]] for group in groups]
    return clustered, bin_width, bin_height


def score_cluster(
    points: list[tuple[int, int]],
    width: int,
    height: int,
    bin_width: int,
    bin_height: int,
    trim_fraction: float,
) -> PixelCluster | None:
    """Score a cluster as colored text rather than filled illustration.

    Printed letters occupy only a modest fraction of their bounding box and
    stay in a compact rectangle. Solid artwork is much denser; scattered
    decorations such as snowflakes cover a huge sparse area.
    """

    if len(points) < 60:
        return None

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left = percentile(xs, trim_fraction)
    right = percentile(xs, 1.0 - trim_fraction)
    top = percentile(ys, trim_fraction)
    bottom = percentile(ys, 1.0 - trim_fraction)
    box = (left, top, right, bottom)
    area = box_area(box)
    if area <= 0:
        return None

    fill = len(points) / area
    rel_area = area / (width * height)
    box_w = right - left + 1
    box_h = bottom - top + 1
    bbox_bins = max(1, math.ceil(box_w / bin_width) * math.ceil(box_h / bin_height))
    occupied_bins = {
        (x // bin_width, y // bin_height) for x, y in points if left <= x <= right and top <= y <= bottom
    }
    bin_fill = len(occupied_bins) / bbox_bins

    # Hard filters: filled blobs, page-sized regions, or ink too sparse to be text.
    if fill > 0.30 or fill < 0.04:
        return None
    if rel_area > 0.38 or rel_area < 0.003:
        return None
    if bin_fill < 0.12 and rel_area > 0.15:
        return None
    aspect = box_w / box_h
    rel_w = box_w / width
    rel_h = box_h / height
    if aspect < 0.28 or rel_h < 0.022 or rel_w < 0.04:
        return None

    row_counts: dict[int, int] = {}
    for _, y in points:
        if top <= y <= bottom:
            row_counts[y] = row_counts.get(y, 0) + 1
    ink_rows = {y for y, count in row_counts.items() if count >= 4}
    bands = 0
    in_band = False
    for y in range(top, bottom + 1):
        if y in ink_rows:
            if not in_band:
                bands += 1
                in_band = True
        else:
            in_band = False
    # Large single masses are drawings; titles can be one band if they stay small.
    if bands < 2 and rel_area > 0.055:
        return None

    # Printed paragraphs usually sit in this fill band; sparse drawings sit lower.
    if 0.08 <= fill <= 0.22:
        fill_score = 1.0
    elif fill < 0.08:
        fill_score = fill / 0.08
    else:
        fill_score = max(0.15, (0.30 - fill) / 0.08)
    size_score = 1.0 / (1.0 + abs(math.log((rel_area + 1e-6) / 0.08)))
    aspect_score = 1.0 if 0.4 <= aspect <= 8 else 0.4
    band_score = 1.15 if bands >= 2 else 0.85
    score = math.sqrt(len(points)) * fill_score * size_score * aspect_score * band_score * (0.4 + bin_fill)
    return PixelCluster(tuple(points), box, fill, rel_area, score)


def collect_text_clusters(
    points: list[tuple[int, int]],
    width: int,
    height: int,
    trim_fraction: float,
    bin_fraction: float = 0.02,
    depth: int = 0,
) -> list[PixelCluster]:
    """Score clusters, splitting oversized mixed regions at a finer grid."""

    clustered, bin_width, bin_height = spatial_clusters(
        points, width, height, bin_fraction
    )
    found: list[PixelCluster] = []
    for cluster_points in clustered:
        cluster = score_cluster(
            cluster_points, width, height, bin_width, bin_height, trim_fraction
        )
        if cluster is not None:
            found.append(cluster)
            continue
        if depth < 2 and len(cluster_points) >= 400:
            found.extend(
                collect_text_clusters(
                    cluster_points,
                    width,
                    height,
                    trim_fraction,
                    bin_fraction * 0.5,
                    depth + 1,
                )
            )
    found.sort(key=lambda item: item.score, reverse=True)
    return found


def _range_overlap(a0: int, a1: int, b0: int, b1: int) -> float:
    """Return overlap length divided by the shorter span, or 0 if disjoint."""

    overlap = min(a1, b1) - max(a0, b0) + 1
    if overlap <= 0:
        return 0.0
    return overlap / (min(a1 - a0, b1 - b0) + 1)


def boxes_should_merge(first: Box, second: Box, width: int, height: int) -> bool:
    """Return whether two same-color text boxes are one column or one row."""

    x_overlap = _range_overlap(first[0], first[2], second[0], second[2])
    y_overlap = _range_overlap(first[1], first[3], second[1], second[3])
    if x_overlap >= 0.55:
        if first[3] < second[1]:
            gap = second[1] - first[3]
        elif second[3] < first[1]:
            gap = first[1] - second[3]
        else:
            gap = 0
        return gap <= round(0.08 * height)
    if y_overlap >= 0.55:
        if first[2] < second[0]:
            gap = second[0] - first[2]
        elif second[2] < first[0]:
            gap = first[0] - second[2]
        else:
            gap = 0
        return gap <= round(0.08 * width)
    return False


def merge_aligned_clusters(
    clusters: list[PixelCluster],
    width: int,
    height: int,
    bin_width: int,
    bin_height: int,
    trim_fraction: float,
) -> list[PixelCluster]:
    """Join stacked or side-by-side fragments of the same text block."""

    remaining = list(clusters)
    changed = True
    while changed:
        changed = False
        remaining.sort(key=lambda item: item.score, reverse=True)
        used = [False] * len(remaining)
        merged: list[PixelCluster] = []
        for i, first in enumerate(remaining):
            if used[i]:
                continue
            current = first
            used[i] = True
            for j in range(i + 1, len(remaining)):
                if used[j]:
                    continue
                second = remaining[j]
                if not boxes_should_merge(current.box, second.box, width, height):
                    continue
                combined = list(current.points) + list(second.points)
                scored = score_cluster(
                    combined, width, height, bin_width, bin_height, trim_fraction
                )
                if scored is None:
                    continue
                current = scored
                used[j] = True
                changed = True
            merged.append(current)
        remaining = merged
    remaining.sort(key=lambda item: item.score, reverse=True)
    return remaining


def box_has_text_color(
    box: Box,
    color_name: str,
    points: dict[str, list[tuple[int, int]]],
) -> bool:
    """Return whether ``box`` is dominated by ``color_name`` ink, like text."""

    area = box_area(box)
    if area <= 0:
        return False
    own = count_in_box(points[color_name], box)
    if own / area < 0.02:
        return False
    others = sum(
        count_in_box(points[name], box) for name in points if name != color_name
    )
    return own >= others


def pick_text_clusters(
    ranked: dict[str, list[PixelCluster]],
    points: dict[str, list[tuple[int, int]]],
) -> dict[str, PixelCluster]:
    """Choose one text-like cluster per color, avoiding nested boxes."""

    chosen: dict[str, PixelCluster] = {}
    used: dict[str, set[int]] = {name: set() for name in ranked}

    def next_cluster(name: str) -> PixelCluster | None:
        for index, cluster in enumerate(ranked[name]):
            if index in used[name]:
                continue
            if not box_has_text_color(cluster.box, name, points):
                used[name].add(index)
                continue
            return cluster
        return None

    for color in COLORS:
        cluster = next_cluster(color.name)
        if cluster is not None:
            chosen[color.name] = cluster

    # If a larger illustration-like box still swallowed another language, drop it.
    for _ in range(len(COLORS) * 3):
        replaced = False
        names = list(chosen)
        for i, first in enumerate(names):
            for second in names[i + 1 :]:
                a = chosen[first].box
                b = chosen[second].box
                larger_name, smaller_box = (
                    (first, b) if box_area(a) >= box_area(b) else (second, a)
                )
                if not contains_box(chosen[larger_name].box, smaller_box):
                    continue
                for index, cluster in enumerate(ranked[larger_name]):
                    if cluster is chosen[larger_name]:
                        used[larger_name].add(index)
                        break
                replacement = next_cluster(larger_name)
                if replacement is None:
                    del chosen[larger_name]
                else:
                    chosen[larger_name] = replacement
                replaced = True
                break
            if replaced:
                break
        if not replaced:
            break
    return chosen


def shrink_box(box: Box, overlap: Box, axis: str, side: str, gap: int) -> Box | None:
    """Cut ``overlap`` off one edge of ``box``. Return None if the box would collapse."""

    left, top, right, bottom = box
    o_left, o_top, o_right, o_bottom = overlap
    if axis == "x" and side == "right":
        right = o_left - gap
    elif axis == "x" and side == "left":
        left = o_right + gap
    elif axis == "y" and side == "bottom":
        bottom = o_top - gap
    else:
        top = o_bottom + gap
    if right - left < 8 or bottom - top < 8:
        return None
    return (left, top, right, bottom)


def resolve_overlaps(
    boxes: dict[str, Box],
    points: dict[str, list[tuple[int, int]]],
    gap: int = 4,
) -> dict[str, Box]:
    """Shrink overlapping rectangles until they no longer share pixels.

    The language that contributes more of its own ink to the overlap keeps that
    space; the other box is cut back on the cheaper axis.
    """

    names = [color.name for color in COLORS if color.name in boxes]
    resolved = dict(boxes)
    for _ in range(len(names) * 4):
        changed = False
        for i, first in enumerate(names):
            for second in names[i + 1 :]:
                if first not in resolved or second not in resolved:
                    continue
                overlap = overlap_box(resolved[first], resolved[second])
                if overlap is None:
                    continue
                count_first = count_in_box(points[first], overlap)
                count_second = count_in_box(points[second], overlap)
                loser = second if count_first >= count_second else first
                winner = first if loser == second else second
                o_left, o_top, o_right, o_bottom = overlap
                dx = o_right - o_left + 1
                dy = o_bottom - o_top + 1
                loser_box = resolved[loser]
                winner_box = resolved[winner]
                if dx <= dy:
                    axis = "x"
                    loser_center = (loser_box[0] + loser_box[2]) / 2
                    winner_center = (winner_box[0] + winner_box[2]) / 2
                    side = "right" if loser_center <= winner_center else "left"
                else:
                    axis = "y"
                    loser_center = (loser_box[1] + loser_box[3]) / 2
                    winner_center = (winner_box[1] + winner_box[3]) / 2
                    side = "bottom" if loser_center <= winner_center else "top"
                shrunk = shrink_box(loser_box, overlap, axis, side, gap)
                if shrunk is None:
                    other_axis = "y" if axis == "x" else "x"
                    if other_axis == "x":
                        other_side = (
                            "right"
                            if (loser_box[0] + loser_box[2]) / 2
                            <= (winner_box[0] + winner_box[2]) / 2
                            else "left"
                        )
                    else:
                        other_side = (
                            "bottom"
                            if (loser_box[1] + loser_box[3]) / 2
                            <= (winner_box[1] + winner_box[3]) / 2
                            else "top"
                        )
                    shrunk = shrink_box(loser_box, overlap, other_axis, other_side, gap)
                if shrunk is None:
                    # Last resort: split the overlap at its midpoint.
                    if axis == "x":
                        mid = (o_left + o_right) // 2
                        if side == "right":
                            shrunk = (loser_box[0], loser_box[1], mid - gap, loser_box[3])
                        else:
                            shrunk = (mid + gap, loser_box[1], loser_box[2], loser_box[3])
                    else:
                        mid = (o_top + o_bottom) // 2
                        if side == "bottom":
                            shrunk = (loser_box[0], loser_box[1], loser_box[2], mid - gap)
                        else:
                            shrunk = (loser_box[0], mid + gap, loser_box[2], loser_box[3])
                    if shrunk[2] - shrunk[0] < 8 or shrunk[3] - shrunk[1] < 8:
                        continue
                resolved[loser] = shrunk
                changed = True
        if not changed:
            break
    return resolved


def largest_spatial_cluster(
    points: list[tuple[int, int]], width: int, height: int
) -> list[tuple[int, int]]:
    """Return the cluster with the most pixels. Kept for compatibility."""

    clustered, _, _ = spatial_clusters(points, width, height)
    if not clustered:
        return points
    return max(clustered, key=len)


def detect_boxes(
    image: Image.Image,
    saturation: int,
    brightness: int,
    padding_percent: float,
    trim_fraction: float,
) -> dict[str, tuple[int, int, int, int]]:
    """Detect one padded text rectangle per configured color.

    Saturation removes gray/black content, brightness removes very dark scan
    artifacts, and hue assigns each surviving pixel to a language. Clusters that
    look like filled illustrations or scattered decorations are discarded. The
    remaining rectangles are padded, then shrunk so they do not overlap.
    """

    width, height = image.size
    points: dict[str, list[tuple[int, int]]] = {color.name: [] for color in COLORS}

    # HSV separates the color itself (hue) from color intensity (saturation)
    # and lightness (value), making it more tolerant of scans than exact RGB.
    # RGB normalization also handles palette and grayscale source modes.
    hsv = image.convert("RGB").convert("HSV")
    pixels = (
        hsv.get_flattened_data()
        if hasattr(hsv, "get_flattened_data")
        else hsv.getdata()
    )
    # Scan once and classify into all configured colors. ``index`` is converted
    # back to x/y without constructing a second full-size coordinate image.
    for index, (hue, sat, value) in enumerate(pixels):
        if sat < saturation or value < brightness:
            continue
        for color in COLORS:
            if hue_matches(hue, color.hue_ranges):
                points[color.name].append((index % width, index // width))
                break

    ranked: dict[str, list[PixelCluster]] = {color.name: [] for color in COLORS}
    bin_width = max(8, round(width * 0.02))
    bin_height = max(8, round(height * 0.02))
    for color in COLORS:
        ranked[color.name] = merge_aligned_clusters(
            collect_text_clusters(points[color.name], width, height, trim_fraction),
            width,
            height,
            bin_width,
            bin_height,
            trim_fraction,
        )

    chosen = pick_text_clusters(ranked, points)
    if not chosen:
        return {}

    # Percentage padding scales consistently across scans of different sizes.
    pad_x = round(width * padding_percent / 100)
    pad_y = round(height * padding_percent / 100)
    boxes: dict[str, Box] = {}
    for name, cluster in chosen.items():
        left, top, right, bottom = cluster.box
        boxes[name] = (
            max(0, left - pad_x),
            max(0, top - pad_y),
            min(width - 1, right + pad_x),
            min(height - 1, bottom + pad_y),
        )
    return resolve_overlaps(boxes, points)


def percentage_map(
    boxes: dict[str, tuple[int, int, int, int]], width: int, height: int
) -> list[dict[str, str]]:
    """Convert pixel rectangles to the responsive map-file representation.

    Colors without a valid text block are stored as a zero-size placeholder so
    the array order stays German, Hungarian, Croatian.
    """

    result = []
    for color in COLORS:
        if color.name not in boxes:
            result.append({"width": "0%", "height": "0%", "left": "0%", "top": "0%"})
            continue
        left, top, right, bottom = boxes[color.name]
        # Include the right/bottom pixel in the measured rectangle (+1).
        # Values remain strings with ``%`` so the file can be applied directly
        # as CSS by map-tester.html.
        result.append(
            {
                "width": f"{(right - left + 1) * 100 / width:.6f}%",
                "height": f"{(bottom - top + 1) * 100 / height:.6f}%",
                "left": f"{left * 100 / width:.6f}%",
                "top": f"{top * 100 / height:.6f}%",
            }
        )
    return result


def make_html(
    image_source: str,
    boxes: dict[str, tuple[int, int, int, int]],
    width: int,
    height: int,
    links: dict[str, str],
) -> str:
    """Build a self-contained demonstration page using a native HTML map."""

    areas = []
    # Native <area> elements require absolute source-image pixel coordinates.
    for color in COLORS:
        if color.name not in boxes:
            continue
        coords = ",".join(str(number) for number in boxes[color.name])
        areas.append(
            f'    <area shape="rect" coords="{coords}" '
            f'href="{html.escape(links[color.name], quote=True)}" '
            f'alt="{color.language} text" title="{color.language} text">'
        )
    area_markup = "\n".join(areas)
    source = html.escape(image_source, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Clickable text map</title>
  <style>
    body {{ margin: 0; }}
    img {{ display: block; max-width: 100%; height: auto; }}
  </style>
</head>
<body>
  <img id="mapped-image" src="{source}" width="{width}" height="{height}"
       usemap="#text-map" alt="Multilingual illustrated page">
  <map name="text-map">
{area_markup}
  </map>
  <script>
    // Native image-map coordinates are pixels. Rescale them when the image is responsive.
    const image = document.getElementById('mapped-image');
    const areas = [...document.querySelectorAll('area')];
    const original = areas.map(area => area.coords.split(',').map(Number));
    function resizeMap() {{
      const scaleX = image.clientWidth / {width};
      const scaleY = image.clientHeight / {height};
      areas.forEach((area, i) => {{
        const c = original[i];
        area.coords = [c[0] * scaleX, c[1] * scaleY,
                       c[2] * scaleX, c[3] * scaleY].map(Math.round).join(',');
      }});
    }}
    image.addEventListener('load', resizeMap);
    window.addEventListener('resize', resizeMap);
    resizeMap();
  </script>
</body>
</html>
"""


def parse_link(value: str) -> tuple[str, str]:
    """Parse and validate one ``--link COLOR=URL`` command-line value."""

    try:
        color, url = value.split("=", 1)
    except ValueError as error:
        raise argparse.ArgumentTypeError("links must have the form COLOR=URL") from error
    if color not in {item.name for item in COLORS} or not url:
        raise argparse.ArgumentTypeError("COLOR must be blue, red, or purple and URL cannot be empty")
    return color, url


def main() -> None:
    """Validate CLI options, detect areas, and write both output formats."""

    parser = argparse.ArgumentParser(
        description="Detect colored text blocks and create an HTML image map."
    )
    parser.add_argument("image", type=Path, help="PNG/JPEG image to analyze")
    parser.add_argument("--html", type=Path, help="output HTML path")
    parser.add_argument("--map", dest="map_path", type=Path, help="output percentage JSON path")
    parser.add_argument(
        "--link", action="append", type=parse_link, default=[], metavar="COLOR=URL",
        help="target for a color; may be repeated (defaults to #german, etc.)",
    )
    parser.add_argument("--padding", type=float, default=3.0, help="box padding as image percent (default: 3)")
    parser.add_argument("--saturation", type=int, default=90, help="minimum HSV saturation, 0-255")
    parser.add_argument("--brightness", type=int, default=90, help="minimum HSV brightness, 0-255")
    parser.add_argument("--trim", type=float, default=0.0005, help="fraction of outlier pixels trimmed per edge")
    args = parser.parse_args()

    # argparse's ``type`` checks syntax; these checks enforce useful ranges.
    if not 0 <= args.padding <= 25:
        parser.error("--padding must be between 0 and 25")
    if not 0 <= args.saturation <= 255 or not 0 <= args.brightness <= 255:
        parser.error("--saturation and --brightness must be between 0 and 255")
    if not 0 <= args.trim < 0.1:
        parser.error("--trim must be between 0 and 0.1")
    if not args.image.is_file():
        parser.error(f"image does not exist: {args.image}")

    html_path = args.html or args.image.with_suffix(".clickmap.html")
    # Match the supplied example: one JSON array containing every area.
    map_path = args.map_path or args.image.with_suffix(".map.txt")
    # Hash links make areas visibly clickable even when no destination was
    # supplied. Explicit --link values replace only their matching colors.
    links = {color.name: f"#{color.language.lower()}" for color in COLORS}
    links.update(dict(args.link))

    # Pillow opens lazily and the context manager closes the file promptly.
    with Image.open(args.image) as image:
        boxes = detect_boxes(
            image, args.saturation, args.brightness, args.padding, args.trim
        )
        width, height = image.size

    # Keep the HTML portable when --html points to another directory: its image
    # URL is relative to the generated HTML file, not the current shell folder.
    image_source = os.path.relpath(args.image.resolve(), html_path.parent.resolve())
    html_path.write_text(
        make_html(image_source, boxes, width, height, links), encoding="utf-8"
    )
    map_path.write_text(
        json.dumps(percentage_map(boxes, width, height), indent=2) + "\n",
        encoding="utf-8",
    )

    for color in COLORS:
        if color.name in boxes:
            print(f"{color.language:9} ({color.name:6}): {boxes[color.name]}")
        else:
            print(f"{color.language:9} ({color.name:6}): no text-like region")
    print(f"HTML: {html_path}")
    print(f"Map:  {map_path}")


if __name__ == "__main__":
    main()
