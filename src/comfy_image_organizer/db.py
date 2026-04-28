"""SQLite 接続ヘルパとスキーマ定義。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from .config import DB_PATH

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS folders (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  path        TEXT    NOT NULL UNIQUE,
  label       TEXT,
  added_at    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS images (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  folder_id       INTEGER NOT NULL,
  path            TEXT    NOT NULL UNIQUE,
  filename        TEXT    NOT NULL,
  sha1            TEXT    NOT NULL,
  size            INTEGER,
  mtime           REAL,
  width           INTEGER,
  height          INTEGER,
  positive_prompt TEXT,
  negative_prompt TEXT,
  raw_prompt_json TEXT,
  memo            TEXT,
  sort_order      INTEGER DEFAULT 0,
  added_at        REAL    NOT NULL,
  scanned_at      REAL    NOT NULL,
  FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_images_sha1   ON images(sha1);
CREATE INDEX IF NOT EXISTS idx_images_folder ON images(folder_id);

CREATE TABLE IF NOT EXISTS tags (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  name  TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS image_tags (
  image_id INTEGER NOT NULL,
  tag_id   INTEGER NOT NULL,
  PRIMARY KEY (image_id, tag_id),
  FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
  FOREIGN KEY (tag_id)   REFERENCES tags(id)   ON DELETE CASCADE
);

-- お気に入りプロンプトのカテゴリ（ユーザー定義の自由ラベル）
CREATE TABLE IF NOT EXISTS prompt_categories (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT    NOT NULL UNIQUE,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  REAL    NOT NULL
);

-- お気に入りプロンプト本体（positive / negative をペアで 1 レコード）
CREATE TABLE IF NOT EXISTS favorite_prompts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  name            TEXT    NOT NULL,
  category_id     INTEGER,
  positive        TEXT    NOT NULL DEFAULT '',
  negative        TEXT    NOT NULL DEFAULT '',
  memo            TEXT    NOT NULL DEFAULT '',
  source_image_id INTEGER,
  sort_order      INTEGER NOT NULL DEFAULT 0,
  created_at      REAL    NOT NULL,
  updated_at      REAL    NOT NULL,
  FOREIGN KEY (category_id)     REFERENCES prompt_categories(id) ON DELETE SET NULL,
  FOREIGN KEY (source_image_id) REFERENCES images(id)            ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_fav_prompts_category ON favorite_prompts(category_id);
CREATE INDEX IF NOT EXISTS idx_fav_prompts_name     ON favorite_prompts(name);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """SQLite 接続を返す。WAL モード + 外部キー有効。"""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """スキーマを冪等に作成する。

    既存 DB に対しては不足カラムを ALTER TABLE で追加する軽量マイグレーション。
    """
    conn.executescript(SCHEMA_SQL)
    _migrate_missing_columns(conn)


def _migrate_missing_columns(conn: sqlite3.Connection) -> None:
    """既存テーブルに不足している列を追加する。

    SQLite は ALTER TABLE で列追加は素直に通るので、PRAGMA table_info で
    現状を取得し、定義に無いものだけ追加する。
    """
    expected: dict[str, list[tuple[str, str]]] = {
        # table -> [(column, "<type> [DEFAULT ...]")]
        "images": [
            ("memo", "TEXT"),
        ],
    }
    for table, cols in expected.items():
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for name, decl in cols:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI Depends 用の接続ジェネレータ。"""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
