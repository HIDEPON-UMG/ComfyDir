"""PWA キャッシュ画面が API 生存を偽装しないための契約テスト。"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "comfy_image_organizer" / "static"


def test_statusbar_starts_as_connecting_not_connected() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "id=\"watchdogStatus\"" in html
    assert "接続確認中" in html
    assert "id=\"sseStatus\">connecting</span>" in html
    assert "watchdog 監視中</span>" not in html
    assert "id=\"relaunchLink\"" in html
    assert "comfydir://launch" in html


def test_app_marks_backend_offline_on_fetch_failure() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "function markBackendOnline()" in js
    assert "function markBackendOffline(" in js
    assert "ComfyDir 本体に接続できません" in js
    assert "es.onerror = () => {" in js
    assert "setSseStatus(\"disconnected\")" in js


def test_service_worker_does_not_cache_dynamic_api_fallbacks() -> None:
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")

    assert 'const VERSION = "v27";' in sw
    assert "event.respondWith(networkOnly(req));" in sw
    assert "async function networkOnly(req)" in sw
    assert "event.respondWith(networkFirst(req, RUNTIME));" not in sw


# 2026-11-09: 古い画面のキャッシュ（CacheFirst）が残り、直した CSS/JS が 1 回の再読み込みでは出なかった
# （利用者の画面で文字サイズが 12px のままだった）。index.html が読む CSS/JS の URL に sw.js の版を
# 付けておけば、古い Service Worker が動いていても、新しい URL はキャッシュに無いので取りに行く。
def test_asset_urls_carry_the_service_worker_version() -> None:
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    ver = re.search(r'const VERSION = "(v\d+)";', sw).group(1)
    for path in ("/static/style.css", "/static/app.js"):
        assert f"{path}?v={ver}" in html, path
        assert f'"{path}?v={ver}"' in sw, path  # precache も同じ URL


# index.html 自体も、通常のページ移動で古いものをキャッシュから出さない（sw.js と同じく no-cache）
def test_index_html_is_revalidated_every_time() -> None:
    main = (ROOT / "src" / "comfy_image_organizer" / "main.py").read_text(encoding="utf-8")
    m = re.search(r"def index\(\).*?\n\n\n", main, re.S)
    assert m and "no-cache" in m.group(0)
