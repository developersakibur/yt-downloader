// ui.js — purely cosmetic drawer + contextual sidebar behavior.
// Self-contained: no imports, doesn't touch any app state or API calls.
// (Module scope already isolates this, so the old IIFE wrapper is gone —
// it did the same job a plain module file does for free.)

const sidebar    = document.getElementById("sidebar");
const backdrop    = document.getElementById("drawer-backdrop");
const openBtn     = document.getElementById("new-download-toggle");
const railToggle  = document.getElementById("rail-toggle");
const downloadForm  = document.querySelector(".form-card:not(.converter-card)");
const converterForm = document.querySelector(".form-card.converter-card");

function openDrawer() {
  sidebar.classList.add("open");
  backdrop.classList.add("open");
}
function closeDrawer() {
  sidebar.classList.remove("open");
  backdrop.classList.remove("open");
}
function toggleDrawer() {
  sidebar.classList.contains("open") ? closeDrawer() : openDrawer();
}

openBtn?.addEventListener("click", () => {
  const activeTab = document.querySelector(".tab.active")?.dataset.tab || "queue";
  showFormFor(activeTab);
  openDrawer();
});
railToggle?.addEventListener("click", toggleDrawer);
backdrop?.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
});

// Show only the form relevant to the active tab.
function showFormFor(tabName) {
  const isConvert = tabName === "convert";
  downloadForm?.classList.toggle("hidden", isConvert);
  converterForm?.classList.toggle("hidden", !isConvert);
}

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    showFormFor(tab.dataset.tab);
    if (window.innerWidth <= 1099) openDrawer();
  });
});

// Initial state matches the tab that's active on page load (Queue)
const initialTab = document.querySelector(".tab.active")?.dataset.tab || "queue";
showFormFor(initialTab);

// Close the drawer automatically after a download/convert is kicked off,
// so the user lands back on the job list without an extra tap.
document.getElementById("download-btn")?.addEventListener("click", () => {
  setTimeout(() => { if (window.innerWidth <= 1099) closeDrawer(); }, 300);
});
document.getElementById("convert-btn")?.addEventListener("click", () => {
  setTimeout(() => { if (window.innerWidth <= 1099) closeDrawer(); }, 300);
});
