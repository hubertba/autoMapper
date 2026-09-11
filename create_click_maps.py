#!/usr/bin/env python3
"""Run ``create_click_map.py`` on every image in a folder.

By default the folder is ``Buch``. Extra options such as ``--padding`` or
``--link`` are forwarded unchanged to ``create_click_map.py``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


# Pillow-supported raster formats that this project's source pages use.
IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def find_images(folder: Path) -> list[Path]:
    """Return image files in ``folder``, sorted by case-insensitive name."""

    # ``iterdir`` is enough: page scans sit directly in the book folder.
    images = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    return sorted(images, key=lambda path: path.name.lower())


def run_click_maps(
    folder: Path,
    extra: list[str] | None = None,
    script: Path | None = None,
) -> list[Path]:
    """Generate a click-map for every image in ``folder``.

    Extra CLI options such as ``--padding`` are forwarded to
    ``create_click_map.py``. Returns the list of images that failed.
    """

    script = script or Path(__file__).resolve().with_name("create_click_map.py")
    extra = extra or []
    images = find_images(folder)
    if not images:
        raise FileNotFoundError(f"no images found in {folder}")
    if not script.is_file():
        raise FileNotFoundError(f"script does not exist: {script}")

    failed: list[Path] = []
    for index, image in enumerate(images, start=1):
        print(f"[{index}/{len(images)}] {image}")
        command = [sys.executable, str(script), str(image), *extra]
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            print(f"FAILED: {image} (exit {result.returncode})", file=sys.stderr)
            failed.append(image)
        print()

    succeeded = len(images) - len(failed)
    print(f"Done: {succeeded}/{len(images)} succeeded")
    if failed:
        print("Failed files:", file=sys.stderr)
        for image in failed:
            print(f"  {image}", file=sys.stderr)
    return failed


def main() -> None:
    """Discover images, invoke the generator for each, and report a summary."""

    parser = argparse.ArgumentParser(
        description="Apply create_click_map.py to every image in a folder.",
        epilog="All other options are forwarded to create_click_map.py.",
    )
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        default=Path("Buch"),
        help="folder containing page images (default: Buch)",
    )
    parser.add_argument(
        "--script",
        type=Path,
        default=Path(__file__).resolve().with_name("create_click_map.py"),
        help="path to create_click_map.py",
    )
    # Known args stay here; padding, links, saturation, and similar options
    # pass through so this wrapper does not duplicate the generator CLI.
    args, extra = parser.parse_known_args()

    if not args.folder.is_dir():
        parser.error(f"folder does not exist: {args.folder}")

    try:
        failed = run_click_maps(args.folder, extra, args.script)
    except FileNotFoundError as error:
        parser.error(str(error))

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
