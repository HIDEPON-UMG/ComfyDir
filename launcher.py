"""ComfyDir タスクトレイ launcher.

- 同プロセス内で uvicorn を別 thread 起動 (`Server.should_exit = True` で停止)
- pystray でタスクトレイ常駐
- アイコンクリック / 「開く」で Edge or Chrome を `--app=URL` モードで起動
- 既に PWA ウィンドウあれば前面化 (FindWindowW + SetForegroundWindow / EnumWindows 部分一致 fallback)
- 「終了」で uvicorn 停止 + tray 終了

使い方:
    .venv\\Scripts\\pythonw.exe launcher.py
    または start.vbs 経由 (コンソール窓なし)
"""
from __future__ import annotations

import ctypes
import http.client
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# pythonw.exe / VBS hidden 起動で stdout/stderr が None になっても落ちないように差し替え
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import uvicorn  # noqa: E402
import pystray  # noqa: E402
from PIL import Image  # noqa: E402

from comfy_image_organizer.config import HOST, PORT, ICON_PATH  # noqa: E402
from comfy_image_organizer.port_registry import registry_path  # noqa: E402

log = logging.getLogger("comfydir.launcher")

URL = f"http://{HOST}:{PORT}"

# index.html の <title>ComfyDir</title> と完全一致 + Edge `--app=` で末尾に
# ` - <browser>` が付いた場合の部分一致 fallback で前面化する。
WINDOW_TITLE = "ComfyDir"

# Chrome / Edge を起動するときに使う ComfyDir 専用プロファイルディレクトリ。
# 普段使いの Chrome プロファイル (Google アカウントアバター付き) を流用すると、
# タスクバーアイコンに Profile Badging (アバターオーバーレイ) が出てしまい、
# ComfyDir のアイコンが汚れて見える。専用プロファイル (Google 未ログイン) を
# 使うと Chrome は badging を描画しないので、純粋な ComfyDir アイコンが出る。
# 配置先: %LocalAppData%\ComfyDir\ChromeProfile (ユーザー単位ローカル, OneDrive 同期外)
_PROFILE_PARENT = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
CHROME_PROFILE_DIR = Path(_PROFILE_PARENT) / "ComfyDir" / "ChromeProfile"

# subprocess の hidden 起動用フラグ
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

# ---------------- Win32 API (ctypes) ----------------

_user32 = ctypes.windll.user32
_user32.FindWindowW.restype = ctypes.c_void_p
_user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
_user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
_user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
_user32.IsWindowVisible.restype = ctypes.c_bool
_user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
_user32.GetWindowThreadProcessId.restype = ctypes.c_ulong

_kernel32 = ctypes.windll.kernel32
_kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)
]
_kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
_user32.EnumWindows.argtypes = [WNDENUMPROC, ctypes.c_void_p]

# pythonw 起動はコンソールを持たないので、ポート衝突などの致命的事象は
# MessageBox でユーザーに見せないと黙って終了したように見える。
_user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
_user32.MessageBoxW.restype = ctypes.c_int
MB_ICONWARNING = 0x00000030

SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 部分一致 fallback で前面化対象とする owner プロセス名 (小文字)
_BROWSER_EXES = {"msedge.exe", "chrome.exe", "chromium.exe", "brave.exe"}


def _process_name_of(hwnd: int) -> str:
    """hwnd の owner プロセス実行ファイル名 (basename, 小文字) を返す。"""
    pid = ctypes.c_ulong(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == 0:
        return ""
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(buf))
        if not _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        _kernel32.CloseHandle(h)


def _find_browser_window_titled(title: str) -> int | None:
    """タイトルが title で始まり、かつ owner が msedge/chrome 系のウィンドウを返す。

    VSCode 等の「ComfyDir を含むタイトル」を誤検出しないように、
    プロセス名で msedge.exe / chrome.exe / chromium.exe / brave.exe に絞る。
    Edge `--app=` 起動だとタイトルが完全 "ComfyDir" になることが多いが、
    バージョン差で ` - <browser>` が付くケースに備えて prefix 一致でも拾う。
    """
    found: list[int] = []

    def _enum(hwnd: int, _lp: int) -> bool:
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        t = buf.value
        # 完全一致 or "ComfyDir - ..." 形式のみ受け入れる
        # (VSCode の "Fix ComfyDir rescan func..." のような誤検出を排除)
        if t != title and not t.startswith(title + " "):
            return True
        if _process_name_of(hwnd) not in _BROWSER_EXES:
            return True
        found.append(hwnd)
        return False

    _user32.EnumWindows(WNDENUMPROC(_enum), 0)
    return found[0] if found else None


def _find_comfydir_window() -> int | None:
    # 完全一致を優先 (高速、Edge --app= ではこちらでヒットすることが多い)
    hwnd = _user32.FindWindowW(None, WINDOW_TITLE)
    if hwnd:
        # 完全一致でも誤検出を防ぐためにプロセス名で確認
        if _process_name_of(hwnd) in _BROWSER_EXES:
            return hwnd
    # フォールバック: タイトル prefix + browser owner で絞る
    return _find_browser_window_titled(WINDOW_TITLE)


# ---------------- uvicorn (thread 起動) ----------------

_server: uvicorn.Server | None = None
_server_thread: threading.Thread | None = None


def start_server_thread() -> None:
    """uvicorn を別 thread で起動。ポート open まで最大 10s 待つ。"""
    global _server, _server_thread

    # thread からシグナルハンドラを登録できないので、登録処理をパッチでスキップ
    uvicorn.Server.install_signal_handlers = lambda *a, **kw: None  # type: ignore[assignment]

    config = uvicorn.Config(
        "comfy_image_organizer.main:app",
        host=HOST, port=PORT, log_level="info", reload=False,
    )
    _server = uvicorn.Server(config)
    _server_thread = threading.Thread(target=_server.run, daemon=True, name="uvicorn")
    _server_thread.start()

    # 自分の uvicorn が応答するまで待つ (最大 10 秒)。
    # 重要: bare connect 成功で「起動した」と判定してはいけない。別アプリが同ポートを
    # 先取りしていると connect は相手に成功してしまい、誤って「起動済み」と見なして
    # ブラウザを相手アプリに誘導する事故 (2026-06-02 CCA の Basic 認証ダイアログ) を
    # 招く。よって ComfyDir 本人の応答 (manifest に "comfydir") を確認する。
    for _ in range(50):
        if _responds_as_comfydir():
            return
        time.sleep(0.2)
    log.warning("uvicorn が 10 秒以内に %s:%s で ComfyDir として応答しませんでした", HOST, PORT)


def stop_server() -> None:
    global _server
    if _server is not None:
        _server.should_exit = True


# ---------------- comfydir:// プロトコル登録 ----------------

def register_url_protocol() -> None:
    """`comfydir://` カスタム URL プロトコルを HKCU に登録する (冪等)。

    オフライン画面 (offline.html) は Service Worker のキャッシュ配信なので、
    バックエンド (uvicorn) が落ちている状態で表示される。ブラウザのサンドボックスは
    ローカルの .vbs を直接実行できないため、画面内の「ComfyDir を起動」ボタンは
    `location.href = 'comfydir://launch'` で OS 側のプロトコルハンドラを叩く。
    そのハンドラとして wscript.exe + start.vbs を登録しておく。

    launcher.py の起動経路 = 過去に一度でも正常起動できた = SW が登録済みなので、
    起動のたびにここで登録し直しておけば、次回サーバが落ちたときの offline.html から
    ワンクリックで起動し直せる。HKCU のみを書くので管理者権限は不要・可逆。
    """
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    wscript = Path(system_root) / "System32" / "wscript.exe"
    vbs = ROOT / "start.vbs"
    # start.vbs は引数を見ないので %1 は付けない (URL を phantom arg として渡さない)
    command = f'"{wscript}" "{vbs}"'
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\comfydir") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:ComfyDir Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, r"Software\Classes\comfydir\shell\open\command"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        log.info("comfydir:// プロトコル登録: %s", command)
    except OSError as e:
        # 登録に失敗しても本体起動は続行 (offline 画面のボタンが効かないだけ)
        log.warning("comfydir:// プロトコル登録に失敗 (継続): %s", e)


# ---------------- ブラウザ起動 ----------------

def find_browser() -> str | None:
    """Chrome → Edge の順で実行ファイルパスを返す。

    Chrome 優先の理由: ユーザー指定。タスクバーピン留め時のアプリアイコン取り扱いも
    Chrome の方が PWA インストール後ショートカットの解像度選定が素直 (192/512 を直接使う)。
    """
    for name in ("chrome", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    # 既知パスフォールバック (Chrome を先頭に)
    for c in (
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ):
        if Path(c).is_file():
            return c
    return None


def open_or_focus_window() -> None:
    """既存 ComfyDir ウィンドウあれば前面化、なければ Chrome/Edge を新規ウィンドウで起動。

    重要: `--app=URL` モードは **使わない**。Chrome の `--app=` 起動ウィンドウは
    display-mode: standalone と判定され、PWA installability の `beforeinstallprompt`
    が発火しなくなるため、画面内「インストール」ボタンが永久に no-op になる。
    代わりに `--new-window URL` (通常のブラウザウィンドウ) で開き、アドレスバー右の
    インストールアイコン or 画面内 #btnInstall から PWA をインストールしてもらう。
    インストール完了後はスタートメニュー / タスクバーピン留めから直接 PWA を
    起動でき、そちらが本来の「アプリ感」(独自アイコン + standalone) を提供する。
    """
    hwnd = _find_comfydir_window()
    if hwnd:
        _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.SetForegroundWindow(hwnd)
        return

    browser = find_browser()
    if not browser:
        # 最終フォールバック: 既定ブラウザで通常タブ起動
        try:
            os.startfile(URL)  # noqa: S606
        except OSError as e:
            log.warning("既定ブラウザの起動に失敗: %s", e)
        return

    # 専用プロファイルディレクトリを用意 (初回のみ)。
    # 既存ディレクトリでも mkdir(exist_ok=True) は no-op なので毎回呼んで安全。
    try:
        CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("ComfyDir 用プロファイルディレクトリ作成失敗: %s (普段使いプロファイルで続行)", e)

    try:
        subprocess.Popen(
            [
                browser,
                # ComfyDir 専用プロファイル: Google アカウント未ログインなので
                # Chrome がタスクバーアイコンに Profile Badging (アバター) を重ねない。
                f"--user-data-dir={CHROME_PROFILE_DIR}",
                "--new-window",
                URL,
                "--no-first-run",
                "--no-default-browser-check",
                # アプリ感を出すための初期ウィンドウサイズ (1440x900 = 16:10 で
                # ComfyDir の 2 ペイン構成が綺麗に収まる)。
                "--window-size=1440,900",
            ],
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
            close_fds=True,
        )
    except OSError as e:
        log.warning("ブラウザ起動失敗: %s", e)


# ---------------- pystray ----------------

def _on_open(icon, _item):
    open_or_focus_window()


def _on_quit(icon, _item):
    icon.stop()
    stop_server()


def _build_icon() -> pystray.Icon:
    image = Image.open(ICON_PATH)
    menu = pystray.Menu(
        # default=True で trayアイコンのシングルクリックがこのアクションを発火する
        pystray.MenuItem("ComfyDir を開く", _on_open, default=True),
        pystray.MenuItem("終了", _on_quit),
    )
    return pystray.Icon("comfydir", image, "ComfyDir", menu)


# ---------------- main ----------------

def _port_open() -> bool:
    """HOST:PORT に誰かが LISTEN しているか (本人かは問わない)。"""
    with socket.socket() as s:
        s.settimeout(0.3)
        try:
            s.connect((HOST, PORT))
            return True
        except OSError:
            return False


def _responds_as_comfydir() -> bool:
    """HOST:PORT が ComfyDir 本人として応答するか (GET /manifest.json に "comfydir")。"""
    if not _port_open():
        return False
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=1.0)
        try:
            conn.request("GET", "/manifest.json")
            resp = conn.getresponse()
            ok = resp.status == 200
            body = resp.read(4096).decode("utf-8", errors="replace") if ok else ""
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return False
    return ok and "comfydir" in body.lower()


def _is_existing_comfydir_listening() -> bool:
    """既存 ComfyDir 本体が `HOST:PORT` で LISTEN 中なら True。

    `connect` できるだけでは不十分: 別アプリ (例 CCA-StudyApp も以前は既定 8770)
    が同じポートを先に奪っていると「既存 ComfyDir あり」と誤判定し、ブラウザを
    別アプリに誘導してしまう。そこで本人マーカー "comfydir" の応答を確認する。

    多重起動防止 (過去に tray だけ残る累積, 2026-05-21 確認 14 プロセス) も、
    本物の ComfyDir なら manifest が一致するので従来どおり機能する。
    """
    return _responds_as_comfydir()


def _port_held_by_foreign() -> bool:
    """ポートは開いているが ComfyDir 本人ではない = 別アプリが先取りしている。"""
    return _port_open() and not _responds_as_comfydir()


def _notify_port_conflict() -> None:
    """ポートが別アプリに奪われている旨を MessageBox で通知する (pythonw 対策)。"""
    msg = (
        f"ComfyDir はポート {PORT} を使う設定ですが、\n"
        f"別のアプリが既にこのポートを使用しています。\n\n"
        f"ComfyDir は誤って相手アプリの画面 (例: ログイン要求) を\n"
        f"開かないよう、起動を中止しました。\n\n"
        f"対処:\n"
        f"  ・相手アプリを終了してから ComfyDir を起動し直す\n"
        f"  ・または ports.json で 'comfydir' のポートを別番号に変える\n"
        f"    ({registry_path()})"
    )
    try:
        _user32.MessageBoxW(None, msg, "ComfyDir: ポート競合のため起動中止", MB_ICONWARNING)
    except Exception as e:  # noqa: BLE001 — 通知失敗は致命的でない
        log.warning("ポート競合の通知 (MessageBox) に失敗: %s", e)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info("launcher start: URL=%s", URL)

    # offline.html の「ComfyDir を起動」ボタン用に comfydir:// を登録 (冪等・HKCU)
    register_url_protocol()

    # シングルトン保護: 既に ComfyDir が起動済みなら新サーバを起動せず、
    # 既存ウィンドウを前面化 (or 新ウィンドウ作成) して自分は即終了する。
    # start.vbs を二重ダブルクリックしても pythonw / uvicorn / tray が
    # 累積しないようにするための入口ガード。
    if _is_existing_comfydir_listening():
        log.info("既存の ComfyDir を %s で検出 → 既存に処理を委譲して終了", URL)
        try:
            open_or_focus_window()
        except Exception as e:
            log.warning("既存ウィンドウの前面化に失敗 (継続して終了): %s", e)
        return 0

    # ポートが別アプリに奪われている場合は、自分の uvicorn を起動しても bind に
    # 失敗し、かつブラウザを相手アプリに誘導して誤った画面 (例: CCA の Basic 認証
    # ダイアログ) を見せてしまう。ここで明示的に中止し、ユーザーに通知する。
    # 通常は port_registry でアプリごとに一意なポートを割り当てるので、この分岐は
    # 環境変数の上書き衝突や第3のアプリが居る異常時の安全網。
    if _port_held_by_foreign():
        log.error("ポート %s:%s が ComfyDir 以外のアプリに使用されています → 起動中止", HOST, PORT)
        _notify_port_conflict()
        return 3

    start_server_thread()

    # 起動直後にウィンドウを 1 回開く (UX 改善)
    threading.Timer(0.5, open_or_focus_window).start()

    icon = _build_icon()
    icon.run()  # ここでブロックし、_on_quit で icon.stop() が呼ばれるまで戻らない

    # tray 終了後、uvicorn の停止待ち (最大 3 秒)
    if _server_thread is not None:
        _server_thread.join(timeout=3.0)
    log.info("launcher exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
