# Automatic image click-map generator

A small Python tool that finds color-coded text blocks in an image and creates clickable HTML image-map areas for them.

The source material for this project uses three text colors:

| Text color | Language | Output position |
|---|---|---:|
| Blue | German | 1 |
| Red | Hungarian | 2 |
| Purple | Croatian | 3 |

For every input image, the tool creates:

1. **`<name>.map.txt`** — one JSON array containing all three responsive, percentage-based rectangles.
2. **`<name>.clickmap.html`** — a ready-to-open HTML page with native clickable `<area>` elements.

The repository also includes **`map-tester.html`**, a browser-based visual tester for any image and `.map.txt` pair.

## Requirements

- Python 3.9 or newer
- [Pillow](https://python-pillow.org/)
- A modern browser for viewing generated pages or using the map tester

## Installation

Clone the repository and install its one dependency:

```bash
git clone https://github.com/hubertba/autoMapper.git
cd autoMapper
python3 -m pip install -r requirements.txt
```

Using a virtual environment is recommended:

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python3 -m pip install -r requirements.txt
```

## Quick start

```bash
python3 create_click_map.py Seite-03.png
```

Output:

```text
Seite-03.clickmap.html
Seite-03.map.txt
```

Open `Seite-03.clickmap.html` in a browser to try the native HTML map.

Paths containing spaces must be quoted:

```bash
python3 create_click_map.py "Apfel 3.jpeg"
```

Process several files from a shell loop:

```bash
for image in "Apfel 3.jpeg" "Apfel 4.jpeg" "Apfel 5.jpeg"; do
  python3 create_click_map.py "$image"
done
```

## Map-file format

A `.map.txt` file is ordinary JSON. It contains one object per click target:

```json
[
  {
    "width": "54.394299%",
    "height": "17.884131%",
    "left": "40.736342%",
    "top": "8.648195%"
  },
  {
    "width": "20.190024%",
    "height": "47.774979%",
    "left": "74.346793%",
    "top": "31.570109%"
  },
  {
    "width": "20.546318%",
    "height": "34.760705%",
    "left": "2.612827%",
    "top": "45.004198%"
  }
]
```

Coordinates are percentages of the complete image:

- `left`: distance from the image's left edge
- `top`: distance from the image's top edge
- `width`: target width
- `height`: target height

Percentages let the visual tester keep targets aligned when an image is resized. Array order is blue/German, red/Hungarian, and purple/Croatian.

## How detection works

The implementation uses color segmentation rather than OCR:

1. Pillow loads the image and normalizes it to RGB.
2. The image is converted to HSV (hue, saturation, value).
3. Low-saturation and dark pixels are discarded. This removes most white, gray, and black content.
4. Remaining pixels are assigned to configured blue, red, or purple hue ranges.
5. Matching pixels are placed into a coarse grid. Adjacent occupied cells are joined into spatial clusters, allowing separate letters and lines to become one block.
6. For each color, the cluster containing the most matching pixels is selected. A tiny percentile trim removes remote noise.
7. The cluster bounds receive configurable padding and are clamped to the image edges.
8. Pixel rectangles are written to the HTML map; percentage rectangles are written to `.map.txt`.

The hue configuration is the `COLORS` tuple near the top of `create_click_map.py`. Pillow hue values range from 0 to 255, not 0 to 360. Red uses two ranges because hue wraps around at the ends of the scale.

## Command-line options

```text
usage: create_click_map.py [-h] [--html FILE] [--map FILE]
                           [--link COLOR=URL] [--padding NUMBER]
                           [--saturation NUMBER] [--brightness NUMBER]
                           [--trim FRACTION]
                           IMAGE
```

| Option | Purpose | Default |
|---|---|---|
| `IMAGE` | PNG, JPEG, or another Pillow-supported image | required |
| `--html FILE` | Generated HTML destination | `<image>.clickmap.html` |
| `--map FILE` | Generated percentage-map destination | `<image>.map.txt` |
| `--link COLOR=URL` | Destination for one color; repeat for multiple colors | language hash |
| `--padding NUMBER` | Padding around each target as percent of image size | `3` |
| `--saturation NUMBER` | Minimum Pillow HSV saturation, 0–255 | `90` |
| `--brightness NUMBER` | Minimum Pillow HSV value, 0–255 | `90` |
| `--trim FRACTION` | Outlier fraction removed from each edge | `0.0005` |

### Assign real links

Without `--link`, generated areas point to `#german`, `#hungarian`, and `#croatian`. Supply destinations like this:

```bash
python3 create_click_map.py Seite-03.png \
  --link blue=german.html \
  --link red=hungarian.html \
  --link purple=croatian.html
```

The option may also contain full URLs:

```bash
python3 create_click_map.py Seite-03.png \
  --link blue=https://example.com/german
```

## Visual map tester

Open `map-tester.html` directly in a modern browser—no web server or build step is required.

1. Click **Select image** and choose the source image.
2. Click **Select map** and choose its `.map.txt` file.
3. Numbered green rectangles appear over the image.
4. Click or keyboard-activate a rectangle to confirm the target.

The tester reads both files locally with browser APIs. It does not upload them. It validates JSON values before applying them as CSS percentages and reports malformed or out-of-bounds areas.

## Responsive HTML behavior

Native HTML `<area>` coordinates are source pixels and browsers do not automatically update them when CSS scales an image. Each generated `.clickmap.html` therefore stores the original coordinates and runs a short JavaScript function on image load and window resize. The function computes horizontal and vertical scale factors and rewrites every area's coordinates.

The `.map.txt` format does not need that conversion because it already uses percentages.

## Tuning and limitations

This detector works best when:

- each language appears once;
- text has saturated, consistent colors;
- illustrations do not use large regions of the same colors;
- the scan has enough contrast between text and background.

It is **not OCR** and does not understand letters or language. Colored illustrations, logos, handwriting, JPEG artifacts, or multiple same-colored text blocks can win the “largest cluster” test. The `Apfel` sample illustrations contain colors similar to their text, so their checked-in map files were visually reviewed and adjusted after automatic generation.

If automatic results are inaccurate:

1. Inspect them with `map-tester.html`.
2. Adjust `--saturation`, `--brightness`, `--padding`, or `--trim`.
3. Adjust hue ranges in `COLORS` for different ink shades.
4. As a final correction, edit percentage values in `.map.txt` and matching pixel coordinates in generated HTML.

## Feature 2: pictures inside the text

`create_image_maps.py` detects the small illustrations embedded in the German,
Hungarian, and Croatian paragraphs. Run it on the complete supplied book:

```bash
python3 create_image_maps.py Mitlesebuch
```

It prefers the edited PNG when a page folder contains both PNG and JPEG files
and writes `<page>.feat2.map.txt` beside each page. A single image can also be
processed:

```bash
python3 create_image_maps.py "Mitlesebuch/Seite 10/16 - MISI 9.png"
```

Every map entry contains percentage coordinates plus `language`, `picture`, and
`audio`. Audio is assigned in the requested order: German `01.mp3`/`02.mp3`,
Hungarian `03.mp3`/`04.mp3`, and Croatian `05.mp3`/`06.mp3`. The strongest
occurrence of each of the two picture concepts is kept, so every page map has
exactly six rectangles. Open `map-tester-feat2.html`, select the page, generated map, and the page's six
MP3 files, then click the coloured rectangles to test them.

A page folder without a Pillow-readable raster image (for example, one that
only contains a `.pxd` editor document) is reported and skipped; export it as
PNG or JPEG and run the command again.

## Project files

```text
create_click_map.py    Text detection and output generator
create_image_maps.py   Inline-picture detection and audio-map generator
map-tester.html        Text-map visual tester
map-tester-feat2.html  Picture-map and MP3 visual tester
requirements.txt       Python dependency list
*.map.txt              Percentage map examples
*.clickmap.html        Generated native HTML-map examples
```

The Python and JavaScript source is extensively commented to explain the image-processing and rendering decisions.
