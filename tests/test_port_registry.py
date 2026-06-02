"""ポートレジストリ reader の契約テスト。

このテストが守る不変条件 (= なぜ重要か):
- ポート解決の優先順位は「環境変数 > レジストリ > 既定値」でなければならない。
  これが崩れると、共有レジストリで一意ポートを割り当てても別アプリと衝突し、
  2026-06-02 の「ComfyDir の画面に CCA の Basic 認証が出る」事故が再発する。
- 同一ポートを 2 つのキーに割り当てた設定ミスは検出できなければならない
  (将来アプリを足したときの重複を 1 箇所で弾く = illegal state を表現させない)。
"""
from __future__ import annotations

import json

from comfy_image_organizer import port_registry


def _write_registry(tmp_path, mapping: dict[str, int]):
    p = tmp_path / "ports.json"
    p.write_text(json.dumps({"ports": mapping}), encoding="utf-8")
    return p


def test_env_var_takes_precedence_over_registry(tmp_path, monkeypatch):
    reg = _write_registry(tmp_path, {"comfydir": 8772})
    monkeypatch.setenv("PROJECTFOLDERS_PORTS_FILE", str(reg))
    monkeypatch.setenv("CIO_PORT", "9999")
    assert port_registry.resolve_port("comfydir", env_var="CIO_PORT", default=8772) == 9999


def test_registry_used_when_env_absent(tmp_path, monkeypatch):
    reg = _write_registry(tmp_path, {"comfydir": 8772})
    monkeypatch.setenv("PROJECTFOLDERS_PORTS_FILE", str(reg))
    monkeypatch.delenv("CIO_PORT", raising=False)
    assert port_registry.resolve_port("comfydir", env_var="CIO_PORT", default=1234) == 8772


def test_default_used_when_registry_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECTFOLDERS_PORTS_FILE", str(tmp_path / "does_not_exist.json"))
    monkeypatch.delenv("CIO_PORT", raising=False)
    assert port_registry.resolve_port("comfydir", env_var="CIO_PORT", default=8772) == 8772


def test_invalid_env_var_falls_back_to_registry(tmp_path, monkeypatch):
    reg = _write_registry(tmp_path, {"comfydir": 8772})
    monkeypatch.setenv("PROJECTFOLDERS_PORTS_FILE", str(reg))
    monkeypatch.setenv("CIO_PORT", "not-an-int")
    assert port_registry.resolve_port("comfydir", env_var="CIO_PORT", default=1) == 8772


def test_duplicate_ports_detected():
    dups = port_registry.find_duplicate_ports(
        {"comfydir": 8770, "cca-pwa": 8770, "cca-mcp": 8771}
    )
    assert ("comfydir", "cca-pwa", 8770) in dups
    # 重複していない cca-mcp は含まれない
    assert all(8771 != port for _, _, port in dups)


def test_no_duplicates_returns_empty():
    assert port_registry.find_duplicate_ports(
        {"notebooklm-mcp": 8765, "cca-pwa": 8770, "cca-mcp": 8771, "comfydir": 8772}
    ) == []
