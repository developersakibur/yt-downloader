// history.js — the History tab: search/filter controls and rendering
// completed/failed jobs (flat or grouped, reusing Queue's renderers).
import { API, historyCount, historyGroupToggle, historyList } from "./dom.js";
import { expandedGroups } from "./state.js";
import { attachActions, jobCardHTML, renderGroupView } from "./queue.js";
import { ICON } from "./icons.js";

historyGroupToggle.addEventListener("click", () => {
  const active = historyGroupToggle.dataset.active === "true";
  historyGroupToggle.dataset.active = (!active).toString();
  historyGroupToggle.classList.toggle("active", !active);
  fetchHistory();
});

// ── History ───────────────────────────────────────────────────
export const historySearchInput  = document.getElementById("history-search");
export const historyStatusFilter = document.getElementById("history-status-filter");
export const historyFormatFilter = document.getElementById("history-format-filter");
export let historySearchTimer = null;

historySearchInput.addEventListener("input", () => {
  clearTimeout(historySearchTimer);
  historySearchTimer = setTimeout(fetchHistory, 300); // debounce typing
});
historyStatusFilter.addEventListener("change", fetchHistory);
historyFormatFilter.addEventListener("change", fetchHistory);

export async function fetchHistory() {
  try {
    const params = new URLSearchParams();
    if (historySearchInput.value.trim()) params.set("search", historySearchInput.value.trim());
    if (historyStatusFilter.value) params.set("status", historyStatusFilter.value);
    if (historyFormatFilter.value) params.set("format", historyFormatFilter.value);
    const res   = await fetch(`${API}/api/history?${params.toString()}`);
    const data  = await res.json();
    const items = data.history || [];
    historyCount.textContent = `${items.length} item${items.length !== 1 ? "s" : ""}`;
    if (!items.length) {
      const isFiltered = params.toString().length > 0;
      historyList.innerHTML = isFiltered
        ? `<div class="empty-state"><div class="empty-icon">${ICON.search}</div><p>No matching downloads.</p></div>`
        : `<div class="empty-state"><div class="empty-icon">📋</div><p>No completed downloads yet.</p></div>`;
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
