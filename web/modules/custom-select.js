// custom-select.js — turns every native <select> in the app into a
// themed dropdown (no more OS-default popup), with zero changes needed
// in any other module.
//
// How it stays in sync with the rest of the app:
//  - The original <select> stays in the DOM (display:none) as the
//    single source of truth. Existing code that reads/sets `.value`,
//    or listens for a "change" event on it, keeps working untouched.
//  - Picking an option in the custom dropdown sets the native
//    select's value and dispatches a real "change" event on it.
//  - A MutationObserver watches each select's <option> children, so
//    if some other module rebuilds them (e.g. quality.js swapping
//    MP4/MP3/3GP options via innerHTML), the custom list rebuilds
//    itself automatically — no coordination needed.
//  - A light poll catches the one case a MutationObserver can't see:
//    other code setting `select.value = "x"` directly (settings.js
//    does this when restoring saved settings) doesn't fire "change"
//    or mutate the DOM, so we just check every 400ms and refresh the
//    trigger label if the value moved.
//
// Note: this restyles the CLOSED box and the OPEN list (both are our
// own markup), which is the part that's normally impossible to skin
// with CSS alone on a native <select>.

const REGISTRY = new Set(); // { select, wrapper, trigger, list, lastValue }

function optionLabel(select) {
  const opt = select.options[select.selectedIndex];
  return opt ? opt.textContent : "";
}

function buildList(entry) {
  const { select, list } = entry;
  list.innerHTML = "";
  Array.from(select.options).forEach((opt, i) => {
    const li = document.createElement("li");
    li.className = "csel-option" + (i === select.selectedIndex ? " selected" : "");
    li.textContent = opt.textContent;
    li.setAttribute("role", "option");
    li.setAttribute("data-index", String(i));
    li.setAttribute("aria-selected", i === select.selectedIndex ? "true" : "false");
    if (opt.disabled) li.classList.add("disabled");
    li.addEventListener("click", () => {
      if (opt.disabled) return;
      selectIndex(entry, i);
      close(entry);
    });
    list.appendChild(li);
  });
}

function selectIndex(entry, i) {
  const { select } = entry;
  if (select.selectedIndex === i) return;
  select.selectedIndex = i;
  syncTrigger(entry);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

function syncTrigger(entry) {
  entry.trigger.querySelector(".csel-label").textContent = optionLabel(entry.select);
  entry.lastValue = entry.select.value;
  entry.list.querySelectorAll(".csel-option").forEach((li, i) => {
    const isSel = i === entry.select.selectedIndex;
    li.classList.toggle("selected", isSel);
    li.setAttribute("aria-selected", isSel ? "true" : "false");
  });
}

function closeAllExcept(except) {
  REGISTRY.forEach(entry => { if (entry !== except) close(entry); });
}

function open(entry) {
  closeAllExcept(entry);
  entry.wrapper.classList.add("open");
  entry.list.hidden = false;
  entry.trigger.setAttribute("aria-expanded", "true");
  const activeLi = entry.list.querySelector(".csel-option.selected") || entry.list.querySelector(".csel-option");
  activeLi?.classList.add("active");
  activeLi?.scrollIntoView({ block: "nearest" });
}

function close(entry) {
  entry.wrapper.classList.remove("open");
  entry.list.hidden = true;
  entry.trigger.setAttribute("aria-expanded", "false");
  entry.list.querySelectorAll(".csel-option.active").forEach(li => li.classList.remove("active"));
}

function moveActive(entry, dir) {
  const items = Array.from(entry.list.querySelectorAll(".csel-option:not(.disabled)"));
  if (!items.length) return;
  const current = entry.list.querySelector(".csel-option.active");
  let idx = current ? items.indexOf(current) : -1;
  idx = (idx + dir + items.length) % items.length;
  current?.classList.remove("active");
  items[idx].classList.add("active");
  items[idx].scrollIntoView({ block: "nearest" });
}

function enhance(select) {
  if (select.dataset.cselEnhanced) return;
  select.dataset.cselEnhanced = "true";

  const wrapper = document.createElement("div");
  wrapper.className = "csel" + (select.classList.contains("quality-select") ? "" : " csel--sm");

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "csel-trigger";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  trigger.innerHTML = `<span class="csel-label"></span><svg class="csel-chevron" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

  const list = document.createElement("ul");
  list.className = "csel-list";
  list.setAttribute("role", "listbox");
  list.hidden = true;

  select.parentNode.insertBefore(wrapper, select);
  wrapper.appendChild(select);
  wrapper.appendChild(trigger);
  wrapper.appendChild(list);
  select.style.display = "none";

  const entry = { select, wrapper, trigger, list, lastValue: select.value };
  REGISTRY.add(entry);
  buildList(entry);
  syncTrigger(entry);

  trigger.addEventListener("click", () => {
    if (select.disabled) return;
    entry.wrapper.classList.contains("open") ? close(entry) : open(entry);
  });
  trigger.addEventListener("keydown", (e) => {
    if (select.disabled) return;
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      if (!entry.wrapper.classList.contains("open")) { open(entry); return; }
    }
    if (e.key === "ArrowDown") moveActive(entry, 1);
    else if (e.key === "ArrowUp") moveActive(entry, -1);
    else if (e.key === "Enter" || e.key === " ") {
      const active = entry.list.querySelector(".csel-option.active");
      if (active) { selectIndex(entry, Number(active.dataset.index)); close(entry); }
    } else if (e.key === "Escape") close(entry);
  });

  // Reflect disabled state on the trigger.
  const reflectDisabled = () => {
    trigger.classList.toggle("disabled", select.disabled);
    trigger.setAttribute("aria-disabled", select.disabled ? "true" : "false");
  };
  reflectDisabled();

  // Options rebuilt elsewhere (e.g. quality.js) -> rebuild our list too.
  new MutationObserver(() => { buildList(entry); syncTrigger(entry); reflectDisabled(); })
    .observe(select, { childList: true, attributes: true, attributeFilter: ["disabled"] });
}

document.addEventListener("click", (e) => {
  const insideAny = [...REGISTRY].some(entry => entry.wrapper.contains(e.target));
  if (!insideAny) REGISTRY.forEach(close);
});

// Catches direct `select.value = "x"` assignments elsewhere in the app,
// which fire neither "change" nor a DOM mutation.
setInterval(() => {
  REGISTRY.forEach(entry => {
    if (entry.select.value !== entry.lastValue) syncTrigger(entry);
  });
}, 400);

export function enhanceAllSelects(root = document) {
  root.querySelectorAll("select").forEach(enhance);
}

enhanceAllSelects();
