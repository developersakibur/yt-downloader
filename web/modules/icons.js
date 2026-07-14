// icons.js — shared inline SVG icon set. Replaces emoji/text glyphs
// (⏸ ▶ ↻ ⤼ ✕ 🗑 🔐 🔍 ✓ ⚠️ ◐ ◑) used across the UI with real,
// theme-colored (currentColor) SVGs so they render consistently
// across platforms/fonts instead of relying on emoji fonts.
//
// All icons use a SOLID/FILLED style (single fill="currentColor" path)
// rather than thin outline strokes, so they read clearly at small
// sizes and match at a glance across the whole app.
//
// Each icon is a small <svg class="ico"> string, safe to drop into
// template literals. Sizing/alignment lives in style.css (.ico).

export const ICON = {
  pause: `<svg class="ico" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/><rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/></svg>`,

  play: `<svg class="ico" viewBox="0 0 24 24"><path d="M7 4l14 8-14 8V4z" fill="currentColor"/></svg>`,

  retry: `<svg class="ico" viewBox="0 0 24 24"><path d="M17.65 6.35A7.96 7.96 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z" fill="currentColor"/></svg>`,

  skip: `<svg class="ico" viewBox="0 0 24 24"><path d="M4 5l10 7-10 7V5z" fill="currentColor"/><rect x="17" y="5" width="3" height="14" rx="1" fill="currentColor"/></svg>`,

  cancel: `<svg class="ico" viewBox="0 0 24 24"><path d="M18.3 5.71L12 12.01l-6.3-6.3-1.41 1.41L10.59 13.4l-6.3 6.3 1.41 1.41 6.3-6.3 6.3 6.3 1.41-1.41-6.3-6.3 6.3-6.3z" fill="currentColor"/></svg>`,

  trash: `<svg class="ico" viewBox="0 0 24 24"><path d="M6 19a2 2 0 002 2h8a2 2 0 002-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" fill="currentColor"/></svg>`,

  lock: `<svg class="ico" viewBox="0 0 24 24"><path d="M12 1a5 5 0 00-5 5v3H6a2 2 0 00-2 2v9a2 2 0 002 2h12a2 2 0 002-2v-9a2 2 0 00-2-2h-1V6a5 5 0 00-5-5zm-3 8V6a3 3 0 016 0v3H9z" fill="currentColor"/></svg>`,

  unlock: `<svg class="ico" viewBox="0 0 24 24"><path d="M18 8h-1V6a5 5 0 00-9.9-1h2.1a3 3 0 015.8 1v2H6a2 2 0 00-2 2v9a2 2 0 002 2h12a2 2 0 002-2v-9a2 2 0 00-2-2z" fill="currentColor"/></svg>`,

  search: `<svg class="ico" viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16a6.47 6.47 0 004.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0A4.5 4.5 0 1114 9.5 4.5 4.5 0 019.5 14z" fill="currentColor"/></svg>`,

  check: `<svg class="ico" viewBox="0 0 24 24"><path d="M12 2a10 10 0 100 20 10 10 0 000-20zm-1.2 14.6l-4-4 1.4-1.42 2.6 2.6 5.6-5.6 1.4 1.42-7 7z" fill="currentColor"/></svg>`,

  warning: `<svg class="ico" viewBox="0 0 24 24"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z" fill="currentColor"/></svg>`,

  error: `<svg class="ico" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z" fill="currentColor"/></svg>`,

  sun: `<svg class="ico" viewBox="0 0 24 24"><path d="M6.76 4.84L4.96 3.05 3.55 4.46l1.79 1.79 1.42-1.41zM4 10.5H1v2h3v-2zm9-9.95h-2V3.5h2V.55zm7.45 3.91l-1.41-1.41-1.79 1.79 1.41 1.41 1.79-1.79zM17.24 19.16l1.79 1.8 1.41-1.41-1.8-1.79-1.4 1.4zM20 10.5v2h3v-2h-3zM12 5.5a6 6 0 100 12 6 6 0 000-12zM11 22.45h2V19.5h-2v2.95zm-7.45-3.91l1.41 1.41 1.79-1.8-1.41-1.41-1.79 1.8z" fill="currentColor"/></svg>`,

  moon: `<svg class="ico" viewBox="0 0 24 24"><path d="M20 14.5A8.5 8.5 0 119.5 4 6.8 6.8 0 0020 14.5z" fill="currentColor"/></svg>`,

  chevron: `<svg class="ico" viewBox="0 0 24 24"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z" fill="currentColor"/></svg>`,

  list: `<svg class="ico" viewBox="0 0 24 24"><path d="M4 6h16v2H4V6zm0 5h16v2H4v-2zm0 5h16v2H4v-2z" fill="currentColor"/></svg>`,
};
