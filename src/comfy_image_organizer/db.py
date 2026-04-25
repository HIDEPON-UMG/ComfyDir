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
    """スキーマを冪等に作成する。"""
    conn.executescript(SCHEMA_SQL)


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI Depends 用の接続ジェネレータ。"""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
