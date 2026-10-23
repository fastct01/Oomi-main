"""Build the browser favicon — the blue ghost on white, whatever oomi's color: python3 tools/make_favicon.py"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

PKG = Path(__file__).resolve().parent.parent / "oomi" / "assets"
SIZE, PAD = 256, 0.06

a = np.asarray(Image.open(PKG / "ghosts" / "blue.png").convert("RGBA")).astype(float)
rgb, lum = a[..., :3], a[..., :3].max(-1)

# The black backdrop: dark pixels connected to the outside (the eyes are dark too, but enclosed)
mask = Image.fromarray((((lum < 130) | (a[..., 3] < 255)) * 255).astype("uint8")).copy()
ImageDraw.floodfill(mask, (0, 0), 128)
bg = np.asarray(mask) == 128

# How much ghost each pixel holds: all of it inside, a ramp across the soft edge, none on the backdrop
cov = np.where(bg, np.clip((lum - 14) / 116, 0, 1), 1.0)
cov = np.asarray(Image.fromarray((cov * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(0.6))) / 255
cov = np.maximum(cov, ~bg)[..., None]
out = (rgb / np.maximum(cov, 1e-3)).clip(0, 255) * cov + 255 * (1 - cov)

ys, xs = np.nonzero(cov[..., 0] > 0.5)
side = max(np.ptp(xs), np.ptp(ys)) * (1 + 2 * PAD)
cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
box = (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)
Image.fromarray(out.astype("uint8"), "RGB").crop(tuple(round(v) for v in box)).resize((SIZE, SIZE), Image.LANCZOS).save(PKG / "favicon.png")
print("wrote", PKG / "favicon.png")
