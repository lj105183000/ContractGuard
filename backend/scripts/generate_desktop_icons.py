"""生成桌面端应用图标与托盘图标。

Pillow 是 pdfplumber 的现有依赖，不需要额外安装：
    uv run python scripts/generate_desktop_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "desktop" / "assets"

NAVY_TOP = (34, 54, 74, 255)
NAVY_BOTTOM = (23, 36, 49, 255)
NAVY = (27, 46, 64, 255)
GOLD = (212, 162, 76, 255)


def _shield(size: int, scale: int) -> Image.Image:
    side = size * scale
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))

    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, side - 1, side - 1], radius=int(side * 0.22), fill=255
    )

    background = Image.new("RGBA", (side, side))
    for y in range(side):
        ratio = y / max(side - 1, 1)
        color = tuple(
            round(NAVY_TOP[index] + (NAVY_BOTTOM[index] - NAVY_TOP[index]) * ratio)
            for index in range(4)
        )
        background.paste(color, (0, y, side, y + 1))
    image.paste(background, (0, 0), mask)

    draw = ImageDraw.Draw(image)
    outer = [
        (0.50, 0.17),
        (0.76, 0.27),
        (0.76, 0.50),
        (0.50, 0.81),
        (0.24, 0.50),
        (0.24, 0.27),
    ]
    draw.polygon([(x * side, y * side) for x, y in outer], fill=GOLD)

    inner = [
        (0.50, 0.255),
        (0.69, 0.335),
        (0.69, 0.49),
        (0.50, 0.72),
        (0.31, 0.49),
        (0.31, 0.335),
    ]
    draw.polygon([(x * side, y * side) for x, y in inner], fill=NAVY)

    draw.line(
        [(0.37 * side, 0.49 * side), (0.465 * side, 0.59 * side), (0.645 * side, 0.40 * side)],
        fill=GOLD,
        width=max(int(side * 0.055), 2),
        joint="curve",
    )
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _shield(512, 4).save(OUT_DIR / "icon.png")
    _shield(256, 4).save(
        OUT_DIR / "icon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    _shield(32, 4).save(OUT_DIR / "tray.png")
    _shield(64, 4).save(OUT_DIR / "tray@2x.png")
    print(f"图标已生成：{OUT_DIR}")


if __name__ == "__main__":
    main()
