"""Generate ``brand/icon.png`` and ``brand/icon@2x.png``.

    python scripts/make_brand_icons.py

Takes no source artwork, and that is deliberate. The obvious icon for this integration would be
the manufacturer's logo, but this repository is public and unaffiliated: shipping AVPro Edge's
mark here would be redistributing someone else's trademark to make a third-party integration
look official. The icon is therefore an original drawing of what the device *does*.

The motif is a crosspoint matrix -- the grid of switch points that gives this class of hardware
its name -- with one point lit to show a route being made. Home Assistant renders this at around
48 pixels in the integrations list, so the drawing is deliberately coarse: three columns and
three rows of large dots, not a literal 4x4, because sixteen dots at that size is texture rather
than an image.

Pillow is a development dependency only. It is used here and nowhere in the integration, and
``manifest.json`` stays at ``requirements: []``.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
BRAND_DIR = REPO_ROOT / "custom_components" / "ha_avpro_edge" / "brand"

#: A deep neutral slate. Dark enough that the light grid reads on it, neutral enough that it does
#: not imitate any manufacturer's brand colour.
GROUND = (26, 32, 44, 255)
#: The inactive crosspoints.
GRID = (148, 163, 184, 255)
#: The routed point and its path. Warm, so it separates from the cool grid at a glance.
ACCENT = (251, 146, 60, 255)

#: Proportional corner radius: the usual app-icon treatment, so the tile does not sit as a
#: hard-edged square beside the rounded icons Home Assistant shows around it.
CORNER = 0.18
#: How much of the tile the drawing spans, leaving margin clear of the rounded corners.
SPAN = 0.62
#: Which crosspoint is lit, as (column, row), zero-based.
ROUTED = (1, 2)


def render(size: int) -> Image.Image:
    """Draw one square icon at ``size`` pixels."""
    # Supersample and downscale: at 256 px the dots and rules would otherwise alias badly.
    scale = 4
    px = size * scale

    tile = Image.new("RGBA", (px, px), GROUND)
    draw = ImageDraw.Draw(tile)

    span = px * SPAN
    origin = (px - span) / 2
    step = span / 2  # three positions -> two gaps
    dot = px * 0.062
    rule = max(2, int(px * 0.018))

    def centre(col: int, row: int) -> tuple[float, float]:
        return origin + col * step, origin + row * step

    routed_col, routed_row = ROUTED
    rx, ry = centre(routed_col, routed_row)

    # The path: in along the routed row, then out down the routed column. Drawn first so the
    # dots sit on top of it.
    draw.line([(origin, ry), (rx, ry)], fill=ACCENT, width=rule)
    draw.line([(rx, ry), (rx, origin + 2 * step)], fill=ACCENT, width=rule)

    for col in range(3):
        for row in range(3):
            cx, cy = centre(col, row)
            lit = (col, row) == ROUTED
            radius = dot * (1.35 if lit else 1.0)
            draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                fill=ACCENT if lit else GRID,
            )

    tile = tile.resize((size, size), Image.LANCZOS)

    rounded = Image.new("L", (size, size), 0)
    ImageDraw.Draw(rounded).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * CORNER), fill=255
    )
    tile.putalpha(rounded)
    return tile


def main() -> int:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        out = BRAND_DIR / name
        render(size).save(out, "PNG", optimize=True)
        print(f"wrote {out.relative_to(REPO_ROOT)}  {size}x{size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
