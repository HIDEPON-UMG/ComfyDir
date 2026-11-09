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

- （済み 2026-11-09）仮想スクロール：利用者の「全ての操作に 1〜2 秒近いラグがある」という指摘を受けて実施した。1 クリック 20〜30 ms、スクロール 1 フレーム 16.7 ms。正本は `docs/spec.md` の「一覧とタグの表示」。
- ホバー時の元 PNG の取得（ComfyUI へのドラッグの仕組み）：1 枚 200〜1,000 ms の通信が起き、まれにホバーが 200 ms を超える。仕組みの作り直し（ドラッグの開始時にだけ取得する等）は未着手で、別の計画の候補。
- 新規フォルダの初回登録の背景解析の高速化：1 件あたり約 0.25〜0.36 秒（ハッシュと解析）。表示は仮の記録で即時になったが、解析の完了までプロンプト検索には当たらない。
- （不要になった）`.tile` への `contain: layout style paint`：仮想スクロールでタイル数が約 40 枚になったため。

### 範囲外の発見

- 「起動から表示まで 1 時間以上」という体験は再現できなかった。スキャン中でも一覧は約 0.3 秒、縮小画像は約 6 ms で応答した（`tasks/bench_scan_ui.md` の分岐点 1）。実際の遅さの主因が別にある可能性がある。
- 最初の描画は 1,420 → 844〜1,126 ms と早くなったが、全タイルがそろうまでは 2,539 → 4,492〜5,186 ms と遅くなった（先頭 400 枚を先に描く設計の代償）。→ 仮想スクロール（2026-11-09）で「全タイルがそろう」状態自体がなくなり、描画が落ち着くまで 860 ms（最初の描画 790 ms）になった。
- 本番のデータベースの更新日時が、変更前とコールドの測定の前後で変わった（測定ツールは読み取り専用）。本番サーバー自身の登録処理と考えるが、測定と重なった理由は未解明。
