// theme.js — dark/light theme toggle.
import { themeBtn } from "./dom.js";
import { ICON } from "./icons.js";

// ── Theme ─────────────────────────────────────────────────────
export function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  themeBtn.innerHTML = t === "dark" ? ICON.moon : ICON.sun;
  localStorage.setItem("v7_theme", t);
}
applyTheme(localStorage.getItem("v7_theme") || "dark");
themeBtn.addEventListener("click", () =>
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark")
);
