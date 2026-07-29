from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

CANVAS_SIZE = 256
BACKGROUND = "#101827"
ACCENT = "#25F4EE"
FOREGROUND = "#FFFFFF"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw_icon() -> Image.Image:
    image = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((20, 20, 236, 236), radius=48, fill=BACKGROUND)
    draw.polygon(
        (
            (116, 62),
            (140, 62),
            (140, 124),
            (168, 124),
            (128, 166),
            (88, 124),
            (116, 124),
        ),
        fill=ACCENT,
    )
    draw.rectangle((74, 176, 182, 200), fill=FOREGROUND)
    return image


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    ico_path = project_root / "assets" / "app-icon.ico"
    png_path = (
        project_root
        / "src"
        / "douyin_downloader"
        / "web"
        / "static"
        / "app-icon.png"
    )
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.parent.mkdir(parents=True, exist_ok=True)

    image = draw_icon()
    image.save(png_path, format="PNG", optimize=False, compress_level=9)
    image.save(
        ico_path,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
        bitmap_format="png",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
