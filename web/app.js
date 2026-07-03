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
};

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
//   search            -> hard-capped at 100, "All" doesn't apply (endless feed)
//   playlist / channel -> no upper cap, "All" downloads everything
function configureQtyRow(type) {
  qtyAllToggle.checked = false;
  qtyAllToggle.parentElement.classList.remove("checked");
  qtyInput.disabled = false;
  if (type === "search") {
    qtyInput.min = 1;
    qtyInput.max = 100;
    if (parseInt(qtyInput.value || "0") > 100) qtyInput.value = 100;
    qtyAllToggle.parentElement.classList.add("hidden"); // no "All" concept for search
    qtyHint.textContent = "Search results — max 100";
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
  if (type === "video+list") scopeRow.classList.remove("hidden");
}
urlInput.addEventListener("input", updateFormUI);

// ── Scan submission ───────────────────────────────────────────
downloadBtn.addEventListener("click", async () => {
  const url = urlInput.value.trim();
  if (!url) return;
  downloadBtn.disabled = true;
  downloadBtnLbl.classList.add("hidden");
  scanSpinner.classList.remove("hidden");
  const body = {
    url,
    format:   document.querySelector("input[name='format']:checked")?.value || "MP4",
    quality:  qualitySelect.value,
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

function jobCardHTML(job) {
  const pct      = job.status === "completed" ? 100 : Math.round(job.progress_percent || 0);
  const title    = job.original_title || job.video_id || job.url;
  const uploader = job.uploader ? `· ${esc(job.uploader)}` : "";
  const dur      = job.duration ? formatDuration(job.duration) : "";
  const cookieBadge = job.used_cookies ? `<span title="Used cookies">🔐</span>` : "";
  const retryBadge  = job.retry_count > 0 ? `<span>retry #${job.retry_count}</span>` : "";
  const idxBadge    = job.playlist_index != null ? `<span>#${job.playlist_index}</span>` : "";
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
            <div class="job-meta">${[
              job.format + (job.quality && job.quality !== "best" ? ` · ${job.quality}` : ""),
              uploader,
              dur,
              idxBadge,
              retryBadge,
              cookieBadge,
              (pct > 0 && pct < 100) ? `${pct}%` : ""
            ].filter(Boolean).map(x => `<span>${x}</span>`).join("")}</div>
            <div class="job-actions">${actionButtons(job)}</div>
          </div>
          <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
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
    serverPill.textContent = "● online";
    serverPill.className   = "server-pill";

    jobCount.textContent = `${jobs.length} job${jobs.length !== 1 ? "s" : ""}`;

    if (!jobs.length) {
      const html = `<div class="empty-state"><div class="empty-icon">✓</div><p>Queue is empty.<br>All done! Check History for completed downloads.</p></div>`;
      if (lastQueueHTML !== html) { queueList.innerHTML = html; lastQueueHTML = html; }
      return;
    }

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

// ── Boot + polling ────────────────────────────────────────────
fetchQueue();
fetchCookies();
setInterval(fetchQueue, 2000);
setInterval(fetchCookies, 8000);
