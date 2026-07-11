#!/usr/bin/env python3
"""为网页生成 JPG 版本（最长边 1600px），大幅减小加载体积。"""

from pathlib import Path

from PIL import Image

MAX_EDGE = 1600
QUALITY = 82
ROOT = Path(__file__).resolve().parent / "images"


def convert(png: Path) -> Path:
    jpg = png.with_suffix(".jpg")
    with Image.open(png) as im:
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGB")
        w, h = im.size
        long_edge = max(w, h)
        if long_edge > MAX_EDGE:
            ratio = MAX_EDGE / long_edge
            im = im.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)
        im.save(jpg, format="JPEG", quality=QUALITY, optimize=True, progressive=True)
    return jpg


def main() -> None:
    pngs = sorted(ROOT.rglob("*.png"))
    if not pngs:
        print("未找到 PNG 文件")
        return
    before = after = 0
    for png in pngs:
        before += png.stat().st_size
        jpg = convert(png)
        after += jpg.stat().st_size
    print(f"已生成 {len(pngs)} 张 JPG")
    print(f"{before / 1024 / 1024:.1f} MB -> {after / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
