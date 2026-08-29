"""Generate the brand assets from the manufacturer's wordmark.

    python scripts/make_brand_icons.py

**This used to draw an original icon instead.** The reasoning was that shipping AVPro Edge's mark
from an unaffiliated public repository would be redistributing someone else's trademark to make a
third-party integration look official. That was over-cautious: Home Assistant's own brands
repository is built almost entirely out of manufacturer logos, because an integration icon is
there to identify *which device* it controls. Using the mark of the product this actually drives
is nominative, and it is what every user expects to see in the integrations list. The owner asked
for the real logo; the drawn crosspoint icon is gone.

The mark remains the property of AVPro Edge, and this project is not affiliated with them --
recorded in the README rather than only here.

## What gets generated, and why the icon is not the whole wordmark

The source is a 540 x 167 wordmark: a 3.2:1 rectangle. Home Assistant renders an integration icon
at roughly 48 pixels square, and a 3.2:1 wordmark scaled to fit inside that is about 15 pixels
tall -- legible as a grey smudge and nothing else.

So the two assets are cut differently, which is exactly the distinction Home Assistant draws
between an *icon* and a *logo*:

* ``icon.png`` is the **AV ligature alone** (source x30-257). It is bold, nearly square at 1.37:1,
  and still reads at 48 pixels.
* ``logo.png`` is the **full wordmark**, where there is room for it.

The split point is measured rather than eyeballed: scanning the source's column ink profile, the
V's taper thins to a single pixel at x257 and the P's stem starts abruptly at x258 with a full
70-pixel column. There is no ambiguity about where one ends and the other begins.

## Transparency

The source has an opaque white background, and brand assets need alpha. Keying is done at full
resolution with a hard threshold and *then* downsampled, so the anti-aliasing comes from the
resize rather than from a soft key -- which keeps the green block a solid green instead of the
washed-out 75%-alpha colour an unmultiply-from-white would produce.

Before downsampling, ink colour is bled a few pixels into the transparent region. Without that,
the resize averages edge pixels against white and every glyph picks up a pale fringe that only
shows once the icon is composited onto a dark theme.

Pillow is a development dependency only. It is used here and nowhere in the integration, and
``manifest.json`` stays at ``requirements: []``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
BRAND_DIR = REPO_ROOT / "custom_components" / "ha_avpro_edge" / "brand"
SOURCE = REPO_ROOT / "assets" / "avpro-edge-wordmark.png"

#: Anything darker than this on any channel is ink. The background is pure white and the lightest
#: real colour in the mark is the green at (147, 197, 39), so there is a wide margin either side.
WHITE_CUTOFF = 240

#: Measured crops in source pixels, (left, top, right, bottom) inclusive.
AV_LIGATURE = (30, 216, 257, 382)
FULL_WORDMARK = (30, 216, 569, 382)

#: Fraction of the icon's width left clear around the artwork. Home Assistant puts integration
#: icons directly against a card edge, so a mark that runs to the bounding box looks cramped
#: beside icons that do not.
ICON_MARGIN = 0.10

#: How far ink colour is pushed into the transparent region before downsampling. Three pixels
#: comfortably covers the ~2.3-pixel kernel of a 600 -> 256 Lanczos reduction.
BLEED = 3


def keyed(image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Split an opaque-white-backed image into RGB and a hard alpha mask."""
    rgb = np.array(image.convert("RGB")).astype(np.uint8)
    alpha = np.where(rgb.min(axis=2) < WHITE_CUTOFF, 255, 0).astype(np.uint8)
    return rgb, alpha


def bleed_colour(rgb: np.ndarray, alpha: np.ndarray, distance: int = BLEED) -> np.ndarray:
    """Push ink colour outwards into the transparent region.

    The transparent pixels are white, and a Lanczos reduction does not know to ignore them: it
    averages them into every edge, so each glyph ends up ringed in pale grey. Invisible on a white
    page and obvious the moment the icon lands on a dark card.
    """
    rgb = rgb.copy()
    known = alpha > 0
    for _ in range(distance):
        for shift in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            axis = (0, 1)
            neighbour_rgb = np.roll(rgb, shift, axis=axis)
            neighbour_known = np.roll(known, shift, axis=axis)
            take = ~known & neighbour_known
            rgb[take] = neighbour_rgb[take]
            known = known | take
    return rgb


def render(
    rgb: np.ndarray,
    alpha: np.ndarray,
    box: tuple[int, int, int, int],
    size: tuple[int, int],
    *,
    margin: float = 0.0,
) -> Image.Image:
    """Crop, scale to fit inside ``size``, and centre on a transparent canvas.

    RGB and alpha are resized separately. Resizing them together would let Pillow interpolate
    colour and coverage as one quantity, which is only correct for premultiplied data.
    """
    left, top, right, bottom = box
    colour = Image.fromarray(rgb[top : bottom + 1, left : right + 1], "RGB")
    mask = Image.fromarray(alpha[top : bottom + 1, left : right + 1], "L")

    width, height = size
    usable_w = width * (1 - 2 * margin)
    usable_h = height * (1 - 2 * margin)
    scale = min(usable_w / colour.width, usable_h / colour.height)
    target = (max(1, round(colour.width * scale)), max(1, round(colour.height * scale)))

    colour = colour.resize(target, Image.LANCZOS)
    mask = mask.resize(target, Image.LANCZOS)

    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    art = Image.merge("RGBA", (*colour.split(), mask))
    canvas.paste(art, ((width - target[0]) // 2, (height - target[1]) // 2), art)
    return canvas


def main() -> int:
    if not SOURCE.exists():
        print(f"source artwork not found: {SOURCE}")
        return 2

    rgb, alpha = keyed(Image.open(SOURCE))
    rgb = bleed_colour(rgb, alpha)
    BRAND_DIR.mkdir(parents=True, exist_ok=True)

    outputs = {
        # Square, from the AV ligature. Sizes fixed by the Home Assistant brands specification.
        "icon.png": (AV_LIGATURE, (256, 256), ICON_MARGIN),
        "icon@2x.png": (AV_LIGATURE, (512, 512), ICON_MARGIN),
        # Wide, from the whole wordmark. Trimmed rather than padded: a logo is placed by whatever
        # is drawing it, so built-in whitespace only fights that.
        "logo.png": (FULL_WORDMARK, (512, 159), 0.0),
        "logo@2x.png": (FULL_WORDMARK, (1024, 317), 0.0),
    }

    for name, (box, size, margin) in outputs.items():
        image = render(rgb, alpha, box, size, margin=margin)
        image.save(BRAND_DIR / name, optimize=True)
        opaque = np.array(image)[..., 3] > 0
        print(f"{name:14} {image.size[0]}x{image.size[1]}  {opaque.mean():5.1%} covered")

    return 0


if __name__ == "__main__":
    sys.exit(main())
