"""ComfyDir 用のアプリアイコン (assets/app.ico) を生成する。

Claude Design 刷新後のブランドマーク (重なった角丸フレーム = フォルダ+画像スタック)
を Pillow で再現し、マルチサイズの ICO として保存する。

レイヤー構成 (SVG ベースのアートワークと同等):
1. 背面: 角丸枠線のみの矩形 (薄いグレー)
2. 前面: シアン系の対角線グラデーション塗りつぶし矩形
3. 前面上の小円: 「画像」を象徴するドット (濃色)
4. 前面上のジグザグ折れ線: 山並み (濃色 35% 透過)

使い方:
    .venv/Scripts/python.exe tools/make_icon.py

タスクバー固定中のショートカットがある場合、Windows のアイコンキャッシュが
古いままになることがあるので、必要に応じてピン留め解除→再固定するか、
`ie4uinit.exe -show` を叩くとよい。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

OUT_PATH = Path(__file__).resolve().parents[1] / "assets" / "app.ico"

# 新ブランドカラー (favicon.svg と同じ hex)
STROKE_GRAY    = (196, 196, 201, 255)  # 背面フレーム枠線
ACCENT_TOPLEFT = (86, 198, 227, 255)   # 前面グラデ TL (color-accent)
ACCENT_BOTRGT  = (130, 214, 236, 255)  # 前面グラデ BR (color-accent-hover)
DARK_FG        = (28, 28, 33, 255)     # 小円・山並み (color-bg-base 近似)


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _gradient_rect(w: int, h: int, c1: tuple[int, int, int, int],
                   c2: tuple[int, int, int, int]) -> Image.Image:
    """対角線グラデーション (TL→BR) の矩形画像を返す。"""
    img = Image.new("RGBA", (w, h))
    px = img.load()
    denom = max(1, (w - 1) + (h - 1))
    for y in range(h):
        for x in range(w):
            t = (x + y) / denom
            px[x, y] = (
                _lerp(c1[0], c2[0], t),
                _lerp(c1[1], c2[1], t),
                _lerp(c1[2], c2[2], t),
                _lerp(c1[3], c2[3], t),
            )
    return img


def _rounded_mask(w: int, h: int, radius: int) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    return m


def make_icon(size: int = 256) -> Image.Image:
    """元の SVG (viewBox=0 0 32 32) を等倍スケールで size×size に描く。"""
    s = size
    sc = s / 32.0  # SVG 単位 → ピクセル係数
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # 1) 背面フレーム: 角丸枠線のみ。SVG: (x=4 y=8 w=20 h=20 rx=5 stroke-width=1.6)
    bx0, by0 = int(4 * sc), int(8 * sc)
    bx1, by1 = int(24 * sc), int(28 * sc)
    br = max(2, int(5 * sc))
    bw = max(2, round(1.6 * sc))
    ImageDraw.Draw(img).rounded_rectangle(
        (bx0, by0, bx1, by1), radius=br, fill=None,
        outline=STROKE_GRAY, width=bw,
    )

    # 2) 前面: 角丸グラデーション塗り。SVG: (x=9 y=3 w=20 h=20 rx=5)
    fx0, fy0 = int(9 * sc), int(3 * sc)
    fx1, fy1 = int(29 * sc), int(23 * sc)
    fw, fh = fx1 - fx0, fy1 - fy0
    fr = max(2, int(5 * sc))
    grad = _gradient_rect(fw, fh, ACCENT_TOPLEFT, ACCENT_BOTRGT)
    front_mask = _rounded_mask(fw, fh, fr)
    img.paste(grad, (fx0, fy0), front_mask)

    # 3) 小円 (画像メタファ). SVG: cx=22 cy=10 r=2 fill=bg-base
    cx, cy = int(22 * sc), int(10 * sc)
    cr = max(2, int(2 * sc))
    ImageDraw.Draw(img).ellipse(
        (cx - cr, cy - cr, cx + cr, cy + cr), fill=DARK_FG,
    )

    # 4) 山並み (opacity 0.35) を前面矩形にクリップして合成
    poly = [
        (int(9 * sc),  int(21 * sc)),
        (int(15 * sc), int(15 * sc)),
        (int(20 * sc), int(19 * sc)),
        (int(26 * sc), int(13 * sc)),
        (int(26 * sc), int(23 * sc)),
    ]
    overlay = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).polygon(
        poly, fill=(DARK_FG[0], DARK_FG[1], DARK_FG[2], int(255 * 0.35)),
    )
    # overlay のアルファを「前面ラウンド矩形マスクの全体配置版」と掛け算し、
    # 角丸の外側にはみ出さないようクリップする
    full_mask = Image.new("L", (s, s), 0)
    full_mask.paste(front_mask, (fx0, fy0))
    a = overlay.split()[3]
    overlay.putalpha(ImageChops.multiply(a, full_mask))
    img.alpha_composite(overlay)

    return img


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    big = make_icon(256)
    # Windows は 16/32/48 を主に使うが、Hi-DPI 対応で 256/128/64 も入れておく
    big.save(
        OUT_PATH,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
