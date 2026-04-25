"""アプリ全体の設定値。

実行時パスは run.py からの相対 ( = プロジェクトルート ) に解決する。
"""
from __future__ import annotations

import os
from pathlib import Path

# プロジェクトルート (= run.py のあるディレクトリ)
ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "index.sqlite"
THUMB_DIR = DATA_DIR / "thumbs"
LOG_PATH = DATA_DIR / "server.log"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# サーバ設定
HOST = os.environ.get("CIO_HOST", "127.0.0.1")
PORT = int(os.environ.get("CIO_PORT", "8765"))

# サムネ離散段 (px)
THUMB_STEPS: tuple[int, ...] = (128, 192, 256, 384, 512)

# 対象拡張子
IMAGE_EXTS: tuple[str, ...] = (".png",)

# 起動時にディレクトリを必ず作成
DATA_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR.mkdir(parents=True, exist_ok=True)
