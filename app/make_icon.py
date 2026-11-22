"""Draws the app icon (icon.ico and icon.png) with Pillow: a bold slanted yellow F on a dark purple tile.
Usage: python make_icon.py
"""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024  # drawn large and scaled down for smooth edges
DARK, PURPLE = (24, 28, 48), (90, 60, 200)
YELLOW, BROWN, WHITE = (255, 214, 64), (150, 80, 10), (255, 255, 255)


def tile():
    v = Image.linear_gradient("L").resize((S, S))
    diagonal = ImageChops.add(v, v.transpose(Image.Transpose.ROTATE_270), scale=2.0)
    img = Image.composite(Image.new("RGBA", (S, S), PURPLE + (255,)), Image.new("RGBA", (S, S), DARK + (255,)), diagonal)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([32, 32, S - 33, S - 33], radius=230, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    shine = Image.new("L", (S, S), 0)
    ImageDraw.Draw(shine).ellipse([-200, -700, S + 200, 420], fill=38)
    out.paste(Image.new("RGBA", (S, S), WHITE + (255,)), (0, 0), ImageChops.multiply(shine, mask))
    return out


def letter_f(img, slant=0.16):
    """Thick condensed F (Impact), slanted, with a white outline and a 3D drop below."""
    try:
        font = ImageFont.truetype("impact.ttf", 820)
    except OSError:
        font = ImageFont.truetype("bahnschrift.ttf", 760)
    glyph = Image.new("L", (S, S), 0)
    ImageDraw.Draw(glyph).text((S / 2, S / 2 + 20), "F", font=font, fill=255, anchor="mm")
    glyph = glyph.transform((S, S), Image.AFFINE, (1, slant, -slant * S / 2, 0, 1, 0), resample=Image.BICUBIC)
    img.paste(Image.new("RGBA", (S, S), WHITE + (255,)), (0, 0), glyph.filter(ImageFilter.MaxFilter(41)))
    for i in range(34, 0, -2):
        img.paste(Image.new("RGBA", (S, S), BROWN + (255,)), (0, i), glyph)
    img.paste(Image.new("RGBA", (S, S), YELLOW + (255,)), (0, 0), glyph)
    shine = Image.linear_gradient("L").resize((S, S)).point(lambda v: max(0, 120 - v))
    img.paste(Image.new("RGBA", (S, S), WHITE + (255,)), (0, 0), ImageChops.multiply(glyph, shine))


def main():
    img = tile()
    letter_f(img)
    img = img.resize((256, 256), Image.LANCZOS)
    img.save(os.path.join(HERE, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(os.path.join(HERE, "icon.png"))
    print("icon.ico and icon.png written")


if __name__ == "__main__":
    main()
