#!/usr/bin/env python3
"""生成 Applyrx 的 .icns 图标"""

import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    print("需要 Pillow: pip install pillow")
    sys.exit(1)

OUT_ICNS = Path(__file__).resolve().parent.parent / "assets" / "Applyrx.icns"
OUT_ICNS.parent.mkdir(parents=True, exist_ok=True)


def draw_icon(size: int) -> Image.Image:
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ── 圆角背景 ────────────────────────────────────────
    radius = int(S * 0.22)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=(18, 10, 35, 255))

    # ── 柔和紫色光晕（左上角小圆，不盖住主体）─────────
    glow_img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_img)
    gr = int(S * 0.28)
    gd.ellipse([int(S * 0.08), int(S * 0.06),
                int(S * 0.08) + gr, int(S * 0.06) + gr],
               fill=(110, 60, 200, 90))
    glow_img = glow_img.filter(ImageFilter.GaussianBlur(S * 0.08))
    img = Image.alpha_composite(img, glow_img)
    d = ImageDraw.Draw(img)

    # ── 波形（5根竖条，水平居中，垂直居中）──────────────
    bar_heights = [0.25, 0.45, 0.62, 0.42, 0.22]
    n = len(bar_heights)
    bar_w = S * 0.09
    gap = S * 0.055
    total_w = n * bar_w + (n - 1) * gap
    x0 = (S - total_w) / 2
    cy = S * 0.50

    for i, h in enumerate(bar_heights):
        bh = S * h
        bx0 = x0 + i * (bar_w + gap)
        bx1 = bx0 + bar_w
        by0 = cy - bh / 2
        by1 = cy + bh / 2
        br = bar_w / 2
        # 中间亮，两边稍暗
        alpha = 230 if i == 2 else (210 if i in (1, 3) else 170)
        d.rounded_rectangle(
            [int(bx0), int(by0), int(bx1), int(by1)],
            radius=int(br),
            fill=(210, 185, 255, alpha)
        )

    # ── 底部三个小点 ─────────────────────────────────────
    dr = int(S * 0.03)
    dy = int(S * 0.80)
    dot_colors = [(160, 120, 255, 200), (138, 92, 246, 160), (120, 80, 220, 120)]
    spacing = int(S * 0.085)
    dx_start = int(S / 2 - spacing)
    for i in range(3):
        dx = dx_start + i * spacing
        d.ellipse([dx - dr, dy - dr, dx + dr, dy + dr], fill=dot_colors[i])

    # 最后裁成圆角（mask）
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=255)
    img.putalpha(mask)

    return img


def make_icns():
    sizes = [16, 32, 64, 128, 256, 512, 1024]

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Applyrx.iconset"
        iconset.mkdir()

        for s in sizes:
            img = draw_icon(s)
            img.save(iconset / f"icon_{s}x{s}.png")
            if s <= 512:
                img2 = draw_icon(s * 2)
                img2.save(iconset / f"icon_{s}x{s}@2x.png")

        result = subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT_ICNS)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print("iconutil 失败:", result.stderr)
            sys.exit(1)

    print(f"图标生成完成：{OUT_ICNS}")


if __name__ == "__main__":
    make_icns()
