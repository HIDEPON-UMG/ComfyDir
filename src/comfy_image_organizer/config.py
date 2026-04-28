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
# アプリアイコン (タスクバー / ブラウザ favicon 共用 / マルチサイズ ICO)
ASSETS_DIR = ROOT_DIR / "assets"
ICON_PATH = ASSETS_DIR / "app.ico"
# プロンプトオートコンプリートで参照する Danbooru 風 CSV (a1111-tagcomplete 互換)
# このフォルダの *.csv が起動時に全部マージされる
DANBOORU_TAG_DIR = DATA_DIR / "danbooru_tags"
# 日本語翻訳 CSV (a1111-tagcomplete の Translation 互換 / 形式: "tag,日本語訳" の 2 列)
# このフォルダの *.csv が起動時に全部マージされて、候補に翻訳が併記される
DANBOORU_TRANSLATION_DIR = DATA_DIR / "danbooru_translations"

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
DANBOORU_TAG_DIR.mkdir(parents=True, exist_ok=True)
DANBOORU_TRANSLATION_DIR.mkdir(parents=True, exist_ok=True)
