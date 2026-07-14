// convert.js — the local batch converter tab: form, batch progress
// cards, and the poller that drives them.
import { API } from "./dom.js";
import { QUALITY_OPTIONS } from "./quality.js";
import { esc, badgeClass } from "./utils.js";
import { ICON } from "./icons.js";

// ── Local Batch Converter ────────────────────────────────────
export const convertPathInput     = document.getElementById("convert-path-input");
export const convertTargetSelect  = document.getElementById("convert-target-select");
export const convertQualitySelect = document.getElementById("convert-quality-select");
export const convertRecursive     = document.getElementById("convert-recursive");
export const convertConcurrencySelect = document.getElementById("convert-concurrency-select");
export const convertBtn           = document.getElementById("convert-btn");
export const convertBtnLabel      = document.getElementById("convert-btn-label");
export const convertSpinner       = document.getElementById("convert-spinner");
export const convertHint          = document.getElementById("convert-hint");
export const convertCount         = document.getElementById("convert-count");
export const convertList          = document.getElementById("convert-list");

export let activeBatches   = [];   // [{ id, label, jobs, done }]
export let convertPollTimer = null;

export function populateConvertQualityOptions() {
  const target = convertTargetSelect.value; // "MP3" or "3GP"
  const opts = QUALITY_OPTIONS[target] || QUALITY_OPTIONS.MP3;
  convertQualitySelect.innerHTML = opts.map(o => `<option value="${o.value}">${o.label}</option>`).join("");
}
convertTargetSelect.addEventListener("change", populateConvertQualityOptions);
populateConvertQualityOptions();

export function batchStats(jobs) {
  const done      = jobs.filter(j => j.status === "completed").length;
  const failed    = jobs.filter(j => j.status === "failed").length;
  const skipped   = jobs.filter(j => j.status === "skipped").length;
  const paused    = jobs.filter(j => j.status === "paused").length;
  const remaining = jobs.filter(j => j.status === "queued" || j.status === "converting" || j.status === "paused").length;
  return { done, failed, skipped, paused, remaining };
}

export function convertActionButtons(job) {
  const s = job.status;
  const id = job.id;
  const btns = [];
  if (s === "queued" || s === "converting")
    btns.push(`<button class="job-btn" data-conv-action="pause" data-conv-id="${id}">${ICON.pause} Pause</button>`);
  if (s === "paused")
    btns.push(`<button class="job-btn success" data-conv-action="resume" data-conv-id="${id}">${ICON.play} Resume</button>`);
  if (["queued", "converting", "paused"].includes(s))
    btns.push(`<button class="job-btn" data-conv-action="skip" data-conv-id="${id}">${ICON.skip} Skip</button>`);
  if (job.error_message)
    btns.push(`<button class="job-btn job-btn-error tooltip-host" data-tooltip="${esc(job.error_message)}" type="button">${ICON.warning} Error</button>`);
  btns.push(`<button class="job-btn danger" data-conv-action="delete" data-conv-id="${id}">${ICON.trash} Delete</button>`);
  return btns.join("");
}

export function renderConvertJobs() {
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

export async function pollAllBatches() {
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
    const concurrency = parseInt(convertConcurrencySelect.value, 10) || 2;
    const res = await fetch(`${API}/api/local-convert`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path,
        target_format: target,
        quality: convertQualitySelect.value,
        recursive: convertRecursive.checked,
        concurrency,
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
    convertHint.innerHTML = `${ICON.error} ${e.message}`;
    convertHint.classList.add("error");
  }
  convertBtn.disabled = false;
  convertBtnLabel.classList.remove("hidden");
  convertSpinner.classList.add("hidden");
});
