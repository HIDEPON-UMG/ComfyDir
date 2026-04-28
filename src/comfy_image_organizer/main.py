"""FastAPI アプリ本体。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import scanner
from .config import ICON_PATH, LOG_PATH, STATIC_DIR
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

# 静的ファイル
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# API
app.include_router(router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """ブラウザのタブに表示されるアイコン (assets/app.ico を流用)。"""
    return FileResponse(ICON_PATH, media_type="image/x-icon")
