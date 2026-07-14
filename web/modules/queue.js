// queue.js — the Queue tab: job cards, bulk actions, flat/grouped
// rendering, and the live-polling loop that keeps it up to date.
import { API, groupToggle, historyGroupToggle, jobCount, queueList,
         serverPill, statNums, statSpeedChip } from "./dom.js";
import { esc, badgeClass, fmtSpeed, fmtEta, formatDuration } from "./utils.js";
import { expandedGroups } from "./state.js";
import { ICON } from "./icons.js";

export function actionButtons(job) {
  const s = job.status, id = job.id;
  const btns = [];
  if (["queued","downloading","converting"].includes(s))
    btns.push(`<button class="job-btn" data-action="pause" data-id="${id}">${ICON.pause} Pause</button>`);
  if (s === "paused")
    btns.push(`<button class="job-btn success" data-action="resume" data-id="${id}">${ICON.play} Resume</button>`);
  if (["failed","cancelled"].includes(s))
    btns.push(`<button class="job-btn" data-action="retry" data-id="${id}">${ICON.retry} Retry</button>`);
  if (job.error_message)
    btns.push(`<button class="job-btn job-btn-error tooltip-host" data-tooltip="${esc(job.error_message)}" type="button">${ICON.warning} Error</button>`);
  btns.push(`<button class="job-btn danger" data-action="delete" data-id="${id}">${ICON.trash} Delete</button>`);
  return btns.join("");
}

export function _retryCountdownText(job) {
  if (job.status !== "failed" || !job.next_retry_at) return null;
  // Stored as "YYYY-MM-DD HH:MM:SS" local time, no timezone suffix —
  // swap the space for 'T' so Date() parses it as local time correctly.
  const target = new Date(job.next_retry_at.replace(" ", "T")).getTime();
  const remaining = Math.round((target - Date.now()) / 1000);
  if (remaining <= 0) return "Retrying now…";
  return `Retrying in ${remaining}s (attempt ${job.retry_count + 1}/3)`;
}

export function _jobMetaHTML(job, pct) {
  const uploader = job.uploader ? `· ${esc(job.uploader)}` : "";
  const dur      = job.duration ? formatDuration(job.duration) : "";
  const cookieBadge = job.used_cookies ? `<span title="Used cookies">${ICON.lock}</span>` : "";
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
export function patchJobCards(jobs) {
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
    const retryCountdown = _retryCountdownText(job);
    const errorHtml = retryCountdown ? `⏳ ${retryCountdown}` : null;
    if (errorHtml) {
      if (existingError) {
        if (existingError.innerHTML !== errorHtml) existingError.innerHTML = errorHtml;
      } else {
        card.querySelector(".job-card-body").insertAdjacentHTML(
          "beforeend", `<div class="job-error job-retry-pending">${errorHtml}</div>`
        );
      }
    } else if (existingError) {
      existingError.remove();
    }
  }
  return true;
}

export function jobCardHTML(job) {
  const isConverting = job.status === "converting";
  const pct = job.status === "completed" ? 100
    : isConverting ? Math.round(job.convert_percent || 0)
    : Math.round(job.progress_percent || 0);
  const title    = job.original_title || job.video_id || job.url;
  const retryCountdown = _retryCountdownText(job);
  const error    = retryCountdown
    ? `<div class="job-error job-retry-pending">⏳ ${retryCountdown}</div>`
    : "";
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
export async function bulkAction(action, jobIds) {
  await Promise.all(jobIds.map(id =>
    fetch(`${API}/api/queue/${id}`, {
      method: "PATCH",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({action}),
    }).catch(() => {})
  ));
  fetchQueue();
}

export function globalActionBar(jobs) {
  const pauseable  = jobs.filter(j => ["queued","downloading","converting"].includes(j.status)).map(j=>j.id);
  const resumeable = jobs.filter(j => j.status === "paused").map(j=>j.id);
  const retryable  = jobs.filter(j => ["failed","cancelled"].includes(j.status)).map(j=>j.id);
  if (!pauseable.length && !resumeable.length && !retryable.length) return "";
  const btns = [];
  if (pauseable.length)  btns.push(`<button class="bulk-btn" data-bulk="pause"  data-ids='${JSON.stringify(pauseable)}'>${ICON.pause} Pause All (${pauseable.length})</button>`);
  if (resumeable.length) btns.push(`<button class="bulk-btn" data-bulk="resume" data-ids='${JSON.stringify(resumeable)}'>${ICON.play} Resume All (${resumeable.length})</button>`);
  if (retryable.length)  btns.push(`<button class="bulk-btn" data-bulk="retry"  data-ids='${JSON.stringify(retryable)}'>${ICON.retry} Retry All (${retryable.length})</button>`);
  return `<div class="global-actions">${btns.join("")}</div>`;
}

export function groupActionBar(groupId, jobs) {
  const pauseable  = jobs.filter(j => ["queued","downloading","converting"].includes(j.status)).map(j=>j.id);
  const resumeable = jobs.filter(j => j.status === "paused").map(j=>j.id);
  const retryable  = jobs.filter(j => ["failed","cancelled"].includes(j.status)).map(j=>j.id);
  const deleteable = jobs.map(j=>j.id);
  const btns = [];
  if (pauseable.length)  btns.push(`<button class="job-btn" data-bulk="pause"  data-ids='${JSON.stringify(pauseable)}'>${ICON.pause} Pause All</button>`);
  if (resumeable.length) btns.push(`<button class="job-btn success" data-bulk="resume" data-ids='${JSON.stringify(resumeable)}'>${ICON.play} Resume All</button>`);
  if (retryable.length)  btns.push(`<button class="job-btn" data-bulk="retry"  data-ids='${JSON.stringify(retryable)}'>${ICON.retry} Retry All</button>`);
  btns.push(`<button class="job-btn danger" data-bulk="delete" data-ids='${JSON.stringify(deleteable)}'>${ICON.trash} Delete All</button>`);
  return `<div class="job-actions group-bulk-actions">${btns.join("")}</div>`;
}

// ── Queue rendering ───────────────────────────────────────────
export let lastQueueHTML = "";
export let lastQueueJobIds = "";

export async function fetchQueue() {
  try {
    const res  = await fetch(`${API}/api/queue`);
    if (!res.ok) throw new Error();
    const data = await res.json();
    renderQueueData(data);
  } catch {
    serverPill.textContent = "offline";
    serverPill.className   = "server-pill offline";
  }
}

export function renderQueueData(data) {
  if (!data || data.ok === false) {
    serverPill.textContent = "offline";
    serverPill.className   = "server-pill offline";
    return;
  }
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

  serverPill.textContent = "online";
  serverPill.className   = "server-pill";

  jobCount.textContent = `${jobs.length} job${jobs.length !== 1 ? "s" : ""}`;

  if (!jobs.length) {
    const html = `<div class="empty-state"><div class="empty-icon">${ICON.check}</div><p>Queue is empty.<br>All done! Check History for completed downloads.</p></div>`;
    if (lastQueueHTML !== html) { queueList.innerHTML = html; lastQueueHTML = html; }
    lastQueueJobIds = "";
    return;
  }

  // Cheap path: same set of job ids as last render (in the same order)
  // -> just patch each card's dynamic bits in place (progress, speed,
  // badge, action buttons) instead of rebuilding the whole list.
  // Rebuilding via innerHTML on every tick — even though nothing
  // structural changed, just percentages ticking — was resetting
  // scroll position and losing hover/focus state every time.
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
}

// ── Live queue updates: plain polling ────────────────────────────
// (Tried SSE here — a single persistent EventSource connection to
// 127.0.0.1 permanently occupies one of the browser's 6-connection-
// per-origin HTTP/1.1 slots, which starved the other polling/status
// requests this app already makes. Reverted; 2s polling on localhost
// has no perceptible latency anyway.)
setInterval(fetchQueue, 2000);
fetchQueue(); // immediate first paint, don't wait for the first tick

export function renderFlatView(jobs) {
  return globalActionBar(jobs) + jobs.map(jobCardHTML).join("");
}

export function renderGroupView(jobs) {
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
          <span class="group-toggle">${ICON.chevron}</span>
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
export function attachActions(container) {
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

export async function handleJobAction(action, jobId) {
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
document.getElementById("pause-all-btn").addEventListener("click", async () => {
  await fetch(`${API}/api/queue/pause-all`, { method: "POST" }).catch(() => {});
  lastQueueJobIds = ""; // force a full re-render — statuses changed for many jobs at once
  fetchQueue();
});
document.getElementById("resume-all-btn").addEventListener("click", async () => {
  await fetch(`${API}/api/queue/resume-all`, { method: "POST" }).catch(() => {});
  lastQueueJobIds = "";
  fetchQueue();
});

groupToggle.addEventListener("click", () => {
  const active = groupToggle.dataset.active === "true";
  groupToggle.dataset.active = (!active).toString();
  groupToggle.classList.toggle("active", !active);
  lastQueueHTML = "";
  lastQueueJobIds = "";
  fetchQueue();
});
// ── Live queue updates: plain polling ────────────────────────────
