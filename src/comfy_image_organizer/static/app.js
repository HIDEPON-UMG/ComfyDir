// ComfyImageOrganizer フロントエンド
// 状態管理は単純なグローバルオブジェクト + 必要時に DOM を再描画。
// UI 状態 (フォルダ選択 / サイズ / ソート / フィルタ / ペイン幅) は localStorage に永続化。

(() => {
  // ---------------- 永続化 ----------------
  const PREF_KEY = "cio.prefs.v1";

  function loadPrefs() {
    try {
      const raw = localStorage.getItem(PREF_KEY);
      if (!raw) return {};
      return JSON.parse(raw) || {};
    } catch {
      return {};
    }
  }
  function savePrefs() {
    const p = {
      folderId: state.currentFolderId,
      thumbW: state.thumbW,
      order: state.order,
      direction: state.direction,
      filterTags: state.filterTags,
      filterMode: state.filterMode,
      rightPaneWidth: state.rightPaneWidth,
      promptQuery: state.promptQuery,
      memoQuery: state.memoQuery,
      helpHidden: state.helpHidden,
      paneOrder: state.paneOrder,
      favoritesView: state.favoritesView,
      favoritesCategoryFilter: state.favoritesCategoryFilter,
      favoritesQuery: state.favoritesQuery,
    };
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify(p));
    } catch {}
  }

  const prefs = loadPrefs();

  // ---------------- 状態 ----------------
  const state = {
    folders: [],
    currentFolderId: prefs.folderId ?? null,
    images: [],
    selected: new Set(),
    lastClickedId: null,
    detail: null,
    tags: [],
    filterTags: Array.isArray(prefs.filterTags) ? prefs.filterTags : [],
    filterMode: prefs.filterMode || "and",
    order: prefs.order || "name",
    direction: prefs.direction || "asc",
    thumbW: prefs.thumbW || 200,
    rightPaneWidth: prefs.rightPaneWidth || 360,
    promptQuery: prefs.promptQuery || "",
    memoQuery: prefs.memoQuery || "",
    helpHidden: !!prefs.helpHidden,
    paneOrder: sanitizePaneOrder(prefs.paneOrder),
    // ---- お気に入りプロンプト関連 ----
    favoritesView: !!prefs.favoritesView,
    favoritesCategoryFilter: prefs.favoritesCategoryFilter ?? "all",
    favoritesQuery: prefs.favoritesQuery || "",
    favoriteCategories: [],
    favorites: [],
    // 編集ダイアログのコンテキスト (新規 or 編集対象 ID)
    favoriteEditTarget: null,
    // ライトボックスで表示中の画像 ID (null = 非表示)
    lightboxImageId: null,
    // ライトボックスのズーム倍率 (1 = 全体表示にフィット)
    lightboxZoom: 1,
    // ライトボックスのパン量 (transform-origin: 0 0 + translate(tx,ty) scale(s))
    lightboxPanX: 0,
    lightboxPanY: 0,
  };

  // ライトボックスのズーム範囲とステップ (Google ドライブ風)
  const LIGHTBOX_ZOOM_MIN = 0.25;
  const LIGHTBOX_ZOOM_MAX = 8;
  // ボタン/キー操作の 1 段あたりの倍率
  const LIGHTBOX_ZOOM_STEP = 1.25;
  // ホイール 1 ノッチ (deltaMode=0 で deltaY≈100) あたりの倍率
  const LIGHTBOX_ZOOM_WHEEL_STEP = 1.15;

  // 右ペインのデフォルト並び順 (ファイル名/メタ → Myタグ → メモ → Positive → Negative)
  function sanitizePaneOrder(arr) {
    const known = new Set(["preview", "tags", "memo", "positive", "negative"]);
    const defaults = ["preview", "tags", "memo", "positive", "negative"];
    // プレビュー画像廃止に伴う旧デフォルト順 (= 未カスタマイズの保存値) は新デフォルトにリセット
    const legacyDefault = ["positive", "tags", "memo", "preview", "negative"];
    const isLegacyDefault =
      Array.isArray(arr) && arr.length === legacyDefault.length &&
      arr.every((v, i) => v === legacyDefault[i]);
    if (isLegacyDefault) return [...defaults];
    // 既知 ID だけ残す (旧バージョンの "filename" 等は自動除去)
    const order = Array.isArray(arr) ? arr.filter(s => known.has(s)) : [];
    for (const id of defaults) {
      if (!order.includes(id)) order.push(id);
    }
    return order;
  }

  // ---------------- ヘルパ ----------------
  const $ = (sel) => document.querySelector(sel);
  const setStatus = (msg) => { $("#statusBar").textContent = msg || ""; };

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`${res.status} ${res.statusText}: ${text}`);
    }
    return res.json();
  }

  function snapThumbW(w) {
    const steps = [128, 192, 256, 384, 512];
    let best = steps[0];
    let bestDiff = Infinity;
    for (const s of steps) {
      const d = Math.abs(s - w);
      if (d < bestDiff) { bestDiff = d; best = s; }
    }
    return best;
  }

  // ---------------- フォルダ ----------------
  async function reloadFolders() {
    state.folders = await api("/api/folders");
    const sel = $("#folderSelect");
    sel.innerHTML = "";
    for (const f of state.folders) {
      const o = document.createElement("option");
      o.value = f.id;
      o.textContent = `${f.label} (${f.image_count})`;
      sel.appendChild(o);
    }
    if (state.folders.length === 0) {
      state.currentFolderId = null;
      state.images = [];
      state.selected.clear();
      state.detail = null;
      renderGrid();
      renderRightPane();
      savePrefs();
      return;
    }
    if (!state.folders.find(f => f.id === state.currentFolderId)) {
      state.currentFolderId = state.folders[0].id;
    }
    sel.value = state.currentFolderId;
    savePrefs();
    await reloadImages();
  }

  async function addFolder() {
    const dlg = $("#addFolderDialog");
    $("#folderPathInput").value = "";
    $("#folderLabelInput").value = "";
    dlg.showModal();
    dlg.onclose = async () => {
      if (dlg.returnValue !== "default") return;
      const p = $("#folderPathInput").value.trim();
      if (!p) return;
      const label = $("#folderLabelInput").value.trim() || null;
      try {
        setStatus("フォルダ追加中...");
        await api("/api/folders", {
          method: "POST",
          body: JSON.stringify({ path: p, label }),
        });
        await reloadFolders();
        setStatus("フォルダを追加しました (バックグラウンドでスキャン中)");
      } catch (e) {
        alert("失敗: " + e.message);
        setStatus("");
      }
    };
  }

  async function editCurrentFolder() {
    if (state.currentFolderId == null) return;
    const f = state.folders.find(x => x.id === state.currentFolderId);
    if (!f) return;
    const dlg = $("#editFolderDialog");
    $("#folderEditPathInput").value = f.path;
    // label_raw はユーザーが明示的に入れた生ラベル (null = 未設定)。
    // フォルダ名フォールバックを編集枠に書き戻すと「保存=フォルダ名で固定」になってしまうので避ける。
    $("#folderEditLabelInput").value = f.label_raw ?? "";
    dlg.showModal();
    dlg.onclose = async () => {
      if (dlg.returnValue !== "default") return;
      const newPath = $("#folderEditPathInput").value.trim();
      const newLabel = $("#folderEditLabelInput").value;
      const body = { label_provided: true, label: newLabel };
      if (newPath && newPath !== f.path) body.path = newPath;
      try {
        setStatus("フォルダ情報を更新中...");
        const res = await api(`/api/folders/${state.currentFolderId}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        });
        await reloadFolders();
        if (res.path_changed) {
          setStatus("パスを更新しました (バックグラウンドで再スキャン中)");
        } else {
          setStatus("ラベルを更新しました");
        }
      } catch (e) {
        alert("失敗: " + e.message);
        setStatus("");
      }
    };
  }

  async function removeCurrentFolder() {
    if (state.currentFolderId == null) return;
    const f = state.folders.find(x => x.id === state.currentFolderId);
    if (!confirm(`登録解除しますか?\n${f?.path}\n\n(画像のMyタグ情報も DB から消えますが、ファイル自体は削除されません)`)) return;
    await api(`/api/folders/${state.currentFolderId}`, { method: "DELETE" });
    state.currentFolderId = null;
    await reloadFolders();
    await reloadTags();
  }

  async function rescanCurrentFolder() {
    if (state.currentFolderId == null) return;
    setStatus("再スキャン中...");
    await api(`/api/folders/${state.currentFolderId}/rescan`, { method: "POST" });
    setTimeout(async () => {
      await reloadFolders();
      setStatus("再スキャン完了");
    }, 1500);
  }

  // ---------------- 画像一覧 ----------------
  async function reloadImages() {
    if (state.currentFolderId == null) {
      state.images = [];
      renderGrid();
      return;
    }
    const params = new URLSearchParams({
      folder_id: state.currentFolderId,
      order: state.order,
      direction: state.direction,
      tag_mode: state.filterMode,
    });
    if (state.filterTags.length) params.set("tags", state.filterTags.join(","));
    if (state.promptQuery) params.set("q", state.promptQuery);
    if (state.memoQuery) params.set("qm", state.memoQuery);
    state.images = await api("/api/images?" + params.toString());
    const ids = new Set(state.images.map(i => i.id));
    for (const id of Array.from(state.selected)) {
      if (!ids.has(id)) state.selected.delete(id);
    }
    renderGrid();
    renderRightPane();
  }

  function renderGrid() {
    const g = $("#grid");
    g.innerHTML = "";
    const w = state.thumbW;
    const reqW = snapThumbW(w);
    g.style.setProperty("--thumb-w", `${w}px`);

    for (const img of state.images) {
      const cell = document.createElement("div");
      cell.className = "cell" + (state.selected.has(img.id) ? " selected" : "");
      cell.dataset.id = img.id;
      cell.style.setProperty("--thumb-w", `${w}px`);

      const i = document.createElement("img");
      i.loading = "lazy";
      i.src = `/api/images/${img.id}/thumb?w=${reqW}&v=${img.sha1.slice(0, 8)}`;
      cell.appendChild(i);

      const n = document.createElement("div");
      n.className = "name";
      n.textContent = img.filename;
      cell.appendChild(n);

      cell.addEventListener("click", (ev) => onCellClick(ev, img.id));
      g.appendChild(cell);
    }

    updateSummary();
    updateMoveButton();
  }

  function updateSummary() {
    $("#gridSummary").textContent = `${state.images.length} 枚` +
      (state.selected.size ? ` / ${state.selected.size} 枚選択中` : "");
  }

  function updateMoveButton() {
    $("#btnMove").disabled = state.selected.size === 0;
  }

  function onCellClick(ev, id) {
    if (ev.shiftKey && state.lastClickedId != null) {
      const ids = state.images.map(i => i.id);
      const a = ids.indexOf(state.lastClickedId);
      const b = ids.indexOf(id);
      if (a >= 0 && b >= 0) {
        const [from, to] = a < b ? [a, b] : [b, a];
        for (let k = from; k <= to; k++) state.selected.add(ids[k]);
      }
    } else if (ev.ctrlKey || ev.metaKey) {
      if (state.selected.has(id)) state.selected.delete(id);
      else state.selected.add(id);
      state.lastClickedId = id;
    } else {
      // 同じセルを連続でクリックされたら拡大表示 (二回連続押し = ライトボックス)
      const isSameAsSelected =
        state.selected.size === 1 && state.selected.has(id) && state.lastClickedId === id;
      if (isSameAsSelected) {
        openLightbox(id);
        return;
      }
      state.selected.clear();
      state.selected.add(id);
      state.lastClickedId = id;
    }
    refreshDetail();
    renderGrid();
    renderRightPane();
  }

  // ---------------- 詳細 / 右ペイン ----------------
  async function refreshDetail() {
    if (state.selected.size === 1) {
      const id = [...state.selected][0];
      state.detail = await api(`/api/images/${id}`);
    } else {
      state.detail = null;
    }
    renderRightPane();
  }

  function renderRightPane() {
    const pane = $("#rightPane");
    pane.innerHTML = "";

    // お気に入りビュー優先 (画像選択状態とは独立に表示)
    if (state.favoritesView) {
      renderFavoritesPane(pane);
      return;
    }

    if (state.selected.size === 0) {
      pane.innerHTML = `<div class="empty">画像を選択してください</div>`;
      return;
    }

    if (state.selected.size > 1) {
      renderBulkPane(pane);
      return;
    }

    if (!state.detail) {
      pane.innerHTML = `<div class="empty">読み込み中...</div>`;
      return;
    }
    renderSinglePane(pane, state.detail);
  }

  // ---------------- 単一選択ペイン: セクション辞書 + 並び替え ----------------

  // 各セクション ID → 「中身を組み立てる」関数。
  // 戻り値の DOM が <div class="pane-section"> の中身になる。
  // タイトルは sectionMeta から取得。
  const sectionRenderers = {
    // ファイル名(改名フォーム) + メタ情報。プレビュー画像はグリッドのサムネイルと
    // 重複するため右ペインからは廃止 (拡大はサムネ二回連続クリックでライトボックス)
    preview: (d) => {
      const frag = document.createDocumentFragment();

      // 1) ファイル名 + 改名フォーム
      const ext = d.filename.includes(".") ? d.filename.slice(d.filename.lastIndexOf(".")) : "";
      const stem = ext ? d.filename.slice(0, -ext.length) : d.filename;
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `<input type="text" class="rename-input" value="${escapeAttr(stem)}" /><span>${escapeHtml(ext)}</span><button class="btn-rename btn-primary">改名</button>`;
      const renameInput = row.querySelector(".rename-input");
      const renameBtn = row.querySelector(".btn-rename");
      renameBtn.onclick = async () => {
        const v = renameInput.value.trim();
        if (!v) return;
        try {
          const updated = await api(`/api/images/${d.id}/rename`, {
            method: "POST",
            body: JSON.stringify({ filename: v }),
          });
          setStatus(`改名: ${updated.filename}`);
          await reloadImages();
          await refreshDetail();
        } catch (e) {
          alert("改名失敗: " + e.message);
        }
      };
      frag.appendChild(row);

      // 2) メタ情報 (解像度 / サイズ / 更新日時)
      const meta = document.createElement("div");
      meta.className = "meta";
      const date = new Date(d.mtime * 1000).toLocaleString();
      meta.innerHTML = `${d.width}×${d.height} px / ${formatBytes(d.size)} / ${date}`;
      frag.appendChild(meta);

      return frag;
    },

    tags: (d) => {
      const frag = document.createDocumentFragment();
      const chips = document.createElement("div");
      chips.className = "chips";
      for (const t of d.tags) {
        const c = document.createElement("span");
        c.className = "chip";
        c.innerHTML = `${escapeHtml(t)}<span class="x" data-tag="${escapeAttr(t)}">×</span>`;
        chips.appendChild(c);
      }
      frag.appendChild(chips);

      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `<input type="text" class="tag-add-input" placeholder="Myタグを追加 (Enter)" list="tagDatalist" /><button class="btn-tag-add btn-primary">+</button>`;
      const tagInput = row.querySelector(".tag-add-input");
      const tagBtn = row.querySelector(".btn-tag-add");
      frag.appendChild(row);

      // 既存タグ datalist (詳細パネル内に毎回 1 つだけ生成)
      if (!document.getElementById("tagDatalist")) {
        const dl = document.createElement("datalist");
        dl.id = "tagDatalist";
        for (const t of state.tags) {
          const o = document.createElement("option");
          o.value = t.name;
          dl.appendChild(o);
        }
        frag.appendChild(dl);
      }

      chips.querySelectorAll(".x").forEach(x => {
        x.onclick = async () => {
          const tag = x.dataset.tag;
          await api("/api/tags/assign", {
            method: "POST",
            body: JSON.stringify({ image_ids: [d.id], add: [], remove: [tag] }),
          });
          await refreshDetail();
          await reloadTags();
        };
      });
      const submitTag = async () => {
        const v = tagInput.value.trim();
        if (!v) return;
        await api("/api/tags/assign", {
          method: "POST",
          body: JSON.stringify({ image_ids: [d.id], add: [v], remove: [] }),
        });
        await refreshDetail();
        await reloadTags();
      };
      tagBtn.onclick = submitTag;
      tagInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") { ev.preventDefault(); submitTag(); }
      });
      return frag;
    },

    memo: (d) => {
      const wrap = document.createElement("div");
      wrap.className = "memo-block";
      wrap.innerHTML = `
        <textarea class="memo-input" rows="2" placeholder="この画像についてのメモ (自動保存)"></textarea>
      `;
      const memoInput = wrap.querySelector(".memo-input");
      memoInput.value = d.memo || "";
      let memoSavedValue = memoInput.value;
      let memoTimer = null;
      const memoStatusEl = () => wrap.parentElement?.querySelector(".memo-status");
      const saveMemo = async () => {
        const v = memoInput.value;
        if (v === memoSavedValue) return;
        try {
          if (memoStatusEl()) memoStatusEl().textContent = "保存中...";
          await api(`/api/images/${d.id}/memo`, {
            method: "POST",
            body: JSON.stringify({ memo: v }),
          });
          memoSavedValue = v;
          if (memoStatusEl()) {
            memoStatusEl().textContent = "保存済";
            setTimeout(() => { if (memoStatusEl()) memoStatusEl().textContent = ""; }, 1500);
          }
        } catch (e) {
          if (memoStatusEl()) memoStatusEl().textContent = "保存失敗: " + e.message;
        }
      };
      memoInput.addEventListener("input", () => {
        if (memoStatusEl()) memoStatusEl().textContent = "編集中...";
        clearTimeout(memoTimer);
        memoTimer = setTimeout(saveMemo, 600);
      });
      memoInput.addEventListener("blur", () => {
        clearTimeout(memoTimer);
        saveMemo();
      });
      return wrap;
    },

    positive: (d) => promptBlockBody("Positive Prompt", d.positive_prompt),
    negative: (d) => promptBlockBody("Negative Prompt", d.negative_prompt),
  };

  // セクション ID → タイトル + 補助 (statusスパン等)
  const sectionMeta = {
    preview:  { title: "画像", noTitle: true },     // 画像 + ファイル名 + メタを内包
    tags:     { title: "Myタグ" },
    memo:     { title: "メモ", extraHeader: '<span class="memo-status"></span>' },
    positive: { title: "Positive Prompt" },
    negative: { title: "Negative Prompt" },
  };

  function renderSinglePane(pane, d) {
    pane.innerHTML = "";
    const container = document.createElement("div");
    container.className = "pane-sections";

    for (const id of state.paneOrder) {
      const renderer = sectionRenderers[id];
      const meta = sectionMeta[id];
      if (!renderer || !meta) continue;
      container.appendChild(buildSection(id, meta, renderer(d)));
    }
    pane.appendChild(container);
    setupSectionDnD(container);
  }

  function buildSection(id, meta, bodyDom) {
    const sec = document.createElement("section");
    sec.className = "pane-section";
    sec.dataset.sectionId = id;

    const header = document.createElement("div");
    header.className = "pane-section-header";
    const handle = document.createElement("span");
    handle.className = "drag-handle";
    handle.title = "ドラッグして並び替え";
    handle.textContent = "⋮⋮";
    header.appendChild(handle);
    if (!meta.noTitle) {
      const h = document.createElement("h3");
      h.textContent = meta.title;
      header.appendChild(h);
    }
    if (meta.extraHeader) {
      const ex = document.createElement("span");
      ex.className = "section-extra";
      ex.innerHTML = meta.extraHeader;
      header.appendChild(ex);
    }
    sec.appendChild(header);

    const body = document.createElement("div");
    body.className = "pane-section-body";
    body.appendChild(bodyDom);
    sec.appendChild(body);

    // ハンドルの mousedown 中だけ section を draggable に (テキスト選択を阻害しないため)
    handle.addEventListener("mousedown", () => sec.setAttribute("draggable", "true"));
    handle.addEventListener("mouseup", () => sec.removeAttribute("draggable"));
    handle.addEventListener("mouseleave", () => {
      // ドラッグ開始後ならそのまま、未開始なら解除
      if (!sec.classList.contains("dragging")) sec.removeAttribute("draggable");
    });
    return sec;
  }

  function setupSectionDnD(container) {
    let dragSrc = null;

    container.addEventListener("dragstart", (ev) => {
      const sec = ev.target.closest?.(".pane-section");
      if (!sec || sec.parentElement !== container) return;
      dragSrc = sec;
      sec.classList.add("dragging");
      try { ev.dataTransfer.setData("text/plain", sec.dataset.sectionId); } catch {}
      ev.dataTransfer.effectAllowed = "move";
    });

    container.addEventListener("dragend", () => {
      if (dragSrc) dragSrc.classList.remove("dragging");
      dragSrc = null;
      container.querySelectorAll(".pane-section").forEach(s => {
        s.classList.remove("drop-before", "drop-after");
        s.removeAttribute("draggable");
      });
    });

    container.addEventListener("dragover", (ev) => {
      if (!dragSrc) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
      const tgt = ev.target.closest?.(".pane-section");
      container.querySelectorAll(".pane-section").forEach(s => {
        s.classList.remove("drop-before", "drop-after");
      });
      if (!tgt || tgt === dragSrc) return;
      const rect = tgt.getBoundingClientRect();
      const before = (ev.clientY - rect.top) < rect.height / 2;
      tgt.classList.add(before ? "drop-before" : "drop-after");
    });

    container.addEventListener("drop", (ev) => {
      if (!dragSrc) return;
      ev.preventDefault();
      const tgt = ev.target.closest?.(".pane-section");
      if (!tgt || tgt === dragSrc) return;
      const rect = tgt.getBoundingClientRect();
      const before = (ev.clientY - rect.top) < rect.height / 2;
      if (before) container.insertBefore(dragSrc, tgt);
      else container.insertBefore(dragSrc, tgt.nextSibling);

      // 新しい順序を state に反映 + 永続化
      state.paneOrder = Array.from(container.querySelectorAll(".pane-section"))
        .map(s => s.dataset.sectionId);
      savePrefs();
    });
  }

  // ============================================================
  // プロンプト並び替え（comfy-prompt-helper の sort-tags.js 移植版）
  // 画像メタデータ（ファイル）は一切触らず、表示中の <pre> のテキストだけを
  // カテゴリ別ソート版に切り替える。コピーは現在の表示内容を対象にする。
  // Danbooru CSV ベースの character/copyright/artist/meta 分類は持たず、
  // 純粋なキーワードルールでカテゴリ振り分けする（簡易版）。
  // ============================================================
  const PROMPT_SORT_CATEGORY_ORDER = [
    "character", "copyright", "artist",
    "subject", "body", "skin", "hair", "face", "emotion",
    "clothing", "accessories", "pose", "composition", "scene",
    "lighting", "quality", "meta", "nsfw", "unknown", "lora",
  ];
  const PROMPT_SORT_CATEGORY_LABEL = {
    character: "キャラクター", copyright: "作品", artist: "作者",
    subject: "人物", body: "体型・年齢", skin: "肌・体質", hair: "髪",
    face: "口・目・視線", emotion: "表情・感情", clothing: "服装",
    accessories: "装飾品・持ち物", pose: "姿勢・動作",
    composition: "構図・視点", scene: "背景・場所",
    lighting: "光・色調", quality: "品質", meta: "メタ",
    nsfw: "性表現", unknown: "その他", lora: "LoRA",
  };
  // Danbooru CSV から取得した {タグ名(lower): カテゴリ} の逆引き Map。
  // 起動時に /api/prompt-category-map で一度だけロードする。失敗してもキーワードルールにフォールバックするのでアプリ機能は止めない。
  const PROMPT_DANBOORU_CATEGORY = new Map();

  // ユーザー指定の「品質タグ強制」リスト。CSV カテゴリより優先して quality バケットへ振る。
  // キーは _normalizePromptToken 後の形式（lower-case、スペース→アンダースコア、エスケープ括弧解除済み）。
  // ハイフン形式のタグ（"ultra-detailed"）はそのまま登録する。
  const PROMPT_QUALITY_OVERRIDE = new Set([
    "masterpiece",
    "best_quality",
    "year_2025",
    "newest",
    "nsfw",
    "full_color",
    "colorful",
    "intricate",
    "clean",
    "fine",
    "detailed",
    "very_aesthetic",
    "amazing_quality",
    "oily",
    "extremely_hyghres_resolution",
    "ultra-detailed",
    "intricate_detailed_face_and_eyes",
    "intricate_shiny_hair_line",
    "amazing_detailed_skin",
    "shiny_skin",
    "realistic",
    "uncensored",
  ]);
  // キーワードルール（先頭から評価し、最初にヒットしたカテゴリへ振り分ける）。
  // キーワードは正規化済みタグ文字列（lower-case、'_' のまま）に対する正規表現または部分一致。
  const PROMPT_SORT_KEYWORD_RULES = [
    { cat: "subject", patterns: [/^\d+\+?(girl|boy|other)s?$/, /^(solo|multiple_(girls|boys|others)|group|crowd|no_humans|couple|duo|trio|harem|reverse_harem)$/, "faceless_", /_male$/, /_female$/, "androgynous", "trap", "otoko_no_ko"] },
    { cat: "body", patterns: ["breasts", "nipples", "muscular", "chubby", "thick", "slim", "skinny", "petite", "tall", "short_stature", "loli", "shota", "mature", "milf", "young", "old_man", "old_woman", "adult", "teenager", "child", "elderly", "thigh_gap", "wide_hips", "curvy", "slender", "abs", "navel", "belly", "pregnant", "_thighs", "_legs"] },
    // 肌・体質（skin/汗/タトゥー/ほくろ/痣・傷・包帯/体毛など）。hair より先に置いて "body_hair" 等を確保する。
    { cat: "skin", patterns: [
      // 肌の状態・色
      "damaged_skin", "chapped_skin", "dry_skin", "cracked_skin", "peeling_skin",
      "scaly_skin", "discolored_skin", "dark_skin", "pale_skin", "fair_skin",
      "olive_skin", /^tan$/, "tanned", "sun_tan", "sunburn", "tanlines", "tan_line",
      "dark-skinned",
      // 汗
      /^sweat($|_)/, "sweating", "sweaty", "perspiration", "damp_skin", "dripping_sweat",
      // タトゥー・体表ペイント（pubic_tattoo / arm_tattoo / chest_tattoo など部位別も部分一致で拾う）
      "tattoo", "henna", "body_paint", "face_paint", "body_writing", "branding",
      // ほくろ・そばかす・あざ
      "birthmark", /^mole($|_)/, "freckle", "freckles", "blemish",
      // 傷・痣・包帯
      /^bruis/, /^wound/, "bandage", "bandaged", /^scar(red|s)?$/, /^scar_/, "stitch", "stitches",
      // 体毛・その他体表
      "goosebumps", "body_hair", "arm_hair", "leg_hair", "chest_hair", "armpit_hair",
      "pubic_hair", "shaved_pubic_hair", "trimmed_pubic_hair", "happy_trail",
    ] },
    { cat: "hair", patterns: ["hair", "bangs", "ponytail", "twintails", "twin_tails", "braid", "braids", "bun", "ahoge", "sidelocks", "drill", "ringlet"] },
    { cat: "emotion", patterns: ["smile", "smiling", "frown", "blush", "blushing", "expression", "tear", "tears", "crying", "sobbing", "laughing", "giggle", "angry", "anger", "rage", "fury", "sad", "sadness", "happy", "happiness", "joy", "joyful", "ecstatic", "surprised", "shocked", "embarrassed", "embarrass", "ashamed", "shame", "frustrated", "frustration", "scowl", "smirk", "pout", "pouting", "smug", "shy", "bashful", "timid", "depressed", "gloomy", "melancholic", "melancholy", "nostalgic", "bored", "boredom", "disgust", "disgusted", "disappointed", "disappointment", "scared", "fear", "afraid", "terrified", "horror", "worried", "anxious", "concerned", "confused", "puzzled", "jealous", "envy", "lonely", "peaceful", "relaxed", "calm", "content", "serene", "annoyed", "irritated", "drunk", "tipsy", "tired", "exhausted", "sleepy", "drowsy", "excited", "ecstasy", "seductive", "flirting", "yandere", "tsundere", "kuudere", "dandere", "humiliation", "humiliated", "humiliating", "guilt", "guilty", "regret", "regretful", "proud", "pride", "arrogant", "smug_face", "ahegao", /gao$/, "_face"] },
    // 口・目・視線（旧 eyes と face を統合）
    { cat: "face", patterns: [
      // 目（複数形 "*_eyes" は "eyes" 単純文字列で拾うので、ここでは単数形 "*_eye" や状態系を補強）
      "eyes", "eye_", /_eye$/, "closed_eye", "half_closed_eye", "half_closed_eyes",
      "narrowed_eyes", "empty_eyes", "glowing_eye", "glowing_eyes", "crazy_eyes",
      "wide-eyed", "rolling_eyes", "tareme", "tsurime", "jitome",
      "eyelash", "eyebrow", "pupils", "heterochromia",
      // 口・歯・舌
      "open_mouth", "closed_mouth", "parted_lips", "tongue", "teeth", "fang", "lips", "lipstick",
      // 視線
      "looking_at", "looking_away", "looking_back", "looking_up", "looking_down",
      // 目・口の動作
      "wink", "eyes_closed", "one_eye_closed", "drool", "saliva", "kiss", "kissing", "biting",
    ] },
    { cat: "clothing", patterns: ["shirt", "blouse", "dress", "skirt", "pants", "shorts", "trouser", "jacket", "coat", "hoodie", "uniform", "swimsuit", "bikini", "leotard", "bodysuit", "costume", "armor", "robe", "kimono", "yukata", "haori", "sailor_", "school_", "thigh_high", "stocking", "pantyhose", "tights", "sock", "boot", "shoe", "sandal", "glove", "scarf", "tie", "necktie", "ribbon", "hood", "sleeve", "collar", "apron", "cape", "cloak", "panties", "underwear", "bra", "lingerie", "naked", "nude", "topless", "bottomless", "see-through", "transparent_clothes", "clothes", "clothing", "outfit", "attire", "garment", "wear", "vest", "cardigan", "sweater", "tank_top", "tube_top", "crop_top", "halter", "off_shoulder", "bare_shoulder", "midriff", "cleavage", "formal", "suit", "tuxedo", "business_suit", "casual", "gothic", "lolita", "punk", "sportswear", "tracksuit", "pajama", "nightgown", "nightie", "negligee", "fishnet", "piercing", "pierced", "skin_tight", "tight_dress", "tight_pants", "tight_shirt", "bodycon", "spandex", "latex_clothes", "leather_clothes"] },
    { cat: "accessories", patterns: ["hat", "cap", "helmet", "mask", "glasses", "sunglasses", "monocle", "earring", "necklace", "ring", "bracelet", "anklet", "jewelry", "bag", "backpack", "umbrella", "weapon", "sword", "knife", "gun", "staff", "wand", "shield", "phone", "book", "flower", "holding_", "hairpin", "hair_ornament", "headband", "headphone", "tiara", "crown", "veil", "wings", "tail", "horns", "halo"] },
    { cat: "pose", patterns: ["sitting", "standing", "lying", "kneeling", "squatting", "crouching", "leaning", "bending", "walking", "running", "jumping", "flying", "falling", "floating", "hugging", "kissing", "holding", "carrying", "reaching", "pointing", "waving", "saluting", "dancing", "fighting", "sleeping", "resting", "stretching", "spread", "crossed", "raised", "outstretched", "arms_", "hands_", "legs_", "feet_", "knees_", "fingers_", "_pose", "all_fours", "lap_pillow", "piggyback"] },
    { cat: "composition", patterns: ["close-up", "closeup", "portrait", "full_body", "upper_body", "lower_body", "cowboy_shot", "from_above", "from_below", "from_side", "from_behind", "from_back", "front_view", "back_view", "side_view", "rear_view", "perspective", "depth_of_field", "bokeh", "wide_shot", "medium_shot", "long_shot", "_shot", "_view", "framed", "vignette", "looking_at_viewer", "dutch_angle", "dutch_tilt", "tilted", "tilt", "fisheye", "wide_angle", "telephoto", "macro", "establishing_shot", "bird's-eye_view", "worm's-eye_view", "pov", "first_person", "third_person", "over_the_shoulder", "focus", "_focus", "in_focus", "out_of_focus", "blurry", "blur", "motion_blur", "rule_of_thirds", "centered", "symmetrical_composition", "asymmetric"] },
    { cat: "scene", patterns: ["outdoors", "outside", "indoors", "inside", "sky", "cloud", "tree", "forest", "beach", "ocean", "sea", "river", "lake", "pond", "mountain", "hill", "city", "street", "alley", "rooftop", "room", "bedroom", "bathroom", "kitchen", "living_room", "house", "building", "shop", "school", "classroom", "office", "park", "garden", "field", "meadow", "desert", "snow_field", "cave", "ruins", "castle", "shrine", "temple", "church", "library", "cafe", "restaurant", "bar", "station", "bus", "train", "car", "boat", "ship", "airplane", "starry_sky", "sunset", "sunrise", "twilight", "night", "evening", "morning", "noon", "daytime", "midnight", "dawn", "dusk", /^day$/, /^night$/, "afternoon", "midday", "summer", "winter", "spring", "autumn", "fall_season", "rain", "snow", "snowing", "wind", "fog", "mist", "storm", "rainbow", "background", "scenery", "landscape", "wallpaper", "outdoor", "indoor",
      // 拘束・医療・娯楽・公共・スポーツ系の場所
      "prison", "prison_cell", "jail", "jail_cell", "dungeon", "interrogation_room",
      "courtroom", "hospital", "hospital_room", "clinic", "operating_room",
      "gym", "locker_room", "hotel", "hotel_room", "motel", "casino", "arcade",
      "museum", "theater", "stadium", "swimming_pool", /^pool$/, "sauna",
      "bathhouse", "public_bath", "onsen", "hot_spring", "spa",
      "factory", "warehouse", "attic", "basement", "garage", "abandoned_building"] },
    { cat: "lighting", patterns: ["light", "lighting", "shadow", "shadows", "glow", "glowing", "shine", "shining", "sparkle", "sparkling", "dark", "darkness", "bright", "dim", "lens_flare", "backlight", "rim_light", "cinematic", "dramatic", "soft_light", "hard_light", "natural_light", "neon", "moonlight", "sunlight", "candlelight", "spotlight", "color_palette", "monochrome", "sepia", "grayscale", "vibrant", "vivid", "pastel", "saturated", "desaturated"] },
    { cat: "quality", patterns: ["masterpiece", "best_quality", "high_quality", "highres", "absurdres", "ultra_detailed", "ultra-detailed", "very_detailed", "detailed", "intricate", "8k", "4k", "2k", "hd", "uhd", "fhd", "professional", "perfect", "amazing", "stunning", "beautiful", "gorgeous", "exquisite", "score_", "_quality", "shiny_skin", "smooth_skin", "glossy_skin", "wet_skin", "oily_skin", "skin_texture", "detailed_skin", "realistic_skin"] },
    { cat: "nsfw", patterns: [
      "cum", "semen", "sperm", "ejaculation", "ejaculating", "jizz", "creampie", "bukkake",
      "pussy", "vagina", "cunt", "vulva", "clitoris", "labia",
      "penis", "cock", /^dick$/, "phallic", "testicles", "scrotum",
      "anus", /^anal$/, "anal_sex", "rimjob",
      "sex", "intercourse", "fucking", "fellatio", "blowjob", "handjob", "footjob", "paizuri",
      "deepthroat", "facial", "gangbang", "threesome", "foursome", "orgy",
      "orgasm", "climax", "masturbation", "fingering", "tribadism", "scissoring",
      "futanari", "futa", "futasub", "futadom",
      "erection", "erect_", "throbbing",
      "lewd", "ecchi", "hentai", "porn",
      "molest", "molestation", "groping",
      "hymen", "virgin", "deflower",
      "censored", "uncensored", "mosaic_censoring", "bar_censor",
      "bondage", "bdsm", "shibari", "kinbaku",
      "gaping", "prolapse",
      "breast_sucking", "nipple_sucking",
      // 挿入・乳汁系（明示で nsfw 振り分け）
      "penetration", "vaginal_penetration", "anal_penetration", "double_penetration",
      "lactation", "lactating", "breast_milk", "milking",
    ] },
  ];

  // "(tag:1.2)" / "[tag]" / 重みサフィックスを剥がして lower-case 化する。
  // 注意: 先頭の "@" マーカーは保持する（_classifyPromptToken でアーティスト判定に使う）。
  function _normalizePromptToken(raw) {
    let s = (raw || "").trim();
    if (!s) return "";
    while (s.length >= 2 && ((s[0] === "(" && s[s.length - 1] === ")") || (s[0] === "[" && s[s.length - 1] === "]"))) {
      s = s.slice(1, -1).trim();
      if (!s) return "";
    }
    s = s.replace(/:\s*-?\d+(\.\d+)?\s*$/, "").trim();
    // a1111/ComfyUI でリテラル括弧として書かれる "\(" "\)" のエスケープを外し、
    // "hatsune_miku_\(vocaloid\)" を "hatsune_miku_(vocaloid)" と同じ扱いにする
    s = s.replace(/\\([()])/g, "$1");
    // Booru CSV のキーは "_" 区切り。プロンプト中はスペース区切りで書かれることがあるので統一する。
    s = s.toLowerCase().replace(/\s+/g, "_");
    return s;
  }
  function _isLoraToken(raw) {
    const s = (raw || "").trim();
    return s.startsWith("<lora:") || s.startsWith("<lyco:");
  }
  function _classifyPromptToken(raw) {
    const key = _normalizePromptToken(raw);
    if (!key) return "unknown";
    // Danbooru の慣習で "@..." 始まりはアーティスト印（CSV ヒット可否に関係なく確定）
    if (key.startsWith("@")) return "artist";
    // ユーザー指定の品質タグ強制リスト（CSV カテゴリより優先）
    if (PROMPT_QUALITY_OVERRIDE.has(key)) return "quality";
    // NSFW 強制：cum 系は composition の "_shot" にぶつかるため明示的に先取り。
    // "cumulus" 等の自然語は単語境界条件で除外する。
    if (/(^|[_-])cum([_-]|$)/.test(key)) return "nsfw";
    // META 強制：Danbooru の "hetero" 系メタタグ（heterochromia は次が "c" なので除外される）
    if (/^hetero($|_)/.test(key)) return "meta";
    // Danbooru CSV 由来のカテゴリを最優先で確定（character / copyright / artist / meta）
    const dCat = PROMPT_DANBOORU_CATEGORY.get(key);
    if (dCat) return dCat;
    // "hatsune_miku_(vocaloid)" のような末尾 "_(franchise)" 付きは（CSV に無くても）character 推定
    if (/_\([a-z0-9][a-z0-9_:.\-]*\)$/.test(key)) return "character";
    for (const rule of PROMPT_SORT_KEYWORD_RULES) {
      for (const p of rule.patterns) {
        if (p instanceof RegExp) {
          if (p.test(key)) return rule.cat;
        } else if (key.includes(p)) {
          return rule.cat;
        }
      }
    }
    return "unknown";
  }
  // 起動時に Danbooru CSV のカテゴリ別タグ一覧をロードして PROMPT_DANBOORU_CATEGORY に詰める。
  async function loadPromptDanbooruCategoryMap() {
    try {
      const res = await fetch("/api/prompt-category-map");
      if (!res.ok) return;
      const data = await res.json();
      if (!data || typeof data !== "object") return;
      for (const [cat, tags] of Object.entries(data)) {
        if (!Array.isArray(tags)) continue;
        for (const t of tags) {
          if (typeof t === "string" && t.length > 0) {
            PROMPT_DANBOORU_CATEGORY.set(t.toLowerCase(), cat);
          }
        }
      }
    } catch (e) {
      console.warn("[ComfyImageOrganizer] prompt-category-map のロード失敗:", e);
    }
  }
  // text を「カテゴリ別にラベルコメント付きで並び替えた版」に変換して返す。
  // 区切りはカンマと改行の両方を許容、出力は各カテゴリの先頭に "// ラベル" コメント行を入れる。
  function sortPromptText(text) {
    const tokens = (text || "")
      .split(/[,\n]/)
      .map((t) => t.trim())
      .filter((t) => t.length > 0);
    if (!tokens.length) return text || "";
    const buckets = new Map();
    for (const cat of PROMPT_SORT_CATEGORY_ORDER) buckets.set(cat, []);
    for (const tok of tokens) {
      const cat = _isLoraToken(tok) ? "lora" : _classifyPromptToken(tok);
      buckets.get(cat).push(tok);
    }
    // unknown はアルファベット順に
    const unknownArr = buckets.get("unknown");
    if (unknownArr && unknownArr.length > 1) {
      unknownArr.sort((a, b) => _normalizePromptToken(a).localeCompare(_normalizePromptToken(b)));
    }
    const lines = [];
    for (const cat of PROMPT_SORT_CATEGORY_ORDER) {
      const arr = buckets.get(cat);
      if (!arr.length) continue;
      const label = PROMPT_SORT_CATEGORY_LABEL[cat] ?? cat;
      lines.push(`// ${label}\n${arr.join(", ")}`);
    }
    return lines.join(",\n\n");
  }

  // セクション本体だけを返す (タイトルは buildSection 側で h3 を付ける)
  function promptBlockBody(title, text) {
    const wrap = document.createElement("div");

    const pre = document.createElement("pre");
    pre.textContent = text || "(なし)";
    // Negative はデフォルト縦幅を Positive の約半分にする (Positive 260px / Negative 130px)
    if (/negative/i.test(title)) {
      pre.classList.add("prompt-negative");
    }
    // ボタン bar は pre より上 (= セクションヘッダのすぐ下) に置きたいので
    // wrap への append 順は: bar → pre とする。bar は text が空なら付かない。

    if (text) {
      // ボタン群は 1 つの bar にまとめてレイアウト統一
      const bar = document.createElement("div");
      bar.className = "prompt-btn-bar";

      const copyBtn = document.createElement("button");
      copyBtn.className = "copy-btn btn-sub";
      copyBtn.textContent = "コピー";
      copyBtn.title = "現在表示中のテキストをクリップボードへ";
      copyBtn.onclick = async () => {
        // 並び替え後ならその表示テキストをコピー、そうでなければ元テキスト
        await navigator.clipboard.writeText(pre.textContent || "");
        setStatus(`${title} をクリップボードにコピーしました`);
      };
      bar.appendChild(copyBtn);

      const sortBtn = document.createElement("button");
      sortBtn.className = "sort-btn btn-sub";
      sortBtn.textContent = "並べ替え";
      sortBtn.title = "カテゴリ別に並び替えて表示（ファイルは更新しません）";

      const undoBtn = document.createElement("button");
      undoBtn.className = "undo-btn btn-sub";
      undoBtn.textContent = "戻る";
      undoBtn.title = "並び替え前の表示に戻す";
      undoBtn.disabled = true;

      sortBtn.onclick = () => {
        const sorted = sortPromptText(text);
        if (!sorted || sorted === pre.textContent) return;
        pre.textContent = sorted;
        undoBtn.disabled = false;
        sortBtn.disabled = true;
      };
      undoBtn.onclick = () => {
        pre.textContent = text;
        undoBtn.disabled = true;
        sortBtn.disabled = false;
      };

      bar.appendChild(sortBtn);
      bar.appendChild(undoBtn);

      // ★ お気に入りに追加: 現在の Positive + Negative をペアで保存
      const favBtn = document.createElement("button");
      favBtn.className = "fav-add-btn btn-sub";
      favBtn.textContent = "★ お気に入りに追加";
      favBtn.title = "Positive と Negative をペアで保存";
      favBtn.onclick = () => openFavoriteEditDialogForCurrent();
      bar.appendChild(favBtn);

      // ボタン行を pre より先に置く (セクションタイトルとプロンプト本文の間に表示)
      wrap.appendChild(bar);
    }
    wrap.appendChild(pre);
    return wrap;
  }

  function renderBulkPane(pane) {
    pane.innerHTML = `
      <h3>${state.selected.size} 枚 選択中</h3>
      <div class="row">
        <input type="text" id="bulkAddInput" placeholder="追加するMyタグ (カンマ区切り可)" list="tagDatalist" />
        <button id="bulkAdd" class="btn-primary">+ 付与</button>
      </div>
      <div class="row">
        <input type="text" id="bulkRemoveInput" placeholder="外すMyタグ" list="tagDatalist" />
        <button id="bulkRemove" class="danger">- 解除</button>
      </div>
      <h3>既存Myタグ</h3>
      <div id="bulkTagList" class="chips"></div>
      <datalist id="tagDatalist"></datalist>
    `;
    const dl = $("#tagDatalist");
    for (const t of state.tags) {
      const o = document.createElement("option");
      o.value = t.name;
      dl.appendChild(o);
    }
    const list = $("#bulkTagList");
    for (const t of state.tags) {
      const c = document.createElement("span");
      c.className = "chip";
      c.textContent = `${t.name} (${t.image_count})`;
      c.style.cursor = "pointer";
      c.title = "クリックで選択中の画像にMyタグ付与";
      c.onclick = async () => {
        await api("/api/tags/assign", {
          method: "POST",
          body: JSON.stringify({
            image_ids: [...state.selected],
            add: [t.name], remove: [],
          }),
        });
        setStatus(`${t.name} を ${state.selected.size} 枚に付与`);
        await reloadTags();
      };
      list.appendChild(c);
    }

    const split = (s) => s.split(",").map(x => x.trim()).filter(Boolean);

    $("#bulkAdd").onclick = async () => {
      const v = $("#bulkAddInput").value.trim();
      if (!v) return;
      await api("/api/tags/assign", {
        method: "POST",
        body: JSON.stringify({
          image_ids: [...state.selected],
          add: split(v), remove: [],
        }),
      });
      $("#bulkAddInput").value = "";
      setStatus(`${state.selected.size} 枚にMyタグ付与`);
      await reloadTags();
      renderRightPane();
    };
    $("#bulkRemove").onclick = async () => {
      const v = $("#bulkRemoveInput").value.trim();
      if (!v) return;
      await api("/api/tags/assign", {
        method: "POST",
        body: JSON.stringify({
          image_ids: [...state.selected],
          add: [], remove: split(v),
        }),
      });
      $("#bulkRemoveInput").value = "";
      setStatus(`${state.selected.size} 枚からMyタグ削除`);
      await reloadTags();
      renderRightPane();
    };
  }

  // ---------------- 移動ダイアログ ----------------
  function openMoveDialog() {
    if (state.selected.size === 0) return;
    const dlg = $("#moveDialog");
    $("#moveDialogSummary").textContent = `${state.selected.size} 枚を移動します。`;

    // 登録済みフォルダを select に詰める (現在のフォルダは除外)
    const sel = $("#moveFolderSelect");
    sel.innerHTML = "";
    for (const f of state.folders) {
      if (f.id === state.currentFolderId) continue;
      const o = document.createElement("option");
      o.value = f.id;
      o.textContent = `${f.label} - ${f.path}`;
      sel.appendChild(o);
    }
    if (sel.options.length === 0) {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = "(他に登録フォルダなし)";
      o.disabled = true;
      sel.appendChild(o);
    }

    // モード切替: ラジオに応じて select / text を有効化
    const updateModeUi = () => {
      const mode = document.querySelector('input[name="moveMode"]:checked').value;
      $("#moveFolderSelect").disabled = (mode !== "folder") || state.folders.length <= 1;
      $("#moveSubdir").disabled = mode !== "folder";
      $("#moveCustomPath").disabled = mode !== "path";
      $("#moveCreateDir").disabled = mode !== "path";
    };
    document.querySelectorAll('input[name="moveMode"]').forEach(r => {
      r.onchange = updateModeUi;
    });
    // 初期化
    $("#moveModeFolder").checked = state.folders.length > 1;
    $("#moveModePath").checked = state.folders.length <= 1;
    $("#moveSubdir").value = "";
    $("#moveCustomPath").value = "";
    $("#moveCreateDir").checked = false;
    updateModeUi();

    dlg.showModal();
    dlg.onclose = async () => {
      if (dlg.returnValue !== "default") return;
      const mode = document.querySelector('input[name="moveMode"]:checked').value;
      const body = { image_ids: [...state.selected] };
      if (mode === "folder") {
        const fid = parseInt($("#moveFolderSelect").value, 10);
        if (!fid) {
          alert("移動先フォルダを選択してください");
          return;
        }
        body.dest_folder_id = fid;
        const sub = $("#moveSubdir").value.trim();
        if (sub) body.subdir = sub;
        body.create_dir = true;  // サブフォルダは自動作成許可
      } else {
        const p = $("#moveCustomPath").value.trim();
        if (!p) {
          alert("移動先パスを入力してください");
          return;
        }
        body.dest_path = p;
        body.create_dir = $("#moveCreateDir").checked;
      }
      try {
        setStatus("移動中...");
        const res = await api("/api/images/move", {
          method: "POST",
          body: JSON.stringify(body),
        });
        setStatus(`移動完了: ${res.moved} 件 (失敗 ${res.failed.length} 件)`);
        if (res.failed.length) {
          alert("一部失敗:\n" + res.failed.map(f => `- ${f.filename}: ${f.error}`).join("\n"));
        }
        state.selected.clear();
        await reloadFolders();
        await reloadImages();
      } catch (e) {
        alert("移動失敗: " + e.message);
        setStatus("");
      }
    };
  }

  // ---------------- タグ ----------------
  async function reloadTags() {
    state.tags = await api("/api/tags");
    renderTagFilter();
  }

  function renderTagFilter() {
    const chips = $("#tagFilterChips");
    chips.innerHTML = "";
    for (const t of state.filterTags) {
      const c = document.createElement("span");
      c.className = "chip";
      c.innerHTML = `${escapeHtml(t)}<span class="x">×</span>`;
      c.querySelector(".x").onclick = () => {
        state.filterTags = state.filterTags.filter(x => x !== t);
        renderTagFilter();
        savePrefs();
        reloadImages();
      };
      chips.appendChild(c);
    }
    // datalist (オートコンプリート候補) は既存タグ全件を入れる
    const dl = $("#tagFilterDatalist");
    dl.innerHTML = "";
    for (const t of state.tags) {
      if (state.filterTags.includes(t.name)) continue;
      const o = document.createElement("option");
      o.value = t.name;
      o.textContent = `${t.name} (${t.image_count})`;
      dl.appendChild(o);
    }
  }

  function addFilterTag(name) {
    const v = (name || "").trim();
    if (!v) return;
    if (state.filterTags.includes(v)) return;
    state.filterTags.push(v);
    renderTagFilter();
    savePrefs();
    reloadImages();
  }

  // ---------------- ライトボックス ----------------
  // 現在ライトボックスに表示中の画像 ID。state.images の並び順上でナビゲートする。
  function openLightbox(imageId) {
    const idx = state.images.findIndex(i => i.id === imageId);
    if (idx < 0) return;
    state.lightboxImageId = imageId;
    showLightboxImage(idx);
    const lb = $("#lightbox");
    if (!lb.open) {
      if (typeof lb.showModal === "function") {
        lb.showModal();
      } else {
        // 古いブラウザ向けフォールバック
        lb.setAttribute("open", "");
      }
    }
  }

  function showLightboxImage(idx) {
    const img = state.images[idx];
    if (!img) return;
    state.lightboxImageId = img.id;
    // 画像切替時はズーム/パンを完全リセット (全体表示)
    resetLightboxView();
    const src = `/api/images/${img.id}/preview?v=${img.sha1.slice(0, 8)}`;
    $("#lightboxImg").src = src;
    updateLightboxNavState(idx);
  }

  function updateLightboxNavState(idx) {
    const total = state.images.length;
    $("#lightboxPrev").disabled = idx <= 0;
    $("#lightboxNext").disabled = idx >= total - 1;
  }

  function applyLightboxTransform() {
    const el = $("#lightboxImg");
    if (!el) return;
    el.style.transform =
      `translate(${state.lightboxPanX}px, ${state.lightboxPanY}px) scale(${state.lightboxZoom})`;
  }

  function updateZoomButtons() {
    const z = state.lightboxZoom;
    $("#lightboxZoomOut").disabled = z <= LIGHTBOX_ZOOM_MIN + 1e-6;
    $("#lightboxZoomIn").disabled = z >= LIGHTBOX_ZOOM_MAX - 1e-6;
  }

  // ライトボックスのズーム/パンを完全リセット (画像切替・「全体表示」ボタン)
  function resetLightboxView() {
    state.lightboxZoom = 1;
    state.lightboxPanX = 0;
    state.lightboxPanY = 0;
    applyLightboxTransform();
    updateZoomButtons();
  }

  // 指定アンカー (viewport 座標) を固定点にしてズーム。
  // anchorX/anchorY 省略時は現在の画像の中心を固定点にする。
  // transform-origin: 0 0 前提なので、layout 上の左上 x0 = rect.left - tx と置けて、
  // 新しい rect.left' = anchor - imageLocal * newScale から逆算で新 tx を求める。
  function setLightboxZoom(newScale, anchorX, anchorY) {
    const clamped = Math.max(LIGHTBOX_ZOOM_MIN, Math.min(LIGHTBOX_ZOOM_MAX, newScale));
    const el = $("#lightboxImg");
    if (!el) {
      state.lightboxZoom = clamped;
      updateZoomButtons();
      return;
    }
    if (Math.abs(clamped - state.lightboxZoom) < 1e-6) {
      updateZoomButtons();
      return;
    }
    const rect = el.getBoundingClientRect();
    const ax = (typeof anchorX === "number") ? anchorX : (rect.left + rect.width / 2);
    const ay = (typeof anchorY === "number") ? anchorY : (rect.top + rect.height / 2);
    // 現在 scale 下でアンカーが指す image-local 座標 (未変換ピクセル)
    const ix = (ax - rect.left) / state.lightboxZoom;
    const iy = (ay - rect.top) / state.lightboxZoom;
    // 新 scale 適用後にこの ix,iy を ax,ay の位置に保ちたい
    const newLeft = ax - ix * clamped;
    const newTop  = ay - iy * clamped;
    // x0 = rect.left - panX が layout 上の左上。tx' = newLeft - x0
    state.lightboxPanX = newLeft - (rect.left - state.lightboxPanX);
    state.lightboxPanY = newTop  - (rect.top  - state.lightboxPanY);
    state.lightboxZoom = clamped;
    applyLightboxTransform();
    updateZoomButtons();
  }

  function navigateLightbox(delta) {
    if (state.lightboxImageId == null) return;
    const idx = state.images.findIndex(i => i.id === state.lightboxImageId);
    if (idx < 0) return;
    const next = idx + delta;
    if (next < 0 || next >= state.images.length) return;
    showLightboxImage(next);
  }

  function setupLightbox() {
    const lb = $("#lightbox");
    const closeIt = () => {
      if (typeof lb.close === "function") lb.close();
      else lb.removeAttribute("open");
      state.lightboxImageId = null;
    };
    // 画像クリックは閉じない (Google ドライブと同様 / 拡縮との衝突回避)
    $("#lightboxClose").onclick = closeIt;
    $("#lightboxPrev").addEventListener("click", (ev) => {
      ev.stopPropagation();
      navigateLightbox(-1);
    });
    $("#lightboxNext").addEventListener("click", (ev) => {
      ev.stopPropagation();
      navigateLightbox(+1);
    });
    $("#lightboxZoomIn").addEventListener("click", (ev) => {
      ev.stopPropagation();
      setLightboxZoom(state.lightboxZoom * LIGHTBOX_ZOOM_STEP);
    });
    $("#lightboxZoomOut").addEventListener("click", (ev) => {
      ev.stopPropagation();
      setLightboxZoom(state.lightboxZoom / LIGHTBOX_ZOOM_STEP);
    });
    $("#lightboxZoomReset").addEventListener("click", (ev) => {
      ev.stopPropagation();
      resetLightboxView();
    });
    // マウスホイールでカーソル位置を中心に拡縮 (Google ドライブ風)
    // deltaMode を吸収して 1 ノッチあたり一定倍率にする。
    lb.addEventListener("wheel", (ev) => {
      // dialog 内ではページスクロール/履歴ナビを抑止
      ev.preventDefault();
      // notches: ノッチ数の絶対値 (ピクセル単位:0, 行:1, ページ:2)
      let notches;
      if (ev.deltaMode === 1) notches = Math.abs(ev.deltaY) / 3;       // 行
      else if (ev.deltaMode === 2) notches = Math.abs(ev.deltaY);       // ページ
      else notches = Math.abs(ev.deltaY) / 100;                         // ピクセル
      if (notches <= 0) return;
      // 滑らかさのため指数で適用
      const factor = Math.pow(LIGHTBOX_ZOOM_WHEEL_STEP, notches);
      const next = ev.deltaY < 0 ? state.lightboxZoom * factor
                                 : state.lightboxZoom / factor;
      setLightboxZoom(next, ev.clientX, ev.clientY);
    }, { passive: false });
    // 背景 (画像/UI 以外の領域) クリックで閉じる
    lb.addEventListener("click", (ev) => {
      if (ev.target === lb) closeIt();
    });
    // dialog 標準の Esc 閉じで state を後始末
    lb.addEventListener("close", () => {
      state.lightboxImageId = null;
    });
    // キーボード: ←/→ で前後遷移、+/- でズーム、0 で全体表示
    lb.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowLeft") {
        ev.preventDefault();
        navigateLightbox(-1);
      } else if (ev.key === "ArrowRight") {
        ev.preventDefault();
        navigateLightbox(+1);
      } else if (ev.key === "+" || ev.key === "=") {
        ev.preventDefault();
        setLightboxZoom(state.lightboxZoom * LIGHTBOX_ZOOM_STEP);
      } else if (ev.key === "-" || ev.key === "_") {
        ev.preventDefault();
        setLightboxZoom(state.lightboxZoom / LIGHTBOX_ZOOM_STEP);
      } else if (ev.key === "0") {
        ev.preventDefault();
        resetLightboxView();
      }
    });
  }

  // ---------------- ヘルプバナー ----------------
  function applyHelpVisibility() {
    const banner = $("#helpBanner");
    const reopen = $("#helpReopen");
    if (state.helpHidden) {
      banner.classList.add("hidden");
      reopen.hidden = false;
    } else {
      banner.classList.remove("hidden");
      reopen.hidden = true;
    }
  }

  function setupHelpBanner() {
    applyHelpVisibility();
    $("#helpClose").onclick = () => {
      state.helpHidden = true;
      applyHelpVisibility();
      savePrefs();
    };
    $("#helpReopen").onclick = () => {
      state.helpHidden = false;
      applyHelpVisibility();
      savePrefs();
    };
  }

  // ---------------- スプリッタ ----------------
  function setupSplitter() {
    const splitter = $("#splitter");
    const main = $("#mainSplit");
    // 初期幅を反映
    main.style.setProperty("--right-w", `${state.rightPaneWidth}px`);

    let dragging = false;
    splitter.addEventListener("mousedown", (e) => {
      dragging = true;
      splitter.classList.add("dragging");
      document.body.classList.add("dragging-split");
      e.preventDefault();
    });
    document.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      // 右ペインの幅 = ウィンドウ右端 - マウス X - スプリッタ太さの半分
      const w = window.innerWidth - e.clientX - 3;
      const clamped = Math.max(220, Math.min(900, w));
      state.rightPaneWidth = clamped;
      main.style.setProperty("--right-w", `${clamped}px`);
    });
    document.addEventListener("mouseup", () => {
      if (!dragging) return;
      dragging = false;
      splitter.classList.remove("dragging");
      document.body.classList.remove("dragging-split");
      savePrefs();
    });
  }

  // ---------------- SSE ----------------
  function startEventStream() {
    const es = new EventSource("/api/events");
    es.addEventListener("image_added", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.folder_id === state.currentFolderId) {
          reloadImages();
          reloadFolders();
        }
      } catch {}
    });
    es.addEventListener("image_removed", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.folder_id === state.currentFolderId) {
          reloadImages();
          reloadFolders();
        }
      } catch {}
    });
    es.onerror = () => {};
  }

  // ---------------- ユーティリティ ----------------
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }
  function escapeAttr(s) { return escapeHtml(s); }
  function formatBytes(n) {
    if (n == null) return "";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0, x = n;
    while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
    return `${x.toFixed(i ? 1 : 0)} ${u[i]}`;
  }

  // ---------------- 起動 ----------------
  function bindUi() {
    // 永続化された値を UI に反映
    $("#sizeSlider").value = state.thumbW;
    $("#sizeNumber").value = state.thumbW;
    $("#orderSelect").value = `${state.order}|${state.direction}`;
    $("#tagFilterMode").value = state.filterMode;

    $("#btnAddFolder").onclick = addFolder;
    $("#btnRemoveFolder").onclick = removeCurrentFolder;
    $("#btnEditFolder").onclick = editCurrentFolder;
    $("#btnRescan").onclick = rescanCurrentFolder;
    $("#btnMove").onclick = openMoveDialog;

    // お気に入りプロンプト: トップバーのトグル
    const favToggleBtn = $("#btnFavoritesToggle");
    if (favToggleBtn) {
      // 永続化された状態を反映
      reflectFavoritesToggle();
      favToggleBtn.onclick = async () => {
        state.favoritesView = !state.favoritesView;
        savePrefs();
        reflectFavoritesToggle();
        if (state.favoritesView) {
          await reloadFavoriteCategoriesAndItems();
        }
        renderRightPane();
      };
    }
    setupFavoriteEditDialog();
    setupCategoryManageDialog();

    $("#folderSelect").onchange = (e) => {
      state.currentFolderId = parseInt(e.target.value, 10);
      state.selected.clear();
      savePrefs();
      reloadImages();
    };

    $("#orderSelect").onchange = (e) => {
      const [order, dir] = e.target.value.split("|");
      state.order = order;
      state.direction = dir;
      savePrefs();
      reloadImages();
    };

    // サイズ: スライダーと数値入力の双方向同期
    const slider = $("#sizeSlider");
    const numInput = $("#sizeNumber");
    const SIZE_MIN = parseInt(slider.min, 10) || 80;
    const SIZE_MAX = parseInt(slider.max, 10) || 512;

    const applySize = (raw, { commit }) => {
      // 連続変更中は CSS だけ更新、commit=true でサムネ再フェッチ + 保存
      let v = parseInt(raw, 10);
      if (Number.isNaN(v)) return;
      v = Math.max(SIZE_MIN, Math.min(SIZE_MAX, v));
      state.thumbW = v;
      slider.value = v;
      // フォーカス中の number input は触らない (タイプ中の値を奪わない)
      if (document.activeElement !== numInput) numInput.value = v;
      $("#grid").style.setProperty("--thumb-w", `${v}px`);
      document.querySelectorAll(".cell").forEach(c => {
        c.style.setProperty("--thumb-w", `${v}px`);
      });
      if (commit) {
        renderGrid();
        savePrefs();
      }
    };

    slider.oninput = (e) => applySize(e.target.value, { commit: false });
    slider.onchange = (e) => applySize(e.target.value, { commit: true });

    numInput.oninput = (e) => {
      // タイプ途中の不完全値は反映だけ (CSS), commit はしない
      const v = parseInt(e.target.value, 10);
      if (!Number.isNaN(v) && v >= SIZE_MIN && v <= SIZE_MAX) {
        applySize(v, { commit: false });
      }
    };
    // Enter or blur で確定
    numInput.addEventListener("change", (e) => applySize(e.target.value, { commit: true }));
    numInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); numInput.blur(); }
    });

    // タグフィルタ自由入力: Enter or + ボタンで追加
    const tagInput = $("#tagFilterInput");
    const submitFilterTag = () => {
      addFilterTag(tagInput.value);
      tagInput.value = "";
    };
    tagInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); submitFilterTag(); }
    });
    // datalist 候補から選択 (= input イベントの value が候補と完全一致) なら即追加
    tagInput.addEventListener("input", (ev) => {
      const v = ev.target.value;
      if (!v) return;
      const hit = state.tags.some(t => t.name === v);
      if (hit) {
        addFilterTag(v);
        tagInput.value = "";
      }
    });
    $("#tagFilterAdd").onclick = submitFilterTag;

    $("#tagFilterMode").onchange = (e) => {
      state.filterMode = e.target.value;
      savePrefs();
      reloadImages();
    };

    // ---------------- プロンプト検索バーのオートコンプリート (a1111-tagcomplete 風) ----------------
    // 入力中の最後のスペース区切りトークンに対して /api/prompt-tags?q=... を叩き、
    // ↑↓Tab/Enter で挿入、Esc で閉じる。Booru 風の '_' は ' ' と同一視される。
    function setupPromptSuggest() {
      const input = $("#promptSearch");
      const box = $("#promptSuggest");
      if (!input || !box) return;

      let items = [];           // {name, count, source}[]
      let active = -1;          // ハイライト中の index
      let lastQuery = "";       // 直近で問い合わせた最終トークン
      let timer = null;
      let abortCtrl = null;
      let suppressOpen = false; // 値挿入直後のフォーカス継続で誤って再オープンしないため

      // input の先頭から caret 位置までのテキストから「最後のトークン」を取り出す。
      // 区切りはスペース類のみ（既存検索ロジックが空白区切り AND のため）。
      function lastToken() {
        const pos = input.selectionStart ?? input.value.length;
        const left = input.value.slice(0, pos);
        const m = left.match(/(?:^|\s)([^\s]*)$/);
        return { token: m ? m[1] : "", start: m ? pos - m[1].length : pos, end: pos };
      }

      function close() {
        box.hidden = true;
        box.innerHTML = "";
        items = [];
        active = -1;
      }

      function render() {
        if (!items.length) { close(); return; }
        box.innerHTML = "";
        items.forEach((it, i) => {
          const row = document.createElement("div");
          row.className = "tag-suggest-item" + (i === active ? " active" : "");
          if (it.category) row.classList.add("ts-cat-" + it.category);
          if (it.source) row.classList.add("ts-src-" + it.source);
          row.dataset.index = String(i);
          row.setAttribute("role", "option");
          // 表示名は Booru 風の '_' を ' ' に変換した方が読みやすいので置換 (内部値は維持)
          const display = it.name.replace(/_/g, " ");
          let html = `<span class="ts-name">${escapeHtml(display)}</span>`;
          if (it.translation) {
            // ユーザー指定書式: "<タグ名> ; <日本語翻訳>"
            html += `<span class="ts-translation">; ${escapeHtml(it.translation)}</span>`;
          }
          if (it.alias_hit) {
            // エイリアス由来は "(via <alias>)" を添えて分かるようにする
            const aliasDisp = it.alias_hit.replace(/_/g, " ");
            html += `<span class="ts-alias">↳ ${escapeHtml(aliasDisp)}</span>`;
          }
          if (it.category) {
            // a1111-tagcomplete 風: カテゴリラベルを小さく表示 (general 以外)
            if (it.category !== "general") {
              html += `<span class="ts-cat-label">${escapeHtml(it.category)}</span>`;
            }
          }
          html += `<span class="ts-count">${it.count.toLocaleString()}</span>`;
          row.innerHTML = html;
          row.onmousedown = (ev) => {
            // mousedown で挿入 (click だと先に input の blur が走って box が閉じてしまう)
            ev.preventDefault();
            choose(i);
          };
          box.appendChild(row);
        });
        box.hidden = false;
      }

      async function fetchSuggest(q) {
        if (abortCtrl) abortCtrl.abort();
        abortCtrl = new AbortController();
        try {
          const res = await fetch(
            "/api/prompt-tags?q=" + encodeURIComponent(q) + "&limit=20",
            { signal: abortCtrl.signal },
          );
          if (!res.ok) return;
          const data = await res.json();
          // 入力が変わっていたら破棄
          if (lastToken().token !== q) return;
          items = Array.isArray(data) ? data : [];
          active = items.length ? 0 : -1;
          render();
        } catch (e) {
          if (e.name !== "AbortError") {
            // ネットワーク等の失敗時は閉じるだけ（ユーザー操作は阻害しない）
            close();
          }
        }
      }

      function scheduleQuery() {
        if (suppressOpen) { suppressOpen = false; return; }
        const { token } = lastToken();
        // 1 文字以上で起動（0 文字だと候補数が膨大になりノイジー）
        if (!token || token.length < 1) { close(); return; }
        if (token === lastQuery && !box.hidden) return;
        lastQuery = token;
        clearTimeout(timer);
        timer = setTimeout(() => fetchSuggest(token), 120);
      }

      function choose(idx) {
        if (idx < 0 || idx >= items.length) return;
        const it = items[idx];
        const { start, end } = lastToken();
        // 表示同様、内部値も '_' は ' ' に変換して挿入する（DB 側の検索は空白区切り AND のため）
        const insert = it.name.replace(/_/g, " ");
        const before = input.value.slice(0, start);
        const after = input.value.slice(end);
        // a1111-tagcomplete と同じく挿入直後にスペースを 1 個追加して連続入力できるようにする
        const sep = (after.startsWith(" ") || after === "") ? "" : " ";
        const newVal = before + insert + (after === "" ? " " : sep) + after;
        input.value = newVal;
        const caret = (before + insert + (after === "" ? " " : sep)).length;
        input.setSelectionRange(caret, caret);

        suppressOpen = true;
        close();
        // 既存の bindSearchBox の input ハンドラを起こして検索を反映させる
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
      }

      input.addEventListener("input", scheduleQuery);
      input.addEventListener("focus", scheduleQuery);
      input.addEventListener("click", scheduleQuery);
      input.addEventListener("keyup", (ev) => {
        // 矢印・Home/End によるカーソル移動でもトークンが変わるので候補を更新
        if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(ev.key)) {
          scheduleQuery();
        }
      });

      input.addEventListener("keydown", (ev) => {
        if (box.hidden || !items.length) return;
        if (ev.key === "ArrowDown") {
          ev.preventDefault();
          active = (active + 1) % items.length;
          render();
        } else if (ev.key === "ArrowUp") {
          ev.preventDefault();
          active = (active - 1 + items.length) % items.length;
          render();
        } else if (ev.key === "Enter" || ev.key === "Tab") {
          // 候補を採用したいときだけインターセプト。Enter は bindSearchBox の即時検索より優先。
          if (active >= 0) {
            ev.preventDefault();
            ev.stopPropagation();
            choose(active);
          }
        } else if (ev.key === "Escape") {
          // 入力値はクリアせず、候補ドロップダウンだけ閉じる
          ev.preventDefault();
          ev.stopPropagation();
          close();
        }
      }, true); // capture: bindSearchBox の Enter ハンドラより先に走らせる

      // フォーカス外れたら閉じる (mousedown で choose 済みのため安全)
      input.addEventListener("blur", () => {
        setTimeout(close, 100);
      });
    }

    // 検索ボックス共通: 300ms デバウンス + Enter 即時 + Esc クリア
    function bindSearchBox(inputId, clearBtnId, stateKey) {
      const input = $("#" + inputId);
      input.value = state[stateKey];
      let timer = null;
      const trigger = () => {
        const v = input.value.trim();
        if (v === state[stateKey]) return;
        state[stateKey] = v;
        savePrefs();
        reloadImages();
      };
      input.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(trigger, 300);
      });
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          clearTimeout(timer);
          trigger();
        } else if (ev.key === "Escape") {
          input.value = "";
          clearTimeout(timer);
          trigger();
        }
      });
      $("#" + clearBtnId).onclick = () => {
        input.value = "";
        clearTimeout(timer);
        trigger();
        input.focus();
      };
    }
    bindSearchBox("promptSearch", "promptSearchClear", "promptQuery");
    bindSearchBox("memoSearch", "memoSearchClear", "memoQuery");

    setupPromptSuggest();

    setupSplitter();
    setupLightbox();
    setupHelpBanner();
  }

  async function init() {
    bindUi();
    try {
      await reloadFolders();
      await reloadTags();
    } catch (e) {
      setStatus("初期化失敗: " + e.message);
    }
    // Danbooru CSV カテゴリは並べ替え機能でしか使わないので、メイン UI ロードを止めず後追いで取得
    loadPromptDanbooruCategoryMap();
    startEventStream();
    // お気に入りビューが永続化で ON になっていたら、最初に一度ロードして描画
    if (state.favoritesView) {
      try {
        await reloadFavoriteCategoriesAndItems();
        renderRightPane();
      } catch (e) {
        setStatus("お気に入り読み込み失敗: " + e.message);
      }
    }
  }

  // ============================================================
  // お気に入りプロンプト
  // ------------------------------------------------------------
  // - 右ペインを「画像詳細表示」と「お気に入り表示」で排他切替
  // - Positive / Negative はペアで 1 レコードに保存
  // - カテゴリは自由ラベル (新規入力 or 既存選択)
  // ============================================================

  function reflectFavoritesToggle() {
    const btn = $("#btnFavoritesToggle");
    if (!btn) return;
    btn.setAttribute("aria-pressed", state.favoritesView ? "true" : "false");
    btn.classList.toggle("is-active", !!state.favoritesView);
  }

  async function reloadFavoriteCategories() {
    state.favoriteCategories = await api("/api/favorite-prompt-categories");
  }

  async function reloadFavorites() {
    const params = new URLSearchParams();
    params.set("category_id", String(state.favoritesCategoryFilter));
    if (state.favoritesQuery) params.set("q", state.favoritesQuery);
    state.favorites = await api(`/api/favorite-prompts?${params.toString()}`);
  }

  async function reloadFavoriteCategoriesAndItems() {
    await Promise.all([reloadFavoriteCategories(), reloadFavorites()]);
  }

  function renderFavoritesPane(pane) {
    pane.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "fav-pane";

    // ヘッダ: タイトルのみ (各種ボタンは下のコントロール行に集約)
    const header = document.createElement("div");
    header.className = "fav-pane-header";
    header.innerHTML = `<h3>★ お気に入りプロンプト</h3>`;
    wrap.appendChild(header);

    // 各種ボタン行 (検索 / ＋新規 / ⚙カテゴリ管理) — カテゴリタブの「上」に集約
    const ctl = document.createElement("div");
    ctl.className = "fav-ctl-row";
    ctl.innerHTML = `
      <input type="search" class="fav-search" placeholder="検索 (空白区切りで AND)"
             autocomplete="off" />
      <button class="btn-close fav-search-clear" title="検索クリア">×</button>
      <button class="btn-primary fav-new" title="お気に入りを新規作成 (空のまま開始)">＋ 新規</button>
      <button class="btn-sub fav-manage-categories" title="カテゴリの追加・リネーム・削除">⚙ カテゴリ管理</button>
    `;
    const searchInput = ctl.querySelector(".fav-search");
    searchInput.value = state.favoritesQuery || "";
    let searchTimer = null;
    searchInput.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(async () => {
        state.favoritesQuery = searchInput.value.trim();
        savePrefs();
        try {
          await reloadFavorites();
          renderRightPane();
          // 入力中フォーカスを保てるよう再描画後にフォーカスを戻す
          const again = $(".fav-search");
          if (again) {
            again.focus();
            again.setSelectionRange(again.value.length, again.value.length);
          }
        } catch (e) { setStatus("検索失敗: " + e.message); }
      }, 250);
    });
    ctl.querySelector(".fav-search-clear").onclick = () => {
      if (!searchInput.value) return;
      searchInput.value = "";
      state.favoritesQuery = "";
      savePrefs();
      reloadFavorites().then(() => renderRightPane());
    };
    ctl.querySelector(".fav-new").onclick = () => openFavoriteEditDialogNew();
    ctl.querySelector(".fav-manage-categories").onclick = openCategoryManageDialog;
    wrap.appendChild(ctl);

    // カテゴリタブ
    const tabs = document.createElement("div");
    tabs.className = "fav-tabs";
    const tabDefs = [
      { value: "all", label: "すべて" },
      { value: "uncategorized", label: "未分類" },
      ...state.favoriteCategories.map(c => ({
        value: String(c.id), label: c.name, count: c.item_count,
      })),
    ];
    for (const t of tabDefs) {
      const b = document.createElement("button");
      b.className = "fav-tab";
      b.type = "button";
      const cur = String(state.favoritesCategoryFilter);
      if (cur === t.value) b.classList.add("is-active");
      b.textContent = t.count !== undefined ? `${t.label} (${t.count})` : t.label;
      b.dataset.value = t.value;
      b.onclick = async () => {
        // "all" / "uncategorized" は文字列のまま、数値は Number 化
        if (t.value === "all" || t.value === "uncategorized") {
          state.favoritesCategoryFilter = t.value;
        } else {
          state.favoritesCategoryFilter = Number(t.value);
        }
        savePrefs();
        await reloadFavorites();
        renderRightPane();
      };
      tabs.appendChild(b);
    }
    wrap.appendChild(tabs);

    // 一覧
    const list = document.createElement("div");
    list.className = "fav-list";
    if (state.favorites.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "お気に入りはまだありません。"
        + "画像を選んで右ペインの「★ お気に入りに追加」、または上の「＋ 新規」から登録できます。";
      list.appendChild(empty);
    } else {
      for (const f of state.favorites) {
        list.appendChild(buildFavoriteCard(f));
      }
    }
    wrap.appendChild(list);

    pane.appendChild(wrap);
  }

  function buildFavoriteCard(f) {
    const card = document.createElement("div");
    card.className = "fav-card";

    const head = document.createElement("div");
    head.className = "fav-card-head";
    head.innerHTML = `
      <span class="fav-card-name"></span>
      <span class="fav-card-cat"></span>
    `;
    head.querySelector(".fav-card-name").textContent = f.name;
    const catLabel = f.category_id == null ? "(未分類)" : f.category_name || "(カテゴリ不明)";
    head.querySelector(".fav-card-cat").textContent = catLabel;
    card.appendChild(head);

    const meta = (text, className) => {
      const div = document.createElement("div");
      div.className = className;
      div.textContent = text || "";
      return div;
    };

    if (f.positive) {
      const p = meta(f.positive, "fav-card-positive");
      card.appendChild(p);
    }
    if (f.negative) {
      const n = meta(f.negative, "fav-card-negative");
      card.appendChild(n);
    }
    if (f.memo) {
      const m = meta(f.memo, "fav-card-memo");
      card.appendChild(m);
    }

    // ボタン群
    const bar = document.createElement("div");
    bar.className = "fav-card-actions";

    const copyPosBtn = document.createElement("button");
    copyPosBtn.className = "btn-sub";
    copyPosBtn.textContent = "📋 Pos";
    copyPosBtn.title = "Positive をクリップボードにコピー";
    copyPosBtn.disabled = !f.positive;
    copyPosBtn.onclick = async () => {
      await navigator.clipboard.writeText(f.positive || "");
      setStatus(`Positive をコピー: ${f.name}`);
    };
    bar.appendChild(copyPosBtn);

    const copyNegBtn = document.createElement("button");
    copyNegBtn.className = "btn-sub";
    copyNegBtn.textContent = "📋 Neg";
    copyNegBtn.title = "Negative をクリップボードにコピー";
    copyNegBtn.disabled = !f.negative;
    copyNegBtn.onclick = async () => {
      await navigator.clipboard.writeText(f.negative || "");
      setStatus(`Negative をコピー: ${f.name}`);
    };
    bar.appendChild(copyNegBtn);

    const editBtn = document.createElement("button");
    editBtn.className = "btn-sub";
    editBtn.textContent = "✎ 編集";
    editBtn.onclick = () => openFavoriteEditDialogForExisting(f);
    bar.appendChild(editBtn);

    const delBtn = document.createElement("button");
    delBtn.className = "danger";
    delBtn.textContent = "🗑";
    delBtn.title = "削除";
    delBtn.onclick = async () => {
      if (!confirm(`お気に入り「${f.name}」を削除しますか？`)) return;
      try {
        await api(`/api/favorite-prompts/${f.id}`, { method: "DELETE" });
        setStatus(`削除: ${f.name}`);
        await reloadFavoriteCategoriesAndItems();
        renderRightPane();
      } catch (e) {
        alert("削除失敗: " + e.message);
      }
    };
    bar.appendChild(delBtn);

    card.appendChild(bar);
    return card;
  }

  // 編集ダイアログ: 中身を初期化して開く -------------------------------

  function fillCategorySelect(selectEl, currentId) {
    selectEl.innerHTML = "";
    const optNone = document.createElement("option");
    optNone.value = "";
    optNone.textContent = "(未分類)";
    selectEl.appendChild(optNone);
    for (const c of state.favoriteCategories) {
      const o = document.createElement("option");
      o.value = String(c.id);
      o.textContent = c.name;
      selectEl.appendChild(o);
    }
    selectEl.value = currentId == null ? "" : String(currentId);
  }

  async function openFavoriteEditDialogForCurrent() {
    // 現在画像 (state.detail) からプリセット
    if (!state.detail) {
      alert("画像が選択されていません");
      return;
    }
    if (state.favoriteCategories.length === 0) {
      try { await reloadFavoriteCategories(); } catch {}
    }
    const dlg = $("#favoriteEditDialog");
    state.favoriteEditTarget = null;  // 新規モード
    $("#favoriteEditTitle").textContent = "★ お気に入りに追加";
    $("#favoriteEditName").value = state.detail.filename
      ? state.detail.filename.replace(/\.[^.]+$/, "")
      : "";
    fillCategorySelect($("#favoriteEditCategory"), null);
    $("#favoriteEditCategoryNew").value = "";
    $("#favoriteEditPositive").value = state.detail.positive_prompt || "";
    $("#favoriteEditNegative").value = state.detail.negative_prompt || "";
    $("#favoriteEditMemo").value = "";
    dlg.dataset.sourceImageId = String(state.detail.id || "");
    resetFavEditPromptButtons();
    dlg.showModal();
    setTimeout(() => $("#favoriteEditName").focus(), 0);
  }

  async function openFavoriteEditDialogNew() {
    if (state.favoriteCategories.length === 0) {
      try { await reloadFavoriteCategories(); } catch {}
    }
    const dlg = $("#favoriteEditDialog");
    state.favoriteEditTarget = null;
    $("#favoriteEditTitle").textContent = "★ お気に入りを新規作成";
    $("#favoriteEditName").value = "";
    // 直近選択中のカテゴリタブを初期値にする (整数なら)
    const initCat = typeof state.favoritesCategoryFilter === "number"
      ? state.favoritesCategoryFilter : null;
    fillCategorySelect($("#favoriteEditCategory"), initCat);
    $("#favoriteEditCategoryNew").value = "";
    $("#favoriteEditPositive").value = "";
    $("#favoriteEditNegative").value = "";
    $("#favoriteEditMemo").value = "";
    dlg.dataset.sourceImageId = "";
    resetFavEditPromptButtons();
    dlg.showModal();
    setTimeout(() => $("#favoriteEditName").focus(), 0);
  }

  async function openFavoriteEditDialogForExisting(fav) {
    if (state.favoriteCategories.length === 0) {
      try { await reloadFavoriteCategories(); } catch {}
    }
    const dlg = $("#favoriteEditDialog");
    state.favoriteEditTarget = fav.id;
    $("#favoriteEditTitle").textContent = `✎ 編集: ${fav.name}`;
    $("#favoriteEditName").value = fav.name;
    fillCategorySelect($("#favoriteEditCategory"), fav.category_id);
    $("#favoriteEditCategoryNew").value = "";
    $("#favoriteEditPositive").value = fav.positive || "";
    $("#favoriteEditNegative").value = fav.negative || "";
    $("#favoriteEditMemo").value = fav.memo || "";
    dlg.dataset.sourceImageId = fav.source_image_id ? String(fav.source_image_id) : "";
    resetFavEditPromptButtons();
    dlg.showModal();
    setTimeout(() => $("#favoriteEditName").focus(), 0);
  }

  // ダイアログ内 textarea 用の {コピー/並べ替え/戻る} ボタン制御。
  // promptBlockBody (read-only pre) と違い、ここは編集可能 textarea が対象。
  // 並び替え時に直前の value を backup し、「戻る」で復元する。
  // ダイアログ再オープン時は resetButtonStates() で undo 不可に戻す。
  const _favEditUndoBackup = { positive: null, negative: null };

  function attachFavEditPromptButtons(kind /* 'positive' | 'negative' */, label) {
    const ta = $(kind === "positive" ? "#favoriteEditPositive" : "#favoriteEditNegative");
    const copyBtn = $(kind === "positive" ? "#favEditPosCopy" : "#favEditNegCopy");
    const sortBtn = $(kind === "positive" ? "#favEditPosSort" : "#favEditNegSort");
    const undoBtn = $(kind === "positive" ? "#favEditPosUndo" : "#favEditNegUndo");
    if (!ta || !copyBtn || !sortBtn || !undoBtn) return;

    copyBtn.onclick = async () => {
      try {
        await navigator.clipboard.writeText(ta.value || "");
        setStatus(`${label} をクリップボードにコピーしました`);
      } catch (e) {
        setStatus("コピー失敗: " + e.message);
      }
    };
    sortBtn.onclick = () => {
      const cur = ta.value || "";
      const sorted = sortPromptText(cur);
      if (!sorted || sorted === cur) return;
      _favEditUndoBackup[kind] = cur;
      ta.value = sorted;
      undoBtn.disabled = false;
      sortBtn.disabled = true;
    };
    undoBtn.onclick = () => {
      const backup = _favEditUndoBackup[kind];
      if (backup == null) return;
      ta.value = backup;
      _favEditUndoBackup[kind] = null;
      undoBtn.disabled = true;
      sortBtn.disabled = false;
    };
  }

  function resetFavEditPromptButtons() {
    _favEditUndoBackup.positive = null;
    _favEditUndoBackup.negative = null;
    for (const id of ["#favEditPosUndo", "#favEditNegUndo"]) {
      const b = $(id); if (b) b.disabled = true;
    }
    for (const id of ["#favEditPosSort", "#favEditNegSort"]) {
      const b = $(id); if (b) b.disabled = false;
    }
  }

  function setupFavoriteEditDialog() {
    const dlg = $("#favoriteEditDialog");
    if (!dlg) return;
    attachFavEditPromptButtons("positive", "Positive Prompt");
    attachFavEditPromptButtons("negative", "Negative Prompt");
    const submit = $("#btnFavoriteEditSubmit");
    submit.addEventListener("click", async (ev) => {
      ev.preventDefault();
      const name = $("#favoriteEditName").value.trim();
      if (!name) {
        alert("名前は必須です");
        return;
      }
      const positive = $("#favoriteEditPositive").value;
      const negative = $("#favoriteEditNegative").value;
      const memo = $("#favoriteEditMemo").value;

      // カテゴリ: 新規欄が優先 (空白除去後に値があれば新規作成)
      const newCatName = $("#favoriteEditCategoryNew").value.trim();
      let categoryId = null;
      if (newCatName) {
        try {
          const created = await api("/api/favorite-prompt-categories", {
            method: "POST",
            body: JSON.stringify({ name: newCatName }),
          });
          categoryId = created.id;
          // 候補リストを最新化
          await reloadFavoriteCategories();
        } catch (e) {
          alert("カテゴリ作成失敗: " + e.message);
          return;
        }
      } else {
        const sel = $("#favoriteEditCategory").value;
        categoryId = sel === "" ? null : Number(sel);
      }

      const sourceImageIdStr = dlg.dataset.sourceImageId || "";
      const sourceImageId = sourceImageIdStr ? Number(sourceImageIdStr) : null;

      try {
        if (state.favoriteEditTarget == null) {
          // 新規
          await api("/api/favorite-prompts", {
            method: "POST",
            body: JSON.stringify({
              name,
              category_id: categoryId,
              positive,
              negative,
              memo,
              source_image_id: sourceImageId,
            }),
          });
          setStatus(`お気に入り追加: ${name}`);
        } else {
          // 編集
          await api(`/api/favorite-prompts/${state.favoriteEditTarget}`, {
            method: "PATCH",
            body: JSON.stringify({
              name,
              category_id: categoryId,  // null は未分類化として送る
              positive,
              negative,
              memo,
            }),
          });
          setStatus(`お気に入り更新: ${name}`);
        }
        dlg.close();
        await reloadFavoriteCategoriesAndItems();
        // お気に入りビュー以外でも、追加直後に内部状態だけ更新しておく
        if (state.favoritesView) renderRightPane();
      } catch (e) {
        alert("保存失敗: " + e.message);
      }
    });
  }

  // カテゴリ管理ダイアログ -------------------------------------------------

  function setupCategoryManageDialog() {
    const dlg = $("#categoryManageDialog");
    if (!dlg) return;
    $("#btnCategoryAdd").onclick = async () => {
      const name = $("#categoryAddName").value.trim();
      if (!name) {
        alert("カテゴリ名を入力してください");
        return;
      }
      try {
        await api("/api/favorite-prompt-categories", {
          method: "POST",
          body: JSON.stringify({ name }),
        });
        $("#categoryAddName").value = "";
        await reloadFavoriteCategoriesAndItems();
        renderCategoryManageList();
      } catch (e) {
        alert("追加失敗: " + e.message);
      }
    };
  }

  async function openCategoryManageDialog() {
    const dlg = $("#categoryManageDialog");
    if (!dlg) return;
    try { await reloadFavoriteCategories(); } catch {}
    renderCategoryManageList();
    $("#categoryAddName").value = "";
    dlg.showModal();
    // 閉じたら一覧を再描画 (件数表示などが変わる可能性があるため)
    const onClose = () => {
      dlg.removeEventListener("close", onClose);
      reloadFavoriteCategoriesAndItems().then(() => {
        if (state.favoritesView) renderRightPane();
      });
    };
    dlg.addEventListener("close", onClose);
  }

  function renderCategoryManageList() {
    const list = $("#categoryManageList");
    if (!list) return;
    list.innerHTML = "";
    if (state.favoriteCategories.length === 0) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "カテゴリはまだありません。";
      list.appendChild(li);
      return;
    }
    for (const c of state.favoriteCategories) {
      const li = document.createElement("li");
      li.className = "category-manage-item";

      const nameInput = document.createElement("input");
      nameInput.type = "text";
      nameInput.value = c.name;
      nameInput.className = "category-manage-name";
      nameInput.maxLength = 60;
      li.appendChild(nameInput);

      const cnt = document.createElement("span");
      cnt.className = "category-manage-count";
      cnt.textContent = `${c.item_count} 件`;
      li.appendChild(cnt);

      const saveBtn = document.createElement("button");
      saveBtn.type = "button";
      saveBtn.className = "btn-primary";
      saveBtn.textContent = "保存";
      saveBtn.onclick = async () => {
        const v = nameInput.value.trim();
        if (!v || v === c.name) return;
        try {
          await api(`/api/favorite-prompt-categories/${c.id}`, {
            method: "PATCH",
            body: JSON.stringify({ name: v }),
          });
          setStatus(`カテゴリ更新: ${c.name} → ${v}`);
          await reloadFavoriteCategoriesAndItems();
          renderCategoryManageList();
        } catch (e) {
          alert("リネーム失敗: " + e.message);
        }
      };
      li.appendChild(saveBtn);

      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "danger";
      delBtn.textContent = "削除";
      delBtn.title = "カテゴリを削除（中のお気に入りは未分類になる）";
      delBtn.onclick = async () => {
        const msg = c.item_count > 0
          ? `カテゴリ「${c.name}」を削除しますか？配下の ${c.item_count} 件は「未分類」に戻ります。`
          : `カテゴリ「${c.name}」を削除しますか？`;
        if (!confirm(msg)) return;
        try {
          await api(`/api/favorite-prompt-categories/${c.id}`, { method: "DELETE" });
          // 削除したカテゴリを選択中だった場合は "all" に戻す
          if (state.favoritesCategoryFilter === c.id) {
            state.favoritesCategoryFilter = "all";
            savePrefs();
          }
          await reloadFavoriteCategoriesAndItems();
          renderCategoryManageList();
        } catch (e) {
          alert("削除失敗: " + e.message);
        }
      };
      li.appendChild(delBtn);

      list.appendChild(li);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
