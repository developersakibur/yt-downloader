/* popup.js — v7 extension
 *
 * Three responsibilities (per the Developer Guide):
 *   1. URL submission  → POST /api/scan, then poll /api/scan/status/<id>
 *   2. Cookie sync     → reads chrome.cookies, POSTs to /api/cookies
 *   3. Download trigger (same as #1 — no direct yt-dlp contact ever)
 */

const API = "http://127.0.0.1:5000";

// ── DOM refs ──────────────────────────────────────────────────
const serverPill      = document.getElementById("server-pill");
const themeBtn        = document.getElementById("theme-btn");
const dashboardBtn    = document.getElementById("dashboard-btn");
const syncBtn         = document.getElementById("sync-btn");
const delCookiesBtn   = document.getElementById("del-cookies-btn");
const cookieStatus    = document.getElementById("cookie-status");
const retryBtn        = document.getElementById("retry-btn");
const goYtBtn         = document.getElementById("go-yt-btn");
const downloadBtn     = document.getElementById("download-btn");
const dlLabel         = document.getElementById("dl-label");
const dlSpinner       = document.getElementById("dl-spinner");
const dlStatus        = document.getElementById("dl-status");
const qtyRow          = document.getElementById("qty-row");
const qtyInput        = document.getElementById("qty-input");
const qtyAllToggle    = document.getElementById("qty-all-toggle");
const qtyHint         = document.getElementById("qty-hint");
const scopeRow        = document.getElementById("scope-row");
const scopeCountHint  = document.getElementById("scope-count-hint");
const qualitySelect   = document.getElementById("quality-select");

const views = {
  offline:     document.getElementById("view-offline"),
  notYt:       document.getElementById("view-not-yt"),
  unsupported: document.getElementById("view-unsupported"),
  form:        document.getElementById("view-form"),
};

// ── Theme ─────────────────────────────────────────────────────
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  themeBtn.textContent = t === "dark" ? "◑" : "◐";
  localStorage.setItem("v7_ext_theme", t);
}
applyTheme(localStorage.getItem("v7_ext_theme") || "dark");
themeBtn.addEventListener("click", () => {
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
});

// ── View switcher ─────────────────────────────────────────────
function showView(key, extras = []) {
  Object.values(views).forEach(v => v.classList.add("hidden"));
  qtyRow.classList.add("hidden");
  scopeRow.classList.add("hidden");
  views[key].classList.remove("hidden");
  extras.forEach(el => el.classList.remove("hidden"));
}

// ── Server status ─────────────────────────────────────────────
function setPill(state) {
  serverPill.className = "pill";
  if (state === "online")  { serverPill.textContent = "online";     serverPill.classList.add("pill-online"); }
  else if (state === "offline") { serverPill.textContent = "offline"; serverPill.classList.add("pill-offline"); }
  else                          { serverPill.textContent = "checking…"; serverPill.classList.add("pill-checking"); }
}

async function checkServer() {
  setPill("checking");
  try {
    const r = await fetch(`${API}/api/status`, { signal: AbortSignal.timeout(3000) });
    if (!r.ok) throw new Error();
    setPill("online");
    return true;
  } catch {
    setPill("offline");
    showView("offline");
    return false;
  }
}

// ── Quantity row behavior — mirrors web/app.js ─────────────────
function configureQtyRow(type) {
  qtyAllToggle.checked = false;
  qtyAllToggle.parentElement.classList.remove("checked");
  qtyInput.disabled = false;
  if (type === "search") {
    qtyInput.min = 1;
    qtyInput.max = 250;
    if (parseInt(qtyInput.value || "0") > 250) qtyInput.value = 250;
    qtyAllToggle.parentElement.classList.add("hidden");
    qtyHint.textContent = "Search results — max 250";
  } else {
    qtyInput.min = 1;
    qtyInput.removeAttribute("max");
    qtyAllToggle.parentElement.classList.remove("hidden");
    qtyHint.textContent = "";
  }
}
qtyAllToggle.addEventListener("change", () => {
  qtyInput.disabled = qtyAllToggle.checked;
  qtyAllToggle.parentElement.classList.toggle("checked", qtyAllToggle.checked);
});

// ── URL detection (mirrors scanner.detect_type) ───────────────
function detectUrlType(url) {
  if (!/youtube\.com/i.test(url)) return null;
  if (/youtube\.com\/results/.test(url))             return "search";
  if (/watch\?v=.*list=|list=.*watch\?v=/.test(url)) return "video+list";
  if (/watch\?v=|youtu\.be\//.test(url))             return "video";
  if (/\/shorts\//.test(url))                         return "short";
  if (/playlist.*list=/.test(url))                    return "playlist";
  if (/youtube\.com\/@[^/]+(\/shorts|\/videos)?\/?\s*$/.test(url)) return "channel";
  return null; // unsupported page on youtube.com
}

// ── Show correct view for current tab ────────────────────────
async function loadCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url || "";

  if (!/youtube\.com/i.test(url)) {
    showView("notYt"); return;
  }

  const type = detectUrlType(url);
  if (!type) {
    showView("unsupported"); return;
  }

  if (type === "search" || type === "playlist" || type === "channel") {
    configureQtyRow(type);
    showView("form", [qtyRow]);
  } else if (type === "video+list") {
    scopeCountHint.textContent = "Checking playlist size…";
    showView("form", [scopeRow]);
    fetchScopeCount(url);
  } else {
    showView("form");
  }
}

async function fetchScopeCount(url) {
  try {
    const res  = await fetch(`${API}/api/scan/count?url=${encodeURIComponent(url)}&playlist=true`);
    const data = await res.json();
    if (data.ok && data.count) {
      scopeCountHint.textContent = `This playlist has ${data.count} video${data.count !== 1 ? "s" : ""}.`;
    } else {
      scopeCountHint.textContent = "";
    }
  } catch {
    scopeCountHint.textContent = "";
  }
}

// ── Dashboard shortcut ────────────────────────────────────────
function openDashboard() {
  chrome.tabs.query({ url: `${API}/*` }, (tabs) => {
    if (tabs.length) {
      chrome.tabs.update(tabs[0].id, { active: true });
      chrome.windows.update(tabs[0].windowId, { focused: true });
    } else {
      chrome.tabs.create({ url: `${API}/` });
    }
  });
}
dashboardBtn.addEventListener("click", openDashboard);
goYtBtn.addEventListener("click", () => {
  chrome.tabs.query({ url: "*://*.youtube.com/*" }, (tabs) => {
    if (tabs.length) chrome.tabs.update(tabs[0].id, { active: true });
    else chrome.tabs.create({ url: "https://www.youtube.com/" });
  });
});

// ── Cookie sync (same trusted logic from v6, unchanged) ───────
async function syncCookies() {
  setStatus(cookieStatus, "Reading your YouTube cookies…", "");
  try {
    const [ytCookies, gCookies] = await Promise.all([
      chrome.cookies.getAll({ domain: "youtube.com" }),
      chrome.cookies.getAll({ domain: "google.com" }),
    ]);

    // de-duplicate by name+domain
    const seen = new Set();
    const cookies = [];
    for (const c of [...ytCookies, ...gCookies]) {
      const key = `${c.domain}|${c.name}`;
      if (seen.has(key)) continue;
      seen.add(key);
      cookies.push({ domain: c.domain, path: c.path, secure: c.secure,
                     expirationDate: c.expirationDate, name: c.name, value: c.value });
    }

    if (!cookies.length) {
      setStatus(cookieStatus, "No cookies found — log in to YouTube first.", "err");
      return;
    }

    const res = await fetch(`${API}/api/cookies`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cookies }),
    });
    if (res.ok) {
      setStatus(cookieStatus, `✓ ${cookies.length} cookies synced — private/age-restricted videos will work.`, "ok");
    } else {
      setStatus(cookieStatus, "❌ Server couldn't save cookies.", "err");
    }
  } catch (e) {
    setStatus(cookieStatus, "❌ Could not read or send cookies.", "err");
  }
}

async function deleteCookies() {
  setStatus(cookieStatus, "Removing cookies…", "");
  try {
    const res = await fetch(`${API}/api/cookies`, { method: "DELETE" });
    setStatus(cookieStatus, res.ok ? "🗑 Cookies removed." : "❌ Could not remove cookies.", res.ok ? "" : "err");
  } catch {
    setStatus(cookieStatus, "❌ Could not reach server.", "err");
  }
}

syncBtn.addEventListener("click", syncCookies);
delCookiesBtn.addEventListener("click", deleteCookies);

// ── Download = scan + poll ────────────────────────────────────
let pollTimer = null;

function resetDownloadBtn() {
  downloadBtn.disabled = false;
  dlLabel.classList.remove("hidden");
  dlSpinner.classList.add("hidden");
}

function setStatus(el, msg, cls = "") {
  el.textContent = msg;
  el.className = "status-line" + (cls ? ` ${cls}` : "");
}

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
populateQualityOptions(); // initial fill

downloadBtn.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url || "";
  if (!url) return;

  downloadBtn.disabled = true;
  dlLabel.classList.add("hidden");
  dlSpinner.classList.remove("hidden");

  const type = detectUrlType(url);
  const isGroup = type === "search" || type === "playlist" || type === "channel" ||
    (type === "video+list" && document.querySelector("input[name='playlist']:checked")?.value === "true");

  const format  = document.querySelector("input[name='format']:checked")?.value || "MP4";
  const quality = qualitySelect.value;
  const quantity = qtyAllToggle.checked ? "all" : Math.max(1, parseInt(qtyInput.value || "25", 10));
  const playlist = document.querySelector("input[name='playlist']:checked")?.value === "true";

  if (isGroup) {
    setStatus(dlStatus, "Scanning URL…");
    try {
      const res = await fetch(`${API}/api/scan/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, format, quality, quantity, playlist, source: "extension" }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "scan rejected");
      pollPreviewScan(data.scan_id);
    } catch (e) {
      setStatus(dlStatus, `❌ ${e.message}`, "err");
      resetDownloadBtn();
    }
    return;
  }

  const body = { url, format, quality, quantity, playlist };
  try {
    const res = await fetch(`${API}/api/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "scan rejected");
    pollScan(data.scan_id);
  } catch (e) {
    setStatus(dlStatus, `❌ ${e.message}`, "err");
    resetDownloadBtn();
  }
});

function pollPreviewScan(scanId, attempt = 0) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    try {
      const r = await fetch(`${API}/api/scan/status/${scanId}`);
      const d = await r.json();
      if (d.status === "pending") {
        const p = d.progress;
        setStatus(dlStatus, p && p.total ? `Scanning… ${p.fetched}/${p.total}` : "Scanning…");
        pollPreviewScan(scanId, attempt + 1);
        return;
      }
      if (d.status === "done") {
        const n = d.result.video_count;
        setStatus(dlStatus,
          `✓ Found ${n} video${n !== 1 ? "s" : ""} — open Dashboard → Approve tab to review and start.`, "ok");
      } else {
        setStatus(dlStatus, `❌ ${d.error || "Scan failed."}`, "err");
      }
    } catch {
      setStatus(dlStatus, "❌ Lost connection to server.", "err");
    }
    resetDownloadBtn();
  }, 800);
}

function pollScan(scanId, attempt = 0) {
  if (attempt > 60) {           // 60 × 800ms = 48s timeout
    setStatus(dlStatus, "❌ Scan timed out.", "err");
    resetDownloadBtn();
    return;
  }
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    try {
      const r = await fetch(`${API}/api/scan/status/${scanId}`);
      const d = await r.json();

      if (d.status === "pending") {
        setStatus(dlStatus, `Scanning… (${attempt + 1})`);
        pollScan(scanId, attempt + 1);
        return;
      }
      if (d.status === "done") {
        const n = d.result.video_count;
        setStatus(dlStatus,
          `✓ ${n} video${n !== 1 ? "s" : ""} added to queue — open Dashboard to track progress.`, "ok");
      } else {
        setStatus(dlStatus, `❌ ${d.error || "Scan failed."}`, "err");
      }
    } catch {
      setStatus(dlStatus, "❌ Lost connection to server.", "err");
    }
    resetDownloadBtn();
  }, 800);
}

// ── Boot ──────────────────────────────────────────────────────
retryBtn.addEventListener("click", async () => {
  const ok = await checkServer();
  if (ok) loadCurrentTab();
});

(async () => {
  const ok = await checkServer();
  if (ok) loadCurrentTab();
})();
