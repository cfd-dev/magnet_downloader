"""生成应用图标：马蹄磁铁 + 下载箭头，输出 app_icon.png / app_icon.ico。"""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
S = 1024  # 超采样分辨率，最后缩到 256


def px(v):
    return int(S * v)


def build():
    # 圆角渐变底板
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=px(0.22), fill=255)

    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        gd.line(
            (0, y, S, y),
            fill=(int(24 + 22 * t), int(38 + 58 * t), int(96 + 118 * t), 255),
        )

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    # 柔和光晕
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (px(0.16), px(0.10), px(0.84), px(0.76)), fill=(90, 190, 255, 70)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(px(0.06)))
    img = Image.alpha_composite(img, glow)
    img.putalpha(ImageChops.multiply(img.getchannel("A"), mask))

    d = ImageDraw.Draw(img)
    white = (248, 252, 255, 255)

    # 马蹄磁铁：上半圆弧 + 两条腿
    width = px(0.093)
    d.arc((px(0.22), px(0.17), px(0.78), px(0.67)), 180, 360, fill=white, width=width)
    for cx in (0.22, 0.78):
        d.rounded_rectangle(
            (px(cx) - width // 2, px(0.415), px(cx) + width // 2, px(0.70)),
            radius=width // 2,
            fill=white,
        )

    # 磁极配色：左青右珊瑚，让磁铁一眼可辨
    tip_h = px(0.075)
    d.rounded_rectangle(
        (px(0.22) - width // 2, px(0.635), px(0.22) + width // 2, px(0.635) + tip_h),
        radius=width // 2,
        fill=(34, 211, 238, 255),
    )
    d.rounded_rectangle(
        (px(0.78) - width // 2, px(0.635), px(0.78) + width // 2, px(0.635) + tip_h),
        radius=width // 2,
        fill=(251, 113, 133, 255),
    )

    # 下载箭头
    d.rounded_rectangle(
        (px(0.5) - px(0.028), px(0.33), px(0.5) + px(0.028), px(0.68)),
        radius=px(0.028),
        fill=(255, 209, 102, 255),
    )
    d.polygon(
        [
            (px(0.5) - px(0.105), px(0.645)),
            (px(0.5) + px(0.105), px(0.645)),
            (px(0.5), px(0.785)),
        ],
        fill=(255, 209, 102, 255),
    )

    # 接收托盘
    d.line(
        [
            (px(0.34), px(0.835)),
            (px(0.34), px(0.895)),
            (px(0.66), px(0.895)),
            (px(0.66), px(0.835)),
        ],
        fill=(226, 242, 255, 245),
        width=px(0.034),
        joint="curve",
    )

    # 小星光点缀
    for cx, cy, r, col in (
        (0.155, 0.28, 0.022, (125, 230, 255, 230)),
        (0.86, 0.33, 0.016, (255, 220, 150, 220)),
    ):
        d.polygon(
            [
                (px(cx), px(cy - r)),
                (px(cx + r * 0.28), px(cy - r * 0.28)),
                (px(cx + r), px(cy)),
                (px(cx + r * 0.28), px(cy + r * 0.28)),
                (px(cx), px(cy + r)),
                (px(cx - r * 0.28), px(cy + r * 0.28)),
                (px(cx - r), px(cy)),
                (px(cx - r * 0.28), px(cy - r * 0.28)),
            ],
            fill=col,
        )

    return img.resize((256, 256), Image.LANCZOS)


if __name__ == "__main__":
    icon = build()
    icon.save(os.path.join(OUT_DIR, "app_icon.png"))
    icon.save(
        os.path.join(OUT_DIR, "app_icon.ico"),
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("ICON_OK")
