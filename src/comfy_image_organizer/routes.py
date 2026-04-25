"""FastAPI ルート定義。"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import repo, scanner, thumbnail
from .config import IMAGE_EXTS
from .db import get_conn

log = logging.getLogger(__name__)
router = APIRouter()


# ---------- Pydantic models ----------

class FolderCreate(BaseModel):
    path: str
    label: str | None = None


class TagAssignRequest(BaseModel):
    image_ids: list[int] = Field(default_factory=list)
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class RenameRequest(BaseModel):
    filename: str  # 拡張子は元と同一を強制


class MoveRequest(BaseModel):
    image_ids: list[int] = Field(default_factory=list)
    # 登録フォルダ指定モード
    dest_folder_id: int | None = None
    subdir: str | None = None
    # 任意パス指定モード (dest_folder_id が無いときに使う)
    dest_path: str | None = None
    create_dir: bool = False


# ---------- folders ----------

@router.get("/api/folders")
def list_folders(conn=Depends(get_conn)) -> list[dict[str, Any]]:
    rows = repo.list_folders(conn)
    out: list[dict[str, Any]] = []
    for r in rows:
        # 各フォルダの画像件数を添える (UIでの表示に便利)
        cnt = conn.execute(
            "SELECT COUNT(*) AS n FROM images WHERE folder_id = ?", (r["id"],)
        ).fetchone()["n"]
        out.append({
            "id": r["id"],
            "path": r["path"],
            "label": r["label"] or Path(r["path"]).name,
            "added_at": r["added_at"],
            "image_count": cnt,
        })
    return out


@router.post("/api/folders")
def create_folder(body: FolderCreate, conn=Depends(get_conn)) -> dict[str, Any]:
    p = Path(body.path).expanduser()
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail=f"フォルダが存在しません: {p}")
    abs_path = str(p.resolve())

    existing = conn.execute(
        "SELECT id FROM folders WHERE path = ?", (abs_path,)
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="このフォルダは既に登録済みです")

    row = repo.add_folder(conn, abs_path, body.label)
    folder_id = int(row["id"])

    # バックグラウンドでフルスキャン + watchdog 開始
    def _bootstrap() -> None:
        scanner.full_scan(folder_id, abs_path)
        scanner.manager.start_folder(folder_id, abs_path)
    threading.Thread(target=_bootstrap, daemon=True).start()

    return {
        "id": folder_id,
        "path": row["path"],
        "label": row["label"] or Path(row["path"]).name,
        "added_at": row["added_at"],
        "image_count": 0,
    }


@router.delete("/api/folders/{folder_id}")
def delete_folder(folder_id: int, conn=Depends(get_conn)) -> dict[str, Any]:
    if repo.get_folder(conn, folder_id) is None:
        raise HTTPException(status_code=404, detail="フォルダが見つかりません")
    scanner.manager.stop_folder(folder_id)
    n = repo.delete_folder(conn, folder_id)
    return {"deleted": n}


@router.post("/api/folders/{folder_id}/rescan")
def rescan_folder(folder_id: int, conn=Depends(get_conn)) -> dict[str, Any]:
    f = repo.get_folder(conn, folder_id)
    if f is None:
        raise HTTPException(status_code=404, detail="フォルダが見つかりません")

    def _task() -> None:
        scanner.full_scan(folder_id, f["path"])
        # まだ Observer が起動していなければ開始
        scanner.manager.start_folder(folder_id, f["path"])
    threading.Thread(target=_task, daemon=True).start()
    return {"started": True}


# ---------- images ----------

@router.get("/api/images")
def list_images(
    folder_id: int | None = Query(default=None),
    tags: str = Query(default=""),
    tag_mode: str = Query(default="and", pattern="^(and|or)$"),
    order: str = Query(default="name", pattern="^(name|mtime|added)$"),
    direction: str = Query(default="asc", pattern="^(?i)(asc|desc)$"),
    q: str = Query(default=""),
    conn=Depends(get_conn),
) -> list[dict[str, Any]]:
    tag_names = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    tag_ids: list[int] = []
    for n in tag_names:
        tid = repo.get_tag_id(conn, n)
        if tid is None:
            # 存在しないタグでフィルタ → 空集合
            return []
        tag_ids.append(tid)

    rows = repo.search_images(
        conn,
        folder_id=folder_id,
        tag_ids=tag_ids or None,
        tag_mode=tag_mode,
        order=order,
        direction=direction,
        prompt_query=q.strip() or None,
    )
    return [
        {
            "id": r["id"],
            "folder_id": r["folder_id"],
            "filename": r["filename"],
            "sha1": r["sha1"],
            "size": r["size"],
            "mtime": r["mtime"],
            "width": r["width"],
            "height": r["height"],
        }
        for r in rows
    ]


@router.get("/api/images/{image_id}")
def get_image_detail(image_id: int, conn=Depends(get_conn)) -> dict[str, Any]:
    r = repo.get_image(conn, image_id)
    if r is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    tags = repo.list_tags_for_image(conn, image_id)
    return {
        "id": r["id"],
        "folder_id": r["folder_id"],
        "path": r["path"],
        "filename": r["filename"],
        "sha1": r["sha1"],
        "size": r["size"],
        "mtime": r["mtime"],
        "width": r["width"],
        "height": r["height"],
        "positive_prompt": r["positive_prompt"],
        "negative_prompt": r["negative_prompt"],
        "added_at": r["added_at"],
        "tags": tags,
    }


@router.get("/api/images/{image_id}/preview")
def get_preview(image_id: int, conn=Depends(get_conn)) -> FileResponse:
    r = repo.get_image(conn, image_id)
    if r is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    p = Path(r["path"])
    if not p.exists():
        raise HTTPException(status_code=410, detail="ファイルが消えています")
    return FileResponse(p, media_type="image/png")


@router.get("/api/images/{image_id}/thumb")
def get_thumb(
    image_id: int,
    w: int = Query(default=256, ge=32, le=2048),
    conn=Depends(get_conn),
) -> FileResponse:
    r = repo.get_image(conn, image_id)
    if r is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    try:
        thumb_path = thumbnail.get_or_create_thumb(r["path"], r["sha1"], w)
    except FileNotFoundError:
        raise HTTPException(status_code=410, detail="ファイルが消えています")
    return FileResponse(thumb_path, media_type="image/webp")


@router.post("/api/images/{image_id}/rename")
def rename_image(
    image_id: int, body: RenameRequest, conn=Depends(get_conn)
) -> dict[str, Any]:
    r = repo.get_image(conn, image_id)
    if r is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません")

    src = Path(r["path"])
    if not src.exists():
        raise HTTPException(status_code=410, detail="ファイルが消えています")

    # 拡張子は強制的に元と同一にする (UI 側でも前段のみ編集可能)
    new_stem = body.filename.strip()
    if not new_stem:
        raise HTTPException(status_code=400, detail="ファイル名が空です")
    # ユーザーが拡張子を含めて送ってきても許容するが、最終的に src.suffix を強制
    new_stem_path = Path(new_stem)
    new_name = new_stem_path.stem + src.suffix
    if any(c in new_name for c in '\\/:*?"<>|'):
        raise HTTPException(status_code=400, detail="ファイル名に使えない文字が含まれます")

    dst = src.with_name(new_name)
    if dst == src:
        return {"id": image_id, "filename": src.name, "path": str(src)}
    if dst.exists():
        raise HTTPException(status_code=409, detail=f"既に同名ファイルがあります: {new_name}")

    src.rename(dst)
    repo.update_image_path(conn, image_id, str(dst))
    return {"id": image_id, "filename": dst.name, "path": str(dst)}


@router.post("/api/images/move")
def move_images(body: MoveRequest, conn=Depends(get_conn)) -> dict[str, Any]:
    """選択画像を別フォルダに移動する。

    指定方法は 2 通り:
      - dest_folder_id (+ 任意の subdir): 登録済みフォルダ配下に移動
      - dest_path: 任意のディレクトリ絶対/相対パスに移動

    移動先が登録フォルダ配下と判定できれば folder_id をその ID に更新、
    それ以外の場合は元の folder_id を維持する (将来そのフォルダを登録すれば
    再スキャン時に整合する)。
    """
    if not body.image_ids:
        raise HTTPException(status_code=400, detail="image_ids が空です")

    # 移動先ディレクトリを解決
    if body.dest_folder_id is not None:
        f = repo.get_folder(conn, body.dest_folder_id)
        if f is None:
            raise HTTPException(status_code=404, detail="移動先フォルダが見つかりません")
        dest_dir = Path(f["path"])
        if body.subdir:
            sub = body.subdir.strip().strip("/\\")
            if not sub or any(c in sub for c in ':*?"<>|'):
                raise HTTPException(status_code=400, detail="サブフォルダ名に使えない文字が含まれます")
            dest_dir = dest_dir / sub
    elif body.dest_path:
        dest_dir = Path(body.dest_path).expanduser()
    else:
        raise HTTPException(status_code=400, detail="dest_folder_id または dest_path のいずれかが必要です")

    # ディレクトリ用意
    if not dest_dir.exists():
        if body.create_dir or body.dest_folder_id is not None:
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise HTTPException(status_code=400, detail=f"フォルダ作成失敗: {e}")
        else:
            raise HTTPException(status_code=400, detail=f"移動先フォルダが存在しません: {dest_dir}")
    elif not dest_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"移動先がフォルダではありません: {dest_dir}")

    dest_dir = dest_dir.resolve()

    # 移動先がどの登録フォルダ配下かを判定 (新しい folder_id 用)
    folders = repo.list_folders(conn)
    def find_owning_folder_id(p: Path) -> int | None:
        # 最も長い prefix にマッチする登録フォルダを採用
        best_id: int | None = None
        best_len = -1
        for fr in folders:
            try:
                fp = Path(fr["path"]).resolve()
            except OSError:
                continue
            try:
                p.relative_to(fp)
            except ValueError:
                continue
            if len(str(fp)) > best_len:
                best_len = len(str(fp))
                best_id = int(fr["id"])
        return best_id

    new_folder_id_for_dest = find_owning_folder_id(dest_dir)

    moved = 0
    failed: list[dict[str, Any]] = []

    for image_id in body.image_ids:
        r = repo.get_image(conn, image_id)
        if r is None:
            failed.append({"id": image_id, "filename": None, "error": "DB レコード無し"})
            continue
        src = Path(r["path"])
        if not src.exists():
            failed.append({"id": image_id, "filename": r["filename"], "error": "ファイルが存在しません"})
            continue

        dst = dest_dir / src.name
        # 同名衝突回避: 既存があれば連番サフィックス付与
        if dst.exists():
            stem = dst.stem
            ext = dst.suffix
            i = 1
            while True:
                cand = dest_dir / f"{stem} ({i}){ext}"
                if not cand.exists():
                    dst = cand
                    break
                i += 1

        try:
            shutil.move(str(src), str(dst))
        except Exception as e:
            failed.append({"id": image_id, "filename": r["filename"], "error": str(e)})
            continue

        new_folder_id = new_folder_id_for_dest if new_folder_id_for_dest is not None else int(r["folder_id"])
        try:
            repo.update_image_location(conn, image_id, str(dst), new_folder_id)
            moved += 1
        except Exception as e:
            # ファイルは移動済みだが DB 更新失敗。次回スキャンで整合する想定
            log.warning("DB 更新失敗 (id=%s): %s", image_id, e)
            failed.append({"id": image_id, "filename": r["filename"], "error": f"DB 更新失敗: {e}"})

    return {"moved": moved, "failed": failed}


# ---------- tags ----------

@router.get("/api/tags")
def list_tags(conn=Depends(get_conn)) -> list[dict[str, Any]]:
    rows = repo.list_tags_with_counts(conn)
    return [{"id": r["id"], "name": r["name"], "image_count": r["image_count"]} for r in rows]


@router.post("/api/tags/assign")
def assign_tags(body: TagAssignRequest, conn=Depends(get_conn)) -> dict[str, Any]:
    if not body.image_ids:
        raise HTTPException(status_code=400, detail="image_ids が空です")
    repo.assign_tags(
        conn,
        image_ids=body.image_ids,
        add_tag_names=body.add,
        remove_tag_names=body.remove,
    )
    # 影響を受けたタグ一覧を返す (UI のチップ更新用)
    return {"ok": True}


# ---------- SSE ----------

@router.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    q = scanner.manager.subscribe()

    async def stream():
        try:
            # 接続初期通知
            yield f"event: ready\ndata: {{}}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # keepalive コメント
                    yield ": keepalive\n\n"
                    continue
                payload = json.dumps(asdict(ev), ensure_ascii=False)
                yield f"event: {ev.type}\ndata: {payload}\n\n"
        finally:
            scanner.manager.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream")
