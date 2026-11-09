"""測定道具 tools/bench_startup.py の判定と入口の契約テスト。

作業定義（工程 1 の作業 1-01）
- 目的：変更前後の測定結果を、同じ基準で数から合否にする判定 `judge` と、
  本番に触れない入口の拒否（`measure` の引数検査）を、起動を伴わずに確かめる。
- 完了条件：このファイルの全件が通る。実装前は全件が失敗する（赤）。
- 検証方法：`.venv\\Scripts\\python.exe -m pytest tests/test_bench_startup.py -q`。
  起動を伴う部分（作業コピーの起動・画面の測定）は自動テストで覆わず、
  `measure` の実行そのものを確認とする。
- 完了条件の変更の記録：2026-11-09 条件 5 の境界を「101 ms で偽」から「変更前の 5 分の 1（900 なら 181 ms）で偽」に変えた。理由：利用者が判断 3 で、約 245 ms（約 10 倍の改善）の受け入れを承認したため。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "bench_startup.py"


def _load():
    spec = importlib.util.spec_from_file_location("bench_startup", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bench_startup"] = mod
    spec.loader.exec_module(mod)
    return mod


BEFORE = {
    "files_total": 13774,
    "scan_enumerate_old_s": 10.0,
    "scan_all_done_s": 3300.0,
    "scan_all_done_extrapolated": True,
    "list_ms_median": 100.0,
    "list_ms_max": 400.0,
    "thumb_ms_median": 50.0,
    "thumb_w": 288,
    "ui_first_paint_ms": 800.0,
    "ui_click_ms_median": 900.0,
}

AFTER = {
    "files_total": 13774,
    "scan_all_done_s": 45.0,
    "scan_all_done_extrapolated": False,
    "scan_skipped_total": 13700,
    "scan_deleted_total": 0,
    "enum_diff_count": 0,
    "list_ms_median": 90.0,
    "list_ms_max": 300.0,
    "thumb_ms_median": 40.0,
    "thumb_w": 288,
    "ui_first_paint_ms": 500.0,
    "ui_click_ms_median": 30.0,
    "ui_scroll_keep_px": 0,
    "ui_folder_switch_top_px": 0,
    "ui_selection_ok": True,
    "ui_lightbox_ok": True,
    "ui_tag_ok": True,
    # 条件 8（計画 comfydir-instant-fresh：開いた瞬間に最新が出る）
    "fresh_missing_count": 560,
    "fresh_top_s": 3.0,
    "fresh_all_s": 4.0,
}


def _pair(**after_changes):
    after = copy.deepcopy(AFTER)
    after.update(after_changes)
    return copy.deepcopy(BEFORE), after


def _failed_ids(rows):
    return [r["id"] for r in rows if not r["ok"]]


# (a) 8 条件をすべて満たす組は真
def test_judge_passes_when_all_eight_conditions_hold() -> None:
    bench = _load()
    ok, rows = bench.judge(*_pair())
    assert ok is True
    assert [r["id"] for r in rows] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert _failed_ids(rows) == []


# (b) 8 条件のそれぞれを 1 つだけ破ると、その条件だけが不合格
@pytest.mark.parametrize(
    "cond_id, changes",
    [
        (1, {"scan_all_done_s": 61.0}),  # max(60, 10+30)=60 を超える
        (1, {"scan_all_done_extrapolated": True}),  # 外挿は不可
        (2, {"scan_skipped_total": int(13774 * 0.9) - 1}),  # 読み飛ばし率 90% 未満
        (2, {"scan_deleted_total": 1}),  # 削除 0 が必須
        (2, {"enum_diff_count": 1}),  # 列挙差 0 が必須
        (3, {"list_ms_median": 131.0}),  # 100*1.2+10=130 を超える
        (3, {"thumb_ms_median": 71.0}),  # 50*1.2+10=70 を超える
        (3, {"thumb_w": 384}),  # 縮小画像の幅が変更前と違う
        (4, {"ui_first_paint_ms": 971.0}),  # 800*1.2+10=970 を超える
        (5, {"ui_click_ms_median": 181.0}),  # 変更前 900 の 5 分の 1（180）を超える
        (6, {"ui_scroll_keep_px": 3}),
        (6, {"ui_folder_switch_top_px": 3}),
        (6, {"ui_selection_ok": False}),
        (6, {"ui_lightbox_ok": False}),
        (7, {"ui_tag_ok": False}),
        (8, {"fresh_top_s": 15.1}),  # 最新が先頭に出るまで 15 秒以内
        (8, {"fresh_all_s": 30.1}),  # すべてが出るまで 30 秒以内
        (8, {"fresh_missing_count": 0}),  # 未登録が 0 件では測れていない（不合格）
        (8, {"fresh_top_s": None}),  # 届かなかった
        (8, {"fresh_all_s": None}),
    ],
)
def test_judge_fails_exactly_the_broken_condition(cond_id, changes) -> None:
    bench = _load()
    ok, rows = bench.judge(*_pair(**changes))
    assert ok is False
    assert _failed_ids(rows) == [cond_id]


# (c) キーが欠けた組は、例外にならず不合格
def test_judge_fails_on_missing_keys_without_raising() -> None:
    bench = _load()
    before, after = _pair()
    del after["ui_tag_ok"]
    del after["enum_diff_count"]
    ok, rows = bench.judge(before, after)
    assert ok is False
    assert set(_failed_ids(rows)) == {2, 7}

    ok2, rows2 = bench.judge({}, {})
    assert ok2 is False
    assert _failed_ids(rows2) == [1, 2, 3, 4, 5, 6, 7, 8]

    # 条件 8 のキーだけが無い（古い after.json）も不合格
    before, after = _pair()
    for k in ("fresh_missing_count", "fresh_top_s", "fresh_all_s"):
        del after[k]
    ok3, rows3 = bench.judge(before, after)
    assert ok3 is False
    assert _failed_ids(rows3) == [8]


# 条件 8 の境界：15 秒・30 秒ちょうどは真
def test_judge_fresh_boundaries() -> None:
    bench = _load()
    ok, rows = bench.judge(*_pair(fresh_top_s=15.0, fresh_all_s=30.0, fresh_missing_count=1))
    assert ok is True


def test_judge_fails_when_enum_diff_is_null() -> None:
    # head の測定では enum_diff_count が null になる。変更後で null は不合格。
    bench = _load()
    ok, rows = bench.judge(*_pair(enum_diff_count=None))
    assert ok is False
    assert _failed_ids(rows) == [2]


# (d) 「同等以下」の境界は before × 1.2 + 10 ちょうどで真、1 だけ超えて偽
def test_judge_equal_or_better_boundary() -> None:
    bench = _load()
    ok, rows = bench.judge(*_pair(list_ms_median=130.0))
    assert ok is True
    ok, rows = bench.judge(*_pair(list_ms_median=131.0))
    assert ok is False
    assert _failed_ids(rows) == [3]


# 判断 3（2026-11-09 承認）：条件 5 は「100 ms 以下」でなく「変更前の 5 分の 1 以下」。
# 変更前 900 なら 180 ちょうどで真、181 で偽。
def test_judge_click_boundary_is_one_fifth_of_before() -> None:
    bench = _load()
    ok, rows = bench.judge(*_pair(ui_click_ms_median=180.0))
    assert ok is True
    ok, rows = bench.judge(*_pair(ui_click_ms_median=181.0))
    assert ok is False
    assert _failed_ids(rows) == [5]


def test_judge_scan_time_boundary_uses_larger_of_60_and_enumerate_plus_30() -> None:
    bench = _load()
    before, after = _pair(scan_all_done_s=60.0)
    assert bench.judge(before, after)[0] is True
    before, after = _pair(scan_all_done_s=100.0)
    before["scan_enumerate_old_s"] = 70.0  # 70+30=100 まで許す
    assert bench.judge(before, after)[0] is True
    before["scan_enumerate_old_s"] = 69.0  # 99 まで → 100 は超える
    assert bench.judge(before, after)[0] is False


# (e) judge の副命令は JSON のファイルを読み、終了コードが 0 と 1
def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def test_judge_cli_exit_codes(tmp_path: Path) -> None:
    before, after = _pair()
    bp = tmp_path / "before.json"
    ap = tmp_path / "after.json"
    bp.write_text(json.dumps(before), encoding="utf-8")
    ap.write_text(json.dumps(after), encoding="utf-8")
    good = _run("judge", "--before", str(bp), "--after", str(ap))
    assert good.returncode == 0, good.stdout + good.stderr

    before, bad_after = _pair(ui_tag_ok=False)
    ap.write_text(json.dumps(bad_after), encoding="utf-8")
    bad = _run("judge", "--before", str(bp), "--after", str(ap))
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert "不合格" in bad.stdout


# (f) --help は 0
def test_help_exits_zero() -> None:
    res = _run("--help")
    assert res.returncode == 0
    assert "measure" in res.stdout and "judge" in res.stdout


# (g) measure は本体のフォルダ自身・その内側・ポート 8772 を、何も作らず拒否（終了コード 2）
@pytest.mark.parametrize(
    "workdir_parts",
    [(), ("data",), ("data", "bench"), ("src",), ("tools", "x")],
)
def test_measure_refuses_workdir_inside_the_app_folder(tmp_path: Path, workdir_parts) -> None:
    workdir = ROOT.joinpath(*workdir_parts)
    res = _run(
        "measure", "--source", "head", "--workdir", str(workdir),
        "--port", "8773", "--out", str(tmp_path / "x.json"),
    )
    assert res.returncode == 2, res.stdout + res.stderr
    assert "拒否" in res.stdout + res.stderr  # 道具が無いときの Python 自身の終了コード 2 と区別する
    assert not (tmp_path / "x.json").exists()
    if workdir_parts:
        assert not workdir.exists() or workdir_parts[0] in ("data", "src", "tools")


def test_measure_refuses_port_8772_and_creates_nothing(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    res = _run(
        "measure", "--source", "head", "--workdir", str(workdir),
        "--port", "8772", "--out", str(tmp_path / "x.json"),
    )
    assert res.returncode == 2, res.stdout + res.stderr
    assert "拒否" in res.stdout + res.stderr
    assert not workdir.exists()
    assert not (tmp_path / "x.json").exists()
