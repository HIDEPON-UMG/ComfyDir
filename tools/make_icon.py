"""ComfyImageOrganizer 用のアプリアイコン (assets/app.ico) を生成する。

Pillow で 256x256 の RGBA キャンバスにロゴを描き、マルチサイズの ICO として保存。
気に入らない場合はこのスクリプトを書き換えるか、手持ちの ICO を上書きすればよい。

使い方:
    .venv/Scripts/python.exe tools/make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT_PATH = Path(__file__).resolve().parents[1] / "assets" / "app.ico"

# カラーパレット (濃紺ベース、スクショと同じ世界観)
BG = (40, 60, 110, 255)        # 背景: 濃紺
FRAME = (220, 230, 250, 255)   # 画像枠: 薄い白
MOUNTAIN = (110, 140, 200, 255)
SUN = (255, 200, 100, 255)
LINE = (220, 230, 250, 230)


def make_icon(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    s = size  # 比率で描けるよう短縮
    # 背景: 濃紺の角丸四角
    pad = int(s * 0.03)
    radius = int(s * 0.16)
    d.rounded_rectangle((pad, pad, s - pad, s - pad), radius=radius, fill=BG)

    # 画像枠: 中央上寄り
    fx0, fy0, fx1, fy1 = int(s * 0.16), int(s * 0.23), int(s * 0.84), int(s * 0.70)
    d.rounded_rectangle((fx0, fy0, fx1, fy1), radius=int(s * 0.03), fill=FRAME)

    # 画像の中身: 山と太陽 (小さなランドスケープ)
    inset = int(s * 0.02)
    mx0, my0, mx1, my1 = fx0 + inset, fy0 + inset, fx1 - inset, fy1 - inset
    # 太陽
    sun_r = int(s * 0.07)
    sun_cx = mx0 + int((mx1 - mx0) * 0.72)
    sun_cy = my0 + int((my1 - my0) * 0.30)
    d.ellipse((sun_cx - sun_r, sun_cy - sun_r, sun_cx + sun_r, sun_cy + sun_r), fill=SUN)
    # 山並み (台形と三角の組み合わせ)
    base = my1
    d.polygon([
        (mx0, base),
        (mx0 + int((mx1 - mx0) * 0.20), my0 + int((my1 - my0) * 0.45)),
        (mx0 + int((mx1 - mx0) * 0.45), base),
    ], fill=MOUNTAIN)
    d.polygon([
        (mx0 + int((mx1 - mx0) * 0.35), base),
        (mx0 + int((mx1 - mx0) * 0.65), my0 + int((my1 - my0) * 0.25)),
        (mx1, base),
    ], fill=MOUNTAIN)

    # プロンプト風の線 3 本 (画像枠の下に並べる)
    line_x0 = fx0
    line_h = int(s * 0.04)
    line_gap = int(s * 0.025)
    y = fy1 + int(s * 0.04)
    for i, frac in enumerate([0.85, 0.65, 0.45]):
        line_x1 = line_x0 + int((fx1 - fx0) * frac)
        d.rounded_rectangle(
            (line_x0, y, line_x1, y + line_h),
            radius=line_h // 2, fill=LINE,
        )
        y += line_h + line_gap

    return img


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    big = make_icon(256)
    # マルチサイズ ICO で保存 (Windows は 16/32/48 を主に使う)
    big.save(
        OUT_PATH,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
