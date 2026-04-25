"""サムネイル生成 (Pillow + ファイルキャッシュ)。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from .config import THUMB_DIR, THUMB_STEPS


def snap_width(requested: int) -> int:
    """要求 width を近い離散段にスナップする (キャッシュヒット率向上)。"""
    if requested <= THUMB_STEPS[0]:
        return THUMB_STEPS[0]
    if requested >= THUMB_STEPS[-1]:
        return THUMB_STEPS[-1]
    # 最も近い段を返す
    return min(THUMB_STEPS, key=lambda w: abs(w - requested))


def thumb_path_for(sha1: str, width: int) -> Path:
    return THUMB_DIR / f"{sha1}_{width}.webp"


def get_or_create_thumb(src_path: str, sha1: str, width: int) -> Path:
    """指定幅のサムネイルを返す (なければ生成)。"""
    width = snap_width(width)
    out = thumb_path_for(sha1, width)
    if out.exists():
        return out

    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(src_path)

    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        # アスペクト比維持で width にフィット
        ratio = width / img.width
        new_size = (width, max(1, int(img.height * ratio)))
        thumb = img.resize(new_size, Image.LANCZOS)
        if thumb.mode not in ("RGB", "RGBA"):
            thumb = thumb.convert("RGBA")
        out.parent.mkdir(parents=True, exist_ok=True)
        thumb.save(out, format="WEBP", quality=82, method=4)
    return out
