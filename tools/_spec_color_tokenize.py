"""docs/specs/*.html の SVG 内インライン色直書きを DESIGN.md トークン参照に置換する。

ゲート 4' (HTML 仕様書 lint) で `fill="#XXXXXX"` / `stroke="#XXXXXX"` の直書きが
NG 判定されるため、`var(--color-*)` 経由に書き換えて DESIGN.md 同期を保つ。

`<code>#XXXXXX</code>` のような表示目的のリテラル hex は対象外 (置換しない)。
属性値 (`fill="..."` / `stroke="..."` / `stop-color="..."`) のみ対象。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# DESIGN.md トークン → 実 hex のマッピングを反転 (hex → CSS var)。
# 同一 hex が 2 token に対応するケース (例: #1A1B1F は bg-base と fg-on-accent) は、
# SVG コンテキスト判定が難しいため意味的により一般的な方 (--color-bg-base) を採用。
HEX_TO_TOKEN: dict[str, str] = {
    "#1A1B1F": "var(--color-bg-base)",
    "#232428": "var(--color-bg-surface)",
    "#2D2E33": "var(--color-bg-elevated)",
    "#1E1F23": "var(--color-bg-inset)",
    "#F4F4F6": "var(--color-fg-primary)",
    "#BFC0C5": "var(--color-fg-secondary)",
    "#888A91": "var(--color-fg-muted)",
    "#3D3F45": "var(--color-border)",
    "#5A5C63": "var(--color-border-strong)",
    "#7BC9E6": "var(--color-border-focus)",
    "#56C6E3": "var(--color-accent)",
    "#82D6EC": "var(--color-accent-hover)",
    "#5DBE83": "var(--color-success)",
    "#DCBC4A": "var(--color-warning)",
    "#E36C4F": "var(--color-error)",
    "#7CB6E8": "var(--color-cat-1)",
    "#67C9B0": "var(--color-cat-2)",
    "#D7C26B": "var(--color-cat-3)",
    "#D77E6B": "var(--color-cat-4)",
    "#C9A2D9": "var(--color-cat-5)",
}

ATTR_RE = re.compile(
    r'(?P<attr>\b(?:fill|stroke|stop-color)="?)(?P<hex>#[0-9A-Fa-f]{6})(?P<close>"?)'
)


def tokenize(text: str) -> tuple[str, int]:
    n = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal n
        hex_upper = m.group("hex").upper()
        token = HEX_TO_TOKEN.get(hex_upper)
        if token is None:
            return m.group(0)  # 未知の hex は触らない
        n += 1
        return f'{m.group("attr")}{token}{m.group("close")}'

    return ATTR_RE.sub(repl, text), n


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python _spec_color_tokenize.py <html-file> [<html-file>...]")
        return 2
    total = 0
    for arg in sys.argv[1:]:
        p = Path(arg)
        src = p.read_text(encoding="utf-8")
        out, n = tokenize(src)
        if n:
            p.write_text(out, encoding="utf-8")
        total += n
        print(f"{p}: {n} 箇所を CSS 変数に置換")
    print(f"---\ntotal: {total} 箇所")
    return 0


if __name__ == "__main__":
    sys.exit(main())
