"""差分走査（scanner.full_scan の読み飛ばし）と、その入口の契約テスト。

作業定義（工程 2 の作業 2-01）
- 目的：変更のないファイル（サイズと更新日時が同じ）を読み飛ばし、起動時と再スキャンの
  所要時間を、件数に比例する重い処理（ハッシュ・解析・登録）から、ファイル一覧の取得だけにする。
  起動時の処理が「直下のみ」の設定に従い、サブフォルダの二重処理が起きないようにする。
- 完了条件：このファイルの全件が通る。実装前は (b)(c)(e)(g)(h)(i)(j)(k)(l)(m) の 10 件が
  失敗する（赤）。(a)(d)(f) は従来の挙動の保全なので実装前から通る。
- 検証方法：`.venv\\Scripts\\python.exe -m pytest tests/test_scanner_differential.py -q`。
  一時データベースと一時フォルダだけを使い、本番の `data/` と画像フォルダには触れない。
"""
from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

import pytest
from PIL import Image

from comfy_image_organizer import db, repo, routes, scanner

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "comfy_image_organizer"
STATIC = PKG / "static"


# ---------------------------------------------------------------- 準備

def _make_png(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path)


class Counters:
    def __init__(self) -> None:
        self.sha1 = 0
        self.extract = 0

    def reset(self) -> None:
        self.sha1 = 0
        self.extract = 0


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """一時データベースへ差し替え、重い 2 つの処理の呼び出し回数を数える。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.sqlite")
    conn = db.connect()
    db.init_schema(conn)
    conn.close()

    counters = Counters()
    real_sha1 = scanner.sha1_of
    real_extract = scanner.comfy_prompt.extract_from_file

    def sha1_counting(path, *a, **k):
        counters.sha1 += 1
        return real_sha1(path, *a, **k)

    def extract_counting(path, *a, **k):
        counters.extract += 1
        return real_extract(path, *a, **k)

    monkeypatch.setattr(scanner, "sha1_of", sha1_counting)
    monkeypatch.setattr(scanner.comfy_prompt, "extract_from_file", extract_counting)
    return counters


def _add_folder(path: Path, *, recursive: bool = True) -> int:
    conn = db.connect()
    try:
        row = repo.add_folder(conn, str(path), None, recursive=recursive)
        return int(row["id"])
    finally:
        conn.close()


def _db_paths(folder_id: int | None = None) -> set[str]:
    conn = db.connect()
    try:
        if folder_id is None:
            rows = conn.execute("SELECT path FROM images").fetchall()
        else:
            rows = conn.execute(
                "SELECT path FROM images WHERE folder_id = ?", (folder_id,)
            ).fetchall()
        return {r["path"] for r in rows}
    finally:
        conn.close()


def _owners() -> dict[str, int]:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT path, folder_id FROM images").fetchall()
        return {r["path"]: int(r["folder_id"]) for r in rows}
    finally:
        conn.close()


def _three_images(root: Path) -> list[Path]:
    paths = [root / "a.png", root / "b.png", root / "c.png"]
    for i, p in enumerate(paths):
        _make_png(p, (i * 60, 10, 200 - i * 60))
    return paths


# ---------------------------------------------------------------- (a)〜(g) 走査の挙動

# (a) 1 回目の走査で 3 件が登録される
def test_first_scan_registers_all(tmp_path: Path, env: Counters) -> None:
    root = tmp_path / "img"
    paths = _three_images(root)
    fid = _add_folder(root)
    assert scanner.full_scan(fid, str(root)) == 3
    assert _db_paths(fid) == {str(p) for p in paths}
    # ハッシュと解析は背景の解析 (enrich_pending) で行う (仮登録の計画 作業 1-01)
    scanner.enrich_pending()
    assert env.sha1 == 3 and env.extract == 3


# (b) 2 回目は何も変わっていないので、重い処理を 1 回も呼ばず、返り値も 0
def test_second_scan_skips_unchanged_files(tmp_path: Path, env: Counters) -> None:
    root = tmp_path / "img"
    _three_images(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    env.reset()
    assert scanner.full_scan(fid, str(root)) == 0
    assert env.sha1 == 0 and env.extract == 0


# (c) 1 枚だけ中身と更新日時が変わったら、その 1 枚だけ再登録する
def test_changed_file_is_reindexed_alone(tmp_path: Path, env: Counters) -> None:
    root = tmp_path / "img"
    paths = _three_images(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    scanner.enrich_pending()
    env.reset()

    target = paths[1]
    st = target.stat()
    _make_png(target, (250, 250, 0))
    os.utime(target, (st.st_atime, st.st_mtime + 2))

    assert scanner.full_scan(fid, str(root)) == 1
    scanner.enrich_pending()
    assert env.sha1 == 1 and env.extract == 1


# (d) ディスクから消えたファイルは DB からも消える
def test_deleted_file_is_removed_from_db(tmp_path: Path, env: Counters) -> None:
    root = tmp_path / "img"
    paths = _three_images(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    paths[2].unlink()
    scanner.full_scan(fid, str(root))
    assert _db_paths(fid) == {str(paths[0]), str(paths[1])}


# (e) full=True なら変更の有無にかかわらず全件を処理する
def test_full_flag_reprocesses_everything(tmp_path: Path, env: Counters) -> None:
    root = tmp_path / "img"
    _three_images(root)
    fid = _add_folder(root)
    scanner.full_scan(fid, str(root))
    env.reset()
    assert scanner.full_scan(fid, str(root), full=True) == 3
    assert env.sha1 == 3 and env.extract == 3


# (f) 親（直下のみ）を走査しても、子（サブフォルダ）の記録と持ち主は変わらない
def test_non_recursive_parent_keeps_child_records(tmp_path: Path, env: Counters) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    _make_png(parent / "top.png", (1, 2, 3))
    _make_png(child / "k1.png", (10, 20, 30))
    _make_png(child / "k2.png", (40, 50, 60))
    pid = _add_folder(parent, recursive=False)
    cid = _add_folder(child, recursive=True)

    scanner.full_scan(cid, str(child))
    scanner.full_scan(pid, str(parent), recursive=False)

    owners = _owners()
    assert owners[str(child / "k1.png")] == cid
    assert owners[str(child / "k2.png")] == cid
    assert owners[str(parent / "top.png")] == pid


# (g) 子の画像が持ち主 2 で登録済みなら、親（再帰）の走査でも読み飛ばし、持ち主を変えない
def test_recursive_parent_scan_does_not_steal_child_owner(tmp_path: Path, env: Counters) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    _make_png(child / "k1.png", (10, 20, 30))
    _make_png(child / "k2.png", (40, 50, 60))
    pid = _add_folder(parent, recursive=True)
    cid = _add_folder(child, recursive=True)

    scanner.full_scan(cid, str(child))
    env.reset()
    scanner.full_scan(pid, str(parent), recursive=True)
    scanner.full_scan(cid, str(child))

    assert env.sha1 == 0 and env.extract == 0
    assert set(_owners().values()) == {cid}


# ---------------------------------------------------------------- (h) 列挙の同等性

def _old_enumeration(folder: Path, recursive: bool) -> set[str]:
    it = folder.rglob("*") if recursive else folder.glob("*")
    return {str(p) for p in it if p.is_file() and scanner._is_image(p)}


@pytest.mark.parametrize("recursive", [True, False])
def test_iter_image_files_matches_old_enumeration(tmp_path: Path, recursive: bool) -> None:
    root = tmp_path / "Tree"
    _make_png(root / "a.png", (1, 1, 1))
    _make_png(root / "B.PNG", (2, 2, 2))
    (root / "note.txt").write_text("x", encoding="utf-8")
    _make_png(root / "dir.png" / "inner.png", (3, 3, 3))  # 名前が .png のフォルダ
    _make_png(root / "SubDir" / "x.png", (4, 4, 4))
    _make_png(root / "SubDir" / "Y.PNG", (5, 5, 5))
    _make_png(root / "sub2" / "Deep" / "Er" / "z.png", (6, 6, 6))

    got = list(scanner._iter_image_files(root, recursive))
    # Path どうしの比較は Windows で大文字と小文字を区別しないので、文字列で比べる
    assert {str(p) for p, _size, _mtime in got} == _old_enumeration(root, recursive)
    assert len(got) == len({str(p) for p, _size, _mtime in got})  # 重複なし
    for p, size, mtime in got:
        st = p.stat()
        assert size == st.st_size
        assert mtime == st.st_mtime


# ---------------------------------------------------------------- (i) 起動の経路

def test_run_initial_scan_passes_recursive_flag(tmp_path: Path, env: Counters, monkeypatch) -> None:
    fid_rec = _add_folder(tmp_path / "a", recursive=True)
    fid_dir = _add_folder(tmp_path / "b", recursive=False)
    calls: list[tuple[int, bool]] = []

    def fake_full_scan(folder_id, folder_path, *, recursive=True, full=False):
        calls.append((int(folder_id), bool(recursive)))
        return 0

    monkeypatch.setattr(scanner, "full_scan", fake_full_scan)
    monkeypatch.setattr(scanner.manager, "kick_enrichment", lambda: None)
    scanner.run_initial_scan()
    assert dict(calls) == {fid_rec: True, fid_dir: False}


# ---------------------------------------------------------------- (j)(k)(l) 入口の契約

def test_rescan_route_has_full_parameter_default_false() -> None:
    param = inspect.signature(routes.rescan_folder).parameters["full"]
    assert param.default is False


def test_ui_rescan_button_supports_shift_click() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "rescanCurrentFolder(ev)" in js
    assert "shiftKey" in js
    assert "full=1" in js
    tag = re.search(r'<button\b[^>]*\bid="btnRescan"[^>]*>', html)
    assert tag is not None
    assert "Shift" in tag.group(0)


def test_main_uses_run_initial_scan_not_direct_full_scan() -> None:
    # main を取り込むと本番の data/server.log へログを書き始めるので、文字列として読む
    text = (PKG / "main.py").read_text(encoding="utf-8")
    assert "scanner.run_initial_scan" in text
    assert "scanner.full_scan(" not in text


# ---------------------------------------------------------------- (m) 列挙中の OSError

class _FakeStat:
    st_size = 10
    st_mtime = 1700000000.0


class _FakeEntry:
    def __init__(self, root: str, name: str, *, stat_fails: bool = False) -> None:
        self.name = name
        self.path = os.path.join(root, name)
        self._stat_fails = stat_fails

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return False

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        return True

    def stat(self, *, follow_symlinks: bool = True):
        if self._stat_fails:
            raise OSError("stat 失敗（列挙の途中で消えた）")
        return _FakeStat()


class _FakeScandir:
    def __init__(self, entries) -> None:
        self._entries = entries

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, *exc) -> bool:
        return False

    def __iter__(self):
        return iter(self._entries)


def test_iter_image_files_skips_only_the_entry_that_raises_oserror(monkeypatch) -> None:
    root = "FAKEROOT_DIFFERENTIAL"
    entries = [
        _FakeEntry(root, "x.png"),
        _FakeEntry(root, "bad.png", stat_fails=True),
        _FakeEntry(root, "y.png"),
    ]
    real_scandir = os.scandir

    def fake_scandir(path="."):
        if os.fspath(path) == root:
            return _FakeScandir(entries)
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    got = list(scanner._iter_image_files(Path(root), True))
    assert sorted(p.name for p, _size, _mtime in got) == ["x.png", "y.png"]
