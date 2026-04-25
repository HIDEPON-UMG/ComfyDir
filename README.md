# ComfyDir

ComfyUI で生成した PNG 画像と、その埋め込みプロンプトをローカルブラウザ上で整理するためのツール。
note.com の [ImagePromptManager](https://note.com/aiaicreate/n/n46d71d151594) を参考に、自分のフローに合うように再構築した。

> **アプリ表示名**: ComfyDir / **プロジェクトディレクトリ名**: `ComfyImageOrganizer/`（互換のため変更しない）

## 主な機能

- **画像プレビュー**: フォルダ単位でグリッド表示。スライダーで連続的にサイズ変更
- **並び替え**: 名前 / 更新日時 / 追加日時 × 昇順 / 降順
- **ファイル名編集**: 右ペインで前段（拡張子以外）をリネーム。実ファイルもリネームされる
- **画像の移動**: 単体／複数選択で別フォルダへ移動。登録済みフォルダ＋サブフォルダ自動作成、または任意パス指定
- **プロンプト表示**（read-only）: ComfyUI の PNG メタデータから positive / negative を抽出して表示・コピー。表示エリアは右下グリップで縦伸縮可
- **プロンプト検索**: ヘッダーの検索ボックスで positive / negative を横断的に部分一致検索（空白区切りで AND、大小区別なし）
- **タグ管理**: 単体・複数選択（Ctrl / Shift）でタグ付与・削除
- **タグフィルタ**: 任意のタグ名を自由入力（既存タグはオートコンプリート）。複数タグは AND / OR 切替
- **可変ペイン**: 画像グリッドと右ペインの境界をマウスドラッグで自由にリサイズ
- **UI 状態の永続化**: 直近開いていたフォルダ・サムネサイズ・ソート・タグフィルタ・検索クエリ・ペイン幅をブラウザに記憶（次回起動時に復元）
- **新規画像の自動取り込み**: watchdog でフォルダ監視。ComfyUI が新たに生成した画像が SSE 経由でブラウザに即時反映
- **リネーム追跡**: 内容の SHA-1 を見ているので、ファイル名が変わってもタグが付いたまま追従
- **ワンクリック起動**: `start.vbs` ダブルクリックでコンソール窓なしに起動。ログは `data/server.log`

## 仕組み

```text
ブラウザ (Vanilla JS) ←→ FastAPI ←→ SQLite
                                  ←→ Pillow / watchdog / ComfyUI tEXt parser
                                  ←→ サムネイルキャッシュ (data/thumbs/)
```

- DB は `data/index.sqlite` 1 ファイル。バックアップはこのファイルを退避するだけで完結
- サムネは `data/thumbs/{sha1}_{width}.webp` にキャッシュ。離散段（128/192/256/384/512）にスナップ
- ログは `data/server.log` （ローテーション付き、UTF-8）。ワンクリック起動 (`start.vbs`) ではコンソール窓が出ないので、エラー追跡はこのログを参照

## セットアップ

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt          # macOS / Linux
```

## 起動

3 通りから選べる。

| 方法 | 用途 | 動作 |
| --- | --- | --- |
| `start.vbs` をダブルクリック | 普段使い（推奨） | コンソール窓なしで起動。ブラウザが自動で開く |
| `start.bat` をダブルクリック | デバッグ | コンソール窓ありで起動。終了後 `pause` で止まる |
| `.venv/Scripts/python.exe run.py` | 開発時 | 通常の Python 実行 |

ブラウザは自動で `http://127.0.0.1:8765` を開く（環境によっては手動で開く）。

### デスクトップにショートカットを置く

`start.vbs` を右クリック → **送る → デスクトップ (ショートカットを作成)**。
以後、デスクトップのアイコンをダブルクリックするだけで起動できる。

### タスクバーにアイコン付きで固定する

Windows のタスクバーは `.vbs` を直接固定できないので、
**専用ショートカット (`.lnk`) を作ってからタスクバーに固定**する手順を取る。

#### 1. アイコンを生成（一度だけ）

```bash
.venv/Scripts/python.exe tools/make_icon.py
```

`assets/app.ico` が作られる（マルチサイズ ICO）。気に入らなければ
このスクリプトを書き換えるか、お好みの `.ico` を `assets/app.ico` に上書きする。

#### 2. ショートカットを生成（一度だけ）

`tools/create_shortcut.vbs` をダブルクリック。
プロジェクトルートに **`ComfyDir.lnk`** が生成される。
（中身: `wscript.exe "...\start.vbs"` を `assets/app.ico` のアイコン付きで起動）

#### 3. タスクバーに固定

1. 生成された `ComfyDir.lnk` を右クリック
2. **Windows 11 の場合**: 「その他のオプションを表示」を選択
3. **「タスクバーにピン留めする」** をクリック

以後、タスクバーのアイコンをワンクリックするだけで起動できる。
（生成された `.lnk` は `.gitignore` 済みなのでコミットされない）

### 操作の流れ

1. 「+ 追加」で ComfyUI の出力フォルダを登録（絶対パス）
2. グリッドに画像が並ぶので、スライダーで好きなサイズに
3. 画像をクリック → 右ペインで Positive / Negative プロンプトを確認・コピー（プロンプト枠の右下グリップで高さを伸縮可）
4. タグ欄でタグを付与（Enter）
5. 上部のタグフィルタにタグ名を入力（Enter で追加。既存タグはオートコンプリート）。複数タグは AND / OR で切替
6. 上部の「プロンプト内検索」ボックスにキーワードを入力 → positive / negative を横断検索（空白区切り = AND）
7. 複数選択（Ctrl/Shift クリック）してツールバーの「📁 移動...」で別フォルダに格納

UI の状態（直近開いていたフォルダ・サムネサイズ・ソート・タグフィルタ・検索クエリ・ペイン幅）はブラウザ側 (`localStorage`) に記憶されるので、次回開いたときに前回の表示に戻る。

## 環境変数

| 変数 | デフォルト | 用途 |
| --- | --- | --- |
| `CIO_HOST` | `127.0.0.1` | バインドホスト |
| `CIO_PORT` | `8765` | バインドポート |

## ファイル構成

```text
ComfyImageOrganizer/
├── start.vbs              # ワンクリック起動 (コンソール窓なし)
├── start.bat              # デバッグ起動 (コンソール窓あり)
├── run.py                 # 直接起動エントリ
├── requirements.txt
├── src/comfy_image_organizer/
│   ├── main.py            # FastAPI app + lifespan + ログ設定
│   ├── routes.py          # API (フォルダ/画像/タグ/移動/SSE)
│   ├── db.py / repo.py    # SQLite アクセス
│   ├── comfy_prompt.py    # PNG tEXt → positive/negative
│   ├── thumbnail.py       # Pillow + キャッシュ
│   ├── scanner.py         # フルスキャン + watchdog + SSE
│   ├── config.py
│   └── static/            # index.html / app.js / style.css
└── data/
    ├── index.sqlite       # メイン DB (フォルダ・画像・タグ)
    ├── server.log         # サーバログ
    └── thumbs/            # サムネキャッシュ
```

## 設計上の判断

- **プロンプトは表示のみ**: 編集して PNG に書き戻すと「画像とプロンプトの一致性」が壊れるため、表示・コピー専用
- **対応形式は ComfyUI のみ**: PNG `tEXt` チャンクの `prompt` キー（API 形式 JSON）を解釈。KSampler 系の `inputs.positive` / `inputs.negative` 参照を辿って `CLIPTextEncode` のテキストに到達する
- **並び替えはソート切替のみ**: 手動 D&D 並び替えは未実装。要望次第で追加可（DB に `sort_order` 列は確保済み）
- **削除機能は未実装**: 元記事のツールには Delete でのゴミ箱送りがあるが、要件外
