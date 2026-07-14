// utils.js — small, pure formatting/escaping helpers used across
// several tabs (Approve, Queue, History, Convert).
// ── Speed / ETA formatting helpers ──────────────────────────────
export function fmtSpeed(bytesPerSec) {
  if (!bytesPerSec || bytesPerSec <= 0) return "";
  const kb = bytesPerSec / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB/s`;
  return `${(kb / 1024).toFixed(1)} MB/s`;
}
export function fmtEta(seconds) {
  if (!seconds || seconds <= 0) return "";
  if (seconds < 60) return `${Math.round(seconds)}s left`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m < 60 ? `${m}m ${s}s left` : `${Math.floor(m / 60)}h ${m % 60}m left`;
}


export function fmtDuration(sec) {
  if (!sec && sec !== 0) return "";
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h > 0 ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}` : `${m}:${String(s).padStart(2,"0")}`;
}


// ── Helpers ───────────────────────────────────────────────────
export function esc(s) {
  return (s||"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
export function badgeClass(status) {
  return [`queued`,`downloading`,`converting`,`paused`,`completed`,`failed`,`cancelled`,`skipped`]
    .includes(status) ? `badge-${status}` : "badge-queued";
}
export function formatDuration(secs) {
  if (!secs) return "";
  const h = Math.floor(secs/3600), m = Math.floor((secs%3600)/60), s = secs%60;
  return h > 0
    ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`
    : `${m}:${String(s).padStart(2,"0")}`;
}