"""フォルダのフルスキャンと watchdog による継続監視。

- アプリ起動時に登録フォルダを 1 回フルスキャン
- watchdog Observer を起動し、新規/削除/移動 (リネーム) イベントで DB を更新
- 変更内容は asyncio.Queue 経由で SSE エンドポイントに流す
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from . import comfy_prompt, repo, thumbnail
from .config import IMAGE_EXTS
from .db import connect

log = logging.getLogger(__name__)


@dataclass
class ScanEvent:
    """SSE で流すイベント。"""
    type: str           # 'image_added' | 'image_removed' | 'image_updated' | 'scan_progress' | 'folder_rescanned'
    folder_id: int | None = None
    image_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------
# ファイルハッシュ
# ------------------------------------------------------------------

def sha1_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


# ------------------------------------------------------------------
# スキャン本体
# ------------------------------------------------------------------

def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def _iter_image_files(
    folder: Path, recursive: bool, errors: list[str] | None = None,
) -> Iterator[tuple[Path, int, float]]:
    """画像ファイルを (パス, サイズ, 更新日時) で返す。

    os.scandir の 1 回の取得でサイズと更新日時も得るので、差分判定のための
    追加の stat は要らない (Windows では DirEntry.stat() が一覧の情報を使う)。
    パスは旧方式 (rglob / glob) の str(p) と 1 文字も違わない形にする。
    違うと、削除の掃除が登録済みの記録を消し、連動するタグ・メモ・並び順も消える。

    - recursive=True: サブフォルダへ降りる (シンボリックリンクのフォルダへは降りない。rglob と同じ)。
    - 開けないフォルダは warning にして飛ばす。エントリ単位の OSError はそのエントリだけ飛ばす。
    - errors を渡すと、列挙が不完全になった理由を 1 件ずつ追記する。呼び出し側は、1 件でも
      あれば「ディスクから消えた」と断定できないので、削除の掃除をしない
      (権限・ネットワークドライブの一時切断で、配下の記録とタグ・メモ・並び順を消さないため)。
    """
    stack = [str(folder)]
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError as e:
            log.warning("フォルダを開けません: %s (%s)", current, e)
            if errors is not None:
                errors.append(current)
            continue
        try:
            with it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if recursive:
                                stack.append(entry.path)
                            continue
                        if not _is_image(Path(entry.name)):
                            continue
                        if not entry.is_file():
                            continue
                        st = entry.stat()
                        yield Path(entry.path), st.st_size, st.st_mtime
                    except OSError:
                        # 列挙の途中で消えた・読めないエントリは、そのエントリだけ飛ばす
                        if errors is not None:
                            errors.append(entry.path)
                        continue
        except OSError as e:
            log.warning("フォルダの列挙が途中で失敗しました: %s (%s)", current, e)
            if errors is not None:
                errors.append(current)


def _is_unchanged(known: tuple[Any, Any] | None, size: int, mtime: float) -> bool:
    """登録済みの (サイズ, 更新日時) と同じなら真。ネットワーク上の丸めを 1 ミリ秒まで許す。"""
    if known is None:
        return False
    k_size, k_mtime = known
    if k_size is None or k_mtime is None:
        return False
    return k_size == size and abs(float(k_mtime) - mtime) < 0.001


def _index_one(folder_id: int, file_path: Path) -> dict[str, Any] | None:
    """1 ファイルを DB に登録/更新し、追加/更新内容を dict で返す。"""
    try:
        st = file_path.stat()
    except FileNotFoundError:
        return None

    try:
        sha1 = sha1_of(file_path)
    except OSError as e:
        log.warning("ハッシュ計算失敗: %s (%s)", file_path, e)
        return None

    extraction = comfy_prompt.extract_from_file(file_path)
    width = extraction.image_size[0] if extraction.image_size else None
    height = extraction.image_size[1] if extraction.image_size else None

    conn = connect()
    try:
        image_id = repo.upsert_image(
            conn,
            folder_id=folder_id,
            path=str(file_path),
            sha1=sha1,
            size=st.st_size,
            mtime=st.st_mtime,
            width=width,
            height=height,
            positive_prompt=extraction.positive,
            negative_prompt=extraction.negative,
            raw_prompt_json=extraction.raw_prompt_json,
        )
    except Exception as e:
        # 1 ファイル単位のエラーで全体スキャンを止めないよう warning に留める
        log.warning("DB 登録失敗: %s (%s)", file_path, e)
        return None
    finally:
        conn.close()

    # プロンプトオートコンプリート用のキャッシュを次回参照時に再構築
    repo.invalidate_prompt_tag_index()

    return {
        "image_id": image_id,
        "filename": file_path.name,
        "sha1": sha1,
    }


# 掃除の安全弁: 列挙の件数が、同フォルダの登録件数 (この件数以上のとき) の
# この割合未満なら、ドライブの不調とみなして削除をしない (記録とタグ・メモを守る)。
CLEANUP_GUARD_MIN_ROWS = 20
CLEANUP_GUARD_RATIO = 0.5

# 背景の解析と、詳細を開いたときの先行解析が、同じ画像を同時に処理しないための錠。
_ENRICH_LOCK = threading.Lock()


def _migrate_thumbs(old_sha1: str, new_sha1: str) -> None:
    """縮小画像のキャッシュを、仮の名前 ({仮sha1}_{幅}.webp) から実の名前へ改名する。

    宛先が既にあれば、宛先を残して仮のほうだけ消す。
    """
    if old_sha1 == new_sha1:
        return
    d = thumbnail.THUMB_DIR
    try:
        files = list(d.glob(f"{old_sha1}_*.webp"))
    except OSError as e:
        log.warning("縮小画像の検索に失敗: %s (%s)", old_sha1, e)
        return
    for f in files:
        dest = d / (new_sha1 + f.name[len(old_sha1):])
        try:
            if dest.exists():
                f.unlink(missing_ok=True)
            else:
                f.replace(dest)
        except OSError as e:
            log.warning("縮小画像の改名に失敗: %s (%s)", f, e)


def enrich_image(image_id: int) -> dict[str, Any] | None:
    """仮の行 1 件のハッシュと解析を済ませ、実の値に更新する。

    - 行が無い・ファイルが読めない: None (行は消さない)。
    - 既に実の sha1: {"already": True, ...} (同時に来た後続の呼び出し)。
    - 成功: _index_one の戻り値 ({"image_id", "filename", "sha1"})。
      改名検出で行の id が変わることがあるので、呼び出し側は戻り値の image_id を使う。
    """
    with _ENRICH_LOCK:
        conn = connect()
        try:
            row = repo.get_image(conn, image_id)
        finally:
            conn.close()
        if row is None:
            return None
        sha1 = str(row["sha1"] or "")
        if not sha1.startswith(repo.PENDING_PREFIX):
            return {
                "image_id": int(row["id"]),
                "sha1": sha1,
                "filename": row["filename"],
                "already": True,
            }
        info = _index_one(int(row["folder_id"]), Path(row["path"]))
        if info is None:
            return None
        _migrate_thumbs(sha1, info["sha1"])
        return info


def enrich_pending(limit: int | None = None) -> tuple[int, int]:
    """仮の行を、更新日時の新しい順に解析する。(済んだ件数, 失敗した件数) を返す。

    失敗した行は消さず、この回の処理から外す (次の回で再び対象になる)。
    """
    failed: set[int] = set()
    seen: set[int] = set()
    done = 0
    while limit is None or done < limit:
        conn = connect()
        try:
            rows = repo.list_pending_images(conn, 20, failed)
        finally:
            conn.close()
        if not rows:
            break
        for r in rows:
            rid = int(r["id"])
            if rid in seen:  # 解析したのに仮のまま残る行で、同じ処理を繰り返さない
                failed.add(rid)
                continue
            seen.add(rid)
            info = enrich_image(rid)
            if info is None:
                failed.add(rid)
                continue
            done += 1
            if done % 100 == 0:
                log.info("背景の解析: %d 件済み", done)
            if limit is not None and done >= limit:
                break
        if len(failed) > 500:
            log.warning("背景の解析を打ち切ります: 失敗が %d 件を超えました", len(failed))
            break
    if done or failed:
        log.info("背景の解析が終わりました: %d 件 (失敗 %d 件)", done, len(failed))
    return done, len(failed)


def _rename_suspects(
    new_files: list[tuple[Path, int, float]],
    known: dict[str, tuple[Any, Any]],
    listed: set[str],
) -> list[tuple[Path, int, float]]:
    """改名の疑いのある新規ファイルを返す。

    登録に無い新規のうち、サイズが同じで更新日時の差が 0.001 秒未満の、
    「登録にあるが今回の列挙に無い」行がある (= 同じ内容が別の名前になった) もの。
    仮の行にすると旧行の id (タグ・メモ) の引き継ぎが掃除で失われるので、
    これらだけは掃除の前にその場で解析する (upsert_image の改名検出が id を付け替える)。
    """
    orphans: dict[int, list[float]] = {}
    for kp, (ks, km) in known.items():
        if kp in listed or ks is None or km is None:
            continue
        orphans.setdefault(int(ks), []).append(float(km))
    if not orphans:
        return []
    out = []
    for p, size, mtime in new_files:
        for km in orphans.get(size, ()):
            if abs(km - mtime) < 0.001:
                out.append((p, size, mtime))
                break
    return out


def full_scan(
    folder_id: int,
    folder_path: str,
    *,
    recursive: bool = True,
    full: bool = False,
    cleanup: bool = True,
    silent: bool = False,
) -> int:
    """指定フォルダを走査し、追加/更新件数を返す。

    新しいファイル (と、サイズか更新日時が変わったファイル) は、ハッシュと解析を待たず、
    フォルダ一覧を取った直後に「仮の行」(ファイル名・サイズ・更新日時・仮の sha1) として
    1 回の取引で登録する。ハッシュと解析は enrich_pending が背景で、更新日時の新しい順に行う。
    例外: 改名の疑いのある新規ファイルだけは、掃除の前にその場で解析する (_rename_suspects)。

    recursive=True: サブディレクトリ配下まで再帰スキャン (従来挙動)。
    recursive=False: 直下のファイルだけスキャン (サブフォルダは無視)。
    full=False (既定): DB に同じパスがあり、サイズと更新日時が同じファイルは何もしない。
      持ち主のフォルダは問わない。返り値は「処理した (追加または更新した) 件数」。
    full=True: 新規は仮の行にし、既存の全ファイルを更新日時の新しい順にその場で読み直す
      (中身だけ変わったファイルの救済。Shift+クリックの操作)。
    cleanup=False: ディスクから消えたファイルの記録を削除しない (定期の確認用)。
    silent=True: 進捗の通知を出さず、変化が無ければ完了の通知も出さない (定期の確認用)。

    進捗は `scan_progress` (phase = enumerate / index / cleanup) をスロットル emit し、
    完了時に `folder_rescanned` SSE イベントを emit する。
    """
    folder = Path(folder_path)
    if not folder.exists():
        # 定期の確認 (silent) は 30 秒ごとに来るので、警告を毎回出さない
        (log.debug if silent else log.warning)("監視フォルダが存在しません: %s", folder)
        # フォルダが消えていても、フロントが「再スキャン完了」状態に遷移できるよう通知する
        if not silent:
            manager._emit(ScanEvent(
                type="folder_rescanned",
                folder_id=folder_id,
                payload={"added_or_updated": 0, "missing_folder": True},
            ))
        return 0

    def progress(done: int, total: int, phase: str) -> None:
        if not silent:
            manager._emit(ScanEvent(
                type="scan_progress",
                folder_id=folder_id,
                payload={"done": done, "total": total, "phase": phase},
            ))

    # フェーズ 1: ファイル列挙。「列挙中 (total 未確定)」を最初に通知する。
    progress(0, 0, "enumerate")
    enum_errors: list[str] = []
    image_files = list(_iter_image_files(folder, recursive, enum_errors))
    total = len(image_files)
    listed = {str(p) for p, _size, _mtime in image_files}

    # 差分判定の辞書 (全フォルダのパス → (サイズ, 更新日時))。1 回だけ読む。
    conn = connect()
    try:
        known = repo.list_image_stat_by_path(conn)
    finally:
        conn.close()

    new_files: list[tuple[Path, int, float]] = []
    changed: list[tuple[Path, int, float]] = []
    skipped = 0
    for item in image_files:
        p, size, mtime = item
        k = known.get(str(p))
        if k is None:
            new_files.append(item)
        elif _is_unchanged(k, size, mtime):
            skipped += 1
        else:
            changed.append(item)

    suspects = _rename_suspects(new_files, known, listed)
    suspect_paths = {str(p) for p, _s, _m in suspects}
    stub_targets = new_files if full else new_files + changed
    stub_items = [t for t in stub_targets if str(t[0]) not in suspect_paths]

    # フェーズ 2: 仮の行を 1 回の取引で入れる (ここで一覧に出る)。
    if stub_items:
        conn = connect()
        try:
            repo.insert_stub_images(
                conn, [(folder_id, str(p), size, mtime) for p, size, mtime in stub_items]
            )
        finally:
            conn.close()
    count = len(stub_items)

    if not full:
        progress(count, count, "index")
        # 改名の疑い: 掃除の前に、更新日時の新しい順にその場で解析する
        for p, _size, _mtime in sorted(suspects, key=lambda t: t[2], reverse=True):
            if _index_one(folder_id, p) is not None:
                count += 1
    else:
        # 全件の読み直し: 仮の行にしたもの以外を、更新日時の新しい順にその場で処理する
        skipped = 0  # 全件を読み直すので、読み飛ばしは無い
        stubbed = {str(p) for p, _s, _m in stub_items}
        targets = sorted(
            (t for t in image_files if str(t[0]) not in stubbed),
            key=lambda t: t[2], reverse=True,
        )
        n = len(targets)
        progress(0, n, "index")
        last_emit = time.monotonic()
        for i, (p, _size, _mtime) in enumerate(targets, start=1):
            if _index_one(folder_id, p) is not None:
                count += 1
            # スロットル: 200ms or 最終件で必ず emit。
            now = time.monotonic()
            if now - last_emit >= 0.2 or i == n:
                progress(i, n, "index")
                last_emit = now

    # フェーズ 3: 削除掃除。
    # DB にあるけれど「ディスク上から実際に消えた」ファイルだけを掃除する。
    # ポイント:
    #   - recursive=True (再帰スキャン): 今回 listed に含まれなかった = ディスクから消えた、
    #     とみなして従来どおり削除。
    #   - recursive=False (直下のみ): サブフォルダ配下のレコードは「今回の対象外」だけで
    #     ファイル自体は実在する可能性が高い。ここで削除すると Myタグ / メモ /
    #     sort_order などのユーザーデータが連動削除されてしまうので、
    #     **DB レコードは保持する**。表示側 (search_images の direct_children_of)
    #     で folder 直下のレコードだけ拾うフィルタを掛けるので、グリッドからは
    #     自然に消える (再度 recursive=True に戻したら即復活する)。
    #     ただし「直下に該当ファイルがあったはずなのにディスクから消えた」ものは
    #     現状の挙動どおり削除する (= recursive=True と同じく実消失の整合性は取る)。
    #   - 安全弁: 列挙の件数が登録件数 (20 件以上) の半分未満なら、ドライブの不調と
    #     みなして削除しない。
    removed = 0
    if cleanup and enum_errors:
        log.warning(
            "列挙に失敗した場所があるため、削除をしません: folder_id=%s, 失敗 %d 件 (例: %s)",
            folder_id, len(enum_errors), enum_errors[0],
        )
    elif cleanup:
        progress(total, total, "cleanup")
        folder_norm = str(folder).rstrip("/\\")
        conn = connect()
        try:
            db_paths = repo.list_image_paths_in_folder(conn, folder_id)
            db_count = len(db_paths)
            if db_count >= CLEANUP_GUARD_MIN_ROWS and total < db_count * CLEANUP_GUARD_RATIO:
                log.warning(
                    "列挙の件数が登録の半分未満のため、削除をしません: "
                    "folder_id=%s, 列挙 %d 件, 登録 %d 件",
                    folder_id, total, db_count,
                )
            else:
                for db_path in db_paths:
                    if db_path in listed:
                        continue
                    if recursive:
                        repo.delete_image_by_path(conn, db_path)
                        removed += 1
                        continue
                    # recursive=False かつ listed 外: サブフォルダ配下は残す
                    p = Path(db_path)
                    parent_norm = str(p.parent).rstrip("/\\")
                    if parent_norm == folder_norm and not p.exists():
                        # 直下にあったがディスクから消えたファイル → 削除
                        repo.delete_image_by_path(conn, db_path)
                        removed += 1
        finally:
            conn.close()

    # プロンプトオートコンプリート用のキャッシュを次回参照時に再構築
    if count or removed:
        repo.invalidate_prompt_tag_index()

    quiet = silent and count == 0 and removed == 0
    (log.debug if quiet else log.info)(
        "フルスキャン完了: folder_id=%s, %d 件 (削除 %d 件, 読み飛ばし %d 件)",
        folder_id, count, removed, skipped,
    )

    # フロント (SSE 購読側) に再スキャン完了を通知。フロントは reloadImages() で
    # 一覧を取り直す。定期の確認 (silent) は、変化があったときだけ auto 付きで通知する。
    if not quiet:
        payload: dict[str, Any] = {"added_or_updated": count, "removed": removed}
        if silent:
            payload["auto"] = True
        manager._emit(ScanEvent(
            type="folder_rescanned",
            folder_id=folder_id,
            payload=payload,
        ))
    return count


def run_initial_scan() -> None:
    """起動時に、登録フォルダを 1 つずつ走査する (背景スレッドから呼ぶ)。

    各フォルダの recursive (直下のみの設定) を full_scan へ渡す。渡さないと、
    直下のみのフォルダでもサブフォルダまで読み、サブフォルダ側の走査と二重に処理してしまう。
    main.py ではなくここに置くのは、テストが main (本番の data/server.log へログを書き始める)
    を取り込まずに済むようにするため。
    全フォルダの走査の後に、仮の行の背景解析を起動する。
    """
    # 新着の確認 (refresh_all) と同時に走らないよう、同じ錠を持つ。
    with manager._refresh_lock:
        c = connect()
        try:
            for f in repo.list_folders(c):
                try:
                    full_scan(int(f["id"]), f["path"], recursive=bool(f["recursive"]))
                except Exception as e:
                    log.warning("起動時スキャン失敗: folder_id=%s (%s)", f["id"], e)
        finally:
            c.close()
    manager.kick_enrichment()


# ------------------------------------------------------------------
# Watchdog
# ------------------------------------------------------------------

class _Handler(FileSystemEventHandler):
    """1 フォルダ分の watchdog ハンドラ。"""

    def __init__(self, folder_id: int, manager: "ScannerManager") -> None:
        super().__init__()
        self.folder_id = folder_id
        self.manager = manager

    # 新規ファイル
    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if not _is_image(path):
            return
        # 書き込み完了を少し待つ (ComfyUI の保存遅延対策)
        self.manager.schedule_index(self.folder_id, path, delay=0.5)

    # ファイル削除
    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if not _is_image(path):
            return
        self.manager.schedule_remove(self.folder_id, path)

    # ファイル移動 / リネーム
    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = Path(event.src_path)
        dst = Path(event.dest_path)
        if _is_image(src):
            self.manager.schedule_remove(self.folder_id, src)
        if _is_image(dst):
            self.manager.schedule_index(self.folder_id, dst, delay=0.2)

    # 内容更新 (上書き保存)
    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if not _is_image(path):
            return
        self.manager.schedule_index(self.folder_id, path, delay=0.5)


# ------------------------------------------------------------------
# Scanner マネージャ (Observer + イベントキュー)
# ------------------------------------------------------------------

class ScannerManager:
    """登録フォルダの Observer 群を管理し、SSE 用のイベントキューに流す。"""

    def __init__(self) -> None:
        self._observers: dict[int, Observer] = {}  # folder_id -> Observer
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: list[asyncio.Queue[ScanEvent]] = []
        self._enrich_thread: threading.Thread | None = None
        self._enrich_guard = threading.Lock()
        # 新着の確認 (refresh_all) と起動時の走査 (run_initial_scan) が同時に走らないための錠
        self._refresh_lock = threading.Lock()
        self._refresh_thread: threading.Thread | None = None
        self._refresh_guard = threading.Lock()

    # 新着の確認 -----------------------------------------------------

    def ensure_observers(self) -> int:
        """監視 (Observer) が無い・死んでいるフォルダだけ、作り直す。起動し直した数を返す。

        watchdog の監視が黙って死ぬと新着が反映されなくなる (2026-11-05 の事象) ので、
        新着の確認のたびに見張る。実在しないフォルダは、警告を繰り返さず飛ばす。
        """
        conn = connect()
        try:
            folders = list(repo.list_folders(conn))
        finally:
            conn.close()
        started = 0
        for f in folders:
            fid = int(f["id"])
            with self._lock:
                obs = self._observers.get(fid)
                if obs is not None and obs.is_alive():
                    continue
                if obs is not None:
                    self._observers.pop(fid, None)
            if obs is not None:
                try:
                    obs.stop()
                except Exception:
                    pass
                log.warning("watchdog が止まっていたので作り直します: folder_id=%s", fid)
            if not Path(f["path"]).exists():
                continue
            rec = bool(f["recursive"]) if "recursive" in f.keys() else True
            self.start_folder(fid, f["path"], recursive=rec)
            with self._lock:
                if fid in self._observers:
                    started += 1
        return started

    def refresh_all(self) -> bool:
        """全フォルダの新着を確認する (数秒)。新しいファイルは仮の行として即登録する。

        別の確認や起動時の走査が動いているときは何もせず False を返す。
        掃除 (削除の反映) は監視に任せ、ここでは行わない (cleanup=False)。
        終わったら仮の行の背景解析を起動する。
        """
        if not self._refresh_lock.acquire(blocking=False):
            return False
        try:
            try:
                self.ensure_observers()
            except Exception as e:
                log.warning("監視の見張りに失敗: %s", e)
            conn = connect()
            try:
                folders = list(repo.list_folders(conn))
            finally:
                conn.close()
            for f in folders:
                try:
                    full_scan(
                        int(f["id"]), f["path"],
                        recursive=bool(f["recursive"]), cleanup=False, silent=True,
                    )
                except Exception as e:
                    log.warning("新着の確認に失敗: folder_id=%s (%s)", f["id"], e)
        finally:
            self._refresh_lock.release()
        self.kick_enrichment()
        return True

    def refresh_async(self) -> bool:
        """refresh_all を背景スレッド (名前 cio-refresh) で 1 本だけ起動する。

        すでに動いている (または起動時の走査の最中) なら起動せず False を返す。
        """
        with self._refresh_guard:
            t = self._refresh_thread
            if (t is not None and t.is_alive()) or self._refresh_lock.locked():
                return False

            def _run() -> None:
                try:
                    self.refresh_all()
                except Exception as e:
                    log.warning("新着の確認が止まりました: %s", e)

            t = threading.Thread(target=_run, name="cio-refresh", daemon=True)
            self._refresh_thread = t
            t.start()
            return True

    # 背景の解析 -----------------------------------------------------

    def kick_enrichment(self) -> None:
        """仮の行の背景解析スレッド (名前 cio-enrich) を、動いていなければ 1 本だけ起動する。"""
        with self._enrich_guard:
            t = self._enrich_thread
            if t is not None and t.is_alive():
                return

            def _run() -> None:
                try:
                    enrich_pending()
                except Exception as e:
                    log.warning("背景の解析が止まりました: %s", e)

            t = threading.Thread(target=_run, name="cio-enrich", daemon=True)
            self._enrich_thread = t
            t.start()

    # FastAPI lifespan から呼ばれる ----------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def start_all(self) -> None:
        """DB に登録済みの全フォルダを監視対象に追加する。"""
        conn = connect()
        try:
            for f in repo.list_folders(conn):
                rec = bool(f["recursive"]) if "recursive" in f.keys() else True
                self.start_folder(int(f["id"]), f["path"], recursive=rec)
        finally:
            conn.close()

    def stop_all(self) -> None:
        with self._lock:
            for obs in self._observers.values():
                try:
                    obs.stop()
                    obs.join(timeout=2)
                except Exception:
                    pass
            self._observers.clear()

    # フォルダ単位 -------------------------------------------------

    def start_folder(
        self, folder_id: int, folder_path: str, *, recursive: bool = True
    ) -> None:
        """フォルダを Observer に追加する (重複は無視)。

        recursive=False のときは watchdog も直下のみ監視するので、サブフォルダの
        変更はイベントとして上がらず DB にも反映されない (full_scan の挙動と一致)。
        """
        with self._lock:
            if folder_id in self._observers:
                return
            if not Path(folder_path).exists():
                log.warning("フォルダが存在しないので監視を開始しません: %s", folder_path)
                return
            obs = Observer()
            obs.schedule(_Handler(folder_id, self), folder_path, recursive=recursive)
            obs.daemon = True
            obs.start()
            self._observers[folder_id] = obs
            log.info(
                "watchdog 開始: folder_id=%s, path=%s, recursive=%s",
                folder_id, folder_path, recursive,
            )

    def stop_folder(self, folder_id: int) -> None:
        with self._lock:
            obs = self._observers.pop(folder_id, None)
        if obs:
            try:
                obs.stop()
                obs.join(timeout=2)
            except Exception:
                pass

    # スケジューリング ----------------------------------------------

    def schedule_index(self, folder_id: int, path: Path, delay: float) -> None:
        """別スレッドからファイルを再インデックスし、結果を SSE に流す。"""
        def _task() -> None:
            try:
                time.sleep(delay)
                if not path.exists():
                    return
                info = _index_one(folder_id, path)
                if info is None:
                    return
                self._emit(ScanEvent(
                    type="image_added",
                    folder_id=folder_id,
                    image_id=info["image_id"],
                    payload={"filename": info["filename"], "sha1": info["sha1"]},
                ))
            except Exception as e:
                log.warning("watchdog インデックス失敗: %s (%s)", path, e)
        threading.Thread(target=_task, daemon=True).start()

    def schedule_remove(self, folder_id: int, path: Path) -> None:
        def _task() -> None:
            try:
                conn = connect()
                try:
                    row = conn.execute(
                        "SELECT id FROM images WHERE path = ?", (str(path),)
                    ).fetchone()
                    if row is None:
                        return
                    image_id = int(row["id"])
                    repo.delete_image_by_path(conn, str(path))
                finally:
                    conn.close()
                # プロンプトオートコンプリート用のキャッシュを次回参照時に再構築
                repo.invalidate_prompt_tag_index()
                self._emit(ScanEvent(
                    type="image_removed",
                    folder_id=folder_id,
                    image_id=image_id,
                    payload={"path": str(path)},
                ))
            except Exception as e:
                log.warning("watchdog 削除反映失敗: %s (%s)", path, e)
        threading.Thread(target=_task, daemon=True).start()

    # SSE 配信 -----------------------------------------------------

    def subscribe(self) -> asyncio.Queue[ScanEvent]:
        q: asyncio.Queue[ScanEvent] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[ScanEvent]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _emit(self, event: ScanEvent) -> None:
        loop = self._loop
        if loop is None:
            return
        for q in list(self._subscribers):
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except Exception:
                # キュー満杯などは諦める (UI 側は再読み込みで復旧可能)
                pass


# プロセス全体で共有する Singleton (FastAPI lifespan で使う)
manager = ScannerManager()
