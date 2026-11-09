"""画面（app.js / style.css）の契約テスト。ブラウザを動かさず、ソースの文字列で確かめる。

作業定義（工程 3 の作業 3-01）
- 目的：タグを範囲選択してコピーしたとき「a, b, c」の形で貼り付けられるよう、
  タグとタグの間に「, 」を文字として表示する（行内の並び＋区切りの span）。
- 完了条件：このファイルの全件が通る。実装前は全件が失敗する（赤）。
- 検証方法：`.venv\\Scripts\\python.exe -m pytest tests/test_static_ui_contract.py -q`。
  実際の見え方（背景色・下線の色分け・コピー文字列）は、工程 5 の画面測定（DOM の文字列と
  画像）で確かめる。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "comfy_image_organizer" / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _rule_block(css: str, selector: str) -> str:
    """行頭が selector で、その直後が空白と `{` の規則ブロックの中身を返す。

    `.prompt-token-row--neg` のような、selector で始まる別の規則とは区別される。
    """
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.DOTALL)
    assert m is not None, f"規則ブロックが見つかりません: {selector}"
    return m.group(1)


# (a) タグ行は flex / gap をやめ、行内の並び（block）にする
def test_prompt_token_row_is_inline_flow_not_flex() -> None:
    block = _rule_block(_read("style.css"), ".prompt-token-row")
    assert "flex" not in block
    assert "gap" not in block
    assert re.search(r"display:\s*block", block)


# (b) 区切りの「, 」の見た目の規則があり、既存の変数だけを使う
def test_prompt_sep_rule_exists_and_uses_existing_variable() -> None:
    block = _rule_block(_read("style.css"), ".prompt-sep")
    assert "--color-fg-muted" in block


# (c) renderTokens が、語の間に区切りの span（「, 」）を入れ、見出しの直後では入れない
def test_render_tokens_inserts_comma_separator() -> None:
    js = _read("app.js")
    start = js.index("const renderTokens = () => {")
    end = js.index("if (text) {", start)
    body = js[start:end]
    assert "prompt-sep" in body
    assert '", "' in body
    assert "needSep = true" in body
    assert "needSep = false" in body


# ---------------------------------------------------------------------------
# 作業定義（工程 4 の作業 4-01：一覧の描画の軽量化）
# - 目的：クリックごとの全タイル作り直しと、新着通知ごとの丸ごとの取り直しをやめ、
#   一覧は最初の 400 枚を先に描いて残りを続けて追加する。取り直しの前後でスクロール位置を保つ。
# - 完了条件：下の全件と test_static_connectivity_contract.py（sw.js の版 v23）が通る。
#   実装前は失敗する（赤）。
# - 検証方法：`.venv\Scripts\python.exe -m pytest tests/test_static_ui_contract.py
#   tests/test_static_connectivity_contract.py -q`。実際の動き（クリック時間・スクロール位置・
#   選択の整合）は工程 5 の自動ブラウザの測定で確かめる。
# ---------------------------------------------------------------------------

def _func_body(js: str, name: str) -> str:
    """字下げ 2 文字の `function <name>(` から、次の字下げ 2 文字の閉じ括弧の行までを返す。"""
    m = re.search(r"(?m)^  (?:async )?function " + re.escape(name) + r"\(", js)
    assert m is not None, f"関数が見つかりません: {name}"
    end = js.find("\n  }\n", m.end())
    assert end != -1, f"関数の終わりが見つかりません: {name}"
    return js[m.start():end]


# (a) クリックは、全タイルの作り直しでなく、選択の枠の付け外しだけにする
def test_on_cell_click_toggles_classes_without_rendering_grid() -> None:
    body = _func_body(_read("app.js"), "onCellClick")
    assert "renderGrid(" not in body
    assert "updateSelectionClasses(" in body


# (b) 選択の枠の付け外しは classList.toggle で行う
def test_update_selection_classes_uses_class_toggle() -> None:
    body = _func_body(_read("app.js"), "updateSelectionClasses")
    assert "classList.toggle(" in body
    assert "is-selected" in body


# (c) renderGrid は分割描画・タイルの使い回し・スクロール位置の保持を行う
def test_render_grid_is_chunked_and_reuses_tiles() -> None:
    body = _func_body(_read("app.js"), "renderGrid")
    for needle in (
        "requestAnimationFrame(",
        "replaceChildren(",
        "renderGen",
        "scrollTop",
        "tileKeyOf(",
        "lastViewKey",
        "currentViewKey(",
    ):
        assert needle in body, needle


# (d) 背景クリックでの選択解除も、全タイルの作り直しをしない
def test_grid_background_click_does_not_render_grid() -> None:
    body = _func_body(_read("app.js"), "setupGridBackgroundClick")
    assert "renderGrid(" not in body


# (e) 新着通知による取り直しをまとめる関数がある
def test_schedule_reload_images_is_defined() -> None:
    js = _read("app.js")
    body = _func_body(js, "scheduleReloadImages")
    assert "setTimeout(" in body
    assert "400" in body


# (f) 3 つの通知（追加・削除・再スキャン完了）は、すぐ取り直さず、まとめる関数を呼ぶ
def test_sse_handlers_use_scheduled_reload() -> None:
    js = _read("app.js")
    ranges = [
        ('addEventListener("image_added"', 'addEventListener("image_removed"'),
        ('addEventListener("image_removed"', "// 再スキャン進捗"),
        ('addEventListener("folder_rescanned"', "es.onerror"),
    ]
    for start_mark, end_mark in ranges:
        start = js.index(start_mark)
        end = js.index(end_mark, start)
        body = js[start:end]
        assert "reloadImages();" not in body, start_mark
        assert "scheduleReloadImages(" in body, start_mark


# (g) タイルの使い回しの鍵は、ID に加えて sha1 とファイル名を含む（上書き・改名で古いタイルを残さない）
def test_tile_key_includes_sha1_and_filename() -> None:
    body = _func_body(_read("app.js"), "tileKeyOf")
    assert "sha1" in body
    assert "filename" in body


# ---------------------------------------------------------------------------
# 作業定義（工程 3 の開き直し：作業 3-02。画面の測定で見つけた折り返しの直し）
# - 目的：行内の並びにしたため、複数語のタグ（year 2025 など）が行の途中で折り返されてチップが割れた。
#   各タグを 1 つのチップのまま並べ、折り返しはタグとタグの間（「, 」の後）だけにする。
# - 完了条件：下のテストが実装前は失敗し、実装後は全件が通る。画面では、選択してコピーした文字列が
#   「a, b, c」（改行なし）のままである（工程 5 の測定の ui_tag_ok）。
# - 検証方法：`.venv\Scripts\python.exe -m pytest tests/test_static_ui_contract.py -q`。
# ---------------------------------------------------------------------------

# (h) タグのチップは inline-block（割れない）で、行より長いタグだけ枠内で折り返す
def test_prompt_token_is_inline_block_so_chips_do_not_split() -> None:
    block = _rule_block(_read("style.css"), ".prompt-token")
    assert re.search(r"display:\s*inline-block", block)
    assert re.search(r"max-width:\s*100%", block)
    assert re.search(r"overflow-wrap:\s*anywhere", block)


# ---------------------------------------------------------------------------
# 作業定義（計画 comfydir-instant-fresh の作業 2-01：開いている間の自動更新）
# - 目的：画面を開いた直後と開いている間、新着の確認 (POST /api/refresh) を自動で行い、
#   新しい順を既定にして、最新の画像が先頭に出るようにする。再スキャンの長い待ちを無くす。
# - 完了条件：下の全件が通る。実装前は失敗する（赤）。
# - 検証方法：`.venv\Scripts\python.exe -m pytest tests/test_static_ui_contract.py -q`。
#   実際の動き（追加から 45 秒以内に先頭へ出る）は、工程 3 の測定で確かめる。
# ---------------------------------------------------------------------------

# (i) 既定の並びは更新日時の新しい順
def test_default_order_is_mtime_desc() -> None:
    js = _read("app.js")
    assert 'order: prefs.order || "mtime"' in js
    assert 'direction: prefs.direction || "desc"' in js


# (j) 保存済みの「名前 ↑」は 1 回だけ新しい順へ移し、その後の利用者の選択は尊重する
def test_saved_name_asc_is_migrated_once() -> None:
    js = _read("app.js")
    assert "orderMigrated: 1" in js  # savePrefs が印を書く
    start = js.index("const prefs = loadPrefs();")
    end = js.index("// ---------------- 状態", start)
    block = js[start:end]
    assert "orderMigrated" in block
    assert '"name"' in block and '"asc"' in block
    assert '"mtime"' in block and '"desc"' in block


# (k) 新着の確認 requestRefresh：見えている間だけ、間隔を空けて POST /api/refresh
def test_request_refresh_posts_api_refresh_when_visible() -> None:
    body = _func_body(_read("app.js"), "requestRefresh")
    assert "/api/refresh" in body
    assert "POST" in body
    assert "visibilityState" in body
    assert "lastRefreshAt" in body


# (l) 自動更新の起動：フォアグラウンドへの復帰と 30 秒ごと。二重に起動しない
def test_start_auto_refresh_listens_visibility_and_interval() -> None:
    body = _func_body(_read("app.js"), "startAutoRefresh")
    assert "visibilitychange" in body
    assert "setInterval(" in body
    assert "30000" in body
    assert "autoRefreshStarted" in body


# (m) 接続の確立 (ready) で、新着の確認を 1 回行い、自動更新を始める
def test_ready_handler_requests_refresh_and_starts_auto_refresh() -> None:
    js = _read("app.js")
    start = js.index('addEventListener("ready"')
    end = js.index('addEventListener("image_added"', start)
    body = js[start:end]
    assert "requestRefresh(" in body
    assert "startAutoRefresh(" in body


# (n) 自動の完了通知 (auto) は、一覧を取り直し、回転表示を止めず、静かな文を出す
def test_folder_rescanned_handles_auto_notice_quietly() -> None:
    js = _read("app.js")
    start = js.index('addEventListener("folder_rescanned"')
    end = js.index("es.onerror", start)
    body = js[start:end]
    assert "p.auto" in body
    assert "新着" in body
    assert "scheduleReloadImages(" in body


# (o) ボタンは新着の確認。長い待ちを思わせる文言を持たず、Shift+クリックの案内を保つ
def test_rescan_button_is_a_new_arrivals_check() -> None:
    html = _read("index.html")
    m = re.search(r'<button id="btnRescan"[^>]*title="([^"]*)"', html)
    assert m is not None
    assert "新着の確認" in m.group(1) and "Shift" in m.group(1)
    js = _read("app.js")
    assert "約 55" not in js and "約55" not in js
    body = _func_body(js, "setRescanIndicator")
    assert "新着の確認" in body and "Shift" in body  # 完了後に戻す title にも案内を残す
