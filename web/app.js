/* app/web/app.js — v7 frontend */

const API = "http://127.0.0.1:5000";

// ── DOM refs ──────────────────────────────────────────────────
const urlInput        = document.getElementById("url-input");
const urlHint         = document.getElementById("url-hint");
const qtyRow          = document.getElementById("qty-row");
const qtyInput        = document.getElementById("qty-input");
const qtyAllToggle    = document.getElementById("qty-all-toggle");
const qtyHint         = document.getElementById("qty-hint");
const scopeRow        = document.getElementById("scope-row");
const scopeCountHint  = document.getElementById("scope-count-hint");
const qualitySelect   = document.getElementById("quality-select");
const downloadBtn     = document.getElementById("download-btn");
const downloadBtnLbl  = document.getElementById("download-btn-label");
const scanSpinner     = document.getElementById("scan-spinner");
const queueList       = document.getElementById("queue-list");
const historyList     = document.getElementById("history-list");
const jobCount        = document.getElementById("job-count");
const historyCount    = document.getElementById("history-count");
const serverPill      = document.getElementById("server-pill");
const cookieBar       = document.getElementById("cookie-bar");
const cookieText      = document.getElementById("cookie-text");
const cookieIcon      = document.getElementById("cookie-icon");
const cookieClearBtn  = document.getElementById("cookie-clear-btn");
const groupToggle        = document.getElementById("group-view-toggle");
const historyGroupToggle = document.getElementById("history-group-toggle");
const themeBtn        = document.getElementById("theme-btn");

const statNums = {
  queued:      document.getElementById("stat-queued-n"),
  downloading: document.getElementById("stat-downloading-n"),
  completed:   document.getElementById("stat-completed-n"),
  failed:      document.getElementById("stat-failed-n"),
  speed:       document.getElementById("stat-speed-n"),
};
const statSpeedChip = document.getElementById("stat-speed");

// ── Speed / ETA formatting helpers ──────────────────────────────
function fmtSpeed(bytesPerSec) {
  if (!bytesPerSec || bytesPerSec <= 0) return "";
  const kb = bytesPerSec / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB/s`;
  return `${(kb / 1024).toFixed(1)} MB/s`;
}
function fmtEta(seconds) {
  if (!seconds || seconds <= 0) return "";
  if (seconds < 60) return `${Math.round(seconds)}s left`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m < 60 ? `${m}m ${s}s left` : `${Math.floor(m / 60)}h ${m % 60}m left`;
}

// ── State ─────────────────────────────────────────────────────
let activeScanId   = null;
let scanPollTimer  = null;
let expandedGroups = new Set(); // tracks which groups user has manually opened

// ── Theme ─────────────────────────────────────────────────────
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  themeBtn.textContent = t === "dark" ? "◑" : "◐";
  localStorage.setItem("v7_theme", t);
}
applyTheme(localStorage.getItem("v7_theme") || "dark");
themeBtn.addEventListener("click", () =>
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark")
);

// ── Tabs ──────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "history")  fetchHistory();
    if (tab.dataset.tab === "settings") fetchSettings();
    if (tab.dataset.tab === "approve")  fetchPreviews();
  });
});

// ── Quality options — mirrors _VALID_QUALITIES in routes.py ────
const QUALITY_OPTIONS = {
  MP4: [
    { value: "best",  label: "Best available" },
    { value: "2160p", label: "2160p (4K)" },
    { value: "1440p", label: "1440p (2K)" },
    { value: "1080p", label: "1080p" },
    { value: "720p",  label: "720p" },
    { value: "480p",  label: "480p" },
    { value: "360p",  label: "360p" },
    { value: "240p",  label: "240p" },
  ],
  MP3: [
    { value: "192k", label: "192 kbps (standard)" },
    { value: "320k", label: "320 kbps (high)" },
    { value: "256k", label: "256 kbps" },
    { value: "128k", label: "128 kbps" },
    { value: "96k",  label: "96 kbps (small)" },
  ],
  "3GP": [
    { value: "320x240", label: "320×240 (standard)" },
    { value: "352x288", label: "352×288 (higher)" },
    { value: "176x144", label: "176×144 (smallest)" },
  ],
};

function populateQualityOptions() {
  const fmt = document.querySelector("input[name='format']:checked")?.value || "MP4";
  const opts = QUALITY_OPTIONS[fmt] || QUALITY_OPTIONS.MP4;
  qualitySelect.innerHTML = opts.map(o => `<option value="${o.value}">${o.label}</option>`).join("");
}
document.querySelectorAll("input[name='format']").forEach(radio =>
  radio.addEventListener("change", populateQualityOptions)
);
populateQualityOptions(); // initial fill on page load

// ── URL form ──────────────────────────────────────────────────
// Quantity row behavior differs by type:
//   search            -> hard-capped at 250, "All" doesn't apply (endless feed)
//   playlist / channel -> "All" means everything, up to a 999 safety cap
function configureQtyRow(type) {
  qtyAllToggle.checked = false;
  qtyAllToggle.parentElement.classList.remove("checked");
  qtyInput.disabled = false;
  if (type === "search") {
    qtyInput.min = 1;
    qtyInput.max = 250;
    if (parseInt(qtyInput.value || "0") > 250) qtyInput.value = 250;
    qtyAllToggle.parentElement.classList.add("hidden"); // no "All" concept for search
    qtyHint.textContent = "Search results — max 250";
  } else {
    qtyInput.min = 1;
    qtyInput.removeAttribute("max");
    qtyAllToggle.parentElement.classList.remove("hidden");
    qtyHint.textContent = "\"All\" fetches everything in the playlist/channel, up to 999";
  }
}

qtyAllToggle.addEventListener("change", () => {
  qtyInput.disabled = qtyAllToggle.checked;
  qtyAllToggle.parentElement.classList.toggle("checked", qtyAllToggle.checked);
});

function detectUrlType(url) {
  if (!url) return null;
  if (!/youtube\.com|youtu\.be/i.test(url)) return "invalid";
  if (/youtube\.com\/results/.test(url))             return "search";
  if ((/watch\?v=/.test(url) || /youtu\.be\//.test(url)) && /list=/.test(url)) return "video+list";
  if (/watch\?v=|youtu\.be\//.test(url))             return "video";
  if (/\/shorts\//.test(url))                         return "short";
  if (/playlist.*list=/.test(url))                    return "playlist";
  if (/youtube\.com\/@[^/]+(\/shorts|\/videos)?\/?\s*$/.test(url)) return "channel";
  return "unknown";
}

function updateFormUI() {
  const url  = urlInput.value.trim();
  const type = detectUrlType(url);
  qtyRow.classList.add("hidden");
  scopeRow.classList.add("hidden");
  scopeCountHint.textContent = "";
  clearTimeout(scopeCountTimer);
  urlHint.className = "url-hint";
  urlHint.textContent = "";
  if (!url) { downloadBtn.disabled = true; return; }
  if (type === "invalid") {
    urlHint.textContent = "That doesn't look like a YouTube URL.";
    urlHint.classList.add("error");
    downloadBtn.disabled = true;
    return;
  }
  downloadBtn.disabled = false;
  if (type === "search" || type === "playlist" || type === "channel") {
    qtyRow.classList.remove("hidden");
    configureQtyRow(type);
  }
  if (type === "video+list") {
    scopeRow.classList.remove("hidden");
    scopeCountHint.textContent = "Checking playlist size…";
    scopeCountTimer = setTimeout(() => fetchScopeCount(url), 400); // debounce typing
    updateScopeQtyVisibility();
  }
}
urlInput.addEventListener("input", updateFormUI);

// video+list URLs only show the quantity selector once "Whole playlist"
// is actually chosen — and configureQtyRow() resets it fresh every time,
// so a stale disabled input / stuck "All" checkbox from a previous
// playlist/search scan (before the input got hidden) can't leak through.
function updateScopeQtyVisibility() {
  const wholePlaylist = document.querySelector("input[name='playlist']:checked")?.value === "true";
  if (wholePlaylist) {
    qtyRow.classList.remove("hidden");
    configureQtyRow("playlist");
  } else {
    qtyRow.classList.add("hidden");
  }
}
document.querySelectorAll("input[name='playlist']").forEach(radio => {
  radio.addEventListener("change", updateScopeQtyVisibility);
});

let scopeCountTimer = null;
async function fetchScopeCount(url) {
  try {
    const res  = await fetch(`${API}/api/scan/count?url=${encodeURIComponent(url)}&playlist=true`);
    const data = await res.json();
    if (urlInput.value.trim() !== url) return; // URL changed while this was in flight — stale, ignore
    if (data.ok && data.count) {
      scopeCountHint.textContent = `This playlist has ${data.count} video${data.count !== 1 ? "s" : ""}.`;
    } else {
      scopeCountHint.textContent = "";
    }
  } catch {
    scopeCountHint.textContent = "";
  }
}

// ── Scan submission ───────────────────────────────────────────
// Group-type URLs (playlist / search / channel / "whole playlist" scope)
// go through the preview -> popup -> confirm flow. Single video / short
// keep using the old direct scan -> auto-queue flow, unchanged.
function isGroupSubmission() {
  const type = detectUrlType(urlInput.value.trim());
  if (type === "search" || type === "playlist" || type === "channel") return true;
  if (type === "video+list") {
    return document.querySelector("input[name='playlist']:checked")?.value === "true";
  }
  return false;
}

downloadBtn.addEventListener("click", async () => {
  const url = urlInput.value.trim();
  if (!url) return;
  downloadBtn.disabled = true;
  downloadBtnLbl.classList.add("hidden");
  scanSpinner.classList.remove("hidden");

  const format  = document.querySelector("input[name='format']:checked")?.value || "MP4";
  const quality = qualitySelect.value;

  if (isGroupSubmission()) {
    const body = {
      url,
      format, quality,
      quantity: qtyAllToggle.checked ? "all" : Math.max(1, parseInt(qtyInput.value || "25", 10)),
      playlist: document.querySelector("input[name='playlist']:checked")?.value === "true",
      source: "web",
    };
    try {
      const res  = await fetch(`${API}/api/scan/preview`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "scan rejected");
      activePreviewScanId = data.scan_id;
      urlHint.textContent = "Scanning… check the Approve tab, videos will appear as they're found.";
      urlHint.className   = "url-hint";
      urlInput.value = "";
      updateFormUI();
      refreshApproveBadge();
      if (document.querySelector('.tab[data-tab="approve"]').classList.contains("active")) fetchPreviews();
      pollPreview();
    } catch (e) {
      urlHint.textContent = `❌ ${e.message}`;
      urlHint.classList.add("error");
      resetDownloadBtn();
    }
    return;
  }

  const body = {
    url,
    format, quality,
    quantity: qtyAllToggle.checked ? "all" : Math.max(1, parseInt(qtyInput.value || "25", 10)),
    playlist: document.querySelector("input[name='playlist']:checked")?.value === "true",
  };
  try {
    const res  = await fetch(`${API}/api/scan`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "scan rejected");
    activeScanId = data.scan_id;
    urlHint.textContent = "Scanning URL…";
    urlHint.className   = "url-hint";
    pollScan();
  } catch (e) {
    urlHint.textContent = `❌ ${e.message}`;
    urlHint.classList.add("error");
    resetDownloadBtn();
  }
});

function resetDownloadBtn() {
  downloadBtn.disabled = false;
  downloadBtnLbl.classList.remove("hidden");
  scanSpinner.classList.add("hidden");
}

function pollScan() {
  clearTimeout(scanPollTimer);
  if (!activeScanId) return;
  scanPollTimer = setTimeout(async () => {
    try {
      const res  = await fetch(`${API}/api/scan/status/${activeScanId}`);
      const data = await res.json();
      if (data.status === "pending") { pollScan(); return; }
      if (data.status === "done") {
        const r = data.result;
        urlHint.textContent = `✓ Added ${r.video_count} video${r.video_count !== 1 ? "s" : ""} to queue.`;
        urlHint.className   = "url-hint";
        urlInput.value      = "";
        updateFormUI();
        fetchQueue();
      } else {
        urlHint.textContent = `❌ ${data.error || "Scan failed."}`;
        urlHint.classList.add("error");
      }
    } catch {
      urlHint.textContent = "❌ Could not reach server.";
      urlHint.classList.add("error");
    }
    activeScanId = null;
    resetDownloadBtn();
  }, 800);
}

// ── Preview scanning (playlist / search / channel -> Approve tab) ──
let activePreviewScanId = null;
let previewPollTimer    = null;

function pollPreview() {
  clearTimeout(previewPollTimer);
  if (!activePreviewScanId) return;
  previewPollTimer = setTimeout(async () => {
    try {
      const res  = await fetch(`${API}/api/scan/status/${activePreviewScanId}`);
      const data = await res.json();
      if (data.status === "pending") {
        const p = data.progress;
        urlHint.textContent = (p && p.total)
          ? `Scanning… ${p.fetched}/${p.total}`
          : "Scanning…";
        urlHint.className = "url-hint";
        pollPreview();
        return;
      }
      if (data.status === "done") {
        const n = data.result.video_count;
        urlHint.textContent = `✓ Found ${n} video${n !== 1 ? "s" : ""} — review and confirm in the Approve tab.`;
        urlHint.className   = "url-hint";
        urlInput.value = "";
        updateFormUI();
        refreshApproveBadge();
        if (document.querySelector('.tab[data-tab="approve"]').classList.contains("active")) fetchPreviews();
      } else {
        urlHint.textContent = `❌ ${data.error || "Scan failed."}`;
        urlHint.classList.add("error");
      }
    } catch {
      urlHint.textContent = "❌ Could not reach server.";
      urlHint.classList.add("error");
    }
    activePreviewScanId = null;
    resetDownloadBtn();
  }, 800);
}

function fmtDuration(sec) {
  if (!sec && sec !== 0) return "";
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h > 0 ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}` : `${m}:${String(s).padStart(2,"0")}`;
}

// ── Approve tab (accordion of pending previews) ─────────────────
const approveList  = document.getElementById("approve-list");
const approveCount = document.getElementById("approve-count");
const approveBadge = document.getElementById("approve-badge");

// expandedPreview[id] holds the working selection state once a card is
// opened: { entries, selectionOrder, usePrefix }. Not fetched again
// unless the accordion is re-opened after being closed.
let previewSummaries  = [];
let expandedPreviewId = null;
let expandedState     = null;

async function refreshApproveBadge() {
  try {
    const res  = await fetch(`${API}/api/previews`);
    const data = await res.json();
    if (!data.ok) return;
    const n = data.previews.length;
    if (approveBadge) approveBadge.textContent = n > 0 ? String(n) : "";
    if (approveBadge) approveBadge.classList.toggle("hidden", n === 0);
  } catch {}
}

async function fetchPreviews() {
  try {
    const res  = await fetch(`${API}/api/previews`);
    const data = await res.json();
    if (!data.ok) return;
    previewSummaries = data.previews;
    renderApproveList();
    _syncPreviewAutoRefresh();
  } catch {}
}

// Keeps the Approve tab live while any preview is still streaming
// entries in — polls the summary list (for the growing count on
// collapsed cards) and, if one happens to be expanded, that card's full
// entry list too, until its scan_status flips to 'complete'.
let previewAutoRefreshTimer = null;

function _syncPreviewAutoRefresh() {
  const anyScanning = previewSummaries.some(p => p.scan_status === "scanning");
  if (anyScanning && !previewAutoRefreshTimer) {
    previewAutoRefreshTimer = setInterval(async () => {
      await fetchPreviews();
      if (expandedPreviewId) {
        const p = previewSummaries.find(s => s.id === expandedPreviewId);
        if (p && p.scan_status === "scanning") await _refreshExpandedEntries(expandedPreviewId);
      }
    }, 2000);
  } else if (!anyScanning && previewAutoRefreshTimer) {
    clearInterval(previewAutoRefreshTimer);
    previewAutoRefreshTimer = null;
  }
}

async function _refreshExpandedEntries(id) {
  try {
    const res  = await fetch(`${API}/api/previews/${id}`);
    const data = await res.json();
    if (!data.ok || !expandedState) return;
    const knownIds = new Set(expandedState.entries.map(e => e.video_id));
    const freshEntries = data.preview.entries;
    // Newly-arrived videos default to selected (appended to the end of
    // the selection order), same as the initial load — existing
    // selections/deselections the person already made are untouched.
    for (const e of freshEntries) {
      if (!knownIds.has(e.video_id)) {
        expandedState.selectionOrder.push(e.video_id);
      }
    }
    expandedState.entries = freshEntries;
    renderExpandedBody(id);
  } catch {}
}

function renderApproveList() {
  approveCount.textContent = previewSummaries.length
    ? `${previewSummaries.length} pending`
    : "Nothing pending";

  if (!previewSummaries.length) {
    approveList.innerHTML = `<div class="empty-state"><div class="empty-icon">✅</div><p>Nothing to approve.<br>Playlist / search / channel scans land here — from this app or the browser extension.</p></div>`;
    return;
  }

  approveList.innerHTML = previewSummaries.map(p => {
    const isOpen = p.id === expandedPreviewId;
    const scanning = p.scan_status === "scanning";
    return `
      <div class="approve-card ${isOpen ? "open" : ""}" data-preview-id="${p.id}">
        <div class="approve-card-header">
          <div class="approve-card-title">
            <span class="approve-chevron">${isOpen ? "▾" : "▸"}</span>
            <span>${esc(p.group_name)}</span>
            <span class="badge badge-neutral">${p.type}</span>
            ${p.source === "extension" ? `<span class="badge badge-neutral">extension</span>` : ""}
            ${scanning ? `<span class="badge badge-scanning">⏳ scanning</span>` : ""}
          </div>
          <div class="approve-card-meta">
            <span>${p.video_count} video${p.video_count !== 1 ? "s" : ""}${scanning ? " so far…" : ""} · ${esc(p.format)}/${esc(p.quality)}</span>
            <button class="btn-tiny approve-delete-btn" data-preview-id="${p.id}" title="Delete this pending scan">Delete</button>
          </div>
        </div>
        <div class="approve-card-body ${isOpen ? "" : "hidden"}" id="approve-body-${p.id}"></div>
      </div>`;
  }).join("");

  approveList.querySelectorAll(".approve-card-header").forEach(header => {
    header.addEventListener("click", (ev) => {
      if (ev.target.closest(".approve-delete-btn")) return; // don't toggle when deleting
      const id = header.closest(".approve-card").dataset.previewId;
      toggleApproveCard(id);
    });
  });
  approveList.querySelectorAll(".approve-delete-btn").forEach(btn => {
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const id = btn.dataset.previewId;
      btn.disabled = true;
      try {
        await fetch(`${API}/api/previews/${id}`, { method: "DELETE" });
        if (expandedPreviewId === id) { expandedPreviewId = null; expandedState = null; }
        await fetchPreviews();
        refreshApproveBadge();
      } catch {
        btn.disabled = false;
      }
    });
  });
}

async function toggleApproveCard(id) {
  if (expandedPreviewId === id) {
    expandedPreviewId = null;
    expandedState = null;
    renderApproveList();
    return;
  }
  expandedPreviewId = id;
  expandedState = null;
  renderApproveList(); // shows the chevron flipped + empty body while loading

  try {
    const res  = await fetch(`${API}/api/previews/${id}`);
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Could not load preview");
    expandedState = {
      entries: data.preview.entries,
      selectionOrder: data.preview.entries.map(e => e.video_id), // default: all selected, natural order
      usePrefix: false,
    };
    renderExpandedBody(id);
  } catch (e) {
    const body = document.getElementById(`approve-body-${id}`);
    if (body) body.innerHTML = `<div class="approve-body-error">❌ ${esc(e.message)}</div>`;
  }
}

function renderExpandedBody(id) {
  const body = document.getElementById(`approve-body-${id}`);
  if (!body || !expandedState) return;
  const { entries, selectionOrder, usePrefix } = expandedState;
  const orderIndex = new Map(selectionOrder.map((vid, i) => [vid, i + 1]));

  const itemsHtml = entries.map(e => {
    const selected = orderIndex.has(e.video_id);
    const prefix   = selected ? orderIndex.get(e.video_id) : null;
    const thumb = e.video_id
      ? `<img class="batch-item-thumb" src="${API}/thumbnails/${e.video_id}.jpg" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'batch-item-thumb-placeholder',textContent:'▶'}))">`
      : `<div class="batch-item-thumb-placeholder">▶</div>`;
    return `
      <label class="batch-item ${selected ? "" : "deselected"}" data-video-id="${esc(e.video_id || "")}">
        <input type="checkbox" class="batch-item-check" ${selected ? "checked" : ""}>
        <span class="batch-item-prefix">${prefix !== null ? prefix : ""}</span>
        ${thumb}
        <div class="batch-item-info">
          <div class="batch-item-title" title="${esc(e.title || "")}">${esc(e.title || "(untitled)")}</div>
          <div class="batch-item-meta">${esc(e.uploader || "")}${e.duration ? " · " + fmtDuration(e.duration) : ""}</div>
        </div>
      </label>`;
  }).join("");

  body.innerHTML = `
    <div class="batch-modal-toolbar">
      <button class="btn-text approve-select-all">Select all</button>
      <button class="btn-text approve-deselect-all">Deselect all</button>
      <span class="batch-selected-count">${selectionOrder.length} selected</span>
      <label class="checkbox-row batch-prefix-toggle">
        <input type="checkbox" class="approve-prefix-toggle" ${usePrefix ? "checked" : ""}>
        <span>Add number prefix to filenames</span>
      </label>
    </div>
    <div class="batch-modal-list approve-item-list">${itemsHtml}</div>
    <div class="modal-footer">
      <span class="modal-footer-hint approve-confirm-hint"></span>
      <div class="modal-footer-actions">
        <button class="btn-primary approve-confirm-btn" ${selectionOrder.length ? "" : "disabled"}>
          <span class="approve-confirm-label">Download selected</span>
          <span class="scan-spinner hidden approve-confirm-spinner">starting…</span>
        </button>
      </div>
    </div>`;

  body.querySelectorAll(".batch-item-check").forEach(cb => {
    cb.addEventListener("change", (ev) => {
      const vid = ev.target.closest(".batch-item").dataset.videoId;
      if (ev.target.checked) {
        if (!expandedState.selectionOrder.includes(vid)) expandedState.selectionOrder.push(vid);
      } else {
        expandedState.selectionOrder = expandedState.selectionOrder.filter(v => v !== vid);
      }
      updateSelectionUI(id); // renumber in place — no full re-render, no scroll jump
    });
  });
  body.querySelector(".approve-select-all").addEventListener("click", () => {
    expandedState.selectionOrder = expandedState.entries.map(e => e.video_id);
    updateSelectionUI(id);
  });
  body.querySelector(".approve-deselect-all").addEventListener("click", () => {
    expandedState.selectionOrder = [];
    updateSelectionUI(id);
  });
  body.querySelector(".approve-prefix-toggle").addEventListener("change", (ev) => {
    expandedState.usePrefix = ev.target.checked;
  });
  body.querySelector(".approve-confirm-btn").addEventListener("click", () => confirmApprovePreview(id));
}

// Updates selection state (checkbox checked-ness, prefix numbers,
// deselected styling, count, confirm button) on the EXISTING DOM nodes
// instead of rebuilding the list — rebuilding via innerHTML was
// resetting scroll position to the top on every single click.
function updateSelectionUI(id) {
  const body = document.getElementById(`approve-body-${id}`);
  if (!body || !expandedState) return;
  const { selectionOrder } = expandedState;
  const orderIndex = new Map(selectionOrder.map((vid, i) => [vid, i + 1]));

  body.querySelectorAll(".batch-item").forEach(label => {
    const vid = label.dataset.videoId;
    const selected = orderIndex.has(vid);
    label.classList.toggle("deselected", !selected);
    const cb = label.querySelector(".batch-item-check");
    if (cb) cb.checked = selected;
    const prefixEl = label.querySelector(".batch-item-prefix");
    if (prefixEl) prefixEl.textContent = selected ? orderIndex.get(vid) : "";
  });

  const countEl = body.querySelector(".batch-selected-count");
  if (countEl) countEl.textContent = `${selectionOrder.length} selected`;

  const confirmBtn = body.querySelector(".approve-confirm-btn");
  if (confirmBtn) confirmBtn.disabled = selectionOrder.length === 0;
}

async function confirmApprovePreview(id) {
  if (!expandedState || !expandedState.selectionOrder.length) return;
  const body = document.getElementById(`approve-body-${id}`);
  const btn      = body.querySelector(".approve-confirm-btn");
  const label    = body.querySelector(".approve-confirm-label");
  const spinner  = body.querySelector(".approve-confirm-spinner");
  const hint     = body.querySelector(".approve-confirm-hint");
  btn.disabled = true;
  label.classList.add("hidden");
  spinner.classList.remove("hidden");
  hint.textContent = "";

  const { entries, selectionOrder, usePrefix } = expandedState;
  const byId = new Map(entries.map(e => [e.video_id, e]));
  const selected = selectionOrder.map((vid, idx) => {
    const e = byId.get(vid);
    return {
      video_id: e.video_id,
      url: e.url,
      title: e.title,
      thumbnail_path: e.thumbnail_path,
      duration: e.duration,
      uploader: e.uploader,
      playlist_index: e.playlist_index,
      prefix: usePrefix ? idx + 1 : null,
    };
  });

  try {
    const res = await fetch(`${API}/api/previews/${id}/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries: selected }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Could not queue selection");

    expandedPreviewId = null;
    expandedState = null;
    await fetchPreviews();
    refreshApproveBadge();
    fetchQueue();
    document.querySelector('.tab[data-tab="queue"]').click();
  } catch (e) {
    hint.textContent = `❌ ${e.message}`;
    btn.disabled = false;
    label.classList.remove("hidden");
    spinner.classList.add("hidden");
  }
}

// ── Helpers ───────────────────────────────────────────────────
function esc(s) {
  return (s||"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function badgeClass(status) {
  return [`queued`,`downloading`,`converting`,`paused`,`completed`,`failed`,`cancelled`,`skipped`]
    .includes(status) ? `badge-${status}` : "badge-queued";
}
function formatDuration(secs) {
  if (!secs) return "";
  const h = Math.floor(secs/3600), m = Math.floor((secs%3600)/60), s = secs%60;
  if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
  return `${m}:${String(s).padStart(2,"0")}`;
}

function actionButtons(job) {
  const s = job.status, id = job.id;
  const btns = [];
  if (["queued","downloading","converting"].includes(s))
    btns.push(`<button class="job-btn" data-action="pause" data-id="${id}">⏸ Pause</button>`);
  if (s === "paused")
    btns.push(`<button class="job-btn success" data-action="resume" data-id="${id}">▶ Resume</button>`);
  if (["failed","cancelled"].includes(s))
    btns.push(`<button class="job-btn" data-action="retry" data-id="${id}">↻ Retry</button>`);
  if (["queued","paused","failed"].includes(s))
    btns.push(`<button class="job-btn" data-action="skip" data-id="${id}">⤼ Skip</button>`);
  if (["queued","downloading","converting","paused"].includes(s))
    btns.push(`<button class="job-btn danger" data-action="cancel" data-id="${id}">✕ Cancel</button>`);
  btns.push(`<button class="job-btn danger" data-action="delete" data-id="${id}">🗑</button>`);
  return btns.join("");
}

function _jobMetaHTML(job, pct) {
  const uploader = job.uploader ? `· ${esc(job.uploader)}` : "";
  const dur      = job.duration ? formatDuration(job.duration) : "";
  const cookieBadge = job.used_cookies ? `<span title="Used cookies">🔐</span>` : "";
  const retryBadge  = job.retry_count > 0 ? `<span>retry #${job.retry_count}</span>` : "";
  const idxBadge    = job.playlist_index != null ? `<span>#${job.playlist_index}</span>` : "";
  const speedBadge = job.status === "downloading" && job.speed_bytes_sec
    ? `<span class="job-speed">↓ ${fmtSpeed(job.speed_bytes_sec)}${job.eta_seconds ? " · " + fmtEta(job.eta_seconds) : ""}</span>`
    : "";
  const convSpeedBadge = job.status === "converting" && job.conversion_speed_x
    ? `<span class="job-speed">⚙ ${job.conversion_speed_x.toFixed(1)}x</span>`
    : "";
  return [
    job.format + (job.quality && job.quality !== "best" ? ` · ${job.quality}` : ""),
    uploader, dur, idxBadge, retryBadge, cookieBadge, speedBadge, convSpeedBadge,
    (pct > 0 && pct < 100) ? `${pct}%` : ""
  ].filter(Boolean).map(x => `<span>${x}</span>`).join("");
}

// Updates each existing job card's dynamic bits (progress bar, speed/eta
// text, status badge, action buttons) directly on the DOM nodes already
// there, instead of rebuilding the list — used when the job set itself
// hasn't changed between polls, only percentages/speeds ticking.
// Returns false (caller should fall back to a full rebuild) if any
// expected card is missing from the DOM.
function patchJobCards(jobs) {
  for (const job of jobs) {
    const card = queueList.querySelector(`.job-card[data-id="${job.id}"]`);
    if (!card) return false;

    if (card.dataset.status !== job.status) card.dataset.status = job.status;

    const isConverting = job.status === "converting";
    const pct = job.status === "completed" ? 100
      : isConverting ? Math.round(job.convert_percent || 0)
      : Math.round(job.progress_percent || 0);

    const track = card.querySelector(".progress-track");
    if (track) {
      track.classList.toggle("converting", isConverting);
      const fill = track.querySelector(".progress-fill");
      if (fill) fill.style.width = pct + "%";
    }

    const badge = card.querySelector(".job-header-right .badge");
    if (badge) {
      badge.textContent = job.status;
      badge.className = `badge ${badgeClass(job.status)}`;
    }

    const metaEl = card.querySelector(".job-meta");
    if (metaEl) metaEl.innerHTML = _jobMetaHTML(job, pct);

    const actionsEl = card.querySelector(".job-actions");
    if (actionsEl) {
      const newActionsHtml = actionButtons(job);
      if (actionsEl.innerHTML !== newActionsHtml) {
        actionsEl.innerHTML = newActionsHtml;
        attachActions(actionsEl); // re-bind listeners for the buttons we just replaced
      }
    }

    const existingError = card.querySelector(".job-error");
    if (job.error_message) {
      const errorHtml = esc(job.error_message.slice(0, 200));
      if (existingError) {
        if (existingError.innerHTML !== errorHtml) existingError.innerHTML = errorHtml;
      } else {
        card.querySelector(".job-card-body").insertAdjacentHTML("beforeend", `<div class="job-error">${errorHtml}</div>`);
      }
    } else if (existingError) {
      existingError.remove();
    }
  }
  return true;
}

function jobCardHTML(job) {
  const isConverting = job.status === "converting";
  const pct = job.status === "completed" ? 100
    : isConverting ? Math.round(job.convert_percent || 0)
    : Math.round(job.progress_percent || 0);
  const title    = job.original_title || job.video_id || job.url;
  const error    = job.error_message ? `<div class="job-error">${esc(job.error_message.slice(0,200))}</div>` : "";
  const thumb    = job.video_id
    ? `<img class="job-thumb" src="${API}/thumbnails/${job.video_id}.jpg" loading="lazy" onerror="this.style.display='none'">`
    : `<div class="job-thumb-placeholder"></div>`;
  return `
    <div class="job-card" data-status="${job.status}" data-id="${job.id}">
      <div class="job-card-inner">
        ${thumb}
        <div class="job-card-body">
          <div class="job-header">
            <div class="job-title" title="${esc(title)}">${esc(title)}</div>
            <div class="job-header-right">
              <span class="badge ${badgeClass(job.status)}">${job.status}</span>
            </div>
          </div>
          <div class="job-meta-row">
            <div class="job-meta">${_jobMetaHTML(job, pct)}</div>
            <div class="job-actions">${actionButtons(job)}</div>
          </div>
          <div class="progress-track ${isConverting ? "converting" : ""}"><div class="progress-fill" style="width:${pct}%"></div></div>
          ${error}
        </div>
      </div>
    </div>`;
}

// ── Global bulk actions ───────────────────────────────────────
async function bulkAction(action, jobIds) {
  await Promise.all(jobIds.map(id =>
    fetch(`${API}/api/queue/${id}`, {
      method: "PATCH",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({action}),
    }).catch(() => {})
  ));
  fetchQueue();
}

function globalActionBar(jobs) {
  const pauseable  = jobs.filter(j => ["queued","downloading","converting"].includes(j.status)).map(j=>j.id);
  const resumeable = jobs.filter(j => j.status === "paused").map(j=>j.id);
  const retryable  = jobs.filter(j => ["failed","cancelled"].includes(j.status)).map(j=>j.id);
  if (!pauseable.length && !resumeable.length && !retryable.length) return "";
  const btns = [];
  if (pauseable.length)  btns.push(`<button class="bulk-btn" data-bulk="pause"  data-ids='${JSON.stringify(pauseable)}'>⏸ Pause All (${pauseable.length})</button>`);
  if (resumeable.length) btns.push(`<button class="bulk-btn" data-bulk="resume" data-ids='${JSON.stringify(resumeable)}'>▶ Resume All (${resumeable.length})</button>`);
  if (retryable.length)  btns.push(`<button class="bulk-btn" data-bulk="retry"  data-ids='${JSON.stringify(retryable)}'>↻ Retry All (${retryable.length})</button>`);
  return `<div class="global-actions">${btns.join("")}</div>`;
}

function groupActionBar(groupId, jobs) {
  const pauseable  = jobs.filter(j => ["queued","downloading","converting"].includes(j.status)).map(j=>j.id);
  const resumeable = jobs.filter(j => j.status === "paused").map(j=>j.id);
  const retryable  = jobs.filter(j => ["failed","cancelled"].includes(j.status)).map(j=>j.id);
  const deleteable = jobs.map(j=>j.id);
  const btns = [];
  if (pauseable.length)  btns.push(`<button class="job-btn" data-bulk="pause"  data-ids='${JSON.stringify(pauseable)}'>⏸ Pause All</button>`);
  if (resumeable.length) btns.push(`<button class="job-btn success" data-bulk="resume" data-ids='${JSON.stringify(resumeable)}'>▶ Resume All</button>`);
  if (retryable.length)  btns.push(`<button class="job-btn" data-bulk="retry"  data-ids='${JSON.stringify(retryable)}'>↻ Retry All</button>`);
  btns.push(`<button class="job-btn danger" data-bulk="delete" data-ids='${JSON.stringify(deleteable)}'>🗑 Delete All</button>`);
  return `<div class="job-actions group-bulk-actions">${btns.join("")}</div>`;
}

// ── Queue rendering ───────────────────────────────────────────
let lastQueueHTML = "";
let lastQueueJobIds = "";

async function fetchQueue() {
  try {
    const res  = await fetch(`${API}/api/queue`);
    if (!res.ok) throw new Error();
    const data = await res.json();
    const jobs  = data.jobs  || [];
    const stats = data.stats || {};

    statNums.queued.textContent      = stats.queued || 0;
    statNums.downloading.textContent = (stats.downloading||0) + (stats.converting||0);
    statNums.completed.textContent   = stats.completed || 0;
    statNums.failed.textContent      = stats.failed || 0;

    const totalSpeed = stats.total_speed_bytes_sec || 0;
    if (totalSpeed > 0) {
      statSpeedChip.classList.remove("hidden");
      statNums.speed.textContent = fmtSpeed(totalSpeed);
    } else {
      statSpeedChip.classList.add("hidden");
    }

    serverPill.textContent = "● online";
    serverPill.className   = "server-pill";

    jobCount.textContent = `${jobs.length} job${jobs.length !== 1 ? "s" : ""}`;

    if (!jobs.length) {
      const html = `<div class="empty-state"><div class="empty-icon">✓</div><p>Queue is empty.<br>All done! Check History for completed downloads.</p></div>`;
      if (lastQueueHTML !== html) { queueList.innerHTML = html; lastQueueHTML = html; }
      lastQueueJobIds = "";
      return;
    }

    // Cheap path: same set of job ids as last render (in the same order)
    // -> just patch each card's dynamic bits in place (progress, speed,
    // badge, action buttons) instead of rebuilding the whole list.
    // Rebuilding via innerHTML on every 2s poll — even though nothing
    // structural changed, just percentages ticking — was resetting
    // scroll position and losing hover/focus state on every tick.
    const currentIds = jobs.map(j => j.id).join(",");
    if (currentIds === lastQueueJobIds && patchJobCards(jobs)) {
      return;
    }
    lastQueueJobIds = currentIds;

    const html = groupToggle.dataset.active === "true" ? renderGroupView(jobs) : renderFlatView(jobs);
    if (html !== lastQueueHTML) {
      queueList.innerHTML = html;
      lastQueueHTML = html;
      attachActions(queueList);
      // restore expand state — only groups the user has explicitly opened
      queueList.querySelectorAll(".group-header").forEach(h => {
        const gid = h.closest(".group-card").dataset.gid;
        if (expandedGroups.has(gid)) h.closest(".group-card").classList.add("expanded");
        h.addEventListener("click", () => {
          const card = h.closest(".group-card");
          card.classList.toggle("expanded");
          if (card.classList.contains("expanded")) expandedGroups.add(gid);
          else expandedGroups.delete(gid);
        });
      });
    }
  } catch {
    serverPill.textContent = "● offline";
    serverPill.className   = "server-pill offline";
  }
}

function renderFlatView(jobs) {
  return globalActionBar(jobs) + jobs.map(jobCardHTML).join("");
}

function renderGroupView(jobs) {
  const grouped   = {};
  const ungrouped = [];
  jobs.forEach(j => {
    if (j.group_id) {
      if (!grouped[j.group_id]) grouped[j.group_id] = [];
      grouped[j.group_id].push(j);
    } else {
      ungrouped.push(j);
    }
  });

  const parts = [globalActionBar(jobs)];
  ungrouped.forEach(j => parts.push(jobCardHTML(j)));

  Object.entries(grouped).forEach(([gid, gjobs]) => {
    const completed  = gjobs.filter(j => j.status === "completed").length;
    const pct        = gjobs.length ? Math.round((completed / gjobs.length) * 100) : 0;
    const groupName  = gjobs[0]?.uploader || `Group ${gid}`;
    const isExpanded = expandedGroups.has(gid); // only expand if user clicked

    parts.push(`
      <div class="group-card ${isExpanded ? "expanded" : ""}" data-gid="${gid}">
        <div class="group-header">
          <span class="group-toggle">▶</span>
          <span class="group-name">${esc(groupName)}</span>
          <span class="group-progress-text">${completed}/${gjobs.length}</span>
        </div>
        <div class="group-track"><div class="group-fill" style="width:${pct}%"></div></div>
        <div class="group-jobs">
          ${groupActionBar(gid, gjobs)}
          ${gjobs.map(jobCardHTML).join("")}
        </div>
      </div>`);
  });
  return parts.join("");
}

// ── Action handlers ───────────────────────────────────────────
function attachActions(container) {
  // single job actions
  container.querySelectorAll("[data-action]").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      handleJobAction(btn.dataset.action, btn.dataset.id);
    });
  });
  // bulk actions
  container.querySelectorAll("[data-bulk]").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      const action = btn.dataset.bulk;
      const ids    = JSON.parse(btn.dataset.ids || "[]");
      if (action === "delete") {
        if (!confirm(`Delete ${ids.length} job(s)? Files on disk will NOT be removed.`)) return;
        Promise.all(ids.map(id => fetch(`${API}/api/queue/${id}`, { method:"DELETE" }).catch(()=>{})))
          .then(() => fetchQueue());
      } else {
        bulkAction(action, ids);
      }
    });
  });
}

async function handleJobAction(action, jobId) {
  if (action === "delete") {
    const withFile = confirm("Delete the downloaded file from disk too?\nOK = yes, Cancel = remove from list only.");
    await fetch(`${API}/api/queue/${jobId}?delete_file=${withFile}`, { method:"DELETE" });
    fetchQueue();
    return;
  }
  await fetch(`${API}/api/queue/${jobId}`, {
    method:"PATCH",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({action}),
  }).catch(() => {});
  fetchQueue();
}

// Toggle buttons (replace checkbox .checked with dataset.active)
groupToggle.addEventListener("click", () => {
  const active = groupToggle.dataset.active === "true";
  groupToggle.dataset.active = (!active).toString();
  groupToggle.classList.toggle("active", !active);
  lastQueueHTML = "";
  lastQueueJobIds = "";
  fetchQueue();
});

historyGroupToggle.addEventListener("click", () => {
  const active = historyGroupToggle.dataset.active === "true";
  historyGroupToggle.dataset.active = (!active).toString();
  historyGroupToggle.classList.toggle("active", !active);
  fetchHistory();
});

// ── History ───────────────────────────────────────────────────
async function fetchHistory() {
  try {
    const res   = await fetch(`${API}/api/history`);
    const data  = await res.json();
    const items = data.history || [];
    historyCount.textContent = `${items.length} item${items.length !== 1 ? "s" : ""}`;
    if (!items.length) {
      historyList.innerHTML = `<div class="empty-state"><div class="empty-icon">📋</div><p>No completed downloads yet.</p></div>`;
      return;
    }
    const useGroup = historyGroupToggle.dataset.active === "true";
    historyList.innerHTML = useGroup ? renderGroupView(items) : items.map(jobCardHTML).join("");
    attachActions(historyList);
    if (useGroup) {
      historyList.querySelectorAll(".group-header").forEach(h => {
        const gid = h.closest(".group-card").dataset.gid;
        if (expandedGroups.has("h_" + gid)) h.closest(".group-card").classList.add("expanded");
        h.addEventListener("click", () => {
          const card = h.closest(".group-card");
          card.classList.toggle("expanded");
          if (card.classList.contains("expanded")) expandedGroups.add("h_" + gid);
          else expandedGroups.delete("h_" + gid);
        });
      });
    }
  } catch {}
}

// ── Settings ──────────────────────────────────────────────────
async function fetchSettings() {
  try {
    const res  = await fetch(`${API}/api/settings`);
    const data = await res.json();
    const s    = data.settings || {};
    document.getElementById("setting-concurrent").value = s.concurrent_downloads || "3";
    document.getElementById("setting-format").value     = s.default_format || "MP4";
    await fetchCookieSettings();
  } catch {}
}

document.getElementById("save-settings-btn").addEventListener("click", async () => {
  const feedback = document.getElementById("save-feedback");
  try {
    await fetch(`${API}/api/settings`, {
      method:"PUT",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({
        concurrent_downloads: document.getElementById("setting-concurrent").value,
        default_format:       document.getElementById("setting-format").value,
      }),
    });
    feedback.classList.remove("hidden");
    setTimeout(() => feedback.classList.add("hidden"), 2000);
  } catch {}
});

async function fetchCookieSettings() {
  const block = document.getElementById("cookie-settings-status");
  try {
    const res  = await fetch(`${API}/api/cookies/status`);
    const data = await res.json();
    block.textContent = data.synced
      ? `Synced — ${data.cookie_count} cookies (${new Date(data.synced_at + "Z").toLocaleString()})`
      : "Not synced";
  } catch { block.textContent = "Could not check."; }
}

document.getElementById("clear-cookies-btn").addEventListener("click", async () => {
  if (!confirm("Remove synced cookies?")) return;
  await fetch(`${API}/api/cookies`, {method:"DELETE"});
  fetchCookieSettings();
  fetchCookies();
});

// ── Cookie banner ─────────────────────────────────────────────
async function fetchCookies() {
  try {
    const res  = await fetch(`${API}/api/cookies/status`);
    const data = await res.json();
    if (data.synced) {
      cookieBar.classList.add("synced");
      cookieIcon.textContent = "🔐";
      cookieText.textContent = `Cookies synced (${data.cookie_count})`;
      cookieClearBtn.style.display = "block";
    } else {
      cookieBar.classList.remove("synced");
      cookieIcon.textContent = "🔓";
      cookieText.textContent = "Cookies not synced";
      cookieClearBtn.style.display = "none";
    }
  } catch {}
}
cookieClearBtn.addEventListener("click", async () => {
  await fetch(`${API}/api/cookies`, {method:"DELETE"});
  fetchCookies();
});

// ── Local Batch Converter ────────────────────────────────────
const convertPathInput     = document.getElementById("convert-path-input");
const convertTargetSelect  = document.getElementById("convert-target-select");
const convertQualitySelect = document.getElementById("convert-quality-select");
const convertRecursive     = document.getElementById("convert-recursive");
const convertBtn           = document.getElementById("convert-btn");
const convertBtnLabel      = document.getElementById("convert-btn-label");
const convertSpinner       = document.getElementById("convert-spinner");
const convertHint          = document.getElementById("convert-hint");
const convertCount         = document.getElementById("convert-count");
const convertList          = document.getElementById("convert-list");

let activeBatches   = [];   // [{ id, label, jobs, done }]
let convertPollTimer = null;

function populateConvertQualityOptions() {
  const target = convertTargetSelect.value; // "MP3" or "3GP"
  const opts = QUALITY_OPTIONS[target] || QUALITY_OPTIONS.MP3;
  convertQualitySelect.innerHTML = opts.map(o => `<option value="${o.value}">${o.label}</option>`).join("");
}
convertTargetSelect.addEventListener("change", populateConvertQualityOptions);
populateConvertQualityOptions();

function batchStats(jobs) {
  const done      = jobs.filter(j => j.status === "completed").length;
  const failed    = jobs.filter(j => j.status === "failed").length;
  const skipped   = jobs.filter(j => j.status === "skipped").length;
  const paused    = jobs.filter(j => j.status === "paused").length;
  const remaining = jobs.filter(j => j.status === "queued" || j.status === "converting" || j.status === "paused").length;
  return { done, failed, skipped, paused, remaining };
}

function convertActionButtons(job) {
  const s = job.status;
  const id = job.id;
  const btns = [];
  if (s === "queued" || s === "converting")
    btns.push(`<button class="job-btn" data-conv-action="pause" data-conv-id="${id}">⏸ Pause</button>`);
  if (s === "paused")
    btns.push(`<button class="job-btn success" data-conv-action="resume" data-conv-id="${id}">▶ Resume</button>`);
  if (["queued", "converting", "paused"].includes(s))
    btns.push(`<button class="job-btn" data-conv-action="skip" data-conv-id="${id}">⤼ Skip</button>`);
  btns.push(`<button class="job-btn danger" data-conv-action="delete" data-conv-id="${id}">🗑</button>`);
  return btns.join("");
}

function renderConvertJobs() {
  if (!activeBatches.length) {
    convertList.innerHTML = `<div class="empty-state"><div class="empty-icon">🔄</div><p>No conversions yet.<br>Use the "Batch convert" form on the left.</p></div>`;
    convertCount.textContent = "No batch running";
    return;
  }

  const runningCount = activeBatches.filter(b => !b.done).length;
  convertCount.textContent = runningCount > 0
    ? `${runningCount} batch${runningCount > 1 ? "es" : ""} converting`
    : `${activeBatches.length} batch${activeBatches.length > 1 ? "es" : ""} finished`;

  convertList.innerHTML = activeBatches.map(batch => {
    const { done, failed, skipped, remaining } = batchStats(batch.jobs);
    const summary = remaining > 0
      ? `Converting… ${done + failed + skipped}/${batch.jobs.length} done`
      : `${done} converted · ${skipped} skipped · ${failed} failed`;

    const jobsHtml = batch.jobs.map(j => {
      const convSpeed = j.status === "converting" && j.conversion_speed_x
        ? `<span class="job-speed">⚙ ${Number(j.conversion_speed_x).toFixed(1)}x</span>` : "";
      return `
      <div class="job-card" data-status="${j.status}">
        <div class="job-card-inner">
          <div class="job-card-body">
            <div class="job-header">
              <div class="job-title" title="${esc(j.source_filename)}">${esc(j.source_filename)}</div>
              <div class="job-header-right"><span class="badge ${badgeClass(j.status)}">${j.status}</span></div>
            </div>
            <div class="job-meta-row">
              <div class="job-meta"><span>${j.target_format}</span><span>${j.quality}</span>${convSpeed}</div>
              <div class="job-actions">${convertActionButtons(j)}</div>
            </div>
            <div class="progress-track"><div class="progress-fill" style="width:${j.progress_percent || 0}%"></div></div>
            ${j.status === "failed" && j.error_message ? `<div class="job-error">${esc(j.error_message)}</div>` : ""}
          </div>
        </div>
      </div>`;
    }).join("");

    return `
      <div class="convert-batch-group" data-batch-id="${batch.id}">
        <div class="convert-batch-header">
          <div class="convert-batch-label">${esc(batch.label)}</div>
          <div class="convert-batch-summary">
            <span>${summary}</span>
            ${batch.done ? `<button class="btn-tiny convert-batch-clear" data-batch-id="${batch.id}">Clear</button>` : ""}
          </div>
        </div>
        <div class="convert-batch-jobs">${jobsHtml}</div>
      </div>`;
  }).join("");

  convertList.querySelectorAll(".convert-batch-clear").forEach(btn => {
    btn.addEventListener("click", () => {
      activeBatches = activeBatches.filter(b => b.id !== btn.dataset.batchId);
      renderConvertJobs();
    });
  });

  convertList.querySelectorAll("[data-conv-action]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const action = btn.dataset.convAction;
      const jobId  = btn.dataset.convId;
      if (action === "delete") {
        if (!confirm("Delete this conversion job? Any partial output will be removed.")) return;
        await fetch(`${API}/api/local-convert/jobs/${jobId}`, { method: "DELETE" }).catch(() => {});
      } else {
        await fetch(`${API}/api/local-convert/jobs/${jobId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action }),
        }).catch(() => {});
      }
      // Resuming a batch whose workers had all exited needs polling
      // restarted, since pollAllBatches stops once everything looked done.
      if (!convertPollTimer) convertPollTimer = setInterval(pollAllBatches, 1500);
      pollAllBatches();
    });
  });
}

async function pollAllBatches() {
  const running = activeBatches.filter(b => !b.done);
  if (!running.length) {
    if (convertPollTimer) { clearInterval(convertPollTimer); convertPollTimer = null; }
    return;
  }

  await Promise.all(running.map(async batch => {
    try {
      const res  = await fetch(`${API}/api/local-convert/jobs?batch_id=${batch.id}`);
      const data = await res.json();
      if (!data.ok) return;
      batch.jobs = data.jobs;
      const { remaining } = batchStats(batch.jobs);
      if (remaining === 0) batch.done = true;
    } catch {}
  }));

  renderConvertJobs();

  if (activeBatches.every(b => b.done) && convertPollTimer) {
    clearInterval(convertPollTimer);
    convertPollTimer = null;
  }
}

convertBtn.addEventListener("click", async () => {
  const path = convertPathInput.value.trim();
  if (!path) {
    convertHint.textContent = "Enter a folder path first.";
    convertHint.classList.add("error");
    return;
  }
  convertHint.textContent = "";
  convertHint.classList.remove("error");
  convertBtn.disabled = true;
  convertBtnLabel.classList.add("hidden");
  convertSpinner.classList.remove("hidden");

  try {
    const target = convertTargetSelect.value;
    const res = await fetch(`${API}/api/local-convert`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path,
        target_format: target,
        quality: convertQualitySelect.value,
        recursive: convertRecursive.checked,
      }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Could not start conversion");

    const folderLabel = path.split(/[\\/]/).filter(Boolean).pop() || path;
    const timeLabel = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    activeBatches.push({
      id: data.batch_id,
      label: `${target} · ${folderLabel} · ${timeLabel}`,
      jobs: [],
      done: false,
    });

    convertHint.textContent = `Found ${data.total} file(s) — ${data.queued} queued, ${data.skipped} already done.`;

    // switch to the Convert tab so the person sees progress immediately
    document.querySelector('.tab[data-tab="convert"]').click();

    renderConvertJobs();
    if (!convertPollTimer) {
      pollAllBatches();
      convertPollTimer = setInterval(pollAllBatches, 1500);
    }
  } catch (e) {
    convertHint.textContent = `❌ ${e.message}`;
    convertHint.classList.add("error");
  }
  convertBtn.disabled = false;
  convertBtnLabel.classList.remove("hidden");
  convertSpinner.classList.add("hidden");
});

// ── Boot + polling ────────────────────────────────────────────
fetchQueue();
fetchCookies();
refreshApproveBadge();
setInterval(fetchQueue, 2000);
setInterval(fetchCookies, 8000);
setInterval(refreshApproveBadge, 5000);
