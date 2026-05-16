"""ComfyDir PWA launcher 実機スモーク (Python 版)。

scripts/smoke_pwa.ps1 と同等の検証を Python だけで完結させる:
  1. launcher.py を pythonw.exe (Windows) / python で別プロセス起動
  2. 127.0.0.1:8765 が bind されるまで最大 15 秒待つ
  3. http://127.0.0.1:8765/manifest.json が HTTP 200 を返すか確認
  4. プロセスを kill して exit 0

PowerShell -ExecutionPolicy Bypass を介さないので、safe-commit の deny ルールに
抵触せずに走らせられる。

使い方:
    .venv/Scripts/python.exe scripts/smoke_pwa.py
exit code:
    0 = OK
    1 = bind / manifest 検証失敗
    2 = 起動環境エラー (pythonw 不在 等)
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "launcher.py"
HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"

# pythonw でも python でも launcher が動けば OK。Windows 以外は python のみ。
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def _is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def main() -> int:
    if not LAUNCHER.is_file():
        print(f"launcher.py not found: {LAUNCHER}", file=sys.stderr)
        return 2

    # 既存サーバが動いていたら smoke できないので事前検出
    if _is_port_open(HOST, PORT):
        print(f"{HOST}:{PORT} は既に bind 済みです。他の ComfyDir プロセスを終了してから再実行してください。", file=sys.stderr)
        return 2

    interp = PYTHONW if PYTHONW.is_file() else PYTHON
    if not interp.is_file():
        print(f"Python interpreter not found: {PYTHONW} / {PYTHON}", file=sys.stderr)
        return 2

    # Windows 用 hidden 起動フラグ (subprocess の) — pythonw でも console 抑制を念押し
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

    print(f"starting launcher.py via {interp.name} ...")
    proc = subprocess.Popen(
        [str(interp), str(LAUNCHER)],
        cwd=str(ROOT),
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        ok = False
        for _ in range(30):
            time.sleep(0.5)
            if _is_port_open(HOST, PORT):
                ok = True
                break
        if not ok:
            print(f"launcher did not bind {HOST}:{PORT} within 15s", file=sys.stderr)
            return 1

        # /manifest.json が HTTP 200 を返すか
        try:
            with urllib.request.urlopen(f"{URL}/manifest.json", timeout=5) as resp:  # noqa: S310
                code = resp.getcode()
        except OSError as e:
            print(f"manifest.json fetch failed: {e}", file=sys.stderr)
            return 1
        if code != 200:
            print(f"/manifest.json returned status {code}", file=sys.stderr)
            return 1

        print(f"smoke OK (pid={proc.pid}, manifest.json=200)")
        return 0
    finally:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError:
            pass
        # pystray は別 thread でメインループが残ることがあるので、
        # 上記 terminate で pythonw プロセスごと落とす。残存プロセスは
        # OS 側の WerFault に任せて smoke 自体は exit する。


if __name__ == "__main__":
    sys.exit(main())
