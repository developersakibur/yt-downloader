// scan-form.js — the "new download" URL form: type detection, the
// quantity/scope rows, submission (single video vs. group/preview
// scan), and polling a single-video scan's progress.
import { API, downloadBtn, downloadBtnLbl, qtyAllToggle, qtyHint, qtyInput,
         qtyRow, qualitySelect, scanSpinner, scopeCountHint, scopeRow,
         urlHint, urlInput } from "./dom.js";
import { fetchQueue } from "./queue.js";
import { startPreviewScan, fetchPreviews, refreshApproveBadge } from "./approve.js";
import { ICON } from "./icons.js";

// ── URL form ──────────────────────────────────────────────────
// ── State (module-private — nothing outside scan-form.js touches these) ──
let activeScanId  = null;
let scanPollTimer = null;

// Quantity row behavior differs by type:
//   search            -> hard-capped at 250, "All" doesn't apply (endless feed)
//   playlist / channel -> "All" means everything, up to a 999 safety cap
export function configureQtyRow(type) {
  qtyAllToggle.checked = false;
  qtyAllToggle.parentElement.classList.remove("checked");
  qtyInput.disabled = false;
  if (type === "search") {
    qtyInput.min = 1;
    qtyInput.max = 250;
    if (parseInt(qtyInput.value || "0") > 250) qtyInput.value = 250;
    qtyAllToggle.parentElement.classList.add("hidden"); // no "All" concept for search
    qtyHint.textContent = "Search results — max 250";
  } else if (type === "mix") {
    qtyInput.min = 1;
    qtyInput.max = 100;
    if (parseInt(qtyInput.value || "0") > 100 || !qtyInput.value) qtyInput.value = 25;
    qtyAllToggle.parentElement.classList.add("hidden"); // no "All" concept for an auto-generated Mix
    qtyHint.textContent = "This is a YouTube Mix/Radio — it has no fixed length, so pick how many you want (max 100).";
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

export function detectUrlType(url) {
  if (!url) return null;
  if (!/youtube\.com|youtu\.be/i.test(url)) return "invalid";
  if (/youtube\.com\/results/.test(url))             return "search";
  // A Mix/Radio's list= id always starts with RD — checked before the
  // ordinary video+list/playlist branches since it overrides both
  // (mirrors scanner.py's _mix_list_id()).
  const listMatch = url.match(/[?&]list=([^&]+)/);
  if (listMatch && /^RD/i.test(listMatch[1]))         return "mix";
  if ((/watch\?v=/.test(url) || /youtu\.be\//.test(url)) && /list=/.test(url)) return "video+list";
  if (/watch\?v=|youtu\.be\//.test(url))             return "video";
  if (/\/shorts\//.test(url))                         return "short";
  if (/playlist.*list=/.test(url))                    return "playlist";
  if (/youtube\.com\/@[^/]+(\/shorts|\/videos)?\/?\s*$/.test(url)) return "channel";
  return "unknown";
}

export function updateFormUI() {
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
  if (type === "search" || type === "playlist" || type === "channel" || type === "mix") {
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
export function updateScopeQtyVisibility() {
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

export let scopeCountTimer = null;
export async function fetchScopeCount(url) {
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
export function isGroupSubmission() {
  const type = detectUrlType(urlInput.value.trim());
  if (type === "search" || type === "playlist" || type === "channel" || type === "mix") return true;
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
      urlHint.textContent = "Scanning… check the Approve tab, videos will appear as they're found.";
      urlHint.className   = "url-hint";
      urlInput.value = "";
      updateFormUI();
      refreshApproveBadge();
      if (document.querySelector('.tab[data-tab="approve"]').classList.contains("active")) fetchPreviews();
      startPreviewScan(data.scan_id); // sets approve.js's activePreviewScanId + kicks off pollPreview()
    } catch (e) {
      urlHint.innerHTML = `${ICON.error} ${e.message}`;
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
    urlHint.innerHTML = `${ICON.error} ${e.message}`;
    urlHint.classList.add("error");
    resetDownloadBtn();
  }
});

export function resetDownloadBtn() {
  downloadBtn.disabled = false;
  downloadBtnLbl.classList.remove("hidden");
  scanSpinner.classList.add("hidden");
}

export function pollScan() {
  clearTimeout(scanPollTimer);
  if (!activeScanId) return;
  scanPollTimer = setTimeout(async () => {
    try {
      const res  = await fetch(`${API}/api/scan/status/${activeScanId}`);
      const data = await res.json();
      if (data.status === "pending") { pollScan(); return; }
      if (data.status === "done") {
        const r = data.result;
        urlHint.innerHTML = `${ICON.check} Added ${r.video_count} video${r.video_count !== 1 ? "s" : ""} to queue.`;
        urlHint.className   = "url-hint";
        urlInput.value      = "";
        updateFormUI();
        fetchQueue();
      } else {
        urlHint.innerHTML = `${ICON.error} ${data.error || "Scan failed."}`;
        urlHint.classList.add("error");
      }
    } catch {
      urlHint.innerHTML = `${ICON.error} Could not reach server.`;
      urlHint.classList.add("error");
    }
    activeScanId = null;
    resetDownloadBtn();
  }, 800);
}
