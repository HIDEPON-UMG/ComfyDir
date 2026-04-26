"""プロンプトオートコンプリート用のタグ辞書 / 日本語翻訳辞書をダウンロードする。

`data/danbooru_tags/` と `data/danbooru_translations/` は .gitignore 対象なので、
リポジトリに含めず、初回セットアップ時にこのスクリプトで配布元から取得する。

使い方:
    python tools/download_tag_dictionaries.py

既にファイルが存在する場合はスキップする (上書きしたければ --force)。
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

# プロジェクトルート (= このスクリプトの 1 階層上)
ROOT = Path(__file__).resolve().parents[1]
TAG_DIR = ROOT / "data" / "danbooru_tags"
TRANS_DIR = ROOT / "data" / "danbooru_translations"

# (保存先パス, ダウンロードURL, 説明)
DOWNLOADS = [
    (
        TAG_DIR / "Anima-preview.csv",
        "https://github.com/BetaDoggo/danbooru-tag-list/releases/download/Model-Tags/Anima-preview.csv",
        "Danbooru タグ辞書 (Anima 推奨設定ベース、a1111-tagcomplete 互換 CSV)",
    ),
    (
        TRANS_DIR / "danbooru-machine-jp.csv",
        "https://raw.githubusercontent.com/boorutan/booru-japanese-tag/main/danbooru-machine-jp.csv",
        "日本語翻訳 (機械翻訳ベース、約 100K 件)",
    ),
    (
        TRANS_DIR / "danbooru-jp.csv",
        "https://raw.githubusercontent.com/boorutan/booru-japanese-tag/main/danbooru-jp.csv",
        "日本語翻訳 (手動翻訳、約 400 件 / 機械翻訳より優先される)",
    ),
]


# 1 ファイルの上限サイズ。配布元が改ざん/差し替えされていてもローカルディスクを埋め尽くされない。
# 既存の最大 (Anima-preview.csv 約 3MB / danbooru-machine-jp.csv 約 3.6MB) より十分大きい値。
_MAX_BYTES = 64 * 1024 * 1024


def _download(url: str, dest: Path) -> int:
    """URL からダウンロードして dest に保存し、書き込んだバイト数を返す。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=30) as resp, dest.open("wb") as out:
        size = 0
        while True:
            chunk = resp.read(1 << 14)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_BYTES:
                # 途中で打ち切ると半端ファイルが残るので削除して例外
                out.close()
                try:
                    dest.unlink()
                except OSError:
                    pass
                raise RuntimeError(
                    f"ダウンロードサイズが上限 {_MAX_BYTES // (1024 * 1024)}MiB を超えました: {url}"
                )
            out.write(chunk)
        return size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="既存ファイルを上書きする")
    args = parser.parse_args()

    print(f"[セットアップ先] {ROOT}")
    print(f"  タグ辞書    : {TAG_DIR}")
    print(f"  翻訳辞書    : {TRANS_DIR}")
    print()

    failed: list[str] = []
    for dest, url, desc in DOWNLOADS:
        rel = dest.relative_to(ROOT)
        if dest.exists() and not args.force:
            print(f"[skip] {rel}  (既に存在 / --force で上書き)")
            continue
        print(f"[get ] {rel}  -- {desc}")
        try:
            n = _download(url, dest)
            print(f"       OK ({n / 1024:.1f} KiB)")
        except Exception as e:
            print(f"       FAILED: {e}", file=sys.stderr)
            failed.append(str(rel))

    if failed:
        print()
        print("以下のダウンロードに失敗しました。ネットワーク状況やURLを確認してください:", file=sys.stderr)
        for f in failed:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print()
    print("完了。サーバを再起動するとオートコンプリートに反映されます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
