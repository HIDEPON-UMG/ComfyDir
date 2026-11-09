"""仮登録（開いた瞬間に最新が出る）と、新しい順の背景解析の契約テスト。

作業定義（計画 comfydir-instant-fresh の作業 1-01）
- 目的：新しいファイルを、フォルダ一覧の取得の直後に仮の行として一覧へ出し、
  重いハッシュと解析を背景で更新日時の新しい順に行う。
- 完了条件：このファイルの全件が通る。実装前は全件が失敗する（赤）。
- 検証方法：`.venv\\Scripts\\python.exe -m pytest tests/test_scanner_instant.py -q`。
  一時データベースと一時フォルダだけを使い、本番の `data/` と画像フォルダには触れない。
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import pytest
from PIL import Image

from comfy_image_organizer import db, repo, scanner, thumbnail

HEX40 = re.compile(r"^[0-9a-f]{40}$")


# ---------------------------------------------------------------- 準備

def _make_png(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path)


class Calls:
    def __init__(self) -> None:
        self.sha1_paths: list[str] = []
        self.extract = 0
        self.events: list[scanner.ScanEvent] = []

    @property
    def sha1(self) -> int:
        return len(self.sha1_paths)

    def reset(self) -> None:
        self.sha1_paths.clear()
        self.extract = 0
        self.events.clear()


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Calls:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.sqlite")
    conn = db.connect()
    db.init_schema(conn)
    conn.close()
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    monkeypatch.setattr(thumbnail, "THUMB_DIR", thumbs)

    calls = Calls()
    real_sha1 = scanner.sha1_of
    real_extract = scanner.comfy_prompt.extract_from_file

    def sha1_recording(path, *a, **k):
        calls.sha1_paths.append(str(path))
        return real_sha1(path, *a, **k)

    def extract_counting(path, *a, **k):
        calls.extract += 1
        return real_extract(path, *a, **k)

    monkeypatch.setattr(scanner, "sha1_of", sha1_recording)
    monkeypatch.setattr(scanner.comfy_prompt, "extract_from_file", extract_counting)
    monkeypatch.setattr(scanner.manager, "_emit", lambda ev: calls.events.append(ev))
    return calls


def _add_folder(path: Path, *, recursive: bool = True) -> int:
    conn = db.connect()
    try:
        return int(repo.add_folder(conn, str(path), None, recursive=recursive)["id"])
    finally:
        conn.close()


def _rows(folder_id: int | None = None) -> list[dict]:
    conn = db.connect()
    try:
        sql = "SELECT * FROM images"
        args: tuple = ()
        if folder_id is not None:
            sql += " WHERE folder_id = ?"
            args = (folder_id,)
        return [dict(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]
    finally:
        conn.close()


def _by_name(folder_id: int | None = None) -> dict[str, dict]:
    return {r["filename"]: r for r in _rows(folder_id)}


def _three(root: Path, *, spread: bool = True) -> list[Path]:
    """a が最古、c が最新になるよう更新日時を 10・20・30 秒ずらして作る。"""
    paths = [root / "a.png", root / "b.png", root / "c.png"]
    for i, p in enumerate(paths):
        _make_png(p, (i * 60, 10, 200 - i * 60))
    if spread:
        base = 1_700_000_000
        for i, p in enumerate(paths):
            os.utime(p, (base + (i + 1) * 10, base + (i + 1) * 10))
    return paths


# ---------------------------------------------------------------- (a)(b) 仮登録

def test_a_first_scan_registers_stubs_without_hashing(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    _three(root)
    fid = _add_folder(root)
    assert scanner.full_scan(fid, str(root)) == 3
    rows = _rows(fid)
    assert len(rows) == 3
    shas = [r["sha1"] for r in rows]
    assert all(s.startswith("pending-") for s in shas)
    assert len(set(shas)) == 3
    assert all(r["width"] is None for r in rows)
    assert env.sha1 == 0 and env.extract == 0


def test_b_second_scan_changes_nothing(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    assert scanner.full_scan(fid, str(root)) == 0
    assert len(_rows(fid)) == 3


# ---------------------------------------------------------------- (c)(m) 新しい順

def test_c_enrich_pending_runs_newest_first(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    paths = _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    assert scanner.enrich_pending() == (3, 0)
    for r in _rows(fid):
        assert HEX40.match(r["sha1"])
        assert r["width"] == 8 and r["height"] == 8
    assert env.sha1_paths == [str(paths[2]), str(paths[1]), str(paths[0])]


def test_m_list_pending_images_orders_by_mtime_desc_and_excludes(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    paths = _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    conn = db.connect()
    try:
        got = repo.list_pending_images(conn, 10)
        assert [r["path"] for r in got] == [str(paths[2]), str(paths[1]), str(paths[0])]
        skip = int(got[0]["id"])
        got2 = repo.list_pending_images(conn, 10, exclude_ids=(skip,))
        assert [r["path"] for r in got2] == [str(paths[1]), str(paths[0])]
    finally:
        conn.close()


# ---------------------------------------------------------------- (d) 変更されたファイル

def test_d_changed_file_keeps_prompt_and_gets_stub_sha1(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    paths = _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    scanner.enrich_pending()
    conn = db.connect()
    try:
        conn.execute("UPDATE images SET positive_prompt = 'keep me' WHERE path = ?", (str(paths[1]),))
    finally:
        conn.close()
    env.reset()

    st = paths[1].stat()
    _make_png(paths[1], (250, 250, 0))
    os.utime(paths[1], (st.st_atime, st.st_mtime + 5))

    assert scanner.full_scan(fid, str(root)) == 1
    row = _by_name(fid)["b.png"]
    assert row["sha1"].startswith("pending-")
    assert row["positive_prompt"] == "keep me"
    assert env.sha1 == 0

    assert scanner.enrich_pending() == (1, 0)
    assert HEX40.match(_by_name(fid)["b.png"]["sha1"])
    assert env.sha1 == 1


# ---------------------------------------------------------------- (e) 縮小画像の改名

def test_e_thumbnail_cache_is_migrated_from_stub_to_real_name(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    row = _by_name(fid)["c.png"]
    stub = row["sha1"]
    stub_file = thumbnail.THUMB_DIR / f"{stub}_192.webp"
    stub_file.write_bytes(b"x")

    info = scanner.enrich_image(int(row["id"]))
    assert info is not None
    real = info["sha1"]
    assert HEX40.match(real)
    assert (thumbnail.THUMB_DIR / f"{real}_192.webp").exists()
    assert not stub_file.exists()


def test_e2_existing_destination_wins_and_stub_is_removed(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    row = _by_name(fid)["c.png"]
    stub_file = thumbnail.THUMB_DIR / f"{row['sha1']}_192.webp"
    stub_file.write_bytes(b"stub")
    real = scanner.sha1_of(root / "c.png")
    dest = thumbnail.THUMB_DIR / f"{real}_192.webp"
    dest.write_bytes(b"real")

    scanner.enrich_image(int(row["id"]))
    assert dest.read_bytes() == b"real"
    assert not stub_file.exists()


# ---------------------------------------------------------------- (f) 改名

def test_f_renamed_file_keeps_row_id_without_a_stub(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    paths = _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    scanner.enrich_pending()
    before = _by_name(fid)["b.png"]
    env.reset()

    new_path = root / "renamed.png"
    os.rename(paths[1], new_path)

    scanner.full_scan(fid, str(root))  # 掃除あり
    rows = _rows(fid)
    assert len(rows) == 3
    after = _by_name(fid)["renamed.png"]
    assert int(after["id"]) == int(before["id"])
    assert after["path"] == str(new_path)
    assert not after["sha1"].startswith("pending-")
    assert env.sha1 == 1
    assert scanner.enrich_pending() == (0, 0)


# ---------------------------------------------------------------- (g) 失敗

def test_g_missing_file_is_counted_as_failure_not_deleted(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    paths = _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    paths[2].unlink()
    assert scanner.enrich_pending() == (2, 1)
    assert len(_rows(fid)) == 3


# ---------------------------------------------------------------- (h) 仮の行の挿入

def test_h_insert_stub_images_is_idempotent_and_atomic(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    fid = _add_folder(root)
    row = (fid, str(root / "x.png"), 10, 1_700_000_000.0)
    conn = db.connect()
    try:
        assert repo.insert_stub_images(conn, [row]) == 1
        assert repo.insert_stub_images(conn, [row]) == 1
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1
        bad = [(fid, str(root / "y.png"), 10, 1.0), (fid, None, 10, 1.0)]
        with pytest.raises(Exception):
            repo.insert_stub_images(conn, bad)
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------- (i) 掃除の安全弁

def test_i_cleanup_guard_skips_deletion_when_listing_is_tiny(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    root.mkdir()
    fid = _add_folder(root)
    conn = db.connect()
    try:
        repo.insert_stub_images(
            conn, [(fid, str(root / f"gone{i}.png"), 10, 1_700_000_000.0 + i) for i in range(30)]
        )
    finally:
        conn.close()
    scanner.full_scan(fid, str(root))
    assert len(_rows(fid)) == 30  # 列挙 0 件 < 30 の半分 → 削除しない


def test_i2_cleanup_still_deletes_below_the_guard_minimum(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    root.mkdir()
    fid = _add_folder(root)
    conn = db.connect()
    try:
        repo.insert_stub_images(
            conn, [(fid, str(root / f"gone{i}.png"), 10, 1_700_000_000.0 + i) for i in range(10)]
        )
    finally:
        conn.close()
    scanner.full_scan(fid, str(root))
    assert len(_rows(fid)) == 0


# ---------------------------------------------------------------- (j) cleanup=False

def test_j_cleanup_flag(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    paths = _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    paths[0].unlink()
    scanner.full_scan(fid, str(root), cleanup=False)
    assert len(_rows(fid)) == 3
    scanner.full_scan(fid, str(root), cleanup=True)
    assert len(_rows(fid)) == 2


# ---------------------------------------------------------------- (k) 列挙の失敗では掃除しない
# 監査役の指摘（中）：サブフォルダが一時的に開けないだけで、その配下の記録（タグ・メモ・並び順も連動）が
# 消えないこと。登録が 20 件未満でも、列挙に 1 件でも失敗があれば、その回は掃除をしない。

def _scandir_failing_on(monkeypatch: pytest.MonkeyPatch, bad_suffixes: tuple[str, ...]) -> None:
    real = os.scandir

    def fake(path="."):
        if str(path).endswith(bad_suffixes):
            raise PermissionError(13, "denied", str(path))
        return real(path)

    monkeypatch.setattr(scanner.os, "scandir", fake)


def test_k_iter_reports_enumeration_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "img"
    _make_png(root / "a.png", (1, 2, 3))
    _make_png(root / "sub" / "b.png", (4, 5, 6))
    errors: list[str] = []
    assert len(list(scanner._iter_image_files(root, True, errors))) == 2
    assert errors == []
    _scandir_failing_on(monkeypatch, ("sub",))
    errors = []
    names = [p.name for p, _s, _m in scanner._iter_image_files(root, True, errors)]
    assert names == ["a.png"]
    assert len(errors) == 1


def test_k2_unreadable_subfolder_keeps_its_records(
    tmp_path: Path, env: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "img"
    _make_png(root / "a.png", (1, 2, 3))
    _make_png(root / "sub" / "b.png", (4, 5, 6))
    _make_png(root / "sub" / "c.png", (7, 8, 9))
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    assert len(_rows(fid)) == 3
    with monkeypatch.context() as m:
        _scandir_failing_on(m, ("sub",))
        scanner.full_scan(fid, str(root))  # sub が開けない回：掃除をしない
    assert len(_rows(fid)) == 3
    (root / "a.png").unlink()
    scanner.full_scan(fid, str(root))  # 失敗が無い回：実際に消えたものは従来どおり掃除する
    assert sorted(_by_name(fid)) == ["b.png", "c.png"]


def test_k3_unreadable_root_keeps_all_records(
    tmp_path: Path, env: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "img"
    _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    assert len(_rows(fid)) == 3
    with monkeypatch.context() as m:
        _scandir_failing_on(m, ("img",))
        scanner.full_scan(fid, str(root))  # 登録 3 件（20 件未満）でも、列挙の失敗では消さない
    assert len(_rows(fid)) == 3


# ---------------------------------------------------------------- (k) silent

def test_k_silent_scan_is_quiet_unless_something_changed(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    env.events.clear()

    scanner.full_scan(fid, str(root), cleanup=False, silent=True)
    assert env.events == []

    _make_png(root / "d.png", (9, 9, 9))
    scanner.full_scan(fid, str(root), cleanup=False, silent=True)
    types = [e.type for e in env.events]
    assert types == ["folder_rescanned"]
    assert env.events[0].payload.get("auto") is True
    assert env.events[0].payload.get("added_or_updated") == 1


# ---------------------------------------------------------------- (l) full=True

def test_l_full_reads_everything_newest_first(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    paths = _three(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    scanner.enrich_pending()
    env.reset()
    assert scanner.full_scan(fid, str(root), full=True) == 3
    assert env.sha1_paths == [str(paths[2]), str(paths[1]), str(paths[0])]


def test_l2_full_stubs_new_files_only(tmp_path: Path, env: Calls) -> None:
    root = tmp_path / "img"
    _three(root, spread=True)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    scanner.enrich_pending()
    _make_png(root / "new.png", (1, 2, 3))
    env.reset()
    scanner.full_scan(fid, str(root), full=True)
    # 既存 3 枚は読み直し（3 回）、新規 1 枚は仮の行のまま
    assert env.sha1 == 3
    assert _by_name(fid)["new.png"]["sha1"].startswith("pending-")


# ---------------------------------------------------------------- (n)(o) 背景処理の起動

def test_n_run_initial_scan_kicks_enrichment_once(tmp_path: Path, env: Calls, monkeypatch) -> None:
    _add_folder(tmp_path / "a")
    kicks: list[int] = []
    monkeypatch.setattr(scanner.manager, "kick_enrichment", lambda: kicks.append(1))
    monkeypatch.setattr(scanner, "full_scan", lambda *a, **k: 0)
    scanner.run_initial_scan()
    assert kicks == [1]


def test_o_kick_enrichment_keeps_a_single_thread(tmp_path: Path, env: Calls, monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    runs: list[int] = []

    def slow_enrich(limit=None):
        runs.append(1)
        started.set()
        release.wait(5)
        return (0, 0)

    monkeypatch.setattr(scanner, "enrich_pending", slow_enrich)
    mgr = scanner.ScannerManager()
    mgr.kick_enrichment()
    assert started.wait(5)
    mgr.kick_enrichment()
    release.set()
    t = mgr._enrich_thread
    assert t is not None
    t.join(5)
    assert runs == [1]
