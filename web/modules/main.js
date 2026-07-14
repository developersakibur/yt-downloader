// main.js — single entry point (loaded via <script type="module">).
// Importing each feature module runs its top-level side effects
// (DOM lookups, event-listener wiring, setInterval polling loops);
// this file's own job is just the cross-tab glue (tab switching) and
// the initial boot calls that used to sit at the bottom of the old
// monolithic app.js.
import "./theme.js";
import "./quality.js";
import "./queue.js";
import "./scan-form.js";
import { fetchPreviews, refreshApproveBadge } from "./approve.js";
import { fetchHistory } from "./history.js";
import { fetchSettings, fetchCookies } from "./settings.js";
import "./convert.js";
import { startLogsAutoRefresh, stopLogsAutoRefresh } from "./logs.js";

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
    if (tab.dataset.tab === "logs") startLogsAutoRefresh(); else stopLogsAutoRefresh();
  });
});

// ── Boot + polling ────────────────────────────────────────────
// ── Boot + polling ────────────────────────────────────────────
fetchCookies();
refreshApproveBadge();
setInterval(fetchCookies, 8000);
setInterval(refreshApproveBadge, 5000);
