// approve.js — the Approve tab: polling a group/preview scan, the
// accordion list of pending previews, per-video selection state, and
// confirming a batch into the download queue.
import { API, urlHint, urlInput } from "./dom.js";
import { esc, fmtDuration } from "./utils.js";
import { fetchQueue } from "./queue.js";
import { resetDownloadBtn, updateFormUI } from "./scan-form.js";
import { ICON } from "./icons.js";

// ── Preview scanning (playlist / search / channel -> Approve tab) ──
let activePreviewScanId = null; // module-private; scan-form.js uses startPreviewScan() below to set it

// Called by scan-form.js when a new group/preview scan is kicked off.
// activePreviewScanId is module-private (see above) so scan-form.js
// can't just import-and-reassign it directly — this setter is the
// clean interface across that module boundary.
export function startPreviewScan(scanId) {
  activePreviewScanId = scanId;
  pollPreview();
}
export let previewPollTimer    = null;

export function pollPreview() {
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
        urlHint.innerHTML = data.result.stopped_early
          ? `${ICON.warning} Found ${n} video${n !== 1 ? "s" : ""} — stopped early because this looked much bigger than expected. Review in the Approve tab.`
          : `${ICON.check} Found ${n} video${n !== 1 ? "s" : ""} — review and confirm in the Approve tab.`;
        urlHint.className   = "url-hint";
        urlInput.value = "";
        updateFormUI();
        refreshApproveBadge();
        if (document.querySelector('.tab[data-tab="approve"]').classList.contains("active")) fetchPreviews();
      } else {
        urlHint.innerHTML = `${ICON.error} ${data.error || "Scan failed."}`;
        urlHint.classList.add("error");
      }
    } catch {
      urlHint.innerHTML = `${ICON.error} Could not reach server.`;
      urlHint.classList.add("error");
    }
    activePreviewScanId = null;
    resetDownloadBtn();
  }, 800);
}

// ── Approve tab (accordion of pending previews) ─────────────────
export const approveList  = document.getElementById("approve-list");
export const approveCount = document.getElementById("approve-count");
export const approveBadge = document.getElementById("approve-badge");

// expandedPreview[id] holds the working selection state once a card is
// opened: { entries, selectionOrder, usePrefix }. Not fetched again
// unless the accordion is re-opened after being closed.
export let previewSummaries  = [];
export let expandedPreviewId = null;
export let expandedState     = null;

export async function refreshApproveBadge() {
  try {
    const res  = await fetch(`${API}/api/previews`);
    const data = await res.json();
    if (!data.ok) return;
    const n = data.previews.length;
    if (approveBadge) approveBadge.textContent = n > 0 ? String(n) : "";
    if (approveBadge) approveBadge.classList.toggle("hidden", n === 0);
  } catch {}
}

export async function fetchPreviews() {
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
export let previewAutoRefreshTimer = null;

export function _syncPreviewAutoRefresh() {
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

export async function _refreshExpandedEntries(id) {
  try {
    const res  = await fetch(`${API}/api/previews/${id}`);
    const data = await res.json();
    if (!data.ok || !expandedState) return;
    const knownIds = new Set(expandedState.entries.map(e => e.video_id));
    const freshEntries = data.preview.entries;
    const newlyArrived = freshEntries.filter(e => !knownIds.has(e.video_id));
    if (!newlyArrived.length) return; // nothing new — don't touch the DOM at all

    // Newly-arrived videos default to selected (appended to the end of
    // the selection order), same as the initial load — existing
    // selections/deselections the person already made are untouched.
    for (const e of newlyArrived) {
      if (!e.already_downloaded) expandedState.selectionOrder.push(e.video_id);
    }
    expandedState.entries = freshEntries;
    // Append-only: build DOM nodes for the new videos and add them to the
    // end of the list. Existing nodes are never touched/replaced, so the
    // list's scroll position stays exactly where the person left it.
    appendApproveItems(id, newlyArrived);
  } catch {}
}

// Builds one card's DOM node (header + empty body shell) and wires its
// own header-click / delete-button listeners. Only called once per
// preview id — after that, the same node is patched in place, never
// replaced, so an open card's contents (and its scroll position) are
// never touched by a background poll.
export function _buildApproveCard(p) {
  const wrapper = document.createElement("div");
  wrapper.innerHTML = `
    <div class="approve-card" data-preview-id="${p.id}">
      <div class="approve-card-header">
        <div class="approve-card-title"></div>
        <div class="approve-card-meta">
          <span class="approve-card-meta-text"></span>
          <button class="btn-danger-ghost approve-delete-btn" data-preview-id="${p.id}" title="Delete this pending scan">${ICON.trash} Delete</button>
        </div>
      </div>
      <div class="approve-card-body hidden" id="approve-body-${p.id}"></div>
    </div>`;
  const card = wrapper.firstElementChild;

  card.querySelector(".approve-card-header").addEventListener("click", (ev) => {
    if (ev.target.closest(".approve-delete-btn")) return; // don't toggle when deleting
    toggleApproveCard(String(p.id));
  });
  card.querySelector(".approve-delete-btn").addEventListener("click", async (ev) => {
    ev.stopPropagation();
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      await fetch(`${API}/api/previews/${p.id}`, { method: "DELETE" });
      if (expandedPreviewId === String(p.id)) { expandedPreviewId = null; expandedState = null; }
      await fetchPreviews();
      refreshApproveBadge();
    } catch {
      btn.disabled = false;
    }
  });

  _patchApproveCardMeta(card, p);
  return card;
}

// Updates only the header text/badges/open-state of an existing card —
// never touches .approve-card-body, so this is safe to call on every
// poll tick without disturbing an expanded card's scroll/content.
export function _patchApproveCardMeta(card, p) {
  const isOpen = p.id === expandedPreviewId || String(p.id) === expandedPreviewId;
  const scanning = p.scan_status === "scanning";

  card.classList.toggle("open", isOpen);
  card.querySelector(".approve-card-title").innerHTML = `
    <span class="approve-chevron">${isOpen ? "▾" : "▸"}</span>
    <span>${esc(p.group_name)}</span>
    <span class="badge badge-neutral">${p.type}</span>
    ${p.source === "extension" ? `<span class="badge badge-neutral">extension</span>` : ""}
    ${scanning ? `<span class="badge badge-scanning">⏳ scanning</span>` : ""}`;
  card.querySelector(".approve-card-meta-text").textContent =
    `${p.video_count} video${p.video_count !== 1 ? "s" : ""}${scanning ? " so far…" : ""} · ${p.format}/${p.quality}`;
  card.querySelector(".approve-card-body").classList.toggle("hidden", !isOpen);
}

export function renderApproveList() {
  approveCount.textContent = previewSummaries.length
    ? `${previewSummaries.length} pending`
    : "Nothing pending";

  if (!previewSummaries.length) {
    approveList.innerHTML = `<div class="empty-state"><div class="empty-icon">✅</div><p>Nothing to approve.<br>Playlist / search / channel scans land here — from this app or the browser extension.</p></div>`;
    return;
  }

  // Diff against what's already in the DOM instead of rebuilding
  // everything: remove cards for deleted previews, patch-in-place cards
  // that already exist (metadata only — body untouched), and append
  // brand-new cards for previews we haven't seen before.
  const currentIds = new Set(previewSummaries.map(p => String(p.id)));
  approveList.querySelectorAll(".approve-card").forEach(card => {
    if (!currentIds.has(card.dataset.previewId)) card.remove();
  });
  if (approveList.querySelector(".empty-state")) approveList.innerHTML = "";

  previewSummaries.forEach(p => {
    const existing = approveList.querySelector(`.approve-card[data-preview-id="${p.id}"]`);
    if (existing) {
      _patchApproveCardMeta(existing, p);
    } else {
      approveList.appendChild(_buildApproveCard(p));
    }
  });
}

export async function toggleApproveCard(id) {
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
      // Default: all selected EXCEPT ones already downloaded before —
      // avoids silently re-downloading something the person already has;
      // they can still tick it back on if they actually want a re-fetch.
      selectionOrder: data.preview.entries.filter(e => !e.already_downloaded).map(e => e.video_id),
      usePrefix: false,
    };
    renderExpandedBody(id);
  } catch (e) {
    const body = document.getElementById(`approve-body-${id}`);
    if (body) body.innerHTML = `<div class="approve-body-error">${ICON.error} ${esc(e.message)}</div>`;
  }
}

// Called ONCE per card-open: builds the toolbar + empty item list shell
// + footer, wires the toolbar's listeners, then delegates the actual
// item rendering to appendApproveItems(). Never called again while the
// card stays open — later updates go through appendApproveItems() only,
// so the toolbar/list DOM nodes are never destroyed mid-scan.
export function renderExpandedBody(id) {
  const body = document.getElementById(`approve-body-${id}`);
  if (!body || !expandedState) return;
  const { selectionOrder, usePrefix } = expandedState;

  body.innerHTML = `
    <div class="batch-modal-toolbar">
      <button class="btn-toolbar approve-select-all">Select all</button>
      <button class="btn-toolbar approve-deselect-all">Deselect all</button>
      <span class="batch-selected-count">${selectionOrder.length} selected</span>
      <label class="toggle-switch-row batch-prefix-toggle">
        <span class="toggle-switch">
          <input type="checkbox" class="approve-prefix-toggle" ${usePrefix ? "checked" : ""}>
          <span class="toggle-switch-track"></span>
        </span>
        <span>Add number prefix to filenames</span>
      </label>
    </div>
    <div class="batch-modal-list approve-item-list"></div>
    <div class="modal-footer">
      <span class="modal-footer-hint approve-confirm-hint"></span>
      <div class="modal-footer-actions">
        <button class="btn-primary approve-confirm-btn" ${selectionOrder.length ? "" : "disabled"}>
          <span class="approve-confirm-label">Download selected</span>
          <span class="scan-spinner hidden approve-confirm-spinner">starting…</span>
        </button>
      </div>
    </div>`;

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

  appendApproveItems(id, expandedState.entries);
}

export function _approveItemHTML(e, selected, prefix) {
  const thumb = e.video_id
    ? `<img class="batch-item-thumb" src="${API}/thumbnails/${e.video_id}.jpg" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'batch-item-thumb-placeholder'}))">`
    : `<div class="batch-item-thumb-placeholder"></div>`;
  return `
    <label class="batch-item ${selected ? "" : "deselected"}" data-video-id="${esc(e.video_id || "")}">
      <input type="checkbox" class="batch-item-check" ${selected ? "checked" : ""}>
      <span class="batch-item-prefix">${prefix !== null ? prefix : ""}</span>
      ${thumb}
      <div class="batch-item-info">
        <div class="batch-item-title" title="${esc(e.title || "")}">${esc(e.title || "(untitled)")}${e.already_downloaded ? ` <span class="badge badge-neutral" title="Already downloaded before">already downloaded</span>` : ""}</div>
        <div class="batch-item-meta">${esc(e.uploader || "")}${e.duration ? " · " + fmtDuration(e.duration) : ""}</div>
      </div>
    </label>`;
}

// Appends DOM nodes only for entries not already rendered in this card's
// list — existing nodes are never touched or replaced, so the list's
// current scroll position is preserved no matter how many times this
// runs during a long scan. This is the core of the scroll-jump fix.
export function appendApproveItems(id, newEntries) {
  const body = document.getElementById(`approve-body-${id}`);
  if (!body || !expandedState) return;
  const listEl = body.querySelector(".approve-item-list");
  if (!listEl) return;

  const alreadyRendered = new Set(
    Array.from(listEl.querySelectorAll(".batch-item")).map(el => el.dataset.videoId)
  );
  const orderIndex = new Map(expandedState.selectionOrder.map((vid, i) => [vid, i + 1]));

  const frag = document.createDocumentFragment();
  newEntries.forEach(e => {
    const vid = e.video_id || "";
    if (alreadyRendered.has(vid)) return; // guards against double-append on overlapping polls
    const selected = orderIndex.has(vid);
    const prefix   = selected ? orderIndex.get(vid) : null;

    const wrapper = document.createElement("div");
    wrapper.innerHTML = _approveItemHTML(e, selected, prefix);
    const node = wrapper.firstElementChild;

    node.querySelector(".batch-item-check").addEventListener("change", (ev) => {
      const v = ev.target.closest(".batch-item").dataset.videoId;
      if (ev.target.checked) {
        if (!expandedState.selectionOrder.includes(v)) expandedState.selectionOrder.push(v);
      } else {
        expandedState.selectionOrder = expandedState.selectionOrder.filter(x => x !== v);
      }
      updateSelectionUI(id); // renumber in place — no full re-render, no scroll jump
    });

    frag.appendChild(node);
  });

  listEl.appendChild(frag);
  updateSelectionUI(id); // refresh count / confirm-btn state; existing rows keep their DOM position
}

// Updates selection state (checkbox checked-ness, prefix numbers,
// deselected styling, count, confirm button) on the EXISTING DOM nodes
// instead of rebuilding the list — rebuilding via innerHTML was
// resetting scroll position to the top on every single click.
export function updateSelectionUI(id) {
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

export async function confirmApprovePreview(id) {
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
    hint.innerHTML = `${ICON.error} ${e.message}`;
    btn.disabled = false;
    label.classList.remove("hidden");
    spinner.classList.add("hidden");
  }
}
