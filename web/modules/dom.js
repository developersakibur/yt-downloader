/* app/web/app.js — v7 frontend */

export const API = "http://127.0.0.1:5000";

// ── DOM refs ──────────────────────────────────────────────────
export const urlInput        = document.getElementById("url-input");
export const urlHint         = document.getElementById("url-hint");
export const qtyRow          = document.getElementById("qty-row");
export const qtyInput        = document.getElementById("qty-input");
export const qtyAllToggle    = document.getElementById("qty-all-toggle");
export const qtyHint         = document.getElementById("qty-hint");
export const scopeRow        = document.getElementById("scope-row");
export const scopeCountHint  = document.getElementById("scope-count-hint");
export const qualitySelect   = document.getElementById("quality-select");
export const downloadBtn     = document.getElementById("download-btn");
export const downloadBtnLbl  = document.getElementById("download-btn-label");
export const scanSpinner     = document.getElementById("scan-spinner");
export const queueList       = document.getElementById("queue-list");
export const historyList     = document.getElementById("history-list");
export const jobCount        = document.getElementById("job-count");
export const historyCount    = document.getElementById("history-count");
export const serverPill      = document.getElementById("server-pill");
export const cookieBar       = document.getElementById("cookie-bar");
export const cookieText      = document.getElementById("cookie-text");
export const cookieIcon      = document.getElementById("cookie-icon");
export const cookieClearBtn  = document.getElementById("cookie-clear-btn");
export const groupToggle        = document.getElementById("group-view-toggle");
export const historyGroupToggle = document.getElementById("history-group-toggle");
export const themeBtn        = document.getElementById("theme-btn");

export const statNums = {
  queued:      document.getElementById("stat-queued-n"),
  downloading: document.getElementById("stat-downloading-n"),
  completed:   document.getElementById("stat-completed-n"),
  failed:      document.getElementById("stat-failed-n"),
  speed:       document.getElementById("stat-speed-n"),
};
export const statSpeedChip = document.getElementById("stat-speed");