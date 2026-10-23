"""Build the ghost in each color the app offers: python3 tools/make_ghosts.py [colors.png] [source.png]

colors.png is a 3x2 sheet of ghosts on black (white, blue, pink / purple, green, peach). White comes from the
app icon's own artwork; the others are cut from the sheet and framed to match it. Each color is written twice:
oomi/assets/ghosts/<color>.png, the ghost alone on a transparent background (what the window shows), and
oomi/assets/dock/<color>.png, the app icon in that color (what the Dock shows).
"""
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from make_icon import OUT, PKG, build_icon, square

SHEET = [["white", "blue", "pink"], ["purple", "green", "peach"]]
SIZE = 512  # big enough for the Dock on a Retina screen
PAD = 0.04  # room around the cut-out ghost, as a fraction of its height


def ghost_box(a: np.ndarray) -> tuple:
    ys, xs = np.where(a.max(axis=2) > 40)
    return xs.min(), xs.max() + 1, ys.min(), ys.max() + 1


def reframe(cell: np.ndarray, ref: tuple) -> Image.Image:
    """Put the ghost in `cell` on a black square, at the same height fraction and centre as the reference art."""
    height_frac, cx_frac, cy_frac = ref
    x0, x1, y0, y1 = ghost_box(cell)
    side = round((y1 - y0) / height_frac)
    left, top = round((x0 + x1) / 2 - cx_frac * side), round((y0 + y1) / 2 - cy_frac * side)
    canvas = Image.new("RGB", (side, side), (0, 0, 0))
    canvas.paste(Image.fromarray(cell), (-left, -top))
    return canvas


def cut_out(art: Image.Image) -> Image.Image:
    """The ghost alone: the black backdrop becomes transparent, its soft edge a ramp of alpha."""
    rgb = np.asarray(art).astype(float)
    lum = rgb.max(axis=2)
    # The backdrop is the dark area connected to the border (the eyes are dark too, but enclosed by the ghost)
    mask = Image.fromarray(((lum < 130) * 255).astype("uint8")).copy()  # a copy: fromarray's image is read-only
    ImageDraw.floodfill(mask, (0, 0), 128)
    bg = np.asarray(mask) == 128
    cov = np.where(bg, np.clip((lum - 14) / 116, 0, 1), 1.0)
    cov = np.asarray(Image.fromarray((cov * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(0.6))) / 255
    cov = np.maximum(cov, ~bg)
    color = (rgb / np.maximum(cov, 1e-3)[..., None]).clip(0, 255)  # the edge was the color faded into black
    out = Image.fromarray(np.dstack([color, cov * 255]).astype("uint8"), "RGBA")
    x0, x1, y0, y1 = ghost_box(np.asarray(art))
    side = (y1 - y0) * (1 + 2 * PAD)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return out.crop(tuple(round(v) for v in (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)))


if __name__ == "__main__":
    sheet = Image.open(sys.argv[1] if len(sys.argv) > 1 else OUT / "oomi-ghost-colors.png").convert("RGB")
    source = square(Image.open(sys.argv[2] if len(sys.argv) > 2 else OUT / "oomi-ghost-source.png").convert("RGB"))
    x0, x1, y0, y1 = ghost_box(np.asarray(source))
    ref = ((y1 - y0) / source.height, (x0 + x1) / 2 / source.width, (y0 + y1) / 2 / source.height)

    ghosts, dock = PKG / "ghosts", PKG / "dock"
    ghosts.mkdir(parents=True, exist_ok=True)
    dock.mkdir(parents=True, exist_ok=True)
    a = np.asarray(sheet)
    cw, ch = sheet.width // len(SHEET[0]), sheet.height // len(SHEET)
    for r, row in enumerate(SHEET):
        for c, name in enumerate(row):
            art = source if name == "white" else reframe(a[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw], ref)
            cut_out(art).resize((SIZE, SIZE), Image.LANCZOS).save(ghosts / f"{name}.png", optimize=True)
            build_icon(art).resize((SIZE, SIZE), Image.LANCZOS).save(dock / f"{name}.png", optimize=True)
    print("wrote", *sorted(p.name for p in ghosts.iterdir()), "and the same in", dock)
