#!/usr/bin/env python3
"""Turn square icon artwork into a proper macOS app-icon PNG.

Center-crops the source to a square, fits it to the macOS 1024 icon grid
(824 body, ~0.2237 corner radius), cuts the rounded-rect with REAL alpha
(transparent corners — no baked checkerboard), and adds a soft drop shadow.

Usage: make_icon.py <source_art.png> <out_appicon_1024.png> [preview.png]
"""
import sys
from PIL import Image, ImageDraw, ImageFilter

src, out = sys.argv[1], sys.argv[2]
preview = sys.argv[3] if len(sys.argv) > 3 else None

CANVAS, BODY, RADIUS = 1024, 824, 185
MARGIN = (CANVAS - BODY) // 2

art = Image.open(src).convert("RGBA")
w, h = art.size
# Pad to a square by replicating edge rows/cols (seamless on a gradient bg) so the
# full emblem is preserved — never center-crop, which can clip the artwork.
if w > h:
    pad = w - h
    top, bot = pad // 2, pad - pad // 2
    sq = Image.new("RGBA", (w, w))
    sq.paste(art.crop((0, 0, w, 1)).resize((w, top)), (0, 0))
    sq.paste(art, (0, top))
    sq.paste(art.crop((0, h - 1, w, h)).resize((w, bot)), (0, top + h))
    art = sq
elif h > w:
    pad = h - w
    left, right = pad // 2, pad - pad // 2
    sq = Image.new("RGBA", (h, h))
    sq.paste(art.crop((0, 0, 1, h)).resize((left, h)), (0, 0))
    sq.paste(art, (left, 0))
    sq.paste(art.crop((w - 1, 0, w, h)).resize((right, h)), (left + w, 0))
    art = sq
art = art.resize((BODY, BODY), Image.LANCZOS)

mask = Image.new("L", (BODY, BODY), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, BODY - 1, BODY - 1], radius=RADIUS, fill=255)
art.putalpha(mask)

canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))

shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
ImageDraw.Draw(shadow).rounded_rectangle(
    [MARGIN, MARGIN + 16, MARGIN + BODY, MARGIN + BODY + 16], radius=RADIUS, fill=(0, 0, 0, 115)
)
shadow = shadow.filter(ImageFilter.GaussianBlur(20))
canvas = Image.alpha_composite(canvas, shadow)
canvas.alpha_composite(art, (MARGIN, MARGIN))
canvas.save(out)

print("size:", canvas.size,
      "| top-left alpha:", canvas.getpixel((4, 4))[3],
      "| center alpha:", canvas.getpixel((512, 512))[3])

if preview:
    bg = Image.new("RGBA", (CANVAS, CANVAS), (236, 236, 238, 255))
    Image.alpha_composite(bg, canvas).convert("RGB").save(preview)
