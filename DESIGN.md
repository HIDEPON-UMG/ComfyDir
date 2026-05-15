---
version: alpha
name: ComfyDir
description: ComfyUI 出力 PNG とプロンプトをローカルで整理するための画像オーガナイザ。ダーク基調 (OKLCH neutral) × Cyan-blue アクセントの落ち着いた作業用 UI。
colors:
  # === 中性色 (Background / Border / Foreground) ===
  # Hex は OKLCH の sRGB 近似値 (参考用)。実装の一次ソースは static/style.css の :root トークン。
  primary:        "#F4F4F6"   # fg-primary  oklch(0.97 0.005 260)
  secondary:      "#BFC0C5"   # fg-secondary oklch(0.78 0.008 260)
  muted:          "#888A91"   # fg-muted    oklch(0.58 0.010 260)
  on-tertiary:    "#1A1B1F"   # fg-on-accent oklch(0.16 0.008 260)
  surface:        "#232428"   # bg-surface  oklch(0.205 0.008 260) — toolbar / helpbar
  surface-base:   "#1A1B1F"   # bg-base     oklch(0.165 0.008 260) — app 全体の地
  surface-elev:   "#2D2E33"   # bg-elevated oklch(0.245 0.009 260) — hover / カード
  surface-inset:  "#1E1F23"   # bg-inset    oklch(0.14 0.008 260)  — input 内側
  border:         "#3D3F45"   # border-subtle oklch(0.30 0.010 260)
  border-strong:  "#5A5C63"   # border-strong oklch(0.42 0.010 260)
  border-focus:   "#7BC9E6"   # border-focus  oklch(0.78 0.16 245)

  # === ブランドアクセント (Cyan-blue) ===
  tertiary:       "#56C6E3"   # accent       oklch(0.78 0.13 215) — 主操作・ブランド
  accent-hover:   "#82D6EC"   # accent-hover oklch(0.84 0.13 215)

  # === セマンティック ===
  success:        "#5DBE83"   # oklch(0.74 0.14 150)
  warning:        "#DCBC4A"   # oklch(0.82 0.14 85)
  error:          "#E36C4F"   # danger oklch(0.68 0.18 25)

  # === カテゴリ色 (お気に入りカテゴリ / プロンプトトークン) ===
  cat-1:          "#7CB6E8"   # blue
  cat-2:          "#67C9B0"   # teal
  cat-3:          "#D7C26B"   # amber
  cat-4:          "#D77E6B"   # coral
  cat-5:          "#C9A2D9"   # violet

typography:
  h1:
    fontFamily: "Inter, 'Noto Sans JP', system-ui, -apple-system, 'Segoe UI', 'Hiragino Kaku Gothic ProN', 'Yu Gothic UI', sans-serif"
    fontSize: 1.375rem   # 22px (--font-size-2xl)
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  h2:
    fontFamily: "Inter, 'Noto Sans JP', system-ui, sans-serif"
    fontSize: 1.125rem   # 18px (--font-size-xl)
    fontWeight: 600
    lineHeight: 1.25
  h3:
    fontFamily: "Inter, 'Noto Sans JP', system-ui, sans-serif"
    fontSize: 0.9375rem  # 15px (--font-size-lg)
    fontWeight: 600
    lineHeight: 1.25
  body-lg:
    fontFamily: "Inter, 'Noto Sans JP', system-ui, sans-serif"
    fontSize: 0.9375rem  # 15px
    lineHeight: 1.5
  body-md:
    fontFamily: "Inter, 'Noto Sans JP', system-ui, sans-serif"
    fontSize: 0.8125rem  # 13px (--font-size-md)
    lineHeight: 1.5
    letterSpacing: "0.005em"
  body-sm:
    fontFamily: "Inter, 'Noto Sans JP', system-ui, sans-serif"
    fontSize: 0.75rem    # 12px (--font-size-sm)
    lineHeight: 1.5
  label:
    fontFamily: "Inter, 'Noto Sans JP', system-ui, sans-serif"
    fontSize: 0.6875rem  # 11px (--font-size-xs)
    fontWeight: 500
    letterSpacing: "0.08em"
  code:
    fontFamily: "JetBrains Mono, 'SF Mono', 'Cascadia Code', 'Hiragino Kaku Gothic ProN', monospace"
    fontSize: 0.75rem
    lineHeight: 1.5

rounded:
  sm:   4px
  md:   8px
  lg:   12px
  xl:   16px
  full: 999px

spacing:
  # 4px ベース (一部 2px 刻みあり)
  xs:  2px    # --space-1
  sm:  4px    # --space-2
  md:  8px    # --space-4
  lg:  12px   # --space-5
  xl:  16px   # --space-6
  2xl: 24px   # --space-7
  3xl: 32px   # --space-8

components:
  button-primary:
    backgroundColor: "{colors.tertiary}"
    textColor: "{colors.on-tertiary}"
    rounded: "{rounded.md}"
    padding: "0 12px"
    typography: "{typography.body-sm}"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface-elev}"
    textColor: "{colors.secondary}"
    rounded: "{rounded.md}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.secondary}"
  button-danger:
    backgroundColor: "transparent"
    textColor: "{colors.error}"
  input:
    backgroundColor: "{colors.surface-inset}"
    textColor: "{colors.primary}"
    rounded: "{rounded.md}"
    padding: "0 8px"
  chip:
    # 実装の地色は accent (cyan) を alpha 0.16 で薄めた tint。
    # design.md は alpha 表現を持たないため、近似として surface-elev を指定する。
    backgroundColor: "{colors.surface-elev}"
    textColor: "{colors.tertiary}"
    rounded: "{rounded.full}"
    typography: "{typography.label}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
  toolbar:
    backgroundColor: "{colors.surface}"
---

# ComfyDir デザイン仕様

ComfyDir のデザインシステムは **OKLCH ベースのダーク UI** と **Cyan-blue アクセント** の組み合わせで、長時間のサムネイル閲覧でも目が疲れないよう、彩度を抑え、コントラストは「文字 > 区切り線 > 背景段差」の順で確保している。

トークンの一次ソースは [`src/comfy_image_organizer/static/style.css`](src/comfy_image_organizer/static/style.css) の `:root`。本ファイルのフロントマターは sRGB 近似値で、実装と乖離した場合は CSS 側を正とする。

---

## トンマナ (Tone & Manner)

| 軸 | 採用 | 不採用 |
| --- | --- | --- |
| **明度** | ダークテーマ単独 (`oklch(L=0.13–0.30)` の中性紺寄り) | ライトテーマ / ハイコントラスト白背景 |
| **彩度** | 背景は完全グレー寄り (`C=0.008`)、アクセントのみ `C=0.13` で発色 | ネオン・ガラスモーフィズム・グラデーション多用 |
| **アクセント** | Cyan-blue 1 色 (`oklch(0.78 0.13 215)`)。冷たすぎず、画像のサムネ (=暖色傾向の生成イラスト) を邪魔しない補色寄り | プロジェクトカラー複数並列 / 紫・ピンク系の派手な強調色 |
| **形状** | 角丸 4–12px の控えめなラウンド、chip だけ pill 形 | シャープ 0px / 完全円形ボタン / スキューモーフィック |
| **影** | 浅い影 (`shadow-sm` = 1px シャドウ + 1px ボーダー) で段差を作る。深い `shadow-lg` はダイアログ/ライトボックスのみ | 全体に大きな drop-shadow / glow を多用 |
| **タイポ** | 13px ベースの情報密度 (作業用ツール) + 等幅 (JetBrains Mono) は KBD と code のみ | 16px+ の読み物寄り / セリフ見出し |
| **モーション** | 120–260ms / `cubic-bezier(0.2, 0, 0, 1)` のみ。bounce/spring 不使用 | フェードイン演出の連発 |
| **アイコン** | 線アイコン (stroke 2.5px) のみ。塗りアイコン禁止 | カラフルなイラスト調アイコン |
| **コピー** | 日本語短文 + 必要時のみ英語ラベル。命令形は避ける | 過剰な敬語 / 絵文字での装飾 |

設計思想は **「画像が主役、UI は裏方」**。サムネのアスペクト比尊重 (`object-fit: cover` + 自然比追従) と、右ペインのドラッグ可変、ライトボックスのパン/ズーム/前後遷移など、操作系は前面に出すが装飾は徹底的に削っている。

---

## 配色階層

### 背景の 4 段階

```text
bg-base       (0.165) ← アプリ全体の地
  └─ bg-surface  (0.205) ← toolbar / helpbar / カード
       └─ bg-elevated (0.245) ← hover / 選択中 / icon-btn hover
            └─ bg-inset   (0.14)  ← input・textarea の内側 (沈み込み表現)
```

「elevated は base より明るい / inset は base より暗い」で立体感を作る。

### 文字色の 3 段階

| トークン | 用途 |
| --- | --- |
| `--color-fg-primary` (0.97) | 本文・主見出し |
| `--color-fg-secondary` (0.78) | 副情報・選択中ラベル |
| `--color-fg-muted` (0.58) | プレースホルダ・LABEL 大文字・無効状態 |

### アクセント発火条件

Cyan-blue (`--color-accent`) は次の場面でのみ使う:

1. **主操作ボタン** (`.btn--primary`): 追加・適用・保存
2. **トグル ON 状態** (`.btn--toggle[aria-pressed="true"]`): Myタグフィルタなど
3. **フォーカスリング** (`:focus-visible` の `--shadow-glow-accent`)
4. **件数バッジ・選択件数の強調** (`.grid-toolbar__count em`)
5. **ブランド名のサブテキスト** (`.brand__name small`)

それ以外でアクセントを散らさない (chip の地色は `accent-soft` = alpha 0.16 で控えめに)。

---

## タイポグラフィ運用

| クラス相当 | サイズ | weight | 用途 |
| --- | --- | --- | --- |
| ブランド `.brand__name strong` | 22px | 700 | アプリ名 (ComfyDir) のみ |
| h2 相当 | 18px | 600 | ダイアログタイトル |
| h3 相当 | 15px | 600 | 右ペインのセクション見出し |
| body-md (既定) | 13px | 400 | アプリ全域の本文・ボタン・リスト |
| body-sm | 12px | 400 | ボタン内ラベル・サブ情報 |
| label | 11px | 500 | LABEL (`text-transform: uppercase` + `letter-spacing: 0.08em`) |
| code/kbd | 10–12px | — | `<kbd>` ・コード表示 |

字間は通常 `0.005em` (`body` ルート)、ブランドのみ `-0.025em` で締める。LABEL は `0.08em` で抜く。これだけで「情報密度を落とさずヒエラルキを出す」設計。

---

## レイアウト基準

- **App shell**: `grid-template-rows: auto auto 1fr auto` (toolbar / helpbar / main / statusbar)
- **メインスプリット**: `grid-template-columns: 1fr auto var(--right-w, 360px)` で中央=ドラッグハンドル=右ペインの 3 列。右ペイン幅は `localStorage` に永続化
- **サムネグリッド**: タイル自然比 (`object-fit: cover`)、サイズは `--thumb-size` (既定 200px) を localStorage 永続化
- **ライトボックス**: 全画面オーバーレイ `--color-bg-overlay` (alpha 0.72)、シャドウは `shadow-lg`

---

## モーション

| トークン | 値 | 使い分け |
| --- | --- | --- |
| `--motion-duration-fast` | 120ms | ボタン hover / icon-btn の色変化 |
| `--motion-duration-normal` | 180ms | フィールドのフォーカスリング、トースト |
| `--motion-duration-slow` | 260ms | ダイアログ・ライトボックス開閉 |

イージングは全て `cubic-bezier(0.2, 0, 0, 1)` で統一。`transform: translateY(1px)` のクリックフィードバックは fast のみ。

---

## 実装ルール

1. **色・余白・角丸・影・モーションを直書きしない**。必ず `var(--color-*)` / `var(--space-*)` / `var(--radius-*)` / `var(--shadow-*)` / `var(--motion-*)` を参照する
2. **新しい色は基本追加しない**。意味的に既存のセマンティック (`success` / `warning` / `danger`) や中性段階 (`bg-base`/`surface`/`elevated`/`inset`) に必ず割り当てる
3. **アクセント色を「飾り」で使わない**。発火条件は上記 5 つに限る
4. **アイコンは線・stroke 2.5px** に統一 (favicon と select 矢印も同思想)
5. **ボタンの高さは 32px**、icon-btn は 24px。それ以外のサイズを新設しない
6. **コードに色名 (`#56c6e3` 等) を書きそうになったら CSS の `:root` に新トークンを追加 → JS/CSS は必ずトークン経由で参照**

---

## ライトテーマ非対応の理由

`[data-theme="dark"]` セレクタは `:root` と同じトークンセットを上書きする形で書かれているが、現状ダークのみ。理由は次の 2 つ:

- 生成画像 (主にイラスト) のサムネ閲覧は暗背景の方が色味が安定する
- ローカル単独利用が前提のため、配布先デバイスのライト/ダーク切替に追従する必要が薄い

将来ライト版を足す場合は `:root[data-theme="light"]` で同じトークン名を再定義する想定 (実装は未着手)。
