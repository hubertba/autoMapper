#!/usr/bin/env python3
"""Generate image-map files from color-coded multilingual page images.

The source pages used by this project contain one text block per language. Text
color identifies the language: blue is German, red is Hungarian, and purple is
Croatian. This script finds pixels in those hue ranges, groups nearby pixels,
and turns the largest group for each color into a rectangular click target.

Two files are produced:

* ``<image>.map.txt`` stores responsive percentage coordinates as JSON.
* ``<image>.clickmap.html`` demonstrates a native HTML ``<map>`` whose pixel
  coordinates are rescaled by JavaScript whenever the image changes size.

This is color segmentation, not OCR. Images containing illustrations in the
same colors may require threshold tuning or manual correction of the map file.
"""

from __future__ import annotations

import argparse
import html
import json
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


def largest_spatial_cluster(
    points: list[tuple[int, int]], width: int, height: int
) -> list[tuple[int, int]]:
    """Separate remote same-hue artwork/noise from the largest text block.

    Individual letters are disconnected pixel islands, so normal connected
    component analysis would return one component per letter. Instead, pixels
    are placed into coarse bins covering 2% of the image. Adjacent occupied bins
    become one cluster, which joins letters and lines while leaving distant
    logos or colored marks separate. The cluster with most matching pixels is
    assumed to be the text block.
    """

    # Never use bins smaller than eight pixels on very small input images.
    bin_width = max(8, round(width * 0.02))
    bin_height = max(8, round(height * 0.02))
    bins: dict[tuple[int, int], list[tuple[int, int]]] = {}
    # Keep original points in each bin because the final box should retain
    # source-image precision rather than snapping to the coarse grid.
    for x, y in points:
        bins.setdefault((x // bin_width, y // bin_height), []).append((x, y))

    # One noisy pixel should not bridge two otherwise separate regions.
    occupied = {key for key, pixels in bins.items() if len(pixels) >= 2}
    clusters: list[set[tuple[int, int]]] = []
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
        clusters.append(cluster)

    # This fallback is possible only when every occupied bin held one pixel.
    if not clusters:
        return points

    # Pixel count is a better score than bin count: dense printed text should
    # beat a large but very sparse patch of similarly colored scan noise.
    winner = max(clusters, key=lambda group: sum(len(bins[key]) for key in group))
    return [point for key in winner for point in bins[key]]


def detect_boxes(
    image: Image.Image,
    saturation: int,
    brightness: int,
    padding_percent: float,
    trim_fraction: float,
) -> dict[str, tuple[int, int, int, int]]:
    """Detect and return one padded source-pixel rectangle per configured color.

    Rectangles use ``(left, top, right, bottom)`` coordinates. Saturation removes
    gray/black content, brightness removes very dark scan artifacts, and hue
    then assigns each surviving pixel to a language.
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

    # Percentage padding scales consistently across scans of different sizes.
    pad_x = round(width * padding_percent / 100)
    pad_y = round(height * padding_percent / 100)
    boxes: dict[str, tuple[int, int, int, int]] = {}
    for color in COLORS:
        if not points[color.name]:
            raise RuntimeError(
                f"No {color.name} pixels found. Try lowering --saturation/--brightness "
                "or adjusting the hue ranges in COLORS."
            )

        cluster = largest_spatial_cluster(points[color.name], width, height)
        xs = [point[0] for point in cluster]
        ys = [point[1] for point in cluster]
        # Ignore a very small number of outlying pixels within the selected cluster.
        left = percentile(xs, trim_fraction)
        right = percentile(xs, 1.0 - trim_fraction)
        top = percentile(ys, trim_fraction)
        bottom = percentile(ys, 1.0 - trim_fraction)
        # Clamp padding to image edges so generated HTML coordinates are valid.
        boxes[color.name] = (
            max(0, left - pad_x),
            max(0, top - pad_y),
            min(width - 1, right + pad_x),
            min(height - 1, bottom + pad_y),
        )
    return boxes


def percentage_map(
    boxes: dict[str, tuple[int, int, int, int]], width: int, height: int
) -> list[dict[str, str]]:
    """Convert pixel rectangles to the responsive map-file representation."""

    result = []
    for color in COLORS:
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
        print(f"{color.language:9} ({color.name:6}): {boxes[color.name]}")
    print(f"HTML: {html_path}")
    print(f"Map:  {map_path}")


if __name__ == "__main__":
    main()
