"""SQLite 上のデータアクセス層。

シンプルさを優先し、ORM は使わず素 SQL で書く。
sqlite3.Row を返すので呼び出し側は dict ライクに扱える。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


# ---------- folders ----------

def list_folders(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, path, label, added_at FROM folders ORDER BY added_at ASC"
    ).fetchall()


def get_folder(conn: sqlite3.Connection, folder_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, path, label, added_at FROM folders WHERE id = ?", (folder_id,)
    ).fetchone()


def add_folder(
    conn: sqlite3.Connection, path: str, label: str | None
) -> sqlite3.Row:
    now = time.time()
    cur = conn.execute(
        "INSERT INTO folders (path, label, added_at) VALUES (?, ?, ?)",
        (path, label, now),
    )
    return conn.execute(
        "SELECT id, path, label, added_at FROM folders WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone()


def delete_folder(conn: sqlite3.Connection, folder_id: int) -> int:
    cur = conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    return cur.rowcount


# ---------- images ----------

def upsert_image(
    conn: sqlite3.Connection,
    *,
    folder_id: int,
    path: str,
    sha1: str,
    size: int,
    mtime: float,
    width: int | None,
    height: int | None,
    positive_prompt: str | None,
    negative_prompt: str | None,
    raw_prompt_json: str | None,
) -> int:
    """画像レコードを挿入または更新し、image_id を返す。

    リネーム検出: 同 folder_id 内に同 sha1 を持つ別 path のレコードがあれば、
    その path を新パスに付け替える（タグ紐付けはそのまま継承）。
    通常の path 衝突は ON CONFLICT(path) DO UPDATE で原子的に解決し、
    複数スレッドから同 path を同時 upsert しても UNIQUE 違反にしない。
    """
    now = time.time()
    filename = Path(path).name

    # リネーム/移動検出: 同 folder_id 内に同 sha1 を持つ別 path のレコードがあり、
    # かつそれらの「古いパスがディスク上から消えている」場合のみ付け替えとみなす。
    # (重複コピーされた同一内容の別ファイルを誤って統合しないため)
    rename_targets = conn.execute(
        "SELECT id, path FROM images "
        "WHERE sha1 = ? AND folder_id = ? AND path != ?",
        (sha1, folder_id, path),
    ).fetchall()
    target_id: int | None = None
    for cand in rename_targets:
        if not Path(cand["path"]).exists():
            target_id = int(cand["id"])
            break
    if target_id is not None:
        # 今回のパスに既に別レコードがあると UNIQUE で衝突するので先に消す
        conn.execute(
            "DELETE FROM images WHERE path = ? AND id != ?",
            (path, target_id),
        )
        conn.execute(
            """
            UPDATE images SET
              path = ?, filename = ?, size = ?, mtime = ?,
              width = ?, height = ?,
              positive_prompt = ?, negative_prompt = ?, raw_prompt_json = ?,
              scanned_at = ?
            WHERE id = ?
            """,
            (
                path, filename, size, mtime,
                width, height,
                positive_prompt, negative_prompt, raw_prompt_json,
                now,
                target_id,
            ),
        )
        return target_id

    # 通常 upsert: path をユニークキーとした原子操作
    conn.execute(
        """
        INSERT INTO images (
          folder_id, path, filename, sha1, size, mtime,
          width, height,
          positive_prompt, negative_prompt, raw_prompt_json,
          sort_order, added_at, scanned_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          folder_id       = excluded.folder_id,
          filename        = excluded.filename,
          sha1            = excluded.sha1,
          size            = excluded.size,
          mtime           = excluded.mtime,
          width           = excluded.width,
          height          = excluded.height,
          positive_prompt = excluded.positive_prompt,
          negative_prompt = excluded.negative_prompt,
          raw_prompt_json = excluded.raw_prompt_json,
          scanned_at      = excluded.scanned_at
        """,
        (
            folder_id, path, filename, sha1, size, mtime,
            width, height,
            positive_prompt, negative_prompt, raw_prompt_json,
            now, now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM images WHERE path = ?", (path,)
    ).fetchone()
    return int(row["id"])


def delete_image_by_path(conn: sqlite3.Connection, path: str) -> int:
    cur = conn.execute("DELETE FROM images WHERE path = ?", (path,))
    return cur.rowcount


def get_image(conn: sqlite3.Connection, image_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM images WHERE id = ?", (image_id,)
    ).fetchone()


def list_image_paths_in_folder(
    conn: sqlite3.Connection, folder_id: int
) -> list[str]:
    rows = conn.execute(
        "SELECT path FROM images WHERE folder_id = ?", (folder_id,)
    ).fetchall()
    return [r["path"] for r in rows]


def search_images(
    conn: sqlite3.Connection,
    *,
    folder_id: int | None,
    tag_ids: list[int] | None,
    tag_mode: str,           # 'and' | 'or'
    order: str,              # 'name' | 'mtime' | 'added'
    direction: str,          # 'asc' | 'desc'
    prompt_query: str | None = None,  # ポジ/ネガに対する部分一致 (大小区別なし)
) -> list[sqlite3.Row]:
    where: list[str] = []
    params: list[Any] = []

    if folder_id is not None:
        where.append("i.folder_id = ?")
        params.append(folder_id)

    if tag_ids:
        placeholders = ",".join("?" * len(tag_ids))
        if tag_mode == "and":
            where.append(
                f"i.id IN (SELECT image_id FROM image_tags "
                f"WHERE tag_id IN ({placeholders}) "
                f"GROUP BY image_id HAVING COUNT(DISTINCT tag_id) = ?)"
            )
            params.extend(tag_ids)
            params.append(len(tag_ids))
        else:  # or
            where.append(
                f"i.id IN (SELECT image_id FROM image_tags "
                f"WHERE tag_id IN ({placeholders}))"
            )
            params.extend(tag_ids)

    if prompt_query:
        # スペース区切りで AND 検索 (各語が positive または negative のいずれかに含まれる)
        # LOWER + LIKE で大小区別なし、Unicode (日本語) でも素直に動く
        terms = [t for t in prompt_query.split() if t]
        for term in terms:
            where.append(
                "(LOWER(COALESCE(i.positive_prompt, '')) LIKE ? "
                "OR LOWER(COALESCE(i.negative_prompt, '')) LIKE ?)"
            )
            like = f"%{term.lower()}%"
            params.append(like)
            params.append(like)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    order_col = {
        "name": "i.filename",
        "mtime": "i.mtime",
        "added": "i.added_at",
    }.get(order, "i.filename")
    dir_sql = "DESC" if direction.lower() == "desc" else "ASC"

    sql = (
        "SELECT i.id, i.folder_id, i.path, i.filename, i.sha1, "
        "       i.size, i.mtime, i.width, i.height, i.added_at "
        f"FROM images i {where_sql} "
        f"ORDER BY {order_col} {dir_sql}, i.id ASC"
    )
    return conn.execute(sql, params).fetchall()


def update_image_path(
    conn: sqlite3.Connection, image_id: int, new_path: str
) -> None:
    conn.execute(
        "UPDATE images SET path = ?, filename = ? WHERE id = ?",
        (new_path, Path(new_path).name, image_id),
    )


def update_image_location(
    conn: sqlite3.Connection,
    image_id: int,
    new_path: str,
    new_folder_id: int,
) -> None:
    """画像の path / filename / folder_id を一括更新する (移動操作用)。"""
    conn.execute(
        "UPDATE images SET path = ?, filename = ?, folder_id = ? WHERE id = ?",
        (new_path, Path(new_path).name, new_folder_id, image_id),
    )


# ---------- tags ----------

def list_tags_with_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT t.id, t.name, COUNT(it.image_id) AS image_count
        FROM tags t
        LEFT JOIN image_tags it ON it.tag_id = t.id
        GROUP BY t.id, t.name
        ORDER BY t.name COLLATE NOCASE ASC
        """
    ).fetchall()


def get_or_create_tag(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("タグ名が空です")
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
    return int(cur.lastrowid)


def get_tag_id(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    return int(row["id"]) if row else None


def list_tags_for_image(conn: sqlite3.Connection, image_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT t.name FROM tags t
        JOIN image_tags it ON it.tag_id = t.id
        WHERE it.image_id = ?
        ORDER BY t.name COLLATE NOCASE ASC
        """,
        (image_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def assign_tags(
    conn: sqlite3.Connection,
    *,
    image_ids: Iterable[int],
    add_tag_names: Iterable[str],
    remove_tag_names: Iterable[str],
) -> None:
    image_ids = list(image_ids)
    if not image_ids:
        return

    add_ids = [get_or_create_tag(conn, n) for n in add_tag_names if n.strip()]
    rem_ids = [tid for n in remove_tag_names if (tid := get_tag_id(conn, n.strip())) is not None]

    if add_ids:
        rows = [(iid, tid) for iid in image_ids for tid in add_ids]
        conn.executemany(
            "INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?, ?)",
            rows,
        )
    if rem_ids:
        # 削除はループで（IN 句の組み立てが複雑になるので素直に）
        for iid in image_ids:
            placeholders = ",".join("?" * len(rem_ids))
            conn.execute(
                f"DELETE FROM image_tags WHERE image_id = ? AND tag_id IN ({placeholders})",
                [iid, *rem_ids],
            )

    # 孤立タグ削除（image_tags に紐付かなくなったタグを掃除）
    conn.execute(
        "DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM image_tags)"
    )
