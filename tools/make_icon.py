"""Build the macOS app icon and the window's ghost images from the artwork:

    python3 tools/make_icon.py assets/oomi-ghost-source.png
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"          # full-size artwork for the README
PKG = ROOT / "oomi" / "assets"  # what ships inside the package
WEB = ROOT / "oomi" / "gui" / "static"  # the window's avatar and favicon
CANVAS, BODY, SS = 1024, 824, 4  # Apple's grid: 824px rounded square on a 1024px canvas


def squircle_mask(size: int, n: float = 5.0) -> Image.Image:
    """Superellipse |x|^n + |y|^n <= 1 — close to Apple's continuous-corner icon shape."""
    big = size * SS
    c = (np.arange(big) + 0.5) / big * 2 - 1
    x, y = np.meshgrid(c, c)
    m = (np.abs(x) ** n + np.abs(y) ** n) <= 1.0
    return Image.fromarray((m * 255).astype("uint8")).resize((size, size), Image.LANCZOS)


def square(art: Image.Image) -> Image.Image:
    side = min(art.size)
    return art.crop(((art.width - side) // 2, (art.height - side) // 2, (art.width + side) // 2, (art.height + side) // 2))


def build_icon(art: Image.Image) -> Image.Image:
    """Square artwork on black -> the 1024px icon: a squircle with a soft shadow on a transparent canvas."""
    body = art.resize((BODY, BODY), Image.LANCZOS).convert("RGBA")
    body.putalpha(squircle_mask(BODY))

    off = (CANVAS - BODY) // 2
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    sh = Image.new("RGBA", (BODY, BODY), (0, 0, 0, 120))
    sh.putalpha(squircle_mask(BODY).point(lambda v: v * 120 // 255))
    shadow.paste(sh, (off, off + 12), sh)
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))

    icon = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    icon.alpha_composite(shadow)
    icon.alpha_composite(body, (off, off))
    return icon


if __name__ == "__main__":
    art = square(Image.open(Path(sys.argv[1])).convert("RGB"))
    icon = build_icon(art)
    OUT.mkdir(exist_ok=True)
    PKG.mkdir(exist_ok=True)
    icon.save(OUT / "oomi-icon.png")
    art.save(OUT / "oomi-ghost-source.png")
    icon.resize((256, 256), Image.LANCZOS).save(PKG / "oomi-icon-256.png")
    icon.save(PKG / "oomi.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
    print("wrote", *sorted(p.name for p in OUT.iterdir()), *sorted(p.name for p in PKG.iterdir()))
