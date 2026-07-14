// state.js — the handful of UI state values genuinely shared across
// more than one feature module. Everything else stays as a module-local
// `let` inside the feature file that owns it (see app.js split notes in
// README / commit message) — this file is intentionally tiny.

// Tracks which group cards the person has manually expanded, in both
// the Queue tab and the History tab (History's keys are prefixed
// "h_" so the two don't collide in the same Set).
export const expandedGroups = new Set();
