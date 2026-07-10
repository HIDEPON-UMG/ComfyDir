"""PWA キャッシュ画面が API 生存を偽装しないための契約テスト。"""
from __future__ import annotations

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

    assert 'const VERSION = "v22";' in sw
    assert "event.respondWith(networkOnly(req));" in sw
    assert "async function networkOnly(req)" in sw
    assert "event.respondWith(networkFirst(req, RUNTIME));" not in sw
