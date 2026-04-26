"""SQLite 上のデータアクセス層。

シンプルさを優先し、ORM は使わず素 SQL で書く。
sqlite3.Row を返すので呼び出し側は dict ライクに扱える。
"""
from __future__ import annotations

import csv
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .config import DANBOORU_TAG_DIR, DANBOORU_TRANSLATION_DIR

log = logging.getLogger(__name__)


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
    memo_query: str | None = None,    # メモ本文に対する部分一致 (大小区別なし)
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

    if memo_query:
        # メモ本文の部分一致 (空白区切りで AND、大小区別なし)
        terms = [t for t in memo_query.split() if t]
        for term in terms:
            where.append("LOWER(COALESCE(i.memo, '')) LIKE ?")
            params.append(f"%{term.lower()}%")

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


def update_image_memo(
    conn: sqlite3.Connection, image_id: int, memo: str | None
) -> None:
    """画像のユーザーメモを更新する。空文字は NULL として保存。"""
    val = memo.strip() if memo else None
    if val == "":
        val = None
    conn.execute(
        "UPDATE images SET memo = ? WHERE id = ?", (val, image_id)
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


# ---------- prompt tag suggest (a1111-tagcomplete 風オートコンプリート) ----------

# 各タグの情報を保持するインメモリ辞書。キーは「正式名（lower-case）」。
# value のフィールド:
#   name        : 正式タグ名（CSV / DB のいずれかに登場した形のうち代表）
#   db_count    : DB 内の出現回数（プロンプト由来 / 0 なら Danbooru CSV だけのタグ）
#   db_pos      : positive プロンプトでの出現回数
#   db_neg      : negative プロンプトでの出現回数
#   ref_count   : Danbooru CSV の post_count（参考件数 / 0 ならCSVに無い）
#   category    : Danbooru CSV のカテゴリ整数 (0:general,1:artist,3:copyright,4:character,5:meta) / -1 で無し
#   aliases     : CSV に書かれた別名リスト (lower-case 正式名へのリバース引きにも使う)
_PROMPT_TAG_LOCK = threading.Lock()
_PROMPT_TAG_INDEX: dict[str, dict[str, Any]] = {}
_PROMPT_ALIAS_INDEX: dict[str, str] = {}     # alias(lower) -> 正式名(lower)
_PROMPT_TAG_DIRTY = True                     # DB 由来部分のみ更新が必要なフラグ
_PROMPT_DANBOORU_LOADED = False              # CSV は起動毎に 1 回だけロード
_PROMPT_DANBOORU_DATA: dict[str, dict[str, Any]] = {}  # CSV 由来データ (キーは lower 正式名)
# 日本語翻訳辞書 (キーは lower-case の英語タグ / lower-case エイリアス)
_PROMPT_TRANSLATION: dict[str, str] = {}
_PROMPT_TRANSLATION_LOADED = False

# Danbooru CSV のカテゴリ整数 -> 表示用ラベル
_DANBOORU_CATEGORY: dict[int, str] = {
    0: "general",
    1: "artist",
    3: "copyright",
    4: "character",
    5: "meta",
}

# プロンプト中の "(tag:1.2)" や "[tag]" の重み記法を剥がすための正規表現
_PROMPT_WEIGHT_RE = re.compile(r":\s*-?\d+(\.\d+)?\s*$")


def invalidate_prompt_tag_index() -> None:
    """画像追加/削除時に呼ぶ。次回 list_prompt_tag_suggestions() で再構築される。"""
    global _PROMPT_TAG_DIRTY
    with _PROMPT_TAG_LOCK:
        _PROMPT_TAG_DIRTY = True


def _normalize_prompt_tag(raw: str) -> str:
    """プロンプト 1 トークンを検索キー用の正規化文字列に変換する。

    - 前後の空白除去
    - "(tag:1.2)" のような重み記法から数値部だけ落とす（タグ名は保持）
    - 先頭末尾の括弧 / 角括弧を剥がす
    - LoRA 記法 "<lora:foo:0.8>" は "<lora:foo>" として保持（識別性を残す）
    """
    s = raw.strip()
    if not s:
        return ""
    # <lora:name:weight> 系は重みだけ落として残す
    if s.startswith("<") and s.endswith(">"):
        body = s[1:-1]
        parts = body.split(":")
        if len(parts) >= 3:
            body = ":".join(parts[:2])
        return f"<{body}>"
    # 先頭末尾の括弧/角括弧を剥がす（ネスト対応）
    while len(s) >= 2 and ((s[0] == "(" and s[-1] == ")") or (s[0] == "[" and s[-1] == "]")):
        s = s[1:-1].strip()
        if not s:
            return ""
    # ":1.2" などの重みサフィックスを除去
    s = _PROMPT_WEIGHT_RE.sub("", s).strip()
    return s


def _split_prompt_tags(text: str | None) -> list[str]:
    """プロンプト本文をカンマ区切りでタグに分解し、正規化したリストを返す。"""
    if not text:
        return []
    out: list[str] = []
    for chunk in text.split(","):
        n = _normalize_prompt_tag(chunk)
        if n:
            out.append(n)
    return out


def _load_danbooru_csv_files() -> dict[str, dict[str, Any]]:
    """data/danbooru_tags/*.csv を全部読み込んでマージする。

    CSV 形式 (a1111-tagcomplete 互換): name,type,post_count,"alias1,alias2,..."
    複数ファイルに同名タグがあれば post_count が大きい方を採用。
    """
    out: dict[str, dict[str, Any]] = {}
    if not DANBOORU_TAG_DIR.exists():
        return out
    files = sorted(DANBOORU_TAG_DIR.glob("*.csv"))
    if not files:
        return out
    for fp in files:
        try:
            with fp.open(encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) < 3:
                        continue
                    name = row[0].strip()
                    if not name:
                        continue
                    try:
                        cat = int(row[1].strip())
                    except ValueError:
                        cat = -1
                    try:
                        cnt = int(row[2].strip())
                    except ValueError:
                        cnt = 0
                    aliases_raw = row[3].strip() if len(row) >= 4 else ""
                    aliases = [a.strip() for a in aliases_raw.split(",") if a.strip()] if aliases_raw else []

                    key = name.lower()
                    prev = out.get(key)
                    if prev is None or cnt > prev["ref_count"]:
                        out[key] = {
                            "name": name,
                            "category": cat,
                            "ref_count": cnt,
                            "aliases": aliases,
                        }
                    else:
                        # post_count が小さくてもエイリアスはマージしておく
                        existing = set(prev["aliases"])
                        for a in aliases:
                            if a not in existing:
                                prev["aliases"].append(a)
                                existing.add(a)
        except Exception as e:
            log.warning("Danbooru CSV 読み込み失敗: %s (%s)", fp, e)
    return out


def _ensure_danbooru_loaded() -> None:
    """Danbooru CSV はプロセス起動毎に 1 回だけ読み込めば足りる。

    NOTE: グローバル `_PROMPT_DANBOORU_LOADED` / `_PROMPT_DANBOORU_DATA` を更新するため、
    必ず `_PROMPT_TAG_LOCK` を保持した状態から呼び出すこと
    (現状は `_rebuild_prompt_tag_index` 経由でしか呼ばれていない)。
    """
    global _PROMPT_DANBOORU_DATA, _PROMPT_DANBOORU_LOADED
    if _PROMPT_DANBOORU_LOADED:
        return
    t0 = time.time()
    _PROMPT_DANBOORU_DATA = _load_danbooru_csv_files()
    _PROMPT_DANBOORU_LOADED = True
    if _PROMPT_DANBOORU_DATA:
        log.info(
            "Danbooru タグ辞書を読み込み: %d 件 (%.0f ms)",
            len(_PROMPT_DANBOORU_DATA),
            (time.time() - t0) * 1000,
        )


def _load_translation_csv_files() -> dict[str, str]:
    """data/danbooru_translations/*.csv を読み込み、英語タグ -> 日本語訳 の辞書を返す。

    形式: "<英語タグ>,<日本語訳>" の 2 列を最低限必要とする。
    a1111-tagcomplete の翻訳ファイルとも互換 (3 列以上あっても先頭2列だけ使う)。
    複数ファイルに同じキーがあれば後に読まれた方で上書きする。
    キーは lower-case で、'_' と ' ' を同一視するため両方を辞書に登録する。
    """
    out: dict[str, str] = {}
    if not DANBOORU_TRANSLATION_DIR.exists():
        return out
    files = sorted(DANBOORU_TRANSLATION_DIR.glob("*.csv"))
    if not files:
        return out
    # 複数ファイルがある場合、手動翻訳 ("danbooru-jp.csv" のように "machine" を含まないもの) を
    # 後に読み込んで優先させる。これで自動翻訳より人間翻訳が勝つ。
    files.sort(key=lambda p: ("machine" not in p.name.lower(), p.name.lower()))
    for fp in files:
        try:
            with fp.open(encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) < 2:
                        continue
                    en = row[0].strip()
                    jp = row[1].strip()
                    if not en or not jp:
                        continue
                    key = en.lower()
                    out[key] = jp
                    # アンダースコアとスペースを同一視するため両形式で登録
                    if "_" in key:
                        out[key.replace("_", " ")] = jp
                    elif " " in key:
                        out[key.replace(" ", "_")] = jp
        except Exception as e:
            log.warning("翻訳 CSV 読み込み失敗: %s (%s)", fp, e)
    return out


def _ensure_translation_loaded() -> None:
    """翻訳 CSV はプロセス起動毎に 1 回だけ読めば足りる。

    NOTE: `_PROMPT_TAG_LOCK` を保持した状態から呼び出すこと
    (現状は `_rebuild_prompt_tag_index` 経由でしか呼ばれていない)。
    """
    global _PROMPT_TRANSLATION, _PROMPT_TRANSLATION_LOADED
    if _PROMPT_TRANSLATION_LOADED:
        return
    t0 = time.time()
    _PROMPT_TRANSLATION = _load_translation_csv_files()
    _PROMPT_TRANSLATION_LOADED = True
    if _PROMPT_TRANSLATION:
        log.info(
            "プロンプト翻訳辞書を読み込み: %d 件 (%.0f ms)",
            len(_PROMPT_TRANSLATION),
            (time.time() - t0) * 1000,
        )


def _lookup_translation(name: str, aliases: list[str] | None = None) -> str | None:
    """タグ名 (および別名) から日本語訳を引く。'_' / ' ' は同一視済み。"""
    if not _PROMPT_TRANSLATION:
        return None
    low = name.lower()
    if low in _PROMPT_TRANSLATION:
        return _PROMPT_TRANSLATION[low]
    if aliases:
        for a in aliases:
            jp = _PROMPT_TRANSLATION.get(a.lower())
            if jp:
                return jp
    return None


def _rebuild_prompt_tag_index(conn: sqlite3.Connection) -> None:
    """Danbooru CSV と DB のプロンプトを統合して _PROMPT_TAG_INDEX を作り直す。

    - 先に Danbooru CSV のタグを全部入れる（参考件数 = post_count）
    - 上に DB 由来のタグを加算（DB の出現回数で db_count を埋める）
    - DB 側で正規化したタグ名と CSV 側のタグ名は、lower-case 比較 + アンダースコア/空白同一視で突合
    """
    global _PROMPT_TAG_INDEX, _PROMPT_ALIAS_INDEX, _PROMPT_TAG_DIRTY

    _ensure_danbooru_loaded()
    _ensure_translation_loaded()

    idx: dict[str, dict[str, Any]] = {}
    alias_idx: dict[str, str] = {}

    # 1) Danbooru CSV を流し込む
    for key, info in _PROMPT_DANBOORU_DATA.items():
        idx[key] = {
            "name": info["name"],
            "db_count": 0,
            "db_pos": 0,
            "db_neg": 0,
            "ref_count": info["ref_count"],
            "category": info["category"],
            "aliases": list(info["aliases"]),
        }
        for a in info["aliases"]:
            alias_idx[a.lower()] = key

    # 2) DB の positive / negative を流し込む
    rows = conn.execute(
        "SELECT positive_prompt, negative_prompt FROM images"
    ).fetchall()

    def _resolve_key(name: str) -> str:
        """DB 由来の正規化済みタグを CSV 辞書のキーに合わせる。

        Danbooru CSV はアンダースコア表記が標準だが、DB のプロンプトはモデルによって
        スペース表記されていることが多いので、両形式を試して既存キーがあれば寄せる。
        """
        low = name.lower()
        if low in idx:
            return low
        # スペース版 / アンダースコア版 を試す
        if " " in low:
            cand = low.replace(" ", "_")
            if cand in idx:
                return cand
        if "_" in low:
            cand = low.replace("_", " ")
            if cand in idx:
                return cand
        # CSV 由来のエイリアスにヒットすれば正式名に寄せる
        if low in alias_idx:
            return alias_idx[low]
        return low

    for r in rows:
        for tag in _split_prompt_tags(r["positive_prompt"]):
            key = _resolve_key(tag)
            ent = idx.setdefault(key, {
                "name": tag,
                "db_count": 0, "db_pos": 0, "db_neg": 0,
                "ref_count": 0, "category": -1, "aliases": [],
            })
            ent["db_count"] += 1
            ent["db_pos"] += 1
        for tag in _split_prompt_tags(r["negative_prompt"]):
            key = _resolve_key(tag)
            ent = idx.setdefault(key, {
                "name": tag,
                "db_count": 0, "db_pos": 0, "db_neg": 0,
                "ref_count": 0, "category": -1, "aliases": [],
            })
            ent["db_count"] += 1
            ent["db_neg"] += 1

    _PROMPT_TAG_INDEX = idx
    _PROMPT_ALIAS_INDEX = alias_idx
    _PROMPT_TAG_DIRTY = False


def list_prompt_tag_suggestions(
    conn: sqlite3.Connection,
    *,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """オートコンプリート候補を返す。

    - query は前後空白を除去し、Booru 風アンダースコアはスペースとも一致させる
      (`long_hair` でも `long hair` でもヒット)
    - 大小区別なし、部分一致 (前方一致は強い preference)
    - DB 実在タグは Danbooru 由来のみのタグより優先表示
    - 表示順:
        完全一致 → DB実在優先 → DBの出現回数 or Danbooru post_count → 短い → 名前
    - エイリアスにヒットした場合は正式名を返し alias_hit を埋める

    レスポンス要素:
        name        : 正式タグ名 (Booru 風アンダースコア形式が標準)
        count       : DB 内出現回数 (なければ Danbooru CSV の post_count を返す)
        source      : 'positive' | 'negative' | 'both' | 'reference' (CSV由来のみ)
        category    : Danbooru カテゴリ ('general'/'artist'/'copyright'/'character'/'meta'/null)
        alias_hit   : マッチに使われた別名 (一致したのが正式名なら null)
        translation : 日本語翻訳辞書にヒットすればその訳語 (なければ null)
    """
    global _PROMPT_TAG_DIRTY
    with _PROMPT_TAG_LOCK:
        if _PROMPT_TAG_DIRTY or not _PROMPT_TAG_INDEX:
            _rebuild_prompt_tag_index(conn)
        index = _PROMPT_TAG_INDEX
        alias_idx = _PROMPT_ALIAS_INDEX

    q = (query or "").strip().lower()
    # アンダースコアもスペースも同一視する 2 通りのキーを作って OR マッチ
    q_alt = q.replace("_", " ") if "_" in q else (q.replace(" ", "_") if " " in q else None)

    matches: list[tuple[int, int, int, str, dict[str, Any], str | None]] = []
    seen_keys: set[str] = set()

    def _consider(key: str, ent: dict[str, Any], alias_hit: str | None) -> None:
        if key in seen_keys:
            return
        # ランク決定 (rank0=完全一致 / rank1=本名前方一致 / rank2=エイリアス前方一致 / rank3=本名部分一致)
        low = key
        if q == "":
            rank = 1
        elif low == q or (q_alt and low == q_alt):
            rank = 0
        elif alias_hit is not None:
            # エイリアス由来は完全一致でも前方一致でも rank2
            rank = 2
        elif low.startswith(q) or (q_alt and low.startswith(q_alt)):
            rank = 1
        else:
            rank = 3
        # DB 実在を優先 (db=0 / 非DB=1)
        db_pref = 0 if ent["db_count"] > 0 else 1
        # 数値ランキング: DB 内出現回数を最優先、なければ Danbooru post_count
        weight = -(ent["db_count"] if ent["db_count"] > 0 else ent["ref_count"])
        matches.append((rank, db_pref, weight, key, ent, alias_hit))
        seen_keys.add(key)

    if q == "":
        # 空クエリ: DB に出現する人気タグを上から
        for key, ent in index.items():
            if ent["db_count"] == 0:
                continue
            _consider(key, ent, None)
    else:
        # 1) 本名（key）で部分一致
        for key, ent in index.items():
            low = key
            if q in low or (q_alt is not None and q_alt in low):
                _consider(key, ent, None)
        # 2) エイリアスでマッチ
        for alias, official_key in alias_idx.items():
            if q in alias or (q_alt is not None and q_alt in alias):
                ent = index.get(official_key)
                if ent is None:
                    continue
                _consider(official_key, ent, alias)

    matches.sort(key=lambda x: (x[0], x[1], x[2], len(x[3]), x[3]))

    out: list[dict[str, Any]] = []
    for _r, _d, _w, _key, ent, alias_hit in matches[:limit]:
        if ent["db_count"] > 0:
            if ent["db_pos"] > 0 and ent["db_neg"] > 0:
                source = "both"
            elif ent["db_neg"] > 0:
                source = "negative"
            else:
                source = "positive"
            count = ent["db_count"]
        else:
            source = "reference"
            count = ent["ref_count"]
        out.append({
            "name": ent["name"],
            "count": count,
            "source": source,
            "category": _DANBOORU_CATEGORY.get(ent["category"]),
            "alias_hit": alias_hit,
            "translation": _lookup_translation(ent["name"], ent.get("aliases")),
        })
    return out


def get_prompt_category_map() -> dict[str, list[str]]:
    """Danbooru CSV から character/copyright/artist/meta カテゴリのタグだけを抽出する。

    フロント側の並び替え機能で「カッコ無しキャラ名」「@ 抜きアーティスト名」を確実に
    振り分けるために使う。general はサイズが大きい上に分類精度に貢献しないので除外。
    キーは Booru 風 lower-case 正式名。
    """
    with _PROMPT_TAG_LOCK:
        _ensure_danbooru_loaded()
        out: dict[str, list[str]] = {}
        for key, info in _PROMPT_DANBOORU_DATA.items():
            cat = _DANBOORU_CATEGORY.get(info["category"])
            if not cat or cat == "general":
                continue
            out.setdefault(cat, []).append(key)
        return out
