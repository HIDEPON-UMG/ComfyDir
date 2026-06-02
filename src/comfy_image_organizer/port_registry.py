"""ProjectFolders ローカルアプリのポート割当を解決する小さなレジストリ reader。

複数のローカルアプリ (ComfyDir / CCA-StudyApp / notebooklm-mcp 等) が同じポートを
既定値にして奪い合う事故 (2026-06-02: ComfyDir の画面に CCA の Basic 認証が出た件)
の再発防止として導入。CCA-StudyApp 側にも同等の reader (``app/port_registry.py``) を
置く。リポジトリ間で import はしない (各プロジェクトは独立) ── 共有するのはデータ
(``ports.json``) であってコードではない。

単一の真実の源 (single source of truth):
    %LOCALAPPDATA%\\ProjectFolders\\ports.json
    (環境変数 PROJECTFOLDERS_PORTS_FILE で上書き可)

    {
      "ports": {
        "notebooklm-mcp": 8765,
        "cca-pwa": 8770,
        "cca-mcp": 8771,
        "comfydir": 8772
      }
    }

優先順位 (resolve_port):
    1. 明示的な環境変数 (例 CIO_PORT)   ← power user の上書き
    2. レジストリファイルの当該キー       ← 通常はここで決まる
    3. コード内の既定値                  ← ファイルが無いときの安全弁

ファイルが無くても各アプリの既定値が互いに重複しないので衝突は起きない。
レジストリは「将来アプリを足したときに重複を 1 箇所で検査できる」ための仕組み。
本モジュールは read-only (ファイルは別途人手で作成 / README 参照)。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def registry_path() -> Path:
    """ports.json の絶対パス。PROJECTFOLDERS_PORTS_FILE 環境変数で上書き可。"""
    override = os.environ.get("PROJECTFOLDERS_PORTS_FILE")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "ProjectFolders" / "ports.json"


def find_duplicate_ports(ports: dict[str, int]) -> list[tuple[str, str, int]]:
    """同一ポートに割り当てられたキーの組を (先のキー, 後のキー, port) で返す。"""
    seen: dict[int, str] = {}
    dups: list[tuple[str, str, int]] = []
    for key, port in ports.items():
        if port in seen:
            dups.append((seen[port], key, port))
        else:
            seen[port] = key
    return dups


def load_registry() -> dict[str, int]:
    """ports.json を読んで {key: port} を返す。無ければ {}。重複ポートは warn。"""
    path = registry_path()
    try:
        # utf-8-sig: 万一 BOM 付きで保存されても壊れないように許容する
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("ポートレジストリの読込に失敗 (既定値で継続): %s (%s)", path, e)
        return {}
    section = raw.get("ports", {}) if isinstance(raw, dict) else {}
    out: dict[str, int] = {}
    for k, v in section.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    for first, second, port in find_duplicate_ports(out):
        log.warning(
            "ポートレジストリにポート重複: '%s' と '%s' が両方 %d (%s)",
            first, second, port, path,
        )
    return out


def resolve_port(key: str, *, env_var: str, default: int) -> int:
    """key のポートを 環境変数 > レジストリ > 既定値 の優先順位で解決する。"""
    raw = os.environ.get(env_var)
    if raw:
        try:
            return int(raw)
        except ValueError:
            log.warning("%s=%r は整数でないため無視 (レジストリ/既定値で継続)", env_var, raw)
    reg = load_registry()
    if key in reg:
        return reg[key]
    return default
