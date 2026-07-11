#!/usr/bin/env python3
"""
批量压缩项目内 PNG 图片。

规则：
  - 小于 3MB 的图片：跳过，不处理
  - 大于等于 3MB 的图片：在保证清晰度的前提下，尽量压缩到约 5MB
  - 3MB–5.5MB 已接近目标的图片：仅做高质量轻量压缩

依赖：
  pip install -r requirements.txt
  brew install pngquant    # 推荐

用法：
  python compress_pngs.py --dry-run    # 预览
  python compress_pngs.py              # 正式压缩（不备份）
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore

# 体积阈值
MIN_SKIP_BYTES = 3 * 1024 * 1024          # 3MB 以下跳过
TARGET_BYTES = 5 * 1024 * 1024            # 目标约 5MB
TARGET_LOW = 4 * 1024 * 1024              # 可接受下限
TARGET_HIGH = 6.5 * 1024 * 1024           # 可接受上限
LIGHT_ONLY_MAX = 5.5 * 1024 * 1024        # 已接近目标，仅轻量压缩

# pngquant 质量阶梯（从高到低；后部为难以压缩图片的兜底）
QUALITY_LADDER = [
    "92-100",
    "88-98",
    "85-95",
    "82-92",
    "80-90",
    "78-88",
    "75-85",
    "70-82",
    "65-80",
    "60-75",
]

SKIP_DIRS = {".git", ".png_backup", "__pycache__", "node_modules", ".venv", "venv"}
MAX_RESIZE_ROUNDS = 8
MIN_LONG_EDGE = 1600


def human_size(n: float) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def find_pngquant() -> str | None:
    return shutil.which("pngquant")


def iter_pngs(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.png"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return sorted(files)


def run_pngquant(
    src: Path,
    dst: Path,
    pngquant_bin: str,
    quality: str,
) -> int | None:
    """返回压缩后字节数；失败或体积变大时返回 None。"""
    cmd = [
        pngquant_bin,
        f"--quality={quality}",
        "--force",
        "--skip-if-larger",
        "--output",
        str(dst),
        str(src),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode in (99, 98):
        return None
    if result.returncode != 0:
        return None
    if not dst.exists():
        return None
    return dst.stat().st_size


def save_resized(src: Path, dst: Path, scale: float) -> None:
    if Image is None:
        raise RuntimeError("需要 Pillow 来调整大图尺寸：pip install -r requirements.txt")
    with Image.open(src) as img:
        w, h = img.size
        long_edge = max(w, h)
        new_long = max(int(long_edge * scale), MIN_LONG_EDGE)
        if new_long < long_edge:
            ratio = new_long / long_edge
            new_w = max(1, int(w * ratio))
            new_h = max(1, int(h * ratio))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        img.save(dst, format="PNG", optimize=True, compress_level=9)


def in_target_range(size: int) -> bool:
    return TARGET_LOW <= size <= TARGET_HIGH


def try_qualities(
    src: Path,
    pngquant_bin: str,
    qualities: list[str],
) -> tuple[Path | None, int, str]:
    """依次尝试质量阶梯，返回最佳结果（优先接近目标体积）。"""
    best_tmp: Path | None = None
    best_size = 0
    best_quality = ""
    best_score = float("inf")

    for quality in qualities:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            size = run_pngquant(src, tmp_path, pngquant_bin, quality)
            if size is None:
                tmp_path.unlink(missing_ok=True)
                continue

            if in_target_range(size):
                if best_tmp and best_tmp.exists():
                    best_tmp.unlink()
                return tmp_path, size, quality

            score = abs(size - TARGET_BYTES)
            if score < best_score:
                if best_tmp and best_tmp.exists():
                    best_tmp.unlink()
                best_tmp = tmp_path
                best_size = size
                best_quality = quality
                best_score = score
            else:
                tmp_path.unlink(missing_ok=True)
        except Exception:
            tmp_path.unlink(missing_ok=True)

    if best_tmp is None:
        return None, 0, ""
    return best_tmp, best_size, best_quality


def compress_adaptive(
    src: Path,
    pngquant_bin: str,
    *,
    light_only: bool,
) -> tuple[Path | None, int, str]:
    original_size = src.stat().st_size
    qualities = ["88-98", "85-95"] if light_only else QUALITY_LADDER

    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)
        current = work / "current.png"
        shutil.copy2(src, current)
        scale = 1.0

        best_tmp: Path | None = None
        best_size = 0
        best_note = ""

        for round_idx in range(MAX_RESIZE_ROUNDS):
            result_path, size, quality = try_qualities(current, pngquant_bin, qualities)
            if result_path is None:
                if round_idx == MAX_RESIZE_ROUNDS - 1:
                    break
                ratio = 0.85
                scale *= ratio
                resized = work / f"resized_{round_idx}.png"
                save_resized(src, resized, scale)
                current = resized
                qualities = QUALITY_LADDER
                continue

            note = f"q={quality}"
            if scale < 1.0:
                note += f", scale={scale:.0%}"

            if light_only:
                if best_tmp and best_tmp.exists():
                    best_tmp.unlink()
                return result_path, size, note

            if in_target_range(size):
                if best_tmp and best_tmp.exists():
                    best_tmp.unlink()
                return result_path, size, note

            # 记录最接近目标的候选
            if not best_tmp or abs(size - TARGET_BYTES) < abs(best_size - TARGET_BYTES):
                if best_tmp and best_tmp.exists():
                    best_tmp.unlink()
                final_tmp = Path(tempfile.mkstemp(suffix=".png")[1])
                shutil.move(str(result_path), str(final_tmp))
                best_tmp = final_tmp
                best_size = size
                best_note = note
            elif result_path.exists():
                result_path.unlink()

            if size <= TARGET_HIGH:
                break

            # 仍偏大：按比例缩小后重试
            ratio = math.sqrt(TARGET_BYTES / size) * 0.94
            scale *= ratio
            resized = work / f"resized_{round_idx}.png"
            save_resized(src, resized, scale)
            current = resized
            qualities = QUALITY_LADDER

        if best_tmp is None:
            return None, 0, ""
        return best_tmp, best_size, best_note


def process_file(
    path: Path,
    pngquant_bin: str,
    *,
    dry_run: bool,
    min_ratio: float,
) -> tuple[int, int, str]:
    original_size = path.stat().st_size

    if original_size < MIN_SKIP_BYTES:
        return original_size, original_size, "skip-small"

    light_only = original_size <= LIGHT_ONLY_MAX
    result_path, new_size, note = compress_adaptive(
        path, pngquant_bin, light_only=light_only
    )

    if result_path is None or new_size <= 0:
        return original_size, original_size, "skip"

    saved_ratio = 1 - new_size / original_size
    should_replace = new_size < original_size and saved_ratio >= min_ratio

    if light_only and new_size < MIN_SKIP_BYTES:
        should_replace = False

    if not should_replace:
        result_path.unlink(missing_ok=True)
        return original_size, original_size, "skip"

    if dry_run:
        result_path.unlink(missing_ok=True)
        return original_size, new_size, note

    shutil.move(str(result_path), str(path))
    return original_size, new_size, note


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="压缩项目内 PNG（3MB 以下跳过，目标约 5MB）")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="项目根目录",
    )
    parser.add_argument("--dry-run", action="store_true", help="预览，不写文件")
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=0.03,
        help="至少节省多少比例才替换（默认 3%%）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()

    if not root.is_dir():
        print(f"错误：目录不存在 {root}", file=sys.stderr)
        return 1

    pngquant_bin = find_pngquant()
    if not pngquant_bin:
        print("错误：未找到 pngquant，请安装：brew install pngquant", file=sys.stderr)
        return 1
    if Image is None:
        print("错误：未安装 Pillow，请运行：pip install -r requirements.txt", file=sys.stderr)
        return 1

    pngs = iter_pngs(root)
    if not pngs:
        print("未找到 PNG 文件。")
        return 0

    eligible = [p for p in pngs if p.stat().st_size >= MIN_SKIP_BYTES]
    skipped_small = len(pngs) - len(eligible)
    total_before = sum(p.stat().st_size for p in pngs)

    print(f"找到 {len(pngs)} 个 PNG，合计 {human_size(total_before)}")
    print(f"规则：< {human_size(MIN_SKIP_BYTES)} 跳过 | 目标 {human_size(TARGET_BYTES)} 左右")
    print(f"待处理：{len(eligible)} 个，跳过小图：{skipped_small} 个")
    print(f"pngquant: {pngquant_bin}")
    if args.dry_run:
        print("【预览模式】不会修改任何文件\n")

    total_after = sum(p.stat().st_size for p in pngs if p.stat().st_size < MIN_SKIP_BYTES)
    changed = 0
    skipped = skipped_small

    for i, path in enumerate(pngs, 1):
        rel = path.relative_to(root)
        before, after, note = process_file(
            path,
            pngquant_bin,
            dry_run=args.dry_run,
            min_ratio=args.min_ratio,
        )
        total_after += after if before >= MIN_SKIP_BYTES else 0

        if note.startswith("skip"):
            if before >= MIN_SKIP_BYTES:
                skipped += 1
            ratio = 0.0
        else:
            changed += 1
            ratio = (1 - after / before) * 100 if before else 0.0

        print(
            f"[{i:>3}/{len(pngs)}] {note:>16}  "
            f"{human_size(before):>10} -> {human_size(after):>10}  "
            f"({ratio:5.1f}%)  {rel}"
        )

    saved = total_before - total_after
    pct = saved / total_before * 100 if total_before else 0
    print()
    print(f"完成：{changed} 个已压缩，{skipped} 个跳过")
    print(f"合计：{human_size(total_before)} -> {human_size(total_after)}，节省 {human_size(saved)} ({pct:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
