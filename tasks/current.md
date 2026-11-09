# Current Status Snapshot

<!-- generated-by: repo-harness refresh-current-status v1 -->
<!-- updated_at: bootstrap -->
<!-- stale_after: 24h -->

> **Status**: Idle
> **Updated At**: bootstrap
> **Source Branch**: main
> **Source Commit**: bootstrap
> **Target Branch**: main
> **Stale After**: 24h
> **Reason**: bootstrap
> **Derived From**: active-plan, workstreams, handoff, checks, git status

This file is a tracked mainline snapshot derived from repo artifacts. It is not a live lock, not a kanban board, and not an implementation gate. If it is stale, read the source artifacts below.

## 手書きの追記（2026-11-09：起動時スキャンの差分化・一覧の軽量化・タグの「, 」表示）

状態：実装と測定が終わった。測定の結果は `tasks/bench_scan_ui.md`。追加（同日）：新着の即時表示（仮の記録・背景の解析・自動更新・監視の作り直し・既定の並びは更新日時の新しい順）。正本は `docs/spec.md` の「起動時スキャンとフォルダの再スキャン」。

### 先送り（別の計画の候補）

- 仮想スクロール：1 万件超で 1 クリックを約 233 ms から 100 ms 以下にするには、タイルを画面に見える分だけ作る必要がある（約 60〜110 分の見込み。判断 3 で、利用者が今回の 233 ms の受け入れを承認し、これを別の計画に回した）。
- 新規フォルダの初回登録の背景解析の高速化：1 件あたり約 0.25〜0.36 秒（ハッシュと解析）。表示は仮の記録で即時になったが、解析の完了までプロンプト検索には当たらない。
- `.tile` への `contain: layout style paint`：約 100 ms の見通しだが、はみ出す枠線や影を切る恐れの確認が要る。

### 範囲外の発見

- 「起動から表示まで 1 時間以上」という体験は再現できなかった。スキャン中でも一覧は約 0.3 秒、縮小画像は約 6 ms で応答した（`tasks/bench_scan_ui.md` の分岐点 1）。実際の遅さの主因が別にある可能性がある。
- 最初の描画は 1,420 → 844〜1,126 ms と早くなったが、全タイルがそろうまでは 2,539 → 4,492〜5,186 ms と遅くなった（先頭 400 枚を先に描く設計の代償）。
- 本番のデータベースの更新日時が、変更前とコールドの測定の前後で変わった（測定ツールは読み取り専用）。本番サーバー自身の登録処理と考えるが、測定と重なった理由は未解明。
