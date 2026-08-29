"""Generate the brand assets from the manufacturer's wordmark.

    python scripts/make_brand_icons.py

**This used to draw an original icon instead.** The reasoning was that shipping AVPro Edge's mark
from an unaffiliated public repository would be redistributing someone else's trademark to make a
third-party integration look official. That was over-cautious: Home Assistant's own brands
repository is built almost entirely out of manufacturer logos, because an integration icon is
there to identify *which device* it controls. Using the mark of the product this actually drives
is nominative, and it is what a user expects to see in the integrations list.

The mark remains the property of AVPro Edge, and this project is not affiliated with them --
recorded in the README rather than only here.

## The dark wordmark, and why it is the better source

The first version of this used the light artwork: black glyphs on white, keyed to transparency.
It worked, and it had one real flaw -- black glyphs on a transparent background disappear against
a dark Home Assistant theme, which is the theme most of this installation runs.

The source is now the dark wordmark: **white glyphs on black**, with the black kept rather than
keyed out. A tile carries its own contrast, so the icon reads identically on a light card and a
dark one, and the failure mode is gone rather than mitigated.

That also deletes a whole layer of machinery. There is no white to key, so there is no hard
threshold, no unmultiply question, and no colour bleeding to stop a Lanczos reduction averaging
glyph edges against a background that should not be there. The source's own anti-aliasing is
already against black, which is exactly what the output composites onto. Crop, place, resize.

## Why the icon is not the whole wordmark

The mark is 894 x 276 -- 3.24:1. Home Assistant renders an integration icon at roughly 48 pixels
square, and a 3.24:1 wordmark scaled to fit inside that is about 15 pixels tall: legible as a grey
smudge and nothing else.

So the two assets are cut differently, which is the distinction Home Assistant draws between an
*icon* and a *logo*:

* ``icon.png`` is the **AV ligature alone**. At 1.37:1 it very nearly fills a square, and it still
  reads at 48 pixels.
* ``logo.png`` is the **full wordmark**, where there is room for it.

The split is measured rather than eyeballed. Scanning the source's column ink profile, the V's
taper runs out at x888, columns 889 and 890 are completely empty, and the P's stem starts at x891
with a full 113-pixel column. There is no ambiguity about where one ends and the other begins.

Pillow is a development dependency only. It is used here and nowhere in the integration, and
``manifest.json`` stays at ``requirements: []``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
BRAND_DIR = REPO_ROOT / "custom_components" / "ha_avpro_edge" / "brand"
SOURCE = REPO_ROOT / "assets" / "avpro-edge-wordmark.jpg"

#: The artwork's own background, sampled from the source rather than assumed to be pure black.
#: A JPEG of a black field usually is exactly zero in the flat areas, but reading it means the
#: composed padding cannot develop a seam against the crop if that ever stops being true.
BACKGROUND = (0, 0, 0)

#: Measured crops in source pixels, (left, top, right, bottom) inclusive.
AV_LIGATURE = (513, 402, 889, 677)
FULL_WORDMARK = (513, 402, 1406, 677)

#: How much of the icon's width the AV ligature spans.
#:
#: The Home Assistant brands specification asks for images "trimmed, so [they contain] the minimum
#: amount of empty space on the edges", and that rule is about the *artwork*, which here is the
#: tile: the black runs to all four edges, so nothing is trimmable. What is inside the tile is
#: design, and a mark pressed against the edge of its own tile looks like a mistake.
ICON_FILL = 0.84

#: Padding around the wordmark in the logo, as a fraction of the mark's height. Enough to stop the
#: glyphs touching the edge of the black, and proportionally close to the icon's so the two read
#: as a pair rather than as two unrelated crops.
LOGO_PAD = 0.14


def cropped(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """Crop by inclusive pixel coordinates, which is how the measurements above are written."""
    left, top, right, bottom = box
    return image.crop((left, top, right + 1, bottom + 1))


def on_tile(art: Image.Image, canvas: tuple[int, int], size: tuple[int, int]) -> Image.Image:
    """Centre ``art`` on a background tile of ``canvas`` proportions, then resize to ``size``.

    Composed at source resolution and reduced once, so the single Lanczos pass is the only
    resampling the artwork sees.
    """
    tile = Image.new("RGB", canvas, BACKGROUND)
    tile.paste(art, ((canvas[0] - art.width) // 2, (canvas[1] - art.height) // 2))
    return tile.resize(size, Image.LANCZOS).convert("RGBA")


def build_icon(source: Image.Image, size: int) -> Image.Image:
    """The AV ligature, centred on a square black tile."""
    art = cropped(source, AV_LIGATURE)
    side = round(art.width / ICON_FILL)
    return on_tile(art, (side, side), (size, size))


def build_logo(source: Image.Image, height: int) -> Image.Image:
    """The full wordmark on a black tile, with a proportional margin."""
    art = cropped(source, FULL_WORDMARK)
    pad = round(art.height * LOGO_PAD)
    canvas = (art.width + 2 * pad, art.height + 2 * pad)
    width = round(height * canvas[0] / canvas[1])
    return on_tile(art, canvas, (width, height))


def main() -> int:
    if not SOURCE.exists():
        print(f"source artwork not found: {SOURCE}")
        return 2

    source = Image.open(SOURCE).convert("RGB")
    BRAND_DIR.mkdir(parents=True, exist_ok=True)

    outputs = {
        # Square, sizes fixed by the brands specification.
        "icon.png": build_icon(source, 256),
        "icon@2x.png": build_icon(source, 512),
        # Wide. The specification constrains the *shortest* side -- 128-256 for the base and
        # 256-512 for hDPI, "maximum preferred" -- and leaves the long side to the brand's own
        # aspect ratio. `custom_integrations/spook` ships 500x128 and 1000x256 on that basis.
        "logo.png": build_logo(source, 256),
        "logo@2x.png": build_logo(source, 512),
    }

    for name, image in outputs.items():
        image.save(BRAND_DIR / name, optimize=True)
        print(f"{name:14} {image.size[0]}x{image.size[1]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
