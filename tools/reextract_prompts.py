"""DB 内の全画像について、PNG メタデータからプロンプトを再抽出して上書きする。

ComfyUI 抽出ロジックを変更したあと、既存 DB を一括で更新するためのスクリプト。

使い方 (サーバを停止してから):
    .venv/Scripts/python.exe tools/reextract_prompts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from comfy_image_organizer.comfy_prompt import extract_from_file  # noqa: E402
from comfy_image_organizer.db import connect, init_schema  # noqa: E402


def main() -> None:
    conn = connect()
    init_schema(conn)
    rows = conn.execute("SELECT id, path FROM images").fetchall()
    total = len(rows)
    print(f"reextracting {total} images...")

    missing = 0
    fixed_pos = 0
    no_prompt = 0
    errors = 0

    for i, r in enumerate(rows, start=1):
        if i % 50 == 0 or i == total:
            print(f"  {i}/{total}")
        p = Path(r["path"])
        if not p.exists():
            missing += 1
            continue
        try:
            ex = extract_from_file(p)
        except Exception as e:
            errors += 1
            print(f"  err id={r['id']}: {e}")
            continue
        try:
            conn.execute(
                "UPDATE images SET positive_prompt=?, negative_prompt=?, "
                "raw_prompt_json=?, width=?, height=? WHERE id=?",
                (
                    ex.positive,
                    ex.negative,
                    ex.raw_prompt_json,
                    ex.image_size[0] if ex.image_size else None,
                    ex.image_size[1] if ex.image_size else None,
                    r["id"],
                ),
            )
        except Exception as e:
            errors += 1
            print(f"  db err id={r['id']}: {e}")
            continue
        if ex.positive:
            fixed_pos += 1
        else:
            no_prompt += 1

    print()
    print(f"done. positive取得: {fixed_pos} / no_prompt: {no_prompt} / "
          f"missing_file: {missing} / errors: {errors}")


if __name__ == "__main__":
    main()
