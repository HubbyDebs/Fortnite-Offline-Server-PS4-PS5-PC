"""Draws the Frutiger Aero app icon (icon.ico and icon.png) with Pillow:
a glossy aqua-to-emerald glass tile with a curved specular dome and a bold white/gold slanted F.
Usage: python make_icon.py
"""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024  # drawn large and scaled down for smooth edges
SKY_TOP = (6, 108, 218)
AQUA_MID = (26, 198, 255)
LEAF_BOT = (72, 232, 134)
WHITE = (255, 255, 255)
DEEP_BLUE = (4, 62, 128)


def tile():
    # Vertical cerulean -> aqua -> lush green-aqua Frutiger Aero gradient
    grad = Image.new("RGBA", (1, S))
    for y in range(S):
        t = y / (S - 1)
        if t < 0.62:
            k = t / 0.62
            c = tuple(int(SKY_TOP[i] * (1 - k) + AQUA_MID[i] * k) for i in range(3))
        else:
            k = (t - 0.62) / 0.38
            c = tuple(int(AQUA_MID[i] * (1 - k) + LEAF_BOT[i] * k) for i in range(3))
        grad.putpixel((0, y), c + (255,))
    img = grad.resize((S, S), Image.BILINEAR)

    # Bottom luminous caustic glow
    caustic = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(caustic).ellipse([120, 620, S - 120, S + 160], fill=(210, 255, 235, 135))
    caustic = caustic.filter(ImageFilter.GaussianBlur(70))
    img = Image.alpha_composite(img, caustic)

    # Rounded glass tile mask
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([32, 32, S - 33, S - 33], radius=235, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)

    # Classic Frutiger Aero curved top glass gloss
    shine = Image.new("L", (S, S), 0)
    ImageDraw.Draw(shine).ellipse([-180, -560, S + 180, 490], fill=155)
    fade = Image.linear_gradient("L").resize((S, S)).point(lambda v: max(0, 255 - int(v * 1.45)))
    gloss_mask = ImageChops.multiply(ImageChops.multiply(shine, fade), mask)
    out.paste(Image.new("RGBA", (S, S), WHITE + (255,)), (0, 0), gloss_mask)

    # Crisp glass inner rim
    rim = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle([40, 40, S - 41, S - 41], radius=228, outline=(255, 255, 255, 215), width=18)
    out = Image.alpha_composite(out, rim)
    return out


def letter_f(img, slant=0.15):
    """Bold slanted F in crisp glossy white with a soft aqua-navy drop shadow."""
    try:
        font = ImageFont.truetype("impact.ttf", 780)
    except OSError:
        font = ImageFont.truetype("bahnschrift.ttf", 740)
    glyph = Image.new("L", (S, S), 0)
    ImageDraw.Draw(glyph).text((S / 2, S / 2 + 24), "F", font=font, fill=255, anchor="mm")
    glyph = glyph.transform((S, S), Image.AFFINE, (1, slant, -slant * S / 2, 0, 1, 0), resample=Image.BICUBIC)

    # Soft blurred shadow underneath
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", (S, S), DEEP_BLUE + (155,)), (0, 26), glyph)
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    img.alpha_composite(shadow)

    # Crisp pearl-white glyph with subtle sky tint at bottom
    grad = Image.new("RGBA", (1, S))
    for y in range(S):
        t = y / (S - 1)
        r = int(255 - 24 * t)
        g = int(255 - 8 * t)
        b = 255
        grad.putpixel((0, y), (r, g, b, 255))
    fill = grad.resize((S, S), Image.BILINEAR)
    img.paste(fill, (0, 0), glyph)


def main():
    img = tile()
    letter_f(img)
    img = img.resize((256, 256), Image.LANCZOS)
    img.save(os.path.join(HERE, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(os.path.join(HERE, "icon.png"))
    print("Frutiger Aero icon.ico and icon.png written")


if __name__ == "__main__":
    main()
