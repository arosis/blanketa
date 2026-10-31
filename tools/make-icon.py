"""Draws the app icon (assets/blanketa.png, .ico for Windows, .icns for macOS): a form with a signature."""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 1024


def rounded_blue_background():
    gradient = Image.new("RGBA", (S, S))
    top, bottom = (61, 139, 253), (10, 84, 190)
    for y in range(S):
        share = y / S
        color = tuple(round(start + (end - start) * share) for start, end in zip(top, bottom, strict=True))
        ImageDraw.Draw(gradient).line([(0, y), (S, y)], fill=color)
    # macOS icon grid: 824 px body on 1024.
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([100, 100, 924, 924], radius=185, fill=255)
    image = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    image.paste(gradient, (0, 0), mask)
    return image


def draw_paper_with_folded_corner(canvas):
    canvas.polygon([(300, 230), (630, 230), (730, 330), (730, 800), (300, 800)], fill=(255, 255, 255, 255))
    canvas.polygon([(630, 230), (630, 330), (730, 330)], fill=(205, 222, 248, 255))


def draw_form_fields(canvas):
    for y, width in ((380, 300), (450, 360), (520, 250)):
        canvas.rounded_rectangle([355, y, 355 + width, y + 30], radius=8, fill=(214, 228, 252, 255))


def draw_signature(canvas):
    canvas.line([(355, 720), (675, 720)], fill=(150, 170, 200, 255), width=6)
    stroke = [(370, 700), (400, 640), (425, 690), (450, 620), (470, 700), (500, 660), (540, 690), (580, 650),
              (610, 680), (660, 640)]
    canvas.line(stroke, fill=(10, 84, 190, 255), width=16, joint="curve")


def draw():
    image = rounded_blue_background()
    canvas = ImageDraw.Draw(image)
    draw_paper_with_folded_corner(canvas)
    draw_form_fields(canvas)
    draw_signature(canvas)
    return image


def main():
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    icon = draw()
    icon.save(out / "blanketa.png")
    icon.save(out / "blanketa.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "blanketa.iconset"
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                icon.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
                icon.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out / "blanketa.icns")], check=True)


if __name__ == "__main__":
    main()
