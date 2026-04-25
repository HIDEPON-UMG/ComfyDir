"""ComfyImageOrganizer 起動エントリ。

使い方:
    python run.py

ブラウザで http://127.0.0.1:8765 を開く。
"""
from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

# pythonw.exe / VBS hidden 起動などで sys.stdout / stderr が None の場合、
# print や logging の StreamHandler が AttributeError を起こすので
# os.devnull に差し替えておく (このあと main.py 側で FileHandler に出力される)。
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))

# Windows コンソール (cp932) で日本語パスを含むログ/traceback が文字化け
# (例: 「ドキュメント」→「�h�L�������g」) しないよう、stdout/stderr を UTF-8 に。
# Python 3.7+ の reconfigure() を使う。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# src/ をインポートパスに追加
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import uvicorn  # noqa: E402

from comfy_image_organizer.config import HOST, PORT  # noqa: E402


def main() -> None:
    url = f"http://{HOST}:{PORT}"
    print(f"[ComfyDir] starting on {url}")
    try:
        webbrowser.open_new_tab(url)
    except Exception:
        # ブラウザ起動に失敗しても本体は動かす
        pass

    uvicorn.run(
        "comfy_image_organizer.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
