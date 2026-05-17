"""Generate simple .ico files for the desktop launcher."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
SIZE = 64


def make_icon(color: str, dot: str | None = None) -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (24, 24, 24, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, SIZE - 6, SIZE - 6), radius=14, fill=color)
    draw.text((SIZE // 2 - 9, SIZE // 2 - 12), "S", fill="white")
    if dot:
        draw.ellipse((42, 8, 58, 24), fill=dot)
    return image


def save_icon(name: str, image: Image.Image) -> None:
    image.save(ROOT / name, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])


def main() -> None:
    save_icon("tray_icon.ico", make_icon("#248a3d"))
    save_icon("tray_icon_update.ico", make_icon("#248a3d", dot="#0a84ff"))
    save_icon("tray_icon_error.ico", make_icon("#b42318"))


if __name__ == "__main__":
    main()
