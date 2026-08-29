"""Generate the eight brand images from the manufacturer's two wordmarks.

    python scripts/make_brand_icons.py

**This used to draw an original icon instead.** The reasoning was that shipping AVPro Edge's mark
from an unaffiliated public repository would be redistributing someone else's trademark to make a
third-party integration look official. That was over-cautious: Home Assistant's own brands
repository is built almost entirely out of manufacturer logos, because an integration icon is
there to identify *which device* it controls. The mark remains the property of AVPro Edge, and
this project is not affiliated with them -- recorded in the README rather than only here.

## Light and dark, rather than one image that survives both

Home Assistant serves eight filenames per integration -- ``icon``, ``logo``, their ``@2x``
variants, and a ``dark_`` prefixed version of each -- and picks the ``dark_`` one on a dark theme.
The full list and its fallback chains are in ``homeassistant/components/brands/const.py``.

That makes a compromise unnecessary. AVPro publish the wordmark twice, black-on-white and
white-on-black, so each theme gets the artwork drawn for it: dark glyphs on transparency for a
light theme, light glyphs on transparency for a dark one. Nothing is composited onto a tile of its
own, and nothing has to read acceptably against a background it was not designed for.

The fallbacks mean the ``dark_`` files are purely additive -- ``dark_icon.png`` falls back to
``icon.png`` -- so an installation that somehow misses them still shows something.

## Keying: distance from the background, and knockouts stay knockouts

Every pixel far enough from the artwork's own corner colour is ink; everything else becomes
transparent. One rule serves both sources, because the thing being removed is "whatever the page
was", not "white" or "black" specifically.

That makes the letters of *edge* transparent, and it should. They are **knockouts** -- not
painted, but holes in the green parallelogram showing the page behind, which is why they read
white on the light artwork and black on the dark one. Leaving them as holes reproduces exactly
that: they take the colour of whatever Home Assistant puts behind them. The same goes for the
counters of the letters, the triangle inside the A among them.

This replaced a border flood fill, added on the theory that it would keep the *edge* letters
opaque. It did not: the g's descender crosses the parallelogram's edge, so those letters connect
to the outside and get filled regardless. What it did keep were the counters -- as opaque white or
black blobs that would sit slightly off against any card that is not exactly #ffffff or #000000.
It was complexity buying a marginally worse result on a premise that was not true.

## Resizing: premultiplied, not bled

Transparent pixels still carry a colour, and Lanczos averages it into every edge -- so a reduction
against a white background rings each glyph in pale grey, and against a black one in grey. The
earlier fix was to bleed ink colour outwards a few pixels before resizing, which worked and was a
hack. The correct operation is to premultiply by alpha, resize, and divide back out: coverage and
colour are then interpolated as the single quantity they physically are.

## Why the icon is not the whole wordmark

The mark is roughly 3.24:1. Home Assistant renders an integration icon at about 48 pixels square,
where a wordmark scaled to fit is some 15 pixels tall -- a grey smudge. The icon is therefore the
**AV ligature**, which is 1.37:1 and still a shape at that size; the logo is the whole wordmark.

The split is **derived, not hardcoded**, because the two sources are different resolutions and a
crop measured against one produces nonsense against the other. The seam is the point where the V's
tapering right arm meets the P's stem -- a near-empty column against a nearly full-height one --
and the result is checked against the ligature's known aspect ratio before anything is written,
because three earlier rules based on where the mark touches its bounding box each looked plausible
and were each wrong.

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

#: The two published wordmarks, and the filename prefix each one supplies.
SOURCES: dict[str, Path] = {
    "": REPO_ROOT / "assets" / "avpro-edge-light.png",
    "dark_": REPO_ROOT / "assets" / "avpro-edge-dark.jpg",
}

#: How far a pixel must sit from the artwork's own background before it counts as ink, summed
#: across the channels. Comfortably above JPEG noise and anti-aliasing, and far below the distance
#: from either background to any real colour in the mark: white to green is 382, black to green is
#: 383, and the two backgrounds are 765 apart.
INK_DISTANCE = 90

#: Thresholds for finding the AV/P seam, as fractions of the tallest ink column in the mark.
#:
#: The seam is a **taper meeting a stem**: the V's right arm narrows to a point, and the P's stem
#: begins at once at nearly full height. In the light artwork that is a column of one ink pixel
#: against a column of sixty-eight; in the dark one, an empty column against a hundred and
#: thirteen. Nothing else in either mark looks like that.
SEAM_EMPTY = 0.03
SEAM_STEM = 0.30

#: How far into the mark to start looking, as a fraction of its width. The A's own left edge also
#: tapers in from nothing, and without this the search would match it immediately.
SEAM_SKIP = 0.20

#: The AV ligature's aspect ratio, and how far from it :func:`ligature_seam` may land before the
#: script refuses to write anything.
#:
#: A seam derived from image statistics is exactly the kind of thing that goes wrong silently.
#: Three earlier rules -- "touches either extreme", "reaches the baseline", and the same with a
#: tighter tolerance -- each produced a plausible-looking crop and a nonsense icon, because the
#: shape is not what I assumed: only the A's apex reaches the top and only the V's vertex reaches
#: the bottom, while the V's right arm, which sets the ligature's true width, touches neither. The
#: ligature is about 1.37:1 in both artworks by design, so a derivation landing outside this band
#: has found something else.
LIGATURE_ASPECT = (1.20, 1.55)

#: How much of the icon's width the AV ligature spans. The brands specification asks for images
#: "trimmed, so [they contain] the minimum amount of empty space", which for a transparent icon
#: means reaching the canvas edge -- so this is 1.0 horizontally, and the aspect ratio alone
#: decides what is left above and below.
ICON_FILL = 1.0


def keyed(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Load artwork and return RGB plus an alpha mask with the background removed.

    The background colour is read from a corner rather than assumed, so the same rule handles the
    black-backed and white-backed wordmarks without being told which is which.
    """
    rgb = np.array(Image.open(path).convert("RGB")).astype(np.int16)
    background = rgb[0, 0]
    distance = np.abs(rgb - background).sum(axis=2)
    alpha = np.where(distance > INK_DISTANCE, 255, 0).astype(np.uint8)
    return rgb.astype(np.uint8), alpha


def bounds(alpha: np.ndarray) -> tuple[int, int, int, int]:
    """Inclusive bounding box of everything opaque."""
    ys, xs = np.where(alpha > 0)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def ligature_seam(alpha: np.ndarray, box: tuple[int, int, int, int]) -> int:
    """Last column of the AV ligature, found rather than assumed.

    Located by the discontinuity where the V's tapering right arm meets the P's stem: a near-empty
    column immediately followed by a tall one. See :data:`SEAM_EMPTY` for why that, and
    :data:`LIGATURE_ASPECT` for what happens when it goes wrong.
    """
    left, top, right, bottom = box
    counts = (alpha[top : bottom + 1, left : right + 1] > 0).sum(axis=0)
    peak = int(counts.max())

    for i in range(round(len(counts) * SEAM_SKIP), len(counts) - 1):
        if counts[i] <= SEAM_EMPTY * peak and counts[i + 1] >= SEAM_STEM * peak:
            return left + i
    return right


def resized(
    rgb: np.ndarray, alpha: np.ndarray, box: tuple[int, int, int, int], size: tuple[int, int]
) -> Image.Image:
    """Crop and scale, interpolating premultiplied colour so edges do not pick up a fringe."""
    left, top, right, bottom = box
    crop_rgb = rgb[top : bottom + 1, left : right + 1].astype(np.float64)
    crop_a = alpha[top : bottom + 1, left : right + 1].astype(np.float64) / 255.0

    premultiplied = Image.fromarray((crop_rgb * crop_a[..., None]).round().astype(np.uint8), "RGB")
    coverage = Image.fromarray(alpha[top : bottom + 1, left : right + 1], "L")

    small_pm = np.array(premultiplied.resize(size, Image.LANCZOS)).astype(np.float64)
    small_a = np.array(coverage.resize(size, Image.LANCZOS)).astype(np.float64)

    # Lanczos overshoots; clamp before dividing so a ringing pixel cannot explode the colour.
    small_a = np.clip(small_a, 0, 255)
    safe = np.maximum(small_a, 1e-6) / 255.0
    straight = np.clip(small_pm / safe[..., None], 0, 255)

    out = np.dstack([straight.round().astype(np.uint8), small_a.round().astype(np.uint8)])
    return Image.fromarray(out, "RGBA")


def centred(art: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Place art on a transparent canvas of the given size."""
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(art, ((size[0] - art.width) // 2, (size[1] - art.height) // 2), art)
    return canvas


def build(prefix: str, path: Path) -> dict[str, Image.Image]:
    rgb, alpha = keyed(path)
    box = bounds(alpha)
    left, top, right, bottom = box
    seam = ligature_seam(alpha, box)
    ligature = (left, top, seam, bottom)

    lig_w, lig_h = seam - left + 1, bottom - top + 1
    mark_w, mark_h = right - left + 1, bottom - top + 1
    aspect = lig_w / lig_h
    low, high = LIGATURE_ASPECT
    if not low <= aspect <= high:
        raise SystemExit(
            f"{path.name}: the derived ligature is {lig_w}x{lig_h} ({aspect:.2f}:1), outside the "
            f"expected {low}-{high}. The seam detection has found something that is not the AV "
            f"ligature; refusing to write a nonsense icon."
        )
    print(f"  {path.name}: mark {mark_w}x{mark_h}, ligature {lig_w}x{lig_h} ({aspect:.2f}:1)")

    out: dict[str, Image.Image] = {}
    for side in (256, 512):
        suffix = "" if side == 256 else "@2x"
        width = round(side * ICON_FILL)
        art = resized(rgb, alpha, ligature, (width, max(1, round(width * lig_h / lig_w))))
        out[f"{prefix}icon{suffix}.png"] = centred(art, (side, side))

        # Logos are constrained on the *shortest* side: 128-256 for the base and 256-512 for hDPI,
        # maximum preferred. The long side follows the brand's own aspect ratio.
        height = 256 if side == 256 else 512
        out[f"{prefix}logo{suffix}.png"] = resized(
            rgb, alpha, box, (round(height * mark_w / mark_h), height)
        )
    return out


def main() -> int:
    missing = [str(p) for p in SOURCES.values() if not p.exists()]
    if missing:
        print("source artwork not found:\n  " + "\n  ".join(missing))
        return 2

    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    for prefix, path in SOURCES.items():
        for name, image in build(prefix, path).items():
            image.save(BRAND_DIR / name, optimize=True)
            print(f"    {name:20} {image.size[0]}x{image.size[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
