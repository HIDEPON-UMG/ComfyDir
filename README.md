# ComfyDir

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009485.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey.svg)

> **ComfyUI で生成した PNG 画像と、その埋め込みプロンプトを、ローカルブラウザ上で整理するためのツール。**
> 重い管理アプリは入れたくないが、生成画像が増えるにつれ「あの構図のプロンプトをまた使いたい」が辛くなってきた人向け。

note.com の [ImagePromptManager](https://note.com/aiaicreate/n/n46d71d151594) の世界観を参考に、Web ベースで自分のフローに合わせて再構築しました。サーバとブラウザはすべてローカルで完結し、外部に画像やプロンプトを送ることはありません。

> **アプリ表示名**: ComfyDir / **リポジトリ／ディレクトリ名**: `ComfyImageOrganizer/`

---

## 目次

- [主な機能](#主な機能)
- [アーキテクチャ](#アーキテクチャ)
- [動作環境](#動作環境)
- [クイックスタート](#クイックスタート)
- [起動方法 (3 通り)](#起動方法-3-通り)
- [タスクバーにアイコン付きで固定する](#タスクバーにアイコン付きで固定する)
- [操作の流れ](#操作の流れ)
- [API 一覧](#api-一覧)
- [データの場所](#データの場所)
- [環境変数](#環境変数)
- [ファイル構成](#ファイル構成)
- [設計上の判断](#設計上の判断)
- [トラブルシューティング](#トラブルシューティング)
- [貢献](#貢献)
- [ライセンス](#ライセンス)

---

## 主な機能

| カテゴリ | 機能 |
| --- | --- |
| **表示** | フォルダ単位のサムネイルグリッド（タイルは画像の自然アスペクト比に追従、`object-fit: cover` で枠いっぱい表示）／スライダー or 数値で連続的にサイズ変更／グリッドのサムネを 2 回連続クリックで全画面ライトボックス（左右の `‹` `›` ボタン or ←→ キーで現在の並び順に沿って前後の画像へ遷移、下部 −／全体表示／＋ ボタンとマウスホイールでカーソル位置中心に拡縮、画像を左ドラッグでパン、画像左クリックでデフォルト表示にリセット）／可変ペイン (右ペイン幅をドラッグ調整) |
| **UI デザイン** | Claude Design 出力をベースに、OKLCH 配色 (Cyan-blue アクセント) / Inter + Noto Sans JP / CSS カスタムプロパティ (`--color-*` / `--space-*` / `--radius-*` / `--shadow-*` / `--motion-*`) で全コンポーネントをトークン化。プロンプトはトークンチップ表示で Danbooru カテゴリ別の下線色 (character/copyright/artist/meta 等) で読み分け可能。SVG favicon |
| **監視フォルダ管理** | 「+ 追加」「↻ 再スキャン」「✎ 編集（パス／ラベル）」「解除」をヘッダーから直接操作。パス変更時は配下画像レコードのパス列も追従して書き換え、watchdog を新パスで再起動 |
| **並び替え** | 名前 / 更新日時 / 追加日時 × 昇順 / 降順 |
| **プロンプト** | ComfyUI PNG メタデータから positive / negative を自動抽出して表示 (read-only)。Text Concatenate / Power Prompt (rgthree) 等の中継ノードもグラフ追跡で対応。表示中テキストはカテゴリ別「並べ替え」ボタンでソート可能（Danbooru CSV のカテゴリ情報を `/api/prompt-category-map` から取得して character/copyright/artist/meta を高精度に振り分け） |
| **検索** | プロンプト と メモ それぞれ独立した検索ボックスを上部に配置（空白区切り = AND、大小区別なし、デバウンス自動更新、両方併用で AND）／プロンプト検索バーは a1111-tagcomplete 風のオートコンプリート対応（DB 内の既存タグ + Danbooru 風 CSV 辞書をマージ、↑↓Tab/Enter/Esc 操作、エイリアス・カテゴリ色分け） |
| **Myタグ** | ユーザーが自由に付与する分類用ラベル（Danbooru タグとは独立）。単体／複数選択（Ctrl/Shift クリック）で付与・削除。任意の Myタグ名を自由入力＋既存候補オートコンプリート。AND/OR 切替フィルタ |
| **メモ** | 各画像に短いメモ欄（自動保存、デバウンス + blur で確定）。メモ本文も検索対象 |
| **お気に入りプロンプト** | Positive / Negative をペアで 1 レコードとして保管。ユーザー定義カテゴリでグルーピング。トップバー「★ お気に入り」で右ペインを切替（カテゴリタブ + 検索 + 一覧）／各カードから Pos / Neg のクリップボードコピー、編集、削除。編集ダイアログでも `コピー / 並べ替え / 戻る` ボタン使用可能 |
| **ファイル操作** | リネーム（実ファイルも改名） / 別フォルダへ移動（登録済みフォルダ + サブフォルダ自動作成 / 任意パス） |
| **右ペインのカスタマイズ** | ファイル名/Myタグ/メモ/Positive/Negative の各セクションを `⋮⋮` ハンドルでドラッグ&ドロップ並び替え。順序は永続化（プレビュー画像はサムネと重複するため右ペインからは廃止） |
| **自動取り込み** | watchdog でフォルダ監視、SSE で新規生成画像をブラウザに即時反映 |
| **ComfyUI 連携** | サムネに hover してから ComfyUI のキャンバスへドラッグ&ドロップすると、元 PNG (メタデータ込み) が OS 経由で渡り ComfyUI 側でワークフローが再構築される。Desktop 版 (Electron) と Web 版の両方に対応。ライトボックスのパン操作と競合しない |
| **永続性** | SQLite 1 ファイルにすべて集約（Myタグ／メモ／フォルダ／画像インデックス／お気に入りプロンプト＆カテゴリ） |
| **追跡** | 内容の SHA-1 ベースなのでファイル名が変わっても Myタグが追従 |
| **永続 UI 状態** | 直近フォルダ／サイズ／ソート／Myタグフィルタ／プロンプト検索／メモ検索／ペイン幅／セクション順／お気に入りビュー ON/OFF・選択カテゴリタブ・検索クエリを `localStorage` に保存 |
| **起動** | `start.vbs` でコンソール窓なしのワンクリック起動 + アイコン付きショートカット生成スクリプト同梱 |

---

## アーキテクチャ

```text
┌────────────────────────────┐
│  Browser (Vanilla JS)      │  ← localStorage に UI 状態を永続化
└─────┬──────────────▲───────┘
      │ HTTP/SSE     │
      ▼              │
┌────────────────────────────┐
│  FastAPI + uvicorn         │
│  ├ routes.py  (REST + SSE) │
│  ├ scanner.py (watchdog)   │  ── watchdog で監視フォルダの変化を検知
│  ├ thumbnail.py (Pillow)   │
│  └ comfy_prompt.py         │  ── PNG tEXt → グラフ追跡 → positive/negative
└─────┬──────────────────────┘
      │
      ▼
┌────────────────────────────┐
│  SQLite (data/index.sqlite)│
│  + サムネキャッシュ        │
│  + サーバログ              │
└────────────────────────────┘
```

**詳しい構成図**: [`docs/architecture.pptx`](docs/architecture.pptx)

主要モジュール：

| ファイル | 役割 |
| --- | --- |
| `src/comfy_image_organizer/main.py` | FastAPI app + lifespan + ログ設定 |
| `src/comfy_image_organizer/routes.py` | API エンドポイント定義（フォルダ／画像／Myタグ／メモ／移動／SSE／プロンプトカテゴリ） |
| `src/comfy_image_organizer/scanner.py` | フォルダのフルスキャン + watchdog + SSE 配信 |
| `src/comfy_image_organizer/comfy_prompt.py` | PNG `tEXt` を解釈し、KSampler → CLIPTextEncode → 中継ノードを再帰追跡してプロンプト抽出 |
| `src/comfy_image_organizer/thumbnail.py` | Pillow でサムネイル生成 + ファイルキャッシュ（離散段スナップ） |
| `src/comfy_image_organizer/db.py` | SQLite 接続 + スキーマ + 軽量マイグレーション |
| `src/comfy_image_organizer/repo.py` | CRUD（folders / images / tags / image_tags） |
| `src/comfy_image_organizer/static/` | 単一 HTML + Vanilla JS + 単一 CSS（ビルドステップなし） |

---

## 動作環境

- **OS**: Windows 10 / 11（`start.vbs` ベースの起動・ショートカット作成は Windows 前提。バックエンド自体は macOS / Linux でも動作）
- **Python**: 3.10 以上
- **ブラウザ**: Chromium / Edge / Firefox の比較的新しいもの（HTML5 `<dialog>`, `EventSource`, `resize` プロパティに対応していること）
- **ストレージ**: 監視フォルダ自体の容量 + サムネキャッシュ（1 枚あたり数 KB × 5 段）
- **ネットワーク**: 不要（ローカル `127.0.0.1:8765` のみ）

---

## クイックスタート

```bash
# 1. クローン
git clone https://github.com/<your-account>/ComfyImageOrganizer.git
cd ComfyImageOrganizer

# 2. 仮想環境
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt          # macOS / Linux

# 3. 起動
.venv/Scripts/python.exe run.py                                # 開発時はこれ
# または: start.vbs をダブルクリック (Windows / コンソール窓なし)

# 4. ブラウザで http://127.0.0.1:8765 を開き、「+ 追加」で
#    ComfyUI の output フォルダの絶対パスを登録
```

---

## 起動方法 (3 通り)

| 方法 | 用途 | 動作 |
| --- | --- | --- |
| `start.vbs` をダブルクリック | 普段使い（推奨） | コンソール窓なしで起動。ブラウザが自動で開く |
| `start.bat` をダブルクリック | デバッグ | コンソール窓ありで起動。終了後 `pause` で待機 |
| `python run.py` | 開発時 | 通常の Python 実行（venv をアクティベート済みなら） |

ブラウザは自動で `http://127.0.0.1:8765` を開きます（環境によっては手動で開く）。

---

## タスクバーにアイコン付きで固定する

Windows のタスクバーは `.vbs` を直接固定できないので、**アイコン付き専用ショートカット (`.lnk`) を作ってからタスクバーに固定**します。

```bash
# アイコン (assets/app.ico) を生成（一度だけ）
.venv/Scripts/python.exe tools/make_icon.py

# ショートカット ComfyDir.lnk をプロジェクトルートに生成（一度だけ）
wscript tools/create_shortcut.vbs
```

その後：

1. 生成された **`ComfyDir.lnk`** を右クリック
2. **Windows 11 の場合**: 「その他のオプションを表示」を選択
3. **「タスクバーにピン留めする」** をクリック

以後、タスクバーのアイコンをワンクリックするだけで起動できます（`.lnk` は `.gitignore` 済み）。

---

## 操作の流れ

1. 「+ 追加」で ComfyUI の出力フォルダを登録（絶対パス）
2. グリッドに画像が並ぶので、スライダーまたは数値入力で好きなサイズに
3. 画像をクリック → 右ペインで Positive / Negative プロンプトを確認・コピー（同じサムネをもう一度クリックで全画面ライトボックス表示 → 左右の `‹` `›` or ←→ キーで現在の並び順に沿って前後遷移、ホイールでカーソル位置中心に拡縮、画像を左ドラッグでパン、画像左クリックでデフォルト表示にリセット、下部 −／全体表示／＋ ボタンでも拡縮可）
4. Myタグ欄で Myタグを付与（Enter）／メモ欄に短いメモ（自動保存）
5. 上部の Myタグフィルタに Myタグ名を入力（既存 Myタグはオートコンプリート）／複数 Myタグは AND / OR 切替
6. 上部の「プロンプト内検索」「メモ内検索」にキーワード（空白区切りで AND、両方併用で AND）
7. 複数選択（Ctrl / Shift クリック）してツールバーの「📁 別フォルダへ移動」で別フォルダに格納
8. ペイン中央のグレー帯をドラッグして右ペイン幅を変更
9. 右ペインのセクション (ファイル名 / Myタグ / メモ / Positive / Negative) は左上の `⋮⋮` ハンドルで上下にドラッグ&ドロップ並び替え可能

UI の状態（直近フォルダ・サイズ・ソート・Myタグフィルタ・検索クエリ・ペイン幅）はブラウザ側 (`localStorage`) に記憶されるので、次回開いたときに前回の表示に戻ります。

---

## API 一覧

すべての API はローカル `http://127.0.0.1:8765` 配下。

| Method | Path | 用途 |
| --- | --- | --- |
| `GET` | `/api/folders` | 登録フォルダ一覧 |
| `POST` | `/api/folders` | フォルダ登録 + 即時スキャン |
| `PATCH` | `/api/folders/{id}` | パス／ラベルの編集（パス変更時は配下画像のパス列も追従し、watchdog 再起動 + 再スキャン） |
| `DELETE` | `/api/folders/{id}` | 登録解除（連鎖で images も削除） |
| `POST` | `/api/folders/{id}/rescan` | 手動再スキャン |
| `GET` | `/api/images` | 一覧（クエリ: `folder_id`, `tags=a,b`, `tag_mode=and\|or`, `order=name\|mtime\|added`, `direction=asc\|desc`, `q=プロンプト内検索`, `qm=メモ内検索`） |
| `GET` | `/api/images/{id}` | 詳細（プロンプト・タグ・メモ含む） |
| `GET` | `/api/images/{id}/preview` | 原寸画像 |
| `GET` | `/api/images/{id}/thumb?w=256` | サムネ（離散段スナップ） |
| `POST` | `/api/images/{id}/rename` | リネーム（実ファイルも改名） |
| `POST` | `/api/images/{id}/memo` | メモ保存（空文字でクリア） |
| `POST` | `/api/images/move` | 一括移動（登録フォルダ＋サブフォルダ／任意パス） |
| `GET` | `/api/tags` | Myタグ一覧（件数付き） |
| `POST` | `/api/tags/assign` | Myタグの一括付与・削除 |
| `GET` | `/api/prompt-tags` | プロンプト検索バー用オートコンプリート候補（DB 既存タグ + Danbooru 風 CSV 辞書） |
| `GET` | `/api/prompt-category-map` | Danbooru CSV のカテゴリ別タグ一覧（プロンプト並べ替え機能用、character/copyright/artist/meta） |
| `GET` | `/api/favorite-prompts` | お気に入りプロンプト一覧（クエリ: `category_id=all\|uncategorized\|<id>`, `q=空白区切り検索`） |
| `POST` | `/api/favorite-prompts` | お気に入り追加（name, category_id, positive, negative, memo, source_image_id） |
| `PATCH` | `/api/favorite-prompts/{id}` | 部分更新（送信したフィールドだけ更新／`category_id: null` で未分類化） |
| `DELETE` | `/api/favorite-prompts/{id}` | お気に入り削除 |
| `GET` | `/api/favorite-prompt-categories` | カテゴリ一覧（各カテゴリの件数付き） |
| `POST` | `/api/favorite-prompt-categories` | カテゴリ追加（同名は 409） |
| `PATCH` | `/api/favorite-prompt-categories/{id}` | カテゴリのリネーム / 並び替え |
| `DELETE` | `/api/favorite-prompt-categories/{id}` | カテゴリ削除（配下のお気に入りは ON DELETE SET NULL で未分類に） |
| `GET` | `/favicon.ico` | ブラウザタブ用アイコン（`assets/app.ico` を流用） |
| `GET` | `/api/events` | SSE: 新規／削除／更新を即時通知 |

---

## データの場所

| 場所 | 用途 | バックアップ |
| --- | --- | --- |
| `data/index.sqlite` | メイン DB（フォルダ／画像／Myタグ／メモ／お気に入りプロンプト＆カテゴリ） | このファイルをコピーするだけで完結 |
| `data/thumbs/{sha1}_{w}.webp` | サムネキャッシュ | 削除しても再生成される |
| `data/server.log` | サーバログ（UTF-8、ローテーション付き） | 調査時のみ |
| `data/danbooru_tags/*.csv` | プロンプト検索バーのオートコンプリート用 Danbooru 風タグ辞書 (a1111-tagcomplete 互換 CSV) | 任意のモデル別辞書を追加可能 |
| `data/danbooru_translations/*.csv` | プロンプトタグ候補に併記する日本語翻訳辞書 (`tag,訳語` の 2 列 CSV) | 任意の翻訳辞書を追加可能 |

`data/` 配下はすべて `.gitignore` 対象なので、公開リポジトリにユーザーデータは含まれません。

### プロンプトオートコンプリート用辞書のセットアップ

タグ辞書 / 日本語翻訳辞書は配布元への依存とサイズ (合計 7MB 弱) の都合でリポジトリには同梱せず、初回セットアップ時に下記スクリプトで取得します:

```bash
.venv/Scripts/python.exe tools/download_tag_dictionaries.py
```

これで `data/danbooru_tags/Anima-preview.csv`、`data/danbooru_translations/danbooru-machine-jp.csv`、`data/danbooru_translations/danbooru-jp.csv` が配置されます。サーバ再起動でオートコンプリートに反映されます。`--force` を付けると既存ファイルを上書きします。

### Danbooru タグ辞書の追加

`data/danbooru_tags/` に a1111-tagcomplete 互換の CSV (`name,type,post_count,"aliases"` 形式) を入れると、プロンプト検索バーのオートコンプリート候補にマージされます。たとえば [BetaDoggo/danbooru-tag-list](https://github.com/BetaDoggo/danbooru-tag-list/releases/tag/Model-Tags) の各モデル向けタグセット (`NoobAIXL1.1_underscore.csv`, `IllustriousV1.0_underscore.csv` 等) を任意で追加できます。複数 CSV があれば自動マージされ、同名タグは post_count が大きい方を採用します。

### 日本語翻訳辞書の追加

`data/danbooru_translations/` に `<英語タグ>,<日本語訳>` の 2 列 CSV を入れると、オートコンプリート候補に `<タグ名> ; <日本語訳>` 形式で訳語が併記されます。デフォルトの `danbooru-machine-jp.csv` (約100K件、機械翻訳ベース) と `danbooru-jp.csv` (約400件、手動翻訳) は [boorutan/booru-japanese-tag](https://github.com/boorutan/booru-japanese-tag) からのものです。複数 CSV があれば自動マージされ、ファイル名に `machine` を含まないものを後勝ちで優先するので、人間翻訳が機械翻訳を上書きします。

### スキーマアップグレード

抽出ロジックを改良してプロンプトを再抽出したいときは：

```bash
.venv/Scripts/python.exe tools/reextract_prompts.py
```

DB 内の全画像について、PNG メタデータからプロンプトを再抽出して上書きします（Myタグ・メモは保持）。サーバを停止してから実行してください。

---

## 環境変数

| 変数 | デフォルト | 用途 |
| --- | --- | --- |
| `CIO_HOST` | `127.0.0.1` | バインドホスト |
| `CIO_PORT` | `8765` | バインドポート |

---

## ファイル構成

```text
ComfyImageOrganizer/
├── start.vbs              # ワンクリック起動 (コンソール窓なし)
├── start.bat              # デバッグ起動 (コンソール窓あり)
├── run.py                 # 直接起動エントリ
├── requirements.txt
├── README.md
├── LICENSE
├── assets/
│   └── app.ico            # アプリアイコン (マルチサイズ ICO)
├── docs/
│   └── architecture.pptx  # システム構成図
├── tools/
│   ├── make_icon.py       # アイコン生成
│   ├── create_shortcut.vbs # アイコン付きショートカット生成
│   └── reextract_prompts.py # 全画像プロンプト再抽出
├── src/comfy_image_organizer/
│   ├── main.py            # FastAPI app + lifespan + ログ設定
│   ├── routes.py          # API
│   ├── scanner.py         # フルスキャン + watchdog + SSE
│   ├── comfy_prompt.py    # PNG tEXt → positive/negative (グラフ追跡)
│   ├── thumbnail.py       # Pillow + キャッシュ
│   ├── db.py / repo.py    # SQLite アクセス
│   ├── config.py
│   └── static/            # index.html / app.js / style.css
└── data/                  # ユーザーデータ (.gitignore)
    ├── index.sqlite
    ├── server.log
    └── thumbs/
```

---

## 設計上の判断

- **プロンプトは表示のみ**: 編集して PNG に書き戻すと「画像とプロンプトの一致性」が壊れるため、表示・コピー専用
- **お気に入りプロンプトは画像から独立**: PNG メタデータには手を入れず、SQLite に別テーブル (`favorite_prompts` / `prompt_categories`) として保管。元画像が消えてもお気に入りは残る (`source_image_id` は ON DELETE SET NULL で参照だけ切れる)
- **対応形式は ComfyUI のみ**: PNG `tEXt` チャンクの `prompt` キー（API 形式 JSON）を解釈。KSampler 系の `inputs.positive` / `inputs.negative` 参照を辿って `CLIPTextEncode` のテキストに到達。Text Concatenate / Power Prompt (rgthree) などの中継ノードも再帰追跡
- **並び替えはソート切替のみ**: 手動 D&D 並び替えは未実装（DB に `sort_order` 列は確保済みなので将来追加可）
- **削除機能は未実装**: 元ネタのツールには Delete でのゴミ箱送りがあるが、要件外
- **ローカル完結**: 外部 API への送信は一切なし。プロンプトや画像はマシンの外に出ません

---

## トラブルシューティング

### プロンプトが「(なし)」と表示される

カスタムノード経由の特殊なグラフかもしれません。問題の PNG をテストフォルダに置き、以下で生 JSON を確認してください：

```bash
.venv/Scripts/python.exe -c "
from src.comfy_image_organizer.comfy_prompt import extract_from_file
ex = extract_from_file('path/to/your.png')
print(ex.raw_prompt_json[:1000])
"
```

`raw_prompt_json` のグラフ構造を見て、`comfy_prompt.py` の `_resolve_string_value` でカバーできていないキーがあれば issue を立ててください。

### サーバが起動しない

`data/server.log` を見るのが最短ルート。`start.vbs` 起動でもログは必ずファイルに残ります。

### UI 表示がおかしい

ブラウザの開発者ツール (F12) のコンソールで以下を実行すると UI 状態がリセットされます：

```js
localStorage.removeItem('cio.prefs.v1')
```

その後 Ctrl+F5 で再読込。

### 日本語パスのフォルダで動かない

WAL モード + UTF-8 設定済みなので動くはずですが、Windows コンソールで `start.bat` のログが化けて見える場合は、`chcp 65001` 済みの `start.bat` を使うか、`data/server.log` を直接見てください。

---

## 貢献

PR / Issue 歓迎です。特に：

- ComfyUI の特定カスタムノードでプロンプトが取れないケース（PNG サンプル付き）
- macOS / Linux での動作報告
- アクセシビリティ改善
- UI のデザインリファイン

開発時は以下が便利：

```bash
# 起動 (uvicorn の reload は使っていない: 再起動は手動)
.venv/Scripts/python.exe run.py

# 全画像のプロンプトを再抽出 (comfy_prompt 改修後)
.venv/Scripts/python.exe tools/reextract_prompts.py
```

---

## ライセンス

[MIT License](LICENSE)
