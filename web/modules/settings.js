// settings.js — Settings tab (server config form) + cookie sync status
// banner ("cookies not synced" / "synced as ...").
import { API, cookieBar, cookieClearBtn, cookieIcon, cookieText } from "./dom.js";
import { convertConcurrencySelect } from "./convert.js";
import { ICON } from "./icons.js";

// ── Settings ──────────────────────────────────────────────────
export async function fetchSettings() {
  try {
    const res  = await fetch(`${API}/api/settings`);
    const data = await res.json();
    const s    = data.settings || {};
    document.getElementById("setting-concurrent").value = s.concurrent_downloads || "3";
    document.getElementById("setting-format").value     = s.default_format || "MP4";
    document.getElementById("setting-converter-concurrency").value = s.converter_concurrency || "2";
    document.getElementById("setting-downloads-folder").value = s.downloads_folder || "";
    convertConcurrencySelect.value = s.converter_concurrency || "2";
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
        converter_concurrency: document.getElementById("setting-converter-concurrency").value,
        downloads_folder:     document.getElementById("setting-downloads-folder").value.trim(),
      }),
    });
    convertConcurrencySelect.value = document.getElementById("setting-converter-concurrency").value;
    feedback.classList.remove("hidden");
    setTimeout(() => feedback.classList.add("hidden"), 2000);
  } catch {}
});

export async function fetchCookieSettings() {
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
export async function fetchCookies() {
  try {
    const res  = await fetch(`${API}/api/cookies/status`);
    const data = await res.json();
    if (data.synced) {
      cookieBar.classList.add("synced");
      cookieIcon.innerHTML = ICON.lock;
      cookieText.textContent = `Cookies synced (${data.cookie_count})`;
      cookieClearBtn.style.display = "block";
    } else {
      cookieBar.classList.remove("synced");
      cookieIcon.innerHTML = ICON.unlock;
      cookieText.textContent = "Cookies not synced";
      cookieClearBtn.style.display = "none";
    }
  } catch {}
}
cookieClearBtn.addEventListener("click", async () => {
  await fetch(`${API}/api/cookies`, {method:"DELETE"});
  fetchCookies();
});
