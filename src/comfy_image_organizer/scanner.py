"""フォルダのフルスキャンと watchdog による継続監視。

- アプリ起動時に登録フォルダを 1 回フルスキャン
- watchdog Observer を起動し、新規/削除/移動 (リネーム) イベントで DB を更新
- 変更内容は asyncio.Queue 経由で SSE エンドポイントに流す
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from . import comfy_prompt, repo
from .config import IMAGE_EXTS
from .db import connect

log = logging.getLogger(__name__)


@dataclass
class ScanEvent:
    """SSE で流すイベント。"""
    type: str           # 'image_added' | 'image_removed' | 'image_updated' | 'folder_rescanned'
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


def full_scan(folder_id: int, folder_path: str) -> int:
    """指定フォルダを 1 回フルスキャンし、追加/更新件数を返す。

    完了時に `folder_rescanned` SSE イベントを emit する。watchdog の取り逃がし分
    (再スキャン時に増減した画像) をフロントに通知し、UI のグリッドを reload させる
    ためのフック。
    """
    folder = Path(folder_path)
    if not folder.exists():
        log.warning("監視フォルダが存在しません: %s", folder)
        # フォルダが消えていても、フロントが「再スキャン完了」状態に遷移できるよう通知する
        manager._emit(ScanEvent(
            type="folder_rescanned",
            folder_id=folder_id,
            payload={"added_or_updated": 0, "missing_folder": True},
        ))
        return 0

    # 現在ディスク上にあるファイル
    on_disk: set[str] = set()
    count = 0
    for p in folder.rglob("*"):
        if p.is_file() and _is_image(p):
            on_disk.add(str(p))
            if _index_one(folder_id, p) is not None:
                count += 1

    # DB にあるけれど消えたファイルを掃除
    removed = 0
    conn = connect()
    try:
        for db_path in repo.list_image_paths_in_folder(conn, folder_id):
            if db_path not in on_disk:
                repo.delete_image_by_path(conn, db_path)
                removed += 1
    finally:
        conn.close()

    # プロンプトオートコンプリート用のキャッシュを次回参照時に再構築
    repo.invalidate_prompt_tag_index()

    log.info(
        "フルスキャン完了: folder_id=%s, %d 件 (削除 %d 件)",
        folder_id, count, removed,
    )

    # フロント (SSE 購読側) に再スキャン完了を通知。フロントは reloadImages() で
    # 一覧を取り直す
    manager._emit(ScanEvent(
        type="folder_rescanned",
        folder_id=folder_id,
        payload={"added_or_updated": count, "removed": removed},
    ))
    return count


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

    # FastAPI lifespan から呼ばれる ----------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def start_all(self) -> None:
        """DB に登録済みの全フォルダを監視対象に追加する。"""
        conn = connect()
        try:
            for f in repo.list_folders(conn):
                self.start_folder(int(f["id"]), f["path"])
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

    def start_folder(self, folder_id: int, folder_path: str) -> None:
        """フォルダを Observer に追加する (重複は無視)。"""
        with self._lock:
            if folder_id in self._observers:
                return
            if not Path(folder_path).exists():
                log.warning("フォルダが存在しないので監視を開始しません: %s", folder_path)
                return
            obs = Observer()
            obs.schedule(_Handler(folder_id, self), folder_path, recursive=True)
            obs.daemon = True
            obs.start()
            self._observers[folder_id] = obs
            log.info("watchdog 開始: folder_id=%s, path=%s", folder_id, folder_path)

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
