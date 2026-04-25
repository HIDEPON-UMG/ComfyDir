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
  };

  // 右ペインのデフォルト並び順 (ユーザー要望: プロンプト最上部、画像とファイル名は同一セクション)
  function sanitizePaneOrder(arr) {
    const known = new Set(["preview", "tags", "memo", "positive", "negative"]);
    const defaults = ["positive", "tags", "memo", "preview", "negative"];
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

  async function removeCurrentFolder() {
    if (state.currentFolderId == null) return;
    const f = state.folders.find(x => x.id === state.currentFolderId);
    if (!confirm(`登録解除しますか?\n${f?.path}\n\n(画像のタグ情報も DB から消えますが、ファイル自体は削除されません)`)) return;
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
    // ファイル名(改名フォーム) → 画像 → メタ の順で 1 セクションに統合
    preview: (d) => {
      const frag = document.createDocumentFragment();

      // 1) ファイル名 + 改名フォーム (最上部)
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

      // 2) プレビュー画像
      const wrap = document.createElement("div");
      wrap.className = "preview-wrap";
      const img = document.createElement("img");
      img.src = `/api/images/${d.id}/preview?v=${d.sha1.slice(0, 8)}`;
      img.title = "クリックで大画面表示";
      img.addEventListener("click", () => {
        openLightbox(img.src, `${d.filename}  (${d.width}×${d.height})`);
      });
      wrap.appendChild(img);
      frag.appendChild(wrap);

      // 3) メタ情報
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
      row.innerHTML = `<input type="text" class="tag-add-input" placeholder="タグを追加 (Enter)" list="tagDatalist" /><button class="btn-tag-add btn-primary">+</button>`;
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
    tags:     { title: "タグ" },
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

  // セクション本体だけを返す (タイトルは buildSection 側で h3 を付ける)
  function promptBlockBody(title, text) {
    const wrap = document.createElement("div");

    const pre = document.createElement("pre");
    pre.textContent = text || "(なし)";
    // Negative はデフォルト縦幅を Positive の約半分にする (Positive 260px / Negative 130px)
    if (/negative/i.test(title)) {
      pre.classList.add("prompt-negative");
    }
    wrap.appendChild(pre);

    if (text) {
      const btn = document.createElement("button");
      btn.className = "copy-btn btn-sub";
      btn.textContent = "コピー";
      btn.onclick = async () => {
        await navigator.clipboard.writeText(text);
        setStatus(`${title} をクリップボードにコピーしました`);
      };
      wrap.appendChild(btn);
    }
    return wrap;
  }

  function renderBulkPane(pane) {
    pane.innerHTML = `
      <h3>${state.selected.size} 枚 選択中</h3>
      <div class="row">
        <input type="text" id="bulkAddInput" placeholder="追加するタグ (カンマ区切り可)" list="tagDatalist" />
        <button id="bulkAdd" class="btn-primary">+ 付与</button>
      </div>
      <div class="row">
        <input type="text" id="bulkRemoveInput" placeholder="外すタグ" list="tagDatalist" />
        <button id="bulkRemove" class="danger">- 解除</button>
      </div>
      <h3>既存タグ</h3>
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
      c.title = "クリックで選択中の画像にタグ付与";
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
      setStatus(`${state.selected.size} 枚にタグ付与`);
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
      setStatus(`${state.selected.size} 枚からタグ削除`);
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
  function openLightbox(src, caption) {
    const lb = $("#lightbox");
    $("#lightboxImg").src = src;
    $("#lightboxCaption").textContent = caption || "";
    if (typeof lb.showModal === "function") {
      lb.showModal();
    } else {
      // 古いブラウザ向けフォールバック
      lb.setAttribute("open", "");
    }
  }

  function setupLightbox() {
    const lb = $("#lightbox");
    const lbImg = $("#lightboxImg");
    const closeIt = () => {
      if (typeof lb.close === "function") lb.close();
      else lb.removeAttribute("open");
    };
    lbImg.onclick = closeIt;
    $("#lightboxClose").onclick = closeIt;
    // 背景 (画像以外の領域) クリックで閉じる
    lb.addEventListener("click", (ev) => {
      if (ev.target === lb) closeIt();
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
    $("#btnRescan").onclick = rescanCurrentFolder;
    $("#btnMove").onclick = openMoveDialog;

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
    startEventStream();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
