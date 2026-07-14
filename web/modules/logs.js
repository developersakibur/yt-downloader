// logs.js — the Logs tab: tails app.log from the server, lets the
// person copy the whole thing or clear it (truncates the file +
// removes rotated backups server-side, see /api/logs in routes.py).
import { API } from "./dom.js";
import { esc } from "./utils.js";

const logsView       = document.getElementById("logs-view");
const logsCount      = document.getElementById("logs-count");
const refreshBtn     = document.getElementById("logs-refresh-btn");
const copyBtn        = document.getElementById("logs-copy-btn");
const clearBtn       = document.getElementById("logs-clear-btn");

let lastText = "";
let autoTimer = null;

function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// Light syntax coloring: tag ERROR/WARNING lines so problems jump out
// in a wall of INFO text, without needing a whole log-viewer library.
function renderLog(text) {
  if (!text.trim()) {
    logsView.innerHTML = `<span class="logs-empty">Log is empty.</span>`;
    return;
  }
  const html = text.split("\n").map(line => {
    const escaped = esc(line);
    if (/\bERROR\b/.test(line))   return `<span class="log-line log-line-error">${escaped}</span>`;
    if (/\bWARNING\b/.test(line)) return `<span class="log-line log-line-warn">${escaped}</span>`;
    return `<span class="log-line">${escaped}</span>`;
  }).join("\n");
  logsView.innerHTML = html;
}

export async function fetchLogs({ scrollToBottom = true } = {}) {
  try {
    const res  = await fetch(`${API}/api/logs?lines=1500`);
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "couldn't load logs");
    lastText = data.text;
    renderLog(data.text);
    const parts = [`${data.total_lines} line${data.total_lines !== 1 ? "s" : ""}`, fmtBytes(data.size)];
    if (data.truncated) parts.push(`showing last 1500`);
    logsCount.textContent = parts.join(" · ");
    if (scrollToBottom) logsView.scrollTop = logsView.scrollHeight;
  } catch (e) {
    logsCount.textContent = "Couldn't load logs";
    logsView.innerHTML = `<span class="logs-empty">Couldn't reach the server — is the app running?</span>`;
  }
}

refreshBtn.addEventListener("click", () => fetchLogs());

copyBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(lastText);
    const original = copyBtn.innerHTML;
    copyBtn.innerHTML = `<svg class="ico" viewBox="0 0 24 24"><path d="M12 2a10 10 0 100 20 10 10 0 000-20zm-1.2 14.6l-4-4 1.4-1.42 2.6 2.6 5.6-5.6 1.4 1.42-7 7z" fill="currentColor"/></svg> Copied`;
    setTimeout(() => { copyBtn.innerHTML = original; }, 1500);
  } catch {
    alert("Couldn't copy — your browser blocked clipboard access.");
  }
});

clearBtn.addEventListener("click", async () => {
  if (!confirm("Clear the log file? This removes the current log and all rotated backups — it can't be undone.")) return;
  try {
    const res  = await fetch(`${API}/api/logs`, { method: "DELETE" });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "couldn't clear logs");
    fetchLogs();
  } catch (e) {
    alert(`Couldn't clear logs: ${e.message}`);
  }
});

// Auto-refresh only while the Logs tab is actually visible, so it
// doesn't poll in the background for a panel nobody's looking at.
export function startLogsAutoRefresh() {
  stopLogsAutoRefresh();
  fetchLogs();
  autoTimer = setInterval(() => fetchLogs(), 4000);
}
export function stopLogsAutoRefresh() {
  if (autoTimer) clearInterval(autoTimer);
  autoTimer = null;
}
