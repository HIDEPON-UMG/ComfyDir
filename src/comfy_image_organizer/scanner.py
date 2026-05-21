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


def full_scan(folder_id: int, folder_path: str, *, recursive: bool = True) -> int:
    """指定フォルダを 1 回フルスキャンし、追加/更新件数を返す。

    recursive=True: rglob でサブディレクトリ配下まで再帰スキャン (従来挙動)。
    recursive=False: glob で直下のファイルだけスキャン (サブフォルダは無視)。

    進捗は `scan_progress` (phase = enumerate / index / cleanup) を 200ms 間隔で
    スロットル emit し、フロントの determinate progress bar に流す。
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

    # フェーズ 1: ファイル列挙。大量フォルダだと rglob だけで数秒かかるので、
    # 「列挙中 (total 未確定)」を最初に通知して indeterminate 表示にさせる。
    manager._emit(ScanEvent(
        type="scan_progress",
        folder_id=folder_id,
        payload={"done": 0, "total": 0, "phase": "enumerate"},
    ))
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    image_paths: list[Path] = [p for p in iterator if p.is_file() and _is_image(p)]
    total = len(image_paths)

    # フェーズ 2: インデックス。total 確定 → determinate モードへ遷移。
    manager._emit(ScanEvent(
        type="scan_progress",
        folder_id=folder_id,
        payload={"done": 0, "total": total, "phase": "index"},
    ))

    on_disk: set[str] = set()
    count = 0
    last_emit = time.monotonic()
    for i, p in enumerate(image_paths, start=1):
        on_disk.add(str(p))
        if _index_one(folder_id, p) is not None:
            count += 1
        # スロットル: 200ms or 最終件で必ず emit。
        # 全件ごとに emit すると SSE/JSON で帯域を食うので頻度を絞る。
        now = time.monotonic()
        if now - last_emit >= 0.2 or i == total:
            manager._emit(ScanEvent(
                type="scan_progress",
                folder_id=folder_id,
                payload={"done": i, "total": total, "phase": "index"},
            ))
            last_emit = now

    # フェーズ 3: 削除掃除。件数が予想できないので indeterminate 風に再度通知。
    manager._emit(ScanEvent(
        type="scan_progress",
        folder_id=folder_id,
        payload={"done": total, "total": total, "phase": "cleanup"},
    ))

    # DB にあるけれど「ディスク上から実際に消えた」ファイルだけを掃除する。
    # ポイント:
    #   - recursive=True (再帰スキャン): 今回 on_disk に含まれなかった = ディスクから消えた、
    #     とみなして従来どおり削除。
    #   - recursive=False (直下のみ): サブフォルダ配下のレコードは「今回の対象外」だけで
    #     ファイル自体は実在する可能性が高い。ここで削除すると Myタグ / メモ /
    #     sort_order などのユーザーデータが連動削除されてしまうので、
    #     **DB レコードは保持する**。表示側 (search_images の direct_children_of)
    #     で folder 直下のレコードだけ拾うフィルタを掛けるので、グリッドからは
    #     自然に消える (再度 recursive=True に戻したら即復活する)。
    #     ただし「直下に該当ファイルがあったはずなのにディスクから消えた」ものは
    #     現状の挙動どおり削除する (= recursive=True と同じく実消失の整合性は取る)。
    folder_norm = str(folder).rstrip("/\\")
    removed = 0
    conn = connect()
    try:
        for db_path in repo.list_image_paths_in_folder(conn, folder_id):
            if db_path in on_disk:
                continue
            if recursive:
                repo.delete_image_by_path(conn, db_path)
                removed += 1
                continue
            # recursive=False かつ on_disk 外: サブフォルダ配下は残す
            p = Path(db_path)
            parent_norm = str(p.parent).rstrip("/\\")
            if parent_norm == folder_norm and not p.exists():
                # 直下にあったがディスクから消えたファイル → 削除
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
