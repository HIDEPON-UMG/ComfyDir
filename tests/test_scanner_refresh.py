"""自動更新（新着の確認・監視の見張り）の契約テスト。

作業定義（計画 comfydir-instant-fresh の作業 2-01）
- 目的：アプリを開いている間、新しい画像が手動の再スキャン無しで先頭に出るよう、
  サーバー側に「新着の確認」(refresh_all) と「監視の見張り」(ensure_observers) を置く。
- 完了条件：このファイルの全件が通る。実装前は全件が失敗する（赤）。
- 検証方法：`.venv\\Scripts\\python.exe -m pytest tests/test_scanner_refresh.py -q`。
  一時データベースと一時フォルダだけを使い、本番の `data/` と画像フォルダには触れない。
"""
from __future__ import annotations

import inspect
import os
import threading
from pathlib import Path

import pytest
from PIL import Image

from comfy_image_organizer import db, repo, routes, scanner, thumbnail

ROUTES_TEXT = Path(routes.__file__).read_text(encoding="utf-8")


def _make_png(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path)


class FakeObserver:
    """watchdog.Observer の代わり。起動と停止の状態だけを持つ。"""

    instances: list["FakeObserver"] = []

    def __init__(self) -> None:
        self.daemon = False
        self.started = False
        self._alive = False
        self.scheduled: list[tuple[str, bool]] = []
        FakeObserver.instances.append(self)

    def schedule(self, handler, path, recursive=False) -> None:
        self.scheduled.append((str(path), bool(recursive)))

    def start(self) -> None:
        self.started = True
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def stop(self) -> None:
        self._alive = False

    def join(self, timeout=None) -> None:
        return None


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.sqlite")
    conn = db.connect()
    db.init_schema(conn)
    conn.close()
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    monkeypatch.setattr(thumbnail, "THUMB_DIR", thumbs)
    FakeObserver.instances.clear()
    monkeypatch.setattr(scanner, "Observer", FakeObserver)
    mgr = scanner.ScannerManager()
    monkeypatch.setattr(scanner, "manager", mgr)
    return mgr


def _add_folder(path: Path, *, recursive: bool = True) -> int:
    conn = db.connect()
    try:
        return int(repo.add_folder(conn, str(path), None, recursive=recursive)["id"])
    finally:
        conn.close()


# ---------------------------------------------------------------- (a) 監視の見張り

def test_a_ensure_observers_starts_missing_and_recreates_dead(tmp_path: Path, env) -> None:
    r1 = tmp_path / "f1"
    r2 = tmp_path / "f2"
    r1.mkdir()
    r2.mkdir()
    f1 = _add_folder(r1)
    f2 = _add_folder(r2, recursive=False)

    started = env.ensure_observers()
    assert started == 2
    assert len(FakeObserver.instances) == 2
    by_path = {i.scheduled[0][0]: i for i in FakeObserver.instances}
    assert by_path[str(r1)].scheduled[0][1] is True
    assert by_path[str(r2)].scheduled[0][1] is False

    # 生きている間は何も作らない
    assert env.ensure_observers() == 0
    assert len(FakeObserver.instances) == 2

    # 1 本が死んだら、そのフォルダだけ作り直す
    by_path[str(r2)].stop()
    assert env.ensure_observers() == 1
    assert len(FakeObserver.instances) == 3
    assert FakeObserver.instances[-1].scheduled[0][0] == str(r2)
    assert f1 != f2


def test_a2_ensure_observers_skips_missing_folder(tmp_path: Path, env) -> None:
    gone = tmp_path / "gone"
    gone.mkdir()
    _add_folder(gone)
    gone.rmdir()
    assert env.ensure_observers() == 0
    assert FakeObserver.instances == []


# ---------------------------------------------------------------- (b)(c) 新着の確認

def test_b_refresh_all_scans_every_folder_silently_then_kicks(
    tmp_path: Path, env, monkeypatch: pytest.MonkeyPatch
) -> None:
    r1 = tmp_path / "f1"
    r2 = tmp_path / "f2"
    r1.mkdir()
    r2.mkdir()
    f1 = _add_folder(r1)
    f2 = _add_folder(r2, recursive=False)

    calls: list[tuple] = []

    def fake_full_scan(folder_id, folder_path, **kw):
        calls.append((folder_id, folder_path, kw))
        return 0

    kicks: list[int] = []
    monkeypatch.setattr(scanner, "full_scan", fake_full_scan)
    monkeypatch.setattr(env, "kick_enrichment", lambda: kicks.append(1))

    assert env.refresh_all() is True
    assert [c[0] for c in calls] == [f1, f2]
    for _fid, _path, kw in calls:
        assert kw.get("cleanup") is False
        assert kw.get("silent") is True
    assert calls[0][2]["recursive"] is True
    assert calls[1][2]["recursive"] is False
    assert kicks == [1]
    # 監視も同時に見張る
    assert len(FakeObserver.instances) == 2


def test_c_refresh_all_returns_false_while_another_refresh_runs(
    tmp_path: Path, env, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "f"
    root.mkdir()
    _add_folder(root)
    monkeypatch.setattr(scanner, "full_scan", lambda *a, **k: 0)
    monkeypatch.setattr(env, "kick_enrichment", lambda: None)

    assert env._refresh_lock.acquire(blocking=False)
    try:
        assert env.refresh_all() is False
    finally:
        env._refresh_lock.release()
    assert env.refresh_all() is True


def test_c2_refresh_all_survives_one_folder_failing(
    tmp_path: Path, env, monkeypatch: pytest.MonkeyPatch
) -> None:
    r1 = tmp_path / "f1"
    r2 = tmp_path / "f2"
    r1.mkdir()
    r2.mkdir()
    _add_folder(r1)
    f2 = _add_folder(r2)
    seen: list[int] = []

    def fake_full_scan(folder_id, folder_path, **kw):
        seen.append(folder_id)
        if len(seen) == 1:
            raise OSError("boom")
        return 0

    monkeypatch.setattr(scanner, "full_scan", fake_full_scan)
    monkeypatch.setattr(env, "kick_enrichment", lambda: None)
    assert env.refresh_all() is True
    assert seen[-1] == f2 and len(seen) == 2


def test_c3_refresh_task_runs_in_background_and_is_single_flight(
    tmp_path: Path, env, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "f"
    root.mkdir()
    _add_folder(root)
    gate = threading.Event()
    entered = threading.Event()

    def slow_full_scan(*a, **k):
        entered.set()
        gate.wait(5)
        return 0

    monkeypatch.setattr(scanner, "full_scan", slow_full_scan)
    monkeypatch.setattr(env, "kick_enrichment", lambda: None)

    assert env.refresh_async() is True
    assert entered.wait(5)
    assert env.refresh_async() is False  # 動いている間は 2 本目を起動しない
    gate.set()
    t = env._refresh_thread
    assert t is not None
    t.join(5)
    assert not t.is_alive()
    assert env.refresh_async() is True
    env._refresh_thread.join(5)


# ---------------------------------------------------------------- (d) 起動時の走査も同じ錠

def test_d_run_initial_scan_holds_the_refresh_lock(
    tmp_path: Path, env, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "f"
    root.mkdir()
    _add_folder(root)
    held: list[bool] = []

    def fake_full_scan(folder_id, folder_path, **kw):
        # 走査の最中は、新着の確認が割り込めない
        held.append(not env._refresh_lock.acquire(blocking=False))
        return 0

    monkeypatch.setattr(scanner, "full_scan", fake_full_scan)
    monkeypatch.setattr(env, "kick_enrichment", lambda: None)
    scanner.run_initial_scan()
    assert held == [True]
    # 終わったら錠は空く
    assert env._refresh_lock.acquire(blocking=False)
    env._refresh_lock.release()


# ---------------------------------------------------------------- (e) 詳細は仮の行をその場で解析

def test_e_get_image_detail_enriches_a_stub_row(
    tmp_path: Path, env, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "img"
    _make_png(root / "x.png", (10, 20, 30))
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM images").fetchone()
        assert row["sha1"].startswith("pending-")
        image_id = int(row["id"])
        detail = routes.get_image_detail(image_id, conn=conn)
    finally:
        conn.close()
    assert not detail["sha1"].startswith("pending-")
    assert len(detail["sha1"]) == 40
    assert detail["width"] == 8 and detail["height"] == 8


def test_e2_get_image_detail_leaves_real_row_alone(
    tmp_path: Path, env, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "img"
    _make_png(root / "x.png", (10, 20, 30))
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    scanner.enrich_pending()
    calls: list[int] = []
    real = scanner.enrich_image
    monkeypatch.setattr(scanner, "enrich_image", lambda i: (calls.append(i), real(i))[1])
    conn = db.connect()
    try:
        image_id = int(conn.execute("SELECT id FROM images").fetchone()["id"])
        routes.get_image_detail(image_id, conn=conn)
    finally:
        conn.close()
    assert calls == []


# ---------------------------------------------------------------- (f) 経路の文字列契約

def test_f_routes_expose_refresh_and_kick_enrichment_after_rescan() -> None:
    assert '"/api/refresh"' in ROUTES_TEXT
    assert "scanner.manager.refresh_async" in ROUTES_TEXT
    src = inspect.getsource(routes.rescan_folder)
    assert "kick_enrichment" in src


def test_f2_refresh_route_returns_started_flag() -> None:
    fn = getattr(routes, "refresh_images", None)
    assert fn is not None
    assert inspect.signature(fn).parameters == {}
