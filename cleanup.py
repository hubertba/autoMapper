#!/usr/bin/env python3
"""Delete generated click-map files from a book folder.

Removes ``*.clickmap.html`` and ``*.map.txt`` written by
``create_click_map.py``. Images and other assets are left untouched.
"""

from __future__ import annotations

import argparse
from pathlib import Path


GENERATED_SUFFIXES = (".clickmap.html", ".map.txt")


def find_generated(folder: Path) -> list[Path]:
    """Return generated map files in ``folder``, sorted by name."""

    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.name.endswith(GENERATED_SUFFIXES)
    ]
    return sorted(files, key=lambda path: path.name.lower())


def main() -> None:
    """Delete generated HTML and map files from the given folder."""

    parser = argparse.ArgumentParser(
        description="Delete .clickmap.html and .map.txt files from a folder."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        default=Path("Buch"),
        help="folder containing generated map files (default: Buch)",
    )
    args = parser.parse_args()

    if not args.folder.is_dir():
        parser.error(f"folder does not exist: {args.folder}")

    files = find_generated(args.folder)
    if not files:
        print(f"No generated map files in {args.folder}")
        return

    for path in files:
        path.unlink()
        print(f"deleted {path}")

    print(f"Done: {len(files)} files removed")


if __name__ == "__main__":
    main()
