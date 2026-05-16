"""FastAPI アプリ本体。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import scanner
from .config import ASSETS_DIR, ICON_PATH, LOG_PATH, STATIC_DIR
from .db import connect, init_schema
from .routes import router

# ルートロガー: コンソール (StreamHandler) + ファイル (RotatingFileHandler)
# pythonw.exe / VBS hidden 起動でも、ログは data/server.log に必ず残る。
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)
_file_handler = RotatingFileHandler(
    str(LOG_PATH), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
logging.getLogger().addHandler(_file_handler)
log = logging.getLogger("comfy_image_organizer")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # スキーマ初期化
    conn = connect()
    try:
        init_schema(conn)
    finally:
        conn.close()

    # Scanner にイベントループを束ねる + 既存登録フォルダの監視を開始
    loop = asyncio.get_event_loop()
    scanner.manager.bind_loop(loop)
    scanner.manager.start_all()

    # 起動時に各フォルダを 1 回フルスキャン (バックグラウンド)
    def _initial_scan() -> None:
        c = connect()
        try:
            from . import repo
            for f in repo.list_folders(c):
                try:
                    scanner.full_scan(int(f["id"]), f["path"])
                except Exception as e:
                    log.warning("起動時スキャン失敗: folder_id=%s (%s)", f["id"], e)
        finally:
            c.close()

    import threading
    threading.Thread(target=_initial_scan, daemon=True).start()

    log.info("ComfyImageOrganizer ready")
    try:
        yield
    finally:
        scanner.manager.stop_all()


app = FastAPI(title="ComfyDir", lifespan=lifespan)

# CORS: ComfyUI (例: http://127.0.0.1:8188) のブラウザから本サーバの
# /api/images/{id}/preview を fetch してワークフロー復元 (D&D 経由) する
# ために、ローカルからの cross-origin GET を許可する。
# ComfyUI の eventUtils.ts は fetch エラー時に空配列を握り潰して何も
# 起こさないため、ここで preflight 含めて確実に通す必要がある。
# 読み取り専用 + 127.0.0.1 バインドのみなので影響範囲は限定的。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)

# 静的ファイル
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# PWA アイコン群 (icon-192.png / icon-512.png / app.ico) を /assets/* で配信
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

# API
app.include_router(router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """ブラウザのタブに表示されるアイコン (assets/app.ico を流用)。"""
    return FileResponse(ICON_PATH, media_type="image/x-icon")


# ---------------- PWA ----------------

@app.get("/manifest.json", include_in_schema=False)
def manifest() -> FileResponse:
    """PWA manifest。Chromium 系の install プロンプトが参照する。"""
    return FileResponse(
        STATIC_DIR / "manifest.json",
        media_type="application/manifest+json",
    )


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    """Service Worker 本体。Service-Worker-Allowed: / で scope をルートに広げる。

    `Cache-Control: no-cache` で SW 自体は常に最新を取りに行かせる
    (古い sw.js が browser cache から返ると新 VERSION が反映されないため)。
    """
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache",
        },
    )


@app.get("/offline.html", include_in_schema=False)
def offline_page() -> FileResponse:
    """サーバ未起動時の fallback ページ (SW がキャッシュ済の HTML を返す)。"""
    return FileResponse(STATIC_DIR / "offline.html", media_type="text/html")
