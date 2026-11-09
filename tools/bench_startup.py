"""起動時スキャンと一覧表示の、変更前後の測定と合否判定の道具。

作業定義（工程 1 の作業 1-01）
- 目的：変更前と変更後を同じ手順で測り、合否を数で出す。本番のデータと稼働中のサーバー
  （ポート 8772）には触れない。
- 完了条件：`measure` が JSON（スキャンの所要時間・一覧と縮小画像の応答・画面の測定・
  タグのコピー文字列・開いた直後に最新が先頭に出るまで）を出し、`judge` が 8 条件の合否を
  終了コード 0 / 1 で返す。本番の index.sqlite の更新日時とサイズを測定の前後で記録する。
- 検証方法：tests/test_bench_startup.py（judge と入口の拒否）と、`measure` の実行そのもの。
- 完了条件の変更の記録：2026-11-09 judge の条件 5 を「1 クリック 100 ms 以下」から「変更前の 5 分の 1 以下」に変えた。理由：実測で約 245 ms（変更前 2,470 ms の約 10 分の 1）が限界と分かり、利用者が判断 3 でこの受け入れを承認したため（100 ms を目指す仮想スクロールは別の計画）。

使い方
  measure : 作業コピーを作って起動し、測って JSON を書く。
            --source head（変更前＝コミット済みの HEAD）/ worktree（変更後＝作業ツリー）
  judge   : 変更前と変更後の JSON から 8 条件の合否を出す（条件 8 は変更後の JSON だけを見る）。

終了コード
  0 成功 / 1 judge の不合格 / 2 引数の拒否 / 3 複製したデータベースの整合の確認が失敗
  4 作業コピーでない場所のコードを読み込んだ / 5 作業フォルダが空でなく印も無い
  6 作業コピーのサーバーが起動しない / 8 画面の測定が失敗
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import io
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
PROD_DB = APP_ROOT / "data" / "index.sqlite"
FORBIDDEN_PORT = 8772
MARKER = ".bench_workdir"
# R27: コンソールを持たないプロセスから起動しても黒窓を出さない
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------- judge

def _num(d: dict, key: str):
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _limit(before_value: float) -> float:
    """「同等以下」の上限：before × 1.2 + 10（ミリ秒）。"""
    return before_value * 1.2 + 10


def judge(before: dict, after: dict) -> tuple[bool, list[dict]]:
    """変更前後の JSON から 8 条件の合否を出す。キーが無い条件は不合格。"""
    rows: list[dict] = []

    def add(cid: int, name: str, ok: bool, detail: str) -> None:
        rows.append({"id": cid, "name": name, "ok": bool(ok), "detail": detail})

    # (1) 全フォルダのスキャンの所要時間
    done = _num(after, "scan_all_done_s")
    enum_old = _num(before, "scan_enumerate_old_s")
    extrap = after.get("scan_all_done_extrapolated")
    if done is None or enum_old is None or extrap is not False:
        add(1, "起動時スキャンの所要時間", False,
            f"scan_all_done_s={done} 変更前の列挙={enum_old} 外挿={extrap}")
    else:
        cap = max(60.0, enum_old + 30.0)
        add(1, "起動時スキャンの所要時間", done <= cap,
            f"{done:.1f} 秒 <= max(60, {enum_old:.1f}+30)={cap:.1f} 秒")

    # (2) 読み飛ばし率・削除 0・列挙の一致
    total = _num(after, "files_total")
    skipped = _num(after, "scan_skipped_total")
    deleted = _num(after, "scan_deleted_total")
    diff = _num(after, "enum_diff_count")
    if None in (total, skipped, deleted, diff) or not total:
        add(2, "読み飛ばしと削除と列挙の一致", False,
            f"files_total={total} skipped={skipped} deleted={deleted} enum_diff={diff}")
    else:
        ok = skipped >= total * 0.9 and deleted == 0 and diff == 0
        add(2, "読み飛ばしと削除と列挙の一致", ok,
            f"読み飛ばし {skipped:.0f}/{total:.0f}（{skipped / total:.1%}）削除 {deleted:.0f} 列挙差 {diff:.0f}")

    # (3) 一覧と縮小画像の応答（同じ幅で）
    parts = []
    ok3 = True
    for key in ("list_ms_median", "thumb_ms_median"):
        b, a = _num(before, key), _num(after, key)
        if b is None or a is None:
            ok3 = False
            parts.append(f"{key}: 欠落")
        else:
            good = a <= _limit(b) + 1e-9
            ok3 = ok3 and good
            parts.append(f"{key} {a:.1f} <= {_limit(b):.1f}")
    bw, aw = _num(before, "thumb_w"), _num(after, "thumb_w")
    if bw is None or aw is None or bw != aw:
        ok3 = False
        parts.append(f"thumb_w 不一致 {bw} / {aw}")
    else:
        parts.append(f"thumb_w {aw:.0f}")
    add(3, "一覧と縮小画像の応答", ok3, "、".join(parts))

    # (4) 最初の描画
    b, a = _num(before, "ui_first_paint_ms"), _num(after, "ui_first_paint_ms")
    if b is None or a is None:
        add(4, "最初の描画", False, f"before={b} after={a}")
    else:
        add(4, "最初の描画", a <= _limit(b) + 1e-9, f"{a:.0f} ms <= {_limit(b):.0f} ms")

    # (5) 1 クリックの処理
    b, a = _num(before, "ui_click_ms_median"), _num(after, "ui_click_ms_median")
    if b is None or a is None:
        add(5, "1 クリックの処理", False, f"before={b} after={a}")
    else:
        # 判断 3（2026-11-09 承認）：「100 ms 以下」から「変更前の 5 分の 1 以下」に読み替えた
        add(5, "1 クリックの処理", a <= b * 0.2 + 1e-9,
            f"{a:.1f} ms <= 変更前 {b:.0f} ms の 5 分の 1 = {b * 0.2:.0f} ms")

    # (6) スクロール位置・選択・ライトボックス
    keep = _num(after, "ui_scroll_keep_px")
    top = _num(after, "ui_folder_switch_top_px")
    sel = after.get("ui_selection_ok")
    lb = after.get("ui_lightbox_ok")
    ok6 = keep is not None and top is not None and keep <= 2 and top <= 2 and sel is True and lb is True
    add(6, "スクロール位置と選択とライトボックス", ok6,
        f"keep={keep} folder_top={top} selection={sel} lightbox={lb}")

    # (7) タグのコピー文字列
    tag = after.get("ui_tag_ok")
    add(7, "タグの「, 」表示とコピー", tag is True, f"ui_tag_ok={tag}")

    # (8) 開いた瞬間に最新が出る（計画 comfydir-instant-fresh）。未登録が 1 枚以上ある複製で、
    #     最新のファイルが先頭に 15 秒以内、すべての未登録が 30 秒以内に一覧へ出る。
    missing = _num(after, "fresh_missing_count")
    top_s = _num(after, "fresh_top_s")
    all_s = _num(after, "fresh_all_s")
    ok8 = (missing is not None and missing >= 1
           and top_s is not None and top_s <= 15.0
           and all_s is not None and all_s <= 30.0)
    add(8, "開いた瞬間に最新が先頭に出る", ok8,
        f"未登録 {missing} 件、最新が先頭に {top_s} 秒（<=15）、すべてが {all_s} 秒（<=30）")

    return all(r["ok"] for r in rows), rows


def cmd_judge(args) -> int:
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    ok, rows = judge(before, after)
    for r in rows:
        print(f"({r['id']}) {r['name']}: {'合格' if r['ok'] else '不合格'} — {r['detail']}")
    print("総合:", "合格" if ok else "不合格")
    return 0 if ok else 1


# ---------------------------------------------------------------- measure: 準備

def _is_inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def check_args(args) -> str | None:
    """拒否する理由を返す（問題なければ None）。何も作らない。"""
    if args.port == FORBIDDEN_PORT:
        return f"--port {FORBIDDEN_PORT} は稼働中の本番のポートなので使えません"
    workdir = Path(args.workdir).resolve()
    app = APP_ROOT.resolve()
    if _is_inside(workdir, app) or _is_inside(app, workdir):
        return f"--workdir が本体のフォルダ（{app}）の内側か外側の親です"
    return None


def _rmtree(path: Path) -> None:
    def onerr(func, p, _exc):
        os.chmod(p, 0o700)
        func(p)

    shutil.rmtree(path, onerror=onerr)


def prepare_workdir(workdir: Path, source: str) -> None:
    if workdir.exists() and any(workdir.iterdir()):
        if not (workdir / MARKER).exists():
            raise SystemExit(_fail(5, f"{workdir} は空でなく、測定用の印も無いので使えません"))
        _rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    if source == "head":
        res = subprocess.run(
            ["git", "-C", str(APP_ROOT), "archive", "--format=tar", "HEAD"],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        if res.returncode != 0:
            raise SystemExit(_fail(3, "git archive が失敗: " + res.stderr.decode("utf-8", "replace")))
        with tarfile.open(fileobj=io.BytesIO(res.stdout)) as tf:
            try:
                tf.extractall(workdir, filter="data")
            except TypeError:
                tf.extractall(workdir)
    else:
        shutil.copytree(
            APP_ROOT, workdir, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                ".venv", ".git", "data", "__pycache__", ".pytest_cache", "node_modules", "*.pyc"),
        )
    (workdir / MARKER).write_text("bench_startup.py の作業コピー\n", encoding="utf-8")


def backup_db(dst: Path) -> str:
    """本番の DB を読み取り専用で開き、backup() で複製して quick_check の結果を返す。"""
    uri = PROD_DB.resolve().as_uri() + "?mode=ro"
    src = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        out = sqlite3.connect(str(dst))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    chk = sqlite3.connect(str(dst))
    try:
        return str(chk.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        chk.close()


def copy_data(workdir: Path) -> list[dict]:
    data = workdir / "data"
    data.mkdir(parents=True, exist_ok=True)
    check = backup_db(data / "index.sqlite")
    if check != "ok":
        raise SystemExit(_fail(3, f"複製した DB の quick_check が ok ではありません: {check}"))
    for name in ("thumbs", "danbooru_tags", "danbooru_translations"):
        src = APP_ROOT / "data" / name
        if src.is_dir():
            shutil.copytree(src, data / name, dirs_exist_ok=True)
    conn = sqlite3.connect(str(data / "index.sqlite"))
    try:
        return [
            {"id": r[0], "path": r[1], "recursive": bool(r[2])}
            for r in conn.execute("SELECT id, path, recursive FROM folders ORDER BY id")
        ]
    finally:
        conn.close()


def read_exts(workdir: Path) -> tuple[str, ...]:
    text = (workdir / "src" / "comfy_image_organizer" / "config.py").read_text(encoding="utf-8")
    m = re.search(r"IMAGE_EXTS[^=]*=\s*\(([^)]*)\)", text)
    if not m:
        return (".png",)
    return tuple(x.strip().strip("\"'").lower() for x in m.group(1).split(",") if x.strip())


def enumerate_old(folder: Path, recursive: bool, exts) -> list[str]:
    """旧方式の列挙（rglob / glob と is_file() と拡張子）。"""
    it = folder.rglob("*") if recursive else folder.glob("*")
    return [str(p) for p in it if p.is_file() and p.suffix.lower() in exts]


NEW_ENUM_SCRIPT = r"""
import json, sys
from pathlib import Path
from comfy_image_organizer import scanner
folders = json.loads(sys.argv[1])
out = {}
for fid, path, rec in folders:
    out[str(fid)] = [str(item[0]) for item in scanner._iter_image_files(Path(path), bool(rec))]
sys.stdout.write(json.dumps(out, ensure_ascii=False))
"""


def enum_diff(py: str, env: dict, workdir: Path, folders: list[dict], old: dict[int, list[str]], notes: list[str]):
    payload = json.dumps([[f["id"], f["path"], f["recursive"]] for f in folders if f["id"] in old])
    res = subprocess.run(
        [py, "-c", NEW_ENUM_SCRIPT, payload], env=env, cwd=str(workdir),
        capture_output=True, creationflags=CREATE_NO_WINDOW,
    )
    if res.returncode != 0:
        notes.append("新方式の列挙が失敗: " + res.stderr.decode("utf-8", "replace")[-400:])
        return None
    new = json.loads(res.stdout.decode("utf-8"))
    total = 0
    for fid, paths in old.items():
        total += len(set(paths) ^ set(new.get(str(fid), [])))
    return total


def stat_match_rate(db: Path, sample: int = 500):
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT path, size, mtime FROM images ORDER BY RANDOM() LIMIT ?", (sample,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    ok = 0
    for path, size, mtime in rows:
        try:
            st = os.stat(path)
        except OSError:
            continue
        if size is not None and mtime is not None and st.st_size == size and abs(st.st_mtime - mtime) < 0.001:
            ok += 1
    return ok / len(rows)


def fresh_missing(db: Path, old_paths: list[str]) -> tuple[list[str], str | None]:
    """旧方式の列挙のうち、複製した images に無いパスと、その中で更新日時が最新のファイル名。

    「アプリを開いた直後に、まだ登録されていない最新の画像が出るか」を測る土台。
    """
    conn = sqlite3.connect(str(db))
    try:
        known = {r[0] for r in conn.execute("SELECT path FROM images")}
    finally:
        conn.close()
    missing = [p for p in old_paths if p not in known]
    newest: tuple[float, str] | None = None
    for p in missing:
        try:
            mt = os.stat(p).st_mtime
        except OSError:
            continue
        if newest is None or mt > newest[0]:
            newest = (mt, Path(p).name)
    return missing, (newest[1] if newest else None)


def fresh_poll(base: str, folder_id, newest_name: str | None, missing_names: set[str],
               t0: float, limit_s: float) -> dict:
    """起動の直前 t0 から、新しい順の一覧の先頭が最新になるまで (fresh_top_s)、
    未登録のすべてが一覧に出るまで (fresh_all_s) を 0.5 秒ごとに見る。届かなければ null。"""
    url = f"{base}/api/images?folder_id={folder_id}&order=mtime&direction=desc"
    out: dict = {"fresh_top_s": None, "fresh_all_s": None}
    while time.monotonic() - t0 < limit_s:
        try:
            rows = json.loads(http_get(url, 30))
        except Exception:
            time.sleep(0.5)
            continue
        now = time.monotonic() - t0
        if out["fresh_top_s"] is None and rows and rows[0].get("filename") == newest_name:
            out["fresh_top_s"] = now
        if missing_names and missing_names <= {r.get("filename") for r in rows}:
            out["fresh_all_s"] = now
            break
        time.sleep(0.5)
    return out


def prod_db_state() -> dict:
    try:
        st = PROD_DB.stat()
        return {"mtime": st.st_mtime, "size": st.st_size}
    except OSError as e:
        return {"error": str(e)}


def _fail(code: int, message: str) -> int:
    print(f"{'拒否' if code == 2 else '失敗'}: {message}", file=sys.stderr)
    return code


# ---------------------------------------------------------------- measure: 実行中の観測

LOG_TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) ")
RE_DELETED = re.compile(r"削除 (\d+) 件")
RE_SKIPPED = re.compile(r"読み飛ばし (\d+) 件")


def _parse_ts(line: str):
    m = LOG_TS.match(line)
    if not m:
        return None
    return dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp() + int(m.group(2)) / 1000


def http_get(url: str, timeout: float = 60.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


class Sampler(threading.Thread):
    """ログを 1 秒ごとに読み、一覧の応答を 5 秒ごとに測る。"""

    def __init__(self, base: str, log_path: Path, list_url: str, expected_done: int):
        super().__init__(daemon=True)
        self.base, self.log_path, self.list_url = base, log_path, list_url
        self.expected_done = expected_done
        self.stop_ev = threading.Event()
        self.list_ms: list[float] = []
        self.ready_ts: float | None = None
        self.done_lines: list[tuple[float, int, int]] = []  # (時刻, 削除, 読み飛ばし)
        self._pos = 0

    @property
    def scan_complete(self) -> bool:
        return len(self.done_lines) >= self.expected_done

    def _poll_log(self) -> None:
        try:
            with open(self.log_path, "rb") as f:
                f.seek(self._pos)
                chunk = f.read()
        except OSError:
            return
        if not chunk:
            return
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return
        self._pos += cut + 1
        for line in chunk[:cut].decode("utf-8", "replace").splitlines():
            ts = _parse_ts(line)
            if ts is None:
                continue
            if "ComfyImageOrganizer ready" in line and self.ready_ts is None:
                self.ready_ts = ts
            elif "フルスキャン完了" in line:
                d = RE_DELETED.search(line)
                s = RE_SKIPPED.search(line)
                self.done_lines.append((ts, int(d.group(1)) if d else 0, int(s.group(1)) if s else 0))

    def _sample_list(self) -> None:
        t = time.perf_counter()
        try:
            http_get(self.list_url, 120)
        except Exception:
            return
        self.list_ms.append((time.perf_counter() - t) * 1000)

    def run(self) -> None:
        next_list = 0.0
        while not self.stop_ev.is_set():
            self._poll_log()
            if time.monotonic() >= next_list:
                self._sample_list()
                next_list = time.monotonic() + 5
            self.stop_ev.wait(1.0)
        self._poll_log()


class SseReader(threading.Thread):
    """scan_progress を記録する（変更前の処理速度の算出に使う）。"""

    def __init__(self, url: str):
        super().__init__(daemon=True)
        self.url = url
        self.events: list[tuple[float, int | None, int | None, int | None, str | None]] = []
        self.error: str | None = None

    def run(self) -> None:
        try:
            with urllib.request.urlopen(self.url, timeout=60) as r:
                ev = None
                for raw in r:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:") and ev == "scan_progress":
                        d = json.loads(line[5:].strip())
                        p = d.get("payload") or {}
                        self.events.append(
                            (time.monotonic(), d.get("folder_id"), p.get("done"), p.get("total"), p.get("phase")))
        except Exception as e:  # サーバー停止時の切断も入る
            self.error = str(e)

    def rate(self) -> float | None:
        by_folder: dict[int, list] = collections.defaultdict(list)
        for ev in self.events:
            if ev[4] == "index" and (ev[3] or 0) > 0:
                by_folder[ev[1]].append(ev)
        if not by_folder:
            return None
        evs = max(by_folder.values(), key=lambda v: v[0][3])
        if len(evs) < 2:
            return None
        dt_s = evs[-1][0] - evs[0][0]
        dd = (evs[-1][2] or 0) - (evs[0][2] or 0)
        if dt_s < 3 or dd <= 0:
            return None
        return dd / dt_s


# ---------------------------------------------------------------- measure: 画面

WAIT_STABLE_JS = """async ([expected, stableMs, timeoutMs]) => {
  const g = document.querySelector('#grid');
  const t0 = performance.now();
  let last = null, since = performance.now();
  while (performance.now() - t0 < timeoutMs) {
    const n = g.querySelectorAll('.tile').length;
    const st = n + '|' + Math.round(g.scrollTop);
    if (st !== last) { last = st; since = performance.now(); }
    if (n === expected && performance.now() - since >= stableMs) return performance.now() - t0;
    await new Promise(r => setTimeout(r, 50));
  }
  return -1;
}"""

CLICK_TIME_JS = """(idx) => {
  const t = document.querySelectorAll('#grid .tile')[idx];
  if (!t) return null;
  const t0 = performance.now();
  t.click();
  return new Promise(res => requestAnimationFrame(() => requestAnimationFrame(() => res(performance.now() - t0))));
}"""

SELECTION_JS = """async () => {
  const g = document.querySelector('#grid');
  const raf2 = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const T = (i) => g.querySelectorAll('.tile')[i];
  const read = () => {
    const em = document.querySelector('#gridSummary em');
    return { summary: em ? parseInt(em.textContent, 10) : 0, marked: g.querySelectorAll('.tile.is-selected').length };
  };
  const fire = (el, mods) => el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ...mods }));
  const res = [];
  fire(g, {}); await raf2(); res.push(read());
  fire(T(70), {}); await raf2(); res.push(read());
  fire(T(72), { ctrlKey: true }); await raf2(); res.push(read());
  fire(T(80), { shiftKey: true }); await raf2(); res.push(read());
  fire(g, {}); await raf2(); res.push(read());
  return res;
}"""

LIGHTBOX_JS = """async (idx) => {
  const g = document.querySelector('#grid');
  const raf2 = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const T = () => g.querySelectorAll('.tile')[idx];
  T().click(); await raf2();
  T().click(); await raf2();
  await new Promise(r => setTimeout(r, 300));
  return document.getElementById('lightbox').open;
}"""

TAG_JS = """() => {
  const row = document.querySelector('#rightPane .prompt-token-row');
  if (!row) return null;
  if (row.querySelectorAll('.prompt-token').length < 2 || row.querySelector('.prompt-token-section')) return null;
  const text = row.textContent;
  const range = document.createRange();
  range.selectNodeContents(row);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  const s = sel.toString();
  sel.removeAllRanges();
  return { text, selection: s };
}"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def measure_ui(base: str, shots_dir: Path, notes: list[str]) -> dict:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    out: dict = {}
    shots_dir.mkdir(parents=True, exist_ok=True)
    shots: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 1600, "height": 900}, service_workers="block")
            page = ctx.new_page()
            responses: list[str] = []
            errors: list[str] = []
            page.on("response", lambda r: responses.append(r.url)
                    if "/api/images?" in r.url and r.request.method == "GET" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))

            def expected_count() -> int:
                return len(json.loads(http_get(responses[-1])))

            def swap(trigger) -> bool:
                n0 = len(responses)
                trigger()
                deadline = time.time() + 60
                while len(responses) <= n0 and time.time() < deadline:
                    page.wait_for_timeout(50)
                if len(responses) <= n0:
                    notes.append("一覧の取り直しの応答が来ませんでした")
                    return False
                page.wait_for_timeout(300)
                return page.evaluate(WAIT_STABLE_JS, [expected_count(), 500, 120000]) >= 0

            def scroll_top() -> float:
                return page.evaluate("() => document.querySelector('#grid').scrollTop")

            def scroll_to(limit: int) -> None:
                page.evaluate(
                    "(lim) => { const g = document.querySelector('#grid');"
                    " g.scrollTop = Math.min(lim, g.scrollHeight - g.clientHeight); }", limit)
                page.wait_for_timeout(300)

            t0 = time.perf_counter()
            page.goto(base + "/", wait_until="commit")
            page.wait_for_function("document.querySelectorAll('#grid .tile').length >= 20", timeout=300000)
            first_paint = (time.perf_counter() - t0) * 1000
            out["ui_first_paint_ms"] = first_paint

            expected = expected_count()
            ta = time.perf_counter()
            n = page.evaluate(WAIT_STABLE_JS, [expected, 500, 120000])
            out["ui_all_tiles_ms"] = (first_paint + (time.perf_counter() - ta) * 1000 - 500) if n >= 0 else None
            if n < 0:
                notes.append("全タイルが 120 秒以内にそろいませんでした")
            out["ui_tile_count"] = page.evaluate("() => document.querySelectorAll('#grid .tile').length")

            # 縮小画像の幅（画面が実際に要求している値の最頻値）
            srcs = page.eval_on_selector_all("#grid img", "els => els.slice(0, 50).map(i => i.getAttribute('src'))")
            ws = [m.group(1) for s in srcs if (m := re.search(r"[?&]w=(\d+)", s or ""))]
            out["thumb_w"] = int(collections.Counter(ws).most_common(1)[0][0]) if ws else None

            p = shots_dir / "grid.png"
            page.screenshot(path=str(p))
            shots.append(str(p))

            # スクロール位置の保持（並び順を替えて戻す）
            try:
                order0 = page.eval_on_selector("#orderSelect", "s => s.value")
                scroll_to(5000)
                pos0 = scroll_top()
                swap(lambda: page.select_option("#orderSelect", "name|desc" if order0 != "name|desc" else "name|asc"))
                swap(lambda: page.select_option("#orderSelect", order0))
                out["ui_scroll_keep_px"] = abs(scroll_top() - pos0)
                out["ui_scroll_pos_before_px"] = pos0
            except Exception as e:
                notes.append(f"スクロール保持の測定が失敗: {e}")

            # フォルダを替えたら先頭に戻る
            try:
                opts = page.eval_on_selector("#folderSelect", "s => [...s.options].map(o => o.value)")
                cur = page.eval_on_selector("#folderSelect", "s => s.value")
                other = next((o for o in opts if o != cur), None)
                if other is None:
                    out["ui_folder_switch_top_px"] = None
                    notes.append("フォルダが 1 つだけなので切り替えの測定を省きました")
                else:
                    scroll_to(3000)
                    swap(lambda: page.select_option("#folderSelect", other))
                    out["ui_folder_switch_top_px"] = scroll_top()
                    swap(lambda: page.select_option("#folderSelect", cur))
            except Exception as e:
                notes.append(f"フォルダ切り替えの測定が失敗: {e}")

            # 1 クリックの処理（別々の 5 枚）
            try:
                times = []
                for idx in (10, 20, 30, 40, 50):
                    v = page.evaluate(CLICK_TIME_JS, idx)
                    if v is not None:
                        times.append(float(v))
                out["ui_click_ms_all"] = times
                out["ui_click_ms_median"] = statistics.median(times) if times else None
            except Exception as e:
                notes.append(f"クリックの測定が失敗: {e}")

            # 選択の整合
            try:
                r = page.evaluate(SELECTION_JS)
                out["ui_selection_steps"] = r
                out["ui_selection_ok"] = bool(
                    all(x["summary"] == x["marked"] for x in r)
                    and r[0]["marked"] == 0 and r[1]["marked"] == 1 and r[2]["marked"] == 2
                    and r[3]["marked"] >= 3 and r[4]["marked"] == 0)
            except Exception as e:
                notes.append(f"選択の測定が失敗: {e}")

            # ライトボックス
            try:
                out["ui_lightbox_ok"] = bool(page.evaluate(LIGHTBOX_JS, 90))
                page.keyboard.press("Escape")
                page.wait_for_timeout(200)
            except Exception as e:
                notes.append(f"ライトボックスの測定が失敗: {e}")

            # タグのコピー文字列
            found = None
            for i in range(10):
                page.evaluate("(i) => document.querySelectorAll('#grid .tile')[i].click()", i)
                if page.evaluate("() => document.getElementById('lightbox').open"):
                    page.keyboard.press("Escape")
                try:
                    page.wait_for_selector("#rightPane .prompt-token", timeout=3000)
                except PWTimeout:
                    continue
                page.wait_for_timeout(300)
                r = page.evaluate(TAG_JS)
                if r:
                    found = r
                    break
            if found:
                out["ui_tag_text"] = found["text"]
                out["ui_tag_selection"] = found["selection"]
                out["ui_tag_ok"] = bool(
                    ", " in found["selection"] and _norm(found["selection"]) == _norm(found["text"]))
                p = shots_dir / "tags.png"
                page.locator("#rightPane").screenshot(path=str(p))
                shots.append(str(p))
            else:
                out["ui_tag_ok"] = False
                notes.append("2 語以上のタグ行を持つ画像が最初の 10 枚に見つかりませんでした")

            out["ui_page_errors"] = len(errors)
            if errors:
                notes.append("ページのエラー: " + " / ".join(errors[:3]))
            out["ui_screenshots"] = shots
        finally:
            browser.close()
    return out


# ---------------------------------------------------------------- measure: 本体

def cmd_measure(args) -> int:
    problem = check_args(args)
    if problem:
        return _fail(2, problem)
    workdir = Path(args.workdir).resolve()
    out_path = Path(args.out).resolve()
    notes: list[str] = []
    result: dict = {
        "source": args.source, "workdir": str(workdir), "port": args.port,
        "measured_at": dt.datetime.now().isoformat(timespec="seconds"), "notes": notes,
    }
    prod_before = prod_db_state()
    result["prod_db_before"] = prod_before

    py = str(APP_ROOT / ".venv" / "Scripts" / "python.exe")
    if not Path(py).exists():
        py = sys.executable
    env = dict(os.environ)
    env["PYTHONPATH"] = str(workdir / "src")
    env["PYTHONIOENCODING"] = "utf-8"

    print(f"[1/6] 作業コピーを作る（{args.source}）: {workdir}")
    prepare_workdir(workdir, args.source)
    folders = copy_data(workdir)
    exts = read_exts(workdir)
    result["folders"] = folders

    r = subprocess.run(
        [py, "-c", "import comfy_image_organizer as m; print(m.__file__)"],
        env=env, cwd=str(workdir), capture_output=True, text=True, encoding="utf-8", creationflags=CREATE_NO_WINDOW)
    loaded = Path(r.stdout.strip().splitlines()[-1]).resolve() if r.stdout.strip() else None
    if loaded is None or not _is_inside(loaded, workdir):
        return _fail(4, f"作業コピーの外のコードを読み込みました: {loaded}\n{r.stderr[-300:]}")

    print("[2/6] 旧方式の列挙・標本の一致率・新旧の列挙の差")
    old: dict[int, list[str]] = {}
    t = time.perf_counter()
    for f in folders:
        p = Path(f["path"])
        if p.is_dir():
            old[f["id"]] = enumerate_old(p, f["recursive"], exts)
        else:
            notes.append(f"フォルダが実在しません: {f['path']}")
    result["scan_enumerate_old_s"] = time.perf_counter() - t
    result["scan_folders_existing"] = len(old)
    result["stat_match_rate"] = stat_match_rate(workdir / "data" / "index.sqlite")
    result["enum_diff_count"] = (
        enum_diff(py, env, workdir, folders, old, notes) if args.source == "worktree" else None)

    # 条件 8 の下ごしらえ：先頭のフォルダで、複製した images に無い（未登録の）ファイルと、その最新
    fresh_missing_paths: list[str] = []
    fresh_newest: str | None = None
    fresh_folder = folders[0]["id"] if folders else None
    if fresh_folder in old:
        fresh_missing_paths, fresh_newest = fresh_missing(workdir / "data" / "index.sqlite", old[fresh_folder])
    result["fresh_missing_count"] = len(fresh_missing_paths)
    result["fresh_newest_name"] = fresh_newest

    print(f"[3/6] サーバーを起動（ポート {args.port}）")
    log_file = open(workdir / "uvicorn.out.log", "wb")
    t_fresh0 = time.monotonic()
    proc = subprocess.Popen(
        [py, "-m", "uvicorn", "comfy_image_organizer.main:app", "--host", "127.0.0.1", "--port", str(args.port)],
        env=env, cwd=str(workdir), stdout=log_file, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
    base = f"http://127.0.0.1:{args.port}"
    t_start = time.monotonic()
    sampler = None
    sse = None
    try:
        folders_json = None
        while time.monotonic() - t_start < 60:
            if proc.poll() is not None:
                break
            try:
                folders_json = json.loads(http_get(base + "/api/folders", 5))
                break
            except Exception:
                time.sleep(0.5)
        if folders_json is None:
            log_file.flush()
            tail = (workdir / "uvicorn.out.log").read_text(encoding="utf-8", errors="replace")[-800:]
            return _fail(6, "作業コピーのサーバーが 60 秒以内に応答しません\n" + tail)

        result["files_total"] = sum(int(f.get("image_count", 0)) for f in folders_json)
        first_folder = folders_json[0]["id"] if folders_json else None
        if first_folder != fresh_folder:
            notes.append(f"先頭のフォルダが一致しません: API={first_folder} 複製={fresh_folder}")
        list_url = f"{base}/api/images?folder_id={first_folder}&order=name&direction=asc"
        sse = SseReader(base + "/api/events")
        sse.start()
        sampler = Sampler(base, workdir / "data" / "server.log", list_url, len(old))
        sampler.start()

        # 条件 8：最新が先頭に出るまで・未登録のすべてが出るまで（最大 60 秒）。
        # 他の測定（一覧の応答の標本・画面）より先に行い、取得の負荷を混ぜない。
        print("[3b/6] 新着の見え方（最新が先頭に出るまで）")
        if fresh_missing_paths:
            result.update(fresh_poll(
                base, first_folder, fresh_newest,
                {Path(p).name for p in fresh_missing_paths},
                t_fresh0, min(60.0, float(args.seconds))))
        else:
            result.update({"fresh_top_s": None, "fresh_all_s": None})
            notes.append("未登録のファイルが無いため、新着の見え方は測れていません")
        sampler.list_ms.clear()  # 条件 8 の取得の応答は、一覧の応答の標本に含めない

        print("[4/6] 起動の 10 秒後から画面を測る" if not args.no_ui else "[4/6] 画面の測定は省略")
        ui: dict = {}
        if not args.no_ui:
            wait = 10 - (time.monotonic() - t_start)
            if wait > 0:
                time.sleep(wait)
            try:
                ui = measure_ui(base, Path(args.out).with_suffix("").parent
                                / (Path(args.out).stem + "_shots"), notes)
            except Exception as e:
                result["ui_error"] = f"{type(e).__name__}: {e}"
                notes.append("画面の測定が失敗: " + result["ui_error"])
        result.update(ui)

        print("[5/6] スキャンの完了か --seconds まで観測")
        while True:
            elapsed = time.monotonic() - t_start
            if elapsed >= max(args.seconds if not sampler.scan_complete else 15, 15):
                break
            if sampler.scan_complete and elapsed >= 15:
                break
            time.sleep(1)
        sampler.stop_ev.set()
        sampler.join(timeout=10)

        thumb_w = args.thumb_w
        if thumb_w == "auto":
            thumb_w = result.get("thumb_w") or 288
        result["thumb_w"] = int(thumb_w)
        try:
            ids = [i["id"] for i in json.loads(http_get(list_url, 120))[:20]]
            tms = []
            for iid in ids:
                t = time.perf_counter()
                http_get(f"{base}/api/images/{iid}/thumb?w={result['thumb_w']}", 120)
                tms.append((time.perf_counter() - t) * 1000)
            result["thumb_ms_all"] = tms
            result["thumb_ms_median"] = statistics.median(tms) if tms else None
        except Exception as e:
            notes.append(f"縮小画像の測定が失敗: {e}")

        result["list_ms_all"] = sampler.list_ms
        result["list_ms_median"] = statistics.median(sampler.list_ms) if sampler.list_ms else None
        result["list_ms_max"] = max(sampler.list_ms) if sampler.list_ms else None
        result["scan_deleted_total"] = sum(d for _, d, _ in sampler.done_lines)
        result["scan_skipped_total"] = sum(s for _, _, s in sampler.done_lines)
        result["scan_done_lines"] = len(sampler.done_lines)
        result["scan_rate_files_per_s"] = sse.rate()
        if sampler.scan_complete and sampler.ready_ts is not None:
            result["scan_all_done_s"] = max(ts for ts, _, _ in sampler.done_lines) - sampler.ready_ts
            result["scan_all_done_extrapolated"] = False
        else:
            rate = result["scan_rate_files_per_s"]
            result["scan_all_done_s"] = (result.get("files_total", 0) / rate) if rate else None
            result["scan_all_done_extrapolated"] = True
    finally:
        if sampler is not None:
            sampler.stop_ev.set()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()

    print("[6/6] 結果を書く")
    result["prod_db_after"] = prod_db_state()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    keys = ("files_total", "scan_enumerate_old_s", "scan_all_done_s", "scan_all_done_extrapolated",
            "scan_rate_files_per_s", "scan_skipped_total", "scan_deleted_total", "enum_diff_count",
            "stat_match_rate", "thumb_w", "list_ms_median", "list_ms_max", "thumb_ms_median",
            "ui_first_paint_ms", "ui_all_tiles_ms", "ui_click_ms_median", "ui_scroll_keep_px",
            "ui_folder_switch_top_px", "ui_selection_ok", "ui_lightbox_ok", "ui_tag_ok",
            "fresh_missing_count", "fresh_newest_name", "fresh_top_s", "fresh_all_s")
    for k in keys:
        print(f"  {k}: {result.get(k)}")
    for n in notes:
        print("  注意:", n)
    print(f"  出力: {out_path}")
    if "ui_error" in result:
        return 8
    return 0


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="起動時スキャンと一覧表示の測定・判定の道具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure", help="作業コピーを作って起動し、測って JSON を書く")
    m.add_argument("--source", choices=("head", "worktree"), required=True)
    m.add_argument("--workdir", required=True)
    m.add_argument("--port", type=int, required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--seconds", type=int, default=180)
    m.add_argument("--thumb-w", dest="thumb_w", default="auto")
    m.add_argument("--no-ui", dest="no_ui", action="store_true")
    m.set_defaults(func=cmd_measure)
    j = sub.add_parser("judge", help="変更前後の JSON から 8 条件の合否を出す")
    j.add_argument("--before", required=True)
    j.add_argument("--after", required=True)
    j.set_defaults(func=cmd_judge)
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
