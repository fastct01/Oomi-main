// oomi — the window: the sidebar, the workspace of panes (Simple: one conversation; Advanced: splits and tabs),
// the status bar, live events and the keyboard. Conversations themselves are chat.js; dialogs are panels.js.
(() => {
  "use strict";

  const O = window.Oomi;
  const L = window.OomiLayout;
  const { h, icon, avatar, $, S, bot, GET, POST } = O;

  // coalesce repeated redraws into one per frame
  const queued = new Map();
  function soon(key, fn) {
    if (queued.has(key)) return;
    queued.set(key, requestAnimationFrame(() => { queued.delete(key); fn(); }));
  }

  // ---------------------------------------------------------------------------------------------- workspace state
  const W = {
    mode: "simple", // simple | advanced | single (a bot in a window of its own)
    layout: L.group(),
    focus: null, // the pane you're using
    zoom: null, // a pane shown alone for now
    views: new Map(), // pane id -> ChatView
    shells: new Map(), // pane id -> its element
    persist: O.WIN.kind === "main", // only the main window remembers its layout
    sideOrder: [],
  };
  const focusedGroup = () => L.find(W.layout, W.focus) || L.slots(W.layout)[0] || L.groups(W.layout)[0];
  const focusedBot = () => { const g = focusedGroup(); return g ? g.active : null; };
  const focusedView = () => W.views.get((focusedGroup() || {}).id);
  const recentIds = () => S.bots.slice().sort((a, b) => (b.last_at || 0) - (a.last_at || 0)).map((b) => b.id);
  const visibleNow = () => {
    const z = W.zoom && L.find(W.layout, W.zoom);
    return z ? [z.active].filter(Boolean) : L.visibleBots(W.layout);
  };

  function persist() {
    if (!W.persist) return;
    if (W.mode === "advanced") O.saveUi({ layout: W.layout, focus: W.focus });
    else if (W.mode === "simple") O.saveUi({ simple_bot: focusedBot() || null });
  }
  function commit(layout, opts = {}) {
    W.layout = layout;
    if (opts.focus && L.find(layout, opts.focus)) W.focus = opts.focus;
    if (!L.find(layout, W.focus) || L.find(layout, W.focus).minimized) W.focus = (L.slots(layout)[0] || L.groups(layout)[0]).id;
    if (W.zoom && (W.zoom !== W.focus || !L.find(layout, W.zoom) || L.find(layout, W.zoom).minimized)) W.zoom = null;
    persist();
    render();
    if (opts.input !== false) { const v = focusedView(); if (v) v.focusInput(); }
  }

  // ---------------------------------------------------------------------------------------------- drawing the panes
  function shell(gid, view) {
    const el = h("section", { class: "group", dataset: { gid } }, h("span", { class: "slot-num", "aria-hidden": "true" }), view.el);
    el.addEventListener("mousedown", () => focus(gid), true);
    el.addEventListener("focusin", () => focus(gid));
    return el;
  }
  function render() {
    const ws = $("#workspace");
    if (!ws) return;
    const gs = L.groups(W.layout);
    const ids = new Set(gs.map((g) => g.id));
    for (const [gid, v] of W.views) {
      if (ids.has(gid)) continue;
      v.destroy(); W.views.delete(gid);
      const s = W.shells.get(gid); if (s) s.remove(); W.shells.delete(gid);
    }
    for (const v of W.views.values()) v.saveScroll();
    const viewMode = W.mode === "advanced" ? "advanced" : W.mode === "single" ? "single" : "simple";
    for (const g of gs) {
      let v = W.views.get(g.id);
      if (!v) { v = new O.ChatView(g.id, viewMode); W.views.set(g.id, v); W.shells.set(g.id, shell(g.id, v)); }
      v.mode = viewMode;
      if (v.bot !== g.active) v.open(g.active);
      else v.renderHead();
      if (!g.active) v.renderEmpty();
    }
    const zoomed = W.zoom && L.find(W.layout, W.zoom);
    const active = document.activeElement;
    const caret = active && ws.contains(active) && /^(TEXTAREA|INPUT)$/.test(active.tagName)
      ? { el: active, start: active.selectionStart, end: active.selectionEnd } : null;
    ws.replaceChildren(zoomed ? W.shells.get(zoomed.id) : nodeEl(W.layout, [], null));
    const caretPane = caret && caret.el.closest(".group");
    if (caret && caret.el.isConnected && document.activeElement !== caret.el && caretPane && caretPane.dataset.gid === W.focus) {
      caret.el.focus({ preventScroll: true });
      try { caret.el.setSelectionRange(caret.start, caret.end); } catch (_) { /* not a text field */ }
    }
    ws.classList.toggle("multi", !zoomed && L.slots(W.layout).length > 1);
    ws.classList.toggle("zoomed", !!zoomed);
    $("#app").classList.toggle("adv", W.mode === "advanced");
    paintFocus();
    for (const v of W.views.values()) if (v.el.isConnected) v.restoreScroll();
    renderSide();
    renderStatus();
    reportFocus();
    O.drawerFollow();
  }
  function nodeEl(node, path, parentDir) {
    if (node.type === "group") return node.minimized ? railEl(node, parentDir) : W.shells.get(node.id);
    const el = h("div", { class: `split by-${node.dir}` });
    const min = (c) => c.type === "group" && c.minimized;
    node.children.forEach((c, i) => {
      if (i > 0) el.append(min(c) || min(node.children[i - 1]) ? h("div", { class: "gap" }) : resizer(path, i - 1, node.dir));
      const cell = h("div", { class: `cell${min(c) ? " rail-cell" : ""}`, dataset: { i: String(i) } }, nodeEl(c, path.concat(i), node.dir));
      if (!min(c)) cell.style.flex = grow(node.sizes[i]);
      el.append(cell);
    });
    return el;
  }
  // flex-grow below 1 in total leaves space empty (next to a minimized pane), so scale the shares up
  const grow = (size) => `${Math.max(size, 0.001) * 1000} 1 0px`;
  function nodeAt(rootNode, path) {
    let n = rootNode;
    for (const i of path) n = n && n.type === "split" ? n.children[i] : null;
    return n;
  }
  function resizer(path, i, dir) {
    const row = dir === "row";
    const r = h("div", { class: `resizer ${row ? "v" : "h"}`, title: "Drag to resize. Double-click to even out.", role: "separator" });
    r.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const split = nodeAt(W.layout, path);
      if (!split || split.type !== "split") return;
      const cells = [...r.parentElement.children].filter((c) => c.classList.contains("cell"));
      const a = cells.find((c) => c.dataset.i === String(i));
      const b = cells.find((c) => c.dataset.i === String(i + 1));
      if (!a || !b) return;
      const pa = row ? a.offsetWidth : a.offsetHeight;
      const P = pa + (row ? b.offsetWidth : b.offsetHeight);
      const s = split.sizes[i] + split.sizes[i + 1];
      const start = row ? e.clientX : e.clientY;
      const minPx = Math.min(row ? 280 : 170, P / 2);
      const sizes = split.sizes.slice();
      document.body.classList.add(row ? "resizing-v" : "resizing-h");
      r.classList.add("on");
      const move = (ev) => {
        const na = Math.max(minPx, Math.min(P - minPx, pa + (row ? ev.clientX : ev.clientY) - start));
        sizes[i] = (s * na) / P; sizes[i + 1] = s - sizes[i];
        a.style.flex = grow(sizes[i]); b.style.flex = grow(sizes[i + 1]);
      };
      const up = () => {
        removeEventListener("mousemove", move); removeEventListener("mouseup", up);
        document.body.classList.remove("resizing-v", "resizing-h");
        r.classList.remove("on");
        W.layout = L.setSizes(W.layout, path, sizes);
        persist();
      };
      addEventListener("mousemove", move); addEventListener("mouseup", up);
    });
    r.addEventListener("dblclick", () => {
      const split = nodeAt(W.layout, path);
      if (split && split.type === "split") commit(L.setSizes(W.layout, path, split.sizes.map(() => 1 / split.sizes.length)), { input: false });
    });
    return r;
  }
  function railEl(g, parentDir) {
    const across = parentDir === "col"; // a minimized row is a short bar; a minimized column a narrow strip
    const names = g.tabs.map((id) => (bot(id) || {}).name).filter(Boolean).join(", ");
    return h("button", { type: "button", class: `rail ${across ? "across" : "down"}`, title: `Restore ${names}`, dataset: { gid: g.id },
      onclick: () => commit(L.toggleMinimize(W.layout, g.id), { focus: g.id }) },
    g.tabs.map((id) => { const b = bot(id); return b ? h("span", { class: "avatar-wrap xs" }, avatar(b.avatar, "", b.color), O.mark(b, "on-avatar")) : null; }),
    across ? h("span", { class: "rail-name" }, names) : null);
  }
  function paintFocus() {
    for (const [gid, el] of W.shells) el.classList.toggle("focused", gid === W.focus);
    L.slots(W.layout).forEach((g, i) => { const el = W.shells.get(g.id); if (el) $(".slot-num", el).textContent = i < 9 ? String(i + 1) : ""; });
  }
  function focus(gid, opts = {}) {
    const g = L.find(W.layout, gid);
    if (!g || g.minimized) return;
    const changed = W.focus !== gid;
    W.focus = gid;
    if (W.zoom && W.zoom !== gid) { W.zoom = null; render(); } // the pane you're using is always on screen
    if (changed) {
      paintFocus();
      if (W.persist && W.mode === "advanced") O.saveUi({ focus: gid });
      renderSide();
      reportFocus();
      O.drawerFollow();
    }
    if (opts.input) { const v = W.views.get(gid); if (v) v.focusInput(); }
  }

  // ---------------------------------------------------------------------------------------------- opening bots
  function openBot(id, focusItem) {
    if (!bot(id) || W.mode === "single") return;
    const layout = W.mode === "advanced" ? L.open(W.layout, id, W.focus) : L.replace(W.layout, id, W.focus);
    commit(layout, { focus: L.groupOfBot(layout, id).id, input: !focusItem });
    if (focusItem) { const v = focusedView(); if (v) v.open(id, focusItem); }
  }
  // into this pane, wherever it was
  function openIn(gid, id) {
    if (W.mode !== "advanced") return openBot(id);
    commit(L.move(W.layout, id, gid, "center"), { focus: gid });
  }
  // a new pane beside the one you're using
  function openBeside(id, side = "right") {
    if (W.mode !== "advanced") setMode("advanced");
    const next = L.splitAt(W.layout, W.focus, side, id);
    if (next === W.layout) return L.groupOfBot(W.layout, id) ? openBot(id) : O.toast(`Up to ${L.MAX_GROUPS} panes`);
    commit(next, { focus: L.groupOfBot(next, id).id });
  }
  function addPane(side, gid = W.focus) {
    if (W.mode === "single") return;
    if (W.mode !== "advanced") setMode("advanced");
    const before = new Set(L.groups(W.layout).map((g) => g.id));
    const next = L.addEmpty(W.layout, gid === W.focus ? W.focus : gid, side);
    if (next === W.layout) return O.toast(`Up to ${L.MAX_GROUPS} panes`);
    const fresh = L.groups(next).find((g) => !before.has(g.id));
    commit(next, { focus: fresh ? fresh.id : null });
  }
  function closeTab(id) {
    if (W.mode !== "advanced") return;
    commit(L.remove(W.layout, id));
  }
  function closePane(gid) { commit(L.closeGroup(W.layout, gid)); }
  function minimize(gid = W.focus) {
    const next = L.toggleMinimize(W.layout, gid);
    if (next === W.layout) return O.toast("One pane always stays open.");
    commit(next);
  }
  function toggleZoom(gid = W.focus) {
    if (W.mode !== "advanced") return;
    if (!W.zoom && L.slots(W.layout).length < 2) return;
    W.zoom = W.zoom ? null : gid;
    render();
    const v = focusedView(); if (v) v.focusInput();
  }
  function cycleTab(d) {
    const g = focusedGroup();
    if (!g || g.tabs.length < 2) return;
    const i = (g.tabs.indexOf(g.active) + d + g.tabs.length) % g.tabs.length;
    commit(L.activate(W.layout, g.id, g.tabs[i]));
  }
  function moveFocus(dir) {
    const cur = W.shells.get(W.focus);
    if (!cur || !cur.isConnected) return;
    const r0 = cur.getBoundingClientRect();
    const cx = (r0.left + r0.right) / 2, cy = (r0.top + r0.bottom) / 2;
    let best = null, bestD = Infinity;
    for (const g of L.slots(W.layout)) {
      const el = W.shells.get(g.id);
      if (g.id === W.focus || !el || !el.isConnected) continue;
      const r = el.getBoundingClientRect();
      const ok = { left: r.right <= r0.left + 2, right: r.left >= r0.right - 2, up: r.bottom <= r0.top + 2, down: r.top >= r0.bottom - 2 }[dir];
      if (!ok) continue;
      const x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
      const d = dir === "left" || dir === "right" ? Math.abs(x - cx) + 2 * Math.abs(y - cy) : Math.abs(y - cy) + 2 * Math.abs(x - cx);
      if (d < bestD) { bestD = d; best = g.id; }
    }
    if (best) focus(best, { input: true });
  }
  function setMode(m) {
    if (W.mode === m || W.mode === "single" || !["simple", "advanced"].includes(m)) return;
    const cur = focusedBot();
    if (m === "advanced") {
      const ids = S.bots.map((b) => b.id);
      let layout = (W.persist && L.load(S.ui.layout, ids)) || (W.advanced && L.load(W.advanced, ids)) || L.group();
      if (cur) layout = L.open(layout, cur);
      W.mode = m;
      W.layout = layout;
      W.focus = cur ? L.groupOfBot(layout, cur).id : (L.slots(layout)[0] || L.groups(layout)[0]).id;
    } else {
      W.advanced = W.layout;
      W.mode = m;
      W.zoom = null;
      W.layout = L.group(cur ? [cur] : recentIds().slice(0, 1));
      W.focus = W.layout.id;
    }
    if (W.persist) O.saveUi({ mode: m });
    persist();
    render();
    const v = focusedView(); if (v) v.focusInput();
    O.refreshSettings("display");
  }
  function applyShape(shape) {
    if (W.mode !== "advanced") setMode("advanced");
    W.zoom = null;
    commit(L.applyShape(W.layout, shape, recentIds()));
  }
  function saveLayout() {
    O.promptBox("Save this layout", "Saves the arrangement of panes, not which bots are in them. Pick it again from Layout.", "", "Save", (name) => {
      if (!name) throw new Error("Give it a name.");
      const list = O.savedLayouts().filter((x) => x.name !== name);
      list.push({ name, shape: L.geometry(W.layout) });
      O.saveUi({ layouts: list.slice(-20) }, true);
      O.toast(`Saved ${name}`);
    }, { placeholder: "Like Three projects", max: 40 });
  }
  function manageLayouts() {
    O.modal((box, close) => {
      const paint = () => {
        const list = O.savedLayouts();
        box.replaceChildren(h("h3", {}, "Saved layouts"), list.length
          ? h("div", { class: "list" }, list.map((x) => h("div", { class: "li" }, icon("layers", 16), h("div", { class: "grow" }, x.name),
            h("div", { class: "mini" }, h("button", { type: "button", onclick: () => { O.saveUi({ layouts: list.filter((y) => y !== x) }, true); paint(); } }, "Delete")))))
          : h("p", { class: "lead" }, "None left."),
        h("div", { class: "foot" }, h("button", { type: "button", class: "btn", onclick: close }, "Done")));
      };
      paint();
    });
  }
  const PRESET_ICONS = { single: "lay1", columns2: "lay2c", rows2: "lay2r", columns3: "lay3c", main_side: "layMain", grid4: "lay4", grid6: "lay6" };
  function layoutMenu(anchor) {
    const saved = O.savedLayouts();
    O.openMenu(anchor, [
      { head: "Arrange" },
      ...Object.entries(L.PRESETS).map(([k, p]) => ({ label: p.name, icon: PRESET_ICONS[k] || "grid", run: () => applyShape(p.shape()) })),
      saved.length ? "-" : null, saved.length ? { head: "Saved" } : null,
      ...saved.map((x) => ({ label: x.name, icon: "layers", run: () => { const sh = L.loadShape(x.shape); if (sh) applyShape(sh); } })),
      saved.length ? { label: "Delete a saved layout…", icon: "trash", run: manageLayouts } : null,
      "-",
      { label: "Even out sizes", icon: "grid", run: () => commit(L.equalize(W.layout)) },
      { label: "Flip rows and columns", icon: "flip", keys: "⌘⇧F", run: () => commit(L.flip(W.layout)) },
      O.WIN.kind !== "bot" ? { label: "New window", icon: "popout", run: O.newWindow } : null,
    ], { up: true, align: "right" });
  }
  function paneMenu(anchor, gid) {
    const g = L.find(W.layout, gid);
    if (!g) return;
    focus(gid);
    const several = L.slots(W.layout).length > 1;
    O.openMenu(anchor, [
      { label: "Split right", icon: "splitR", keys: "⌘\\", run: () => addPane("right", gid) },
      { label: "Split down", icon: "splitD", keys: "⌘⇧\\", run: () => addPane("bottom", gid) },
      g.tabs.length && several ? { label: "Minimize", icon: "minimize", keys: "⌘⇧M", run: () => minimize(gid) } : null,
      several || W.zoom ? { label: W.zoom === gid ? "Zoom out" : "Zoom in", icon: "zoom", keys: "⌘⇧↩", run: () => toggleZoom(gid) } : null,
      g.active ? { label: "Open in its own window", icon: "popout", keys: "⌘⇧O", run: () => O.popOut(g.active) } : null,
      "-",
      g.active ? { label: "Close tab", icon: "x", keys: "⌘W", run: () => closeTab(g.active) } : null,
      L.groups(W.layout).length > 1 ? { label: "Close pane", icon: "x", run: () => closePane(gid) } : null,
    ], { align: "right" });
  }
  function tabMenu(gid, id) {
    const g = L.find(W.layout, gid);
    const mv = (side) => () => {
      const next = L.splitAt(W.layout, gid, side, id);
      if (next === W.layout) return O.toast(g.tabs.length < 2 ? "It's the only tab in this pane." : `Up to ${L.MAX_GROUPS} panes`);
      commit(next, { focus: L.groupOfBot(next, id).id });
    };
    return [
      { label: "Move to a new pane on the right", icon: "splitR", run: mv("right") },
      { label: "Move to a new pane below", icon: "splitD", run: mv("bottom") },
      O.WIN.kind !== "bot" ? { label: "Open in its own window", icon: "popout", run: () => O.popOut(id) } : null,
      "-",
      { label: "Close tab", icon: "x", run: () => closeTab(id) },
      g && g.tabs.length > 1 ? { label: "Close other tabs", run: () => { let t = W.layout; for (const x of g.tabs) if (x !== id) t = L.remove(t, x); commit(t); } } : null,
    ];
  }
  // the tabs at the top of a pane (Advanced)
  function tabStrip(gid) {
    const g = L.find(W.layout, gid);
    const strip = h("div", { class: "tabs", role: "tablist", dataset: { gid } });
    if (!g) return strip;
    for (const id of g.tabs) {
      const b = bot(id);
      if (!b) continue;
      strip.append(h("div", {
        class: `tab${id === g.active ? " on" : ""}`, draggable: "true", role: "tab", "aria-selected": id === g.active ? "true" : "false", title: b.step && b.status !== "idle" ? `${b.name}: ${b.step}` : b.name, dataset: { bot: id },
        onclick: (e) => { if (!e.target.closest(".tab-x")) commit(L.activate(W.layout, gid, id), { focus: gid }); },
        onauxclick: (e) => { if (e.button === 1) { e.preventDefault(); closeTab(id); } },
        oncontextmenu: (e) => O.contextMenu(e, tabMenu(gid, id)),
        ondragstart: (e) => startDrag(e, id, gid),
        ondragend: endDrag,
      },
      h("span", { class: "avatar-wrap xs" }, avatar(b.avatar, "", b.color), O.mark(b, "on-avatar")),
      h("span", { class: "tab-name" }, b.name),
      h("button", { type: "button", class: "tab-x", title: "Close tab", "aria-label": `Close ${b.name}`, onclick: (e) => { e.stopPropagation(); closeTab(id); } }, icon("x", 12))));
    }
    strip.append(h("button", { type: "button", class: "tab-add", title: "Open a bot in this pane", "aria-label": "Open a bot here",
      onclick: () => O.palette({ placeholder: "Open which bot in this pane?", pick: (id) => openIn(gid, id), allowNew: true }) }, icon("plus", 14)));
    return strip;
  }

  // ---------------------------------------------------------------------------------------------- drag and drop
  // Bots dragged from the sidebar or a tab: the middle of a pane adds a tab there, an edge splits the pane.
  function startDrag(e, id, from) {
    S.drag = { bot: id, from };
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", (bot(id) || {}).name || id); } catch (_) { /* some browsers are fussy */ }
    document.body.classList.add("dragging-bot");
  }
  function endDrag() {
    S.drag = null;
    $("#drop-hint").classList.add("hidden");
    document.body.classList.remove("dragging-bot");
    O.$$(".drop-above, .drop-below, .drop-into").forEach((x) => x.classList.remove("drop-above", "drop-below", "drop-into"));
  }
  function zoneAt(e) {
    const gEl = e.target.closest && e.target.closest(".group");
    if (!gEl || W.mode === "single") return null;
    const gid = gEl.dataset.gid;
    const r = gEl.getBoundingClientRect();
    const tabs = e.target.closest(".tabs");
    if (tabs && W.mode === "advanced") {
      const items = [...tabs.querySelectorAll(".tab")];
      let index = items.length;
      for (let i = 0; i < items.length; i++) { const tr = items[i].getBoundingClientRect(); if (e.clientX < tr.left + tr.width / 2) { index = i; break; } }
      return { gid, zone: "center", index, rect: r };
    }
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    const m = Math.min(x, 1 - x, y, 1 - y);
    let zone = "center";
    if (m < 0.24) zone = m === x ? "left" : m === 1 - x ? "right" : m === y ? "top" : "bottom";
    return { gid, zone, rect: r };
  }
  function showHint(t) {
    const el = $("#drop-hint");
    let { left, top, width, height } = t.rect;
    if (t.zone === "left") width /= 2;
    else if (t.zone === "right") { left += width / 2; width /= 2; }
    else if (t.zone === "top") height /= 2;
    else if (t.zone === "bottom") { top += height / 2; height /= 2; }
    Object.assign(el.style, { left: `${left + 6}px`, top: `${top + 6}px`, width: `${width - 12}px`, height: `${height - 12}px` });
    el.textContent = t.zone !== "center" ? "New pane" : W.mode === "advanced" ? "Add as a tab" : "Show here";
    el.classList.remove("hidden");
  }
  function dropBot(id, t) {
    if (!bot(id)) return;
    if (W.mode === "simple") {
      if (t.zone === "center") return openBot(id);
      setMode("advanced"); // splitting needs the advanced layout
      t.gid = W.focus;
    }
    const next = L.move(W.layout, id, t.gid, t.zone, t.index);
    if (next === W.layout) return t.zone !== "center" && !L.groupOfBot(W.layout, id) ? O.toast(`Up to ${L.MAX_GROUPS} panes`) : null;
    commit(next, { focus: L.groupOfBot(next, id).id });
  }
  function wireDrops() {
    const ws = $("#workspace");
    ws.addEventListener("dragover", (e) => {
      if (!S.drag) return;
      const t = zoneAt(e);
      if (!t) { $("#drop-hint").classList.add("hidden"); return; }
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      showHint(t);
    });
    ws.addEventListener("dragleave", (e) => { if (S.drag && !ws.contains(e.relatedTarget)) $("#drop-hint").classList.add("hidden"); });
    ws.addEventListener("drop", (e) => {
      if (!S.drag) return;
      e.preventDefault();
      const t = zoneAt(e);
      const id = S.drag.bot;
      endDrag();
      if (t) dropBot(id, t);
    });
    // the sidebar: drop on a section to move a bot there; in your own order, drop between bots to reorder
    const list = $("#bot-list");
    const clear = () => O.$$(".drop-above, .drop-below, .drop-into", list).forEach((x) => x.classList.remove("drop-above", "drop-below", "drop-into"));
    list.addEventListener("dragover", (e) => {
      if (!S.drag || S.drag.from !== null) return;
      clear();
      const sec = e.target.closest(".side-sec");
      const row = e.target.closest(".bot-row");
      if (sec && sec.dataset.section !== undefined) { e.preventDefault(); sec.classList.add("drop-into"); return; }
      if (row && O.pref("side_sort") === "manual" && row.dataset.bot !== S.drag.bot) {
        e.preventDefault();
        const r = row.getBoundingClientRect();
        row.classList.add(e.clientY < r.top + r.height / 2 ? "drop-above" : "drop-below");
      }
    });
    list.addEventListener("dragleave", (e) => { if (!list.contains(e.relatedTarget)) clear(); });
    list.addEventListener("drop", async (e) => {
      if (!S.drag || S.drag.from !== null) return;
      const id = S.drag.bot;
      const sec = e.target.closest(".side-sec.drop-into");
      const row = e.target.closest(".bot-row.drop-above, .bot-row.drop-below");
      e.preventDefault();
      const below = row && row.classList.contains("drop-below");
      endDrag();
      try {
        if (sec) { await O.setBot({ section: sec.dataset.section, pinned: sec.dataset.section === "" && sec.dataset.key === "pinned" }, id); return; }
        if (!row) return;
        const target = bot(row.dataset.bot);
        const order = W.sideOrder.filter((x) => x !== id);
        order.splice(order.indexOf(target.id) + (below ? 1 : 0), 0, id);
        const patch = { order: 0 };
        if (O.pref("side_group") === "section" && !target.pinned) patch.section = target.section || "";
        if (!!target.pinned !== !!(bot(id) || {}).pinned) patch.pinned = !!target.pinned;
        // number them all in their new order (few bots; only changed ones are sent)
        for (let i = 0; i < order.length; i++) {
          const b = bot(order[i]);
          if (!b) continue;
          if (b.id === id) await O.setBot({ ...patch, order: i + 1 }, id);
          else if (b.order !== i + 1) await O.setBot({ order: i + 1 }, b.id);
        }
      } catch (er) { O.fail(er); }
    });
  }

  // ---------------------------------------------------------------------------------------------- sidebar
  function sideGroups() {
    const by = O.pref("side_group");
    const sort = O.pref("side_sort");
    const recent = (a, b) => (b.last_at || 0) - (a.last_at || 0);
    const cmp = { name: (a, b) => a.name.localeCompare(b.name), cost: (a, b) => (b.cost || 0) - (a.cost || 0) || recent(a, b),
      manual: (a, b) => (a.order || 1e9) - (b.order || 1e9) || recent(a, b) }[sort] || recent;
    const bots = S.bots.slice().sort(cmp);
    const pinned = bots.filter((b) => b.pinned);
    const rest = bots.filter((b) => !b.pinned);
    const out = [];
    if (pinned.length) out.push({ key: "pinned", title: "Pinned", section: undefined, bots: pinned });
    if (by === "status") {
      for (const [s, t] of [["waiting", "Needs you"], ["working", "Working"], ["unread", "Unread"], ["", "Quiet"]]) {
        const bs = rest.filter((b) => O.statusOf(b) === s);
        if (bs.length) out.push({ key: `st-${s}`, title: t, bots: bs });
      }
    } else if (by === "folder") {
      const key = (b) => (b.private_folder ? "Their own folders" : O.tildify(String(b.folder).replace(/\/[^/]*\/?$/, "")) || "/");
      for (const k of [...new Set(rest.map(key))].sort()) out.push({ key: `f-${k}`, title: k, bots: rest.filter((b) => key(b) === k) });
    } else if (by === "section") {
      const names = [...new Set(rest.map((b) => b.section).filter(Boolean))].sort((a, b) => a.localeCompare(b));
      for (const nm of names) out.push({ key: `s-${nm}`, title: nm, section: nm, bots: rest.filter((b) => b.section === nm) });
      const none = rest.filter((b) => !b.section);
      if (none.length || names.length) out.push({ key: "s-", title: names.length || pinned.length ? "Bots" : null, section: "", bots: none });
    } else out.push({ key: "all", title: pinned.length ? "Bots" : null, bots: rest });
    return out;
  }
  const renderSide = () => soon("side", drawSide);
  function drawSide() {
    const list = $("#bot-list");
    if (!list) return;
    const fb = focusedBot();
    const inLayout = new Set(W.mode === "advanced" ? L.allBots(W.layout) : []);
    const seen = new Set(visibleNow());
    const collapsed = new Set(O.pref("side_collapsed") || []);
    const scroll = list.scrollTop;
    list.replaceChildren();
    W.sideOrder = [];
    if (!S.bots.length) list.append(h("div", { class: "empty-note" }, "No bots yet."));
    for (const grp of sideGroups()) {
      const closed = collapsed.has(grp.key);
      if (grp.title !== null) {
        list.append(h("button", { type: "button", class: `side-sec${closed ? " closed" : ""}`, dataset: grp.section !== undefined ? { section: grp.section, key: grp.key } : { key: grp.key },
          onclick: () => { const c = new Set(O.pref("side_collapsed") || []); if (c.has(grp.key)) c.delete(grp.key); else c.add(grp.key); O.setPref("side_collapsed", [...c]); renderSide(); } },
        icon("down", 12), h("span", { class: "grow" }, grp.title), h("span", { class: "n" }, String(grp.bots.length))));
      }
      if (closed) continue;
      for (const b of grp.bots) {
        W.sideOrder.push(b.id);
        list.append(sideRow(b, W.sideOrder.length, b.id === fb, inLayout.has(b.id), seen.has(b.id)));
      }
    }
    list.scrollTop = scroll;
    O.renderFoot();
  }
  function sideRow(b, n, active, inLayout, seen) {
    const st = O.statusOf(b);
    let preview = O.plain(b.preview) || O.tildify(b.folder);
    let live = false;
    if (b.status === "waiting") { preview = "Needs your OK"; live = true; }
    else if (b.status !== "idle") { preview = b.step || "Working…"; live = true; }
    return h("button", {
      type: "button", draggable: "true", class: `bot-row${active ? " active" : ""}${b.unread ? " unread" : ""}${inLayout ? " in-layout" : ""}`, dataset: { bot: b.id },
      title: W.mode === "advanced" ? "Click to open. ⌘-click for a new pane. Drag onto a pane to place it." : "",
      onclick: (e) => { if ((e.metaKey || e.ctrlKey) && W.mode !== "single") openBeside(b.id); else openBot(b.id); },
      onauxclick: (e) => { if (e.button === 1) { e.preventDefault(); openBeside(b.id); } },
      oncontextmenu: (e) => { e.preventDefault(); O.botMenu(null, b.id, null, { x: e.clientX, y: e.clientY }); },
      ondragstart: (e) => startDrag(e, b.id, null),
      ondragend: endDrag,
    },
    h("span", { class: "avatar-wrap" }, avatar(b.avatar, "", b.color), st === "working" ? h("span", { class: "state working" }) : st === "waiting" ? h("span", { class: "state waiting" }) : null),
    h("span", { class: "who" }, h("div", { class: "name" }, b.pinned ? h("span", { class: "pinned", title: "Pinned" }, icon("pin", 11, 2)) : null, h("span", { class: "nm" }, b.name)),
      h("div", { class: `preview${live ? " live" : ""}` }, preview)),
    h("span", { class: "meta" }, h("span", { class: "time" }, O.sideTime(b.last_at)), b.unread && !seen ? h("span", { class: "dot", title: "Unread" }) : null),
    n <= 9 && W.mode === "simple" ? h("span", { class: "slot-key" }, String(n)) : null);
  }
  O.renderFoot = function renderFoot() {
    const sub = $("#me-sub");
    if (sub) sub.textContent = S.memoryReview ? String(S.memoryReview) : "";
    const me = $("#btn-settings");
    if (me) me.title = `Settings (⌘,)${S.memoryReview ? ` · ${O.plural(S.memoryReview, "1 new memory", "N new memories")} to review` : ""}`;
    if (O.renderSheetNav) O.renderSheetNav();
  };
  function sideMenu(anchor) {
    const g = O.pref("side_group"), s = O.pref("side_sort");
    const pick = (k, v) => () => { O.setPref(k, v); renderSide(); };
    O.openMenu(anchor, [
      { head: "Group by" },
      ...[["section", "Section"], ["status", "What they're doing"], ["folder", "Folder"], ["none", "Nothing"]].map(([k, l]) => ({ label: l, on: g === k, run: pick("side_group", k) })),
      "-", { head: "Sort by" },
      ...[["recent", "Most recent"], ["name", "Name"], ["cost", "Spending"], ["manual", "Your order", "Drag bots to arrange them"]].map(([k, l, d]) => ({ label: l, desc: d, on: s === k, run: pick("side_sort", k) })),
      "-",
      { label: "Mark all read", icon: "check", run: () => POST("bots/read-all").catch(O.fail) },
      { label: "Import a bot…", icon: "upload", run: O.importBot },
      { label: "Overview", icon: "grid", keys: "⌘.", run: O.openOverview },
      O.WIN.kind === "main" ? { label: "New window", icon: "popout", run: O.newWindow } : null,
      { label: "Hide sidebar", icon: "side", keys: "⌘B", run: toggleSidebar },
    ], { align: "left" });
  }
  function toggleSidebar() {
    if (W.mode === "single") return;
    const hidden = !O.pref("sidebar_hidden");
    O.setPref("sidebar_hidden", hidden);
    $("#app").classList.toggle("side-hidden", hidden);
    renderStatus();
  }

  // ---------------------------------------------------------------------------------------------- status bar
  const renderStatus = () => soon("status", drawStatus);
  function drawStatus() {
    const bar = $("#statusbar");
    const working = S.bots.filter((b) => b.status === "working" || b.status === "stopping").length;
    const waiting = S.bots.filter((b) => b.status === "waiting").length;
    const unread = S.bots.filter((b) => b.unread).length;
    document.title = W.mode === "single" ? `${(bot(focusedBot()) || {}).name || "oomi"}${waiting ? " (needs you)" : ""}` : waiting ? `oomi (${waiting} waiting)` : "oomi";
    if (!bar || W.mode === "single") return;
    const spent = S.bots.reduce((a, b) => a + (b.cost || 0), 0);
    const left = h("div", { class: "sb-left" },
      O.pref("sidebar_hidden") ? h("button", { type: "button", class: "sb-item icon", title: "Show sidebar (⌘B)", onclick: toggleSidebar }, icon("side", 14)) : null,
      h("button", { type: "button", class: "sb-item", title: "Overview of every bot (⌘.)", onclick: O.toggleOverview }, (() => {
        const parts = [];
        const add = (cls, text) => parts.push(parts.length ? h("span", { class: "sep-dot" }) : null, h("span", { class: `mark ${cls}` }), text);
        if (waiting) add("waiting", `${waiting} waiting for you`);
        if (working) add("working", `${working} working`);
        if (unread) add("unread", `${unread} unread`);
        if (!parts.length) add("idle", "Nothing running");
        return parts;
      })()));
    const right = h("div", { class: "sb-right" });
    if (spent) right.append(h("span", { class: "sb-item quiet", title: "Spent today" }, O.money(spent)));
    if (W.mode === "advanced" && W.zoom) right.append(h("button", { type: "button", class: "sb-item on", title: "Zoom out (⌘⇧↩)", onclick: () => toggleZoom() }, icon("zoom", 13), "Zoomed in"));
    if (W.mode !== "single") right.append(h("button", { type: "button", class: "sb-item", title: "Arrange panes", onclick: (e) => layoutMenu(e.currentTarget) }, icon("grid", 13), "Layout"));
    bar.replaceChildren(left, right);
  }

  // ---------------------------------------------------------------------------------------------- bots
  O.botMenu = function botMenu(anchor, id, view, pos) {
    const b = bot(id);
    if (!b) return;
    const show = (name) => () => { reveal(id); O.openDrawer(name); };
    const entries = [
      { label: "Bot settings", icon: "gear", run: show("bot") },
      { label: "Changes", icon: "changes", run: show("changes") },
      { label: "Start fresh", icon: "fresh", desc: "New conversation, same folder", run: () => O.freshBot(id) },
      "-",
      O.WIN.kind !== "bot" ? { label: "Open in its own window", icon: "popout", run: () => O.popOut(id) } : null,
      { label: b.pinned ? "Unpin" : "Pin to the top", icon: "pin", run: () => O.setBot({ pinned: !b.pinned }, id).catch(O.fail) },
      { label: "Duplicate", icon: "copy", run: () => O.cloneBot(id) },
      { label: "Export…", icon: "download", run: () => O.exportBot(id) },
    ];
    if (S.native.reveal || O.MAC) {
      entries.push("-", { label: "Show folder", icon: "folder", run: () => POST(`bots/${id}/reveal`).catch(O.fail) });
    }
    entries.push("-", { label: "Delete bot", icon: "trash", delete: true, run: () => O.deleteBot(id) });
    if (pos) O.openMenu(null, entries, pos);
    else O.openMenu(anchor, entries, { align: anchor && anchor.classList.contains("name-pill") ? "center" : "right" });
  };
  // make sure a bot is on screen and is the one you're using
  function reveal(id) {
    const g = L.groupOfBot(W.layout, id);
    if (g && g.active === id && !g.minimized) focus(g.id);
    else openBot(id);
  }
  O.updateBot = function updateBot(st, allowNew = false) {
    const i = S.bots.findIndex((b) => b.id === st.id);
    const before = i >= 0 ? S.bots[i] : null;
    if (i >= 0) S.bots[i] = st;
    else if (allowNew) S.bots.push(st);
    else return;
    for (const v of W.views.values()) {
      if (v.bot === st.id) v.botChanged(before, st);
      else if (!v.bot && !before) v.renderEmpty();
    }
    if (W.mode === "advanced") {
      for (const g of L.groups(W.layout)) {
        if (!g.tabs.includes(st.id)) continue;
        if (g.minimized) {
          const old = $(`.rail[data-gid="${g.id}"]`);
          if (old) old.replaceWith(railEl(g, old.classList.contains("across") ? "col" : "row"));
        }
        else { const v = W.views.get(g.id); if (v && v.bot !== st.id) v.renderHead(); }
      }
    }
    renderSide();
    renderStatus();
    soon("over", O.renderOverview);
  };
  O.botGone = function botGone(id) {
    S.bots = S.bots.filter((x) => x.id !== id);
    if (S.drawerBot === id) O.closeDrawer();
    if (W.mode === "single") {
      $("#workspace").replaceChildren(h("div", { class: "welcome-inner gone" }, h("h1", {}, "This bot was deleted."), h("p", {}, "You can close this window.")));
      return;
    }
    commit(L.remove(W.layout, id), { input: false });
    soon("over", O.renderOverview);
    if (!S.bots.length && O.WIN.kind === "main") O.showWelcome("bot");
  };
  O.commands = function commands() {
    const adv = W.mode === "advanced";
    const b = bot(focusedBot());
    const out = [
      { icon: "plus", label: "New bot", keys: "⌘N", run: () => O.newBot() },
      { icon: "grid", label: "Overview of every bot", keys: "⌘.", words: "dashboard status all approvals", run: O.openOverview },
      { icon: "splitR", label: adv ? "Switch to the Simple layout" : "Switch to the Advanced layout", words: "mode panes split tabs workspace", run: () => setMode(adv ? "simple" : "advanced") },
      { icon: "splitR", label: "Split right", keys: "⌘\\", words: "pane", run: () => addPane("right") },
      { icon: "splitD", label: "Split down", keys: "⌘⇧\\", words: "pane", run: () => addPane("bottom") },
    ];
    if (adv) {
      out.push(
        { icon: "flip", label: "Flip rows and columns", keys: "⌘⇧F", run: () => commit(L.flip(W.layout)) },
        { icon: "grid", label: "Even out pane sizes", run: () => commit(L.equalize(W.layout)) },
        { icon: "zoom", label: W.zoom ? "Zoom out" : "Zoom in on this pane", keys: "⌘⇧↩", run: () => toggleZoom() },
        { icon: "download", label: "Save this layout", run: saveLayout },
        ...Object.values(L.PRESETS).map((p) => ({ icon: "grid", label: `Layout: ${p.name}`, words: "arrange preset", run: () => applyShape(p.shape()) })),
        ...O.savedLayouts().map((x) => ({ icon: "layers", label: `Layout: ${x.name}`, words: "saved arrange", run: () => { const sh = L.loadShape(x.shape); if (sh) applyShape(sh); } })));
    }
    if (b) {
      out.push(
        { icon: "gear", label: `Bot settings: ${b.name}`, words: "instructions model trust allowlist", run: () => O.openDrawer("bot") },
        { icon: "changes", label: `Changes: ${b.name}`, words: "undo diff files", run: () => O.openDrawer("changes") },
        { icon: "fresh", label: `Start fresh: ${b.name}`, words: "new conversation clear", run: () => O.freshBot(b.id) });
      if (O.WIN.kind !== "bot") out.push({ icon: "popout", label: `Own window: ${b.name}`, keys: "⌘⇧O", words: "pop out", run: () => O.popOut(b.id) });
    }
    out.push(
      O.WIN.kind !== "bot" ? { icon: "popout", label: "New window", run: O.newWindow } : null,
      { icon: "stop", label: "Stop every bot", words: "halt all cancel", run: () => POST("bots/stop-all").then((r) => O.toast(r.stopped ? `Stopped ${O.plural(r.stopped, "1 bot", "N bots")}` : "Nothing was running")).catch(O.fail) },
      { icon: "check", label: "Mark all read", run: () => POST("bots/read-all").catch(O.fail) },
      { icon: "upload", label: "Import a bot", run: O.importBot },
      { icon: "side", label: "Show or hide the sidebar", keys: "⌘B", run: toggleSidebar },
      { icon: "gear", label: "Settings", keys: "⌘,", run: () => O.openSettings("providers") },
      { icon: "eye", label: "Settings: Display", words: "text size density width timestamps", run: () => O.openSettings("display") },
      { icon: "bell", label: "Settings: Notifications", words: "sound alerts", run: () => O.openSettings("notifications") },
      { icon: "lock", label: "Settings: Safety and limits", words: "block list protected secrets timeout", run: () => O.openSettings("safety") },
      { icon: "memory", label: "Settings: Memory", run: () => O.openSettings("memory") },
      { icon: "skills", label: "Settings: Skills", run: () => O.openSettings("skills") },
      { icon: "pulse", label: "Settings: Status and spending", words: "usage tokens cost", run: () => O.openSettings("status") },
      { icon: "keyboard", label: "Keyboard shortcuts", keys: "⌘/", run: () => O.openSettings("shortcuts") });
    return out.filter(Boolean);
  };

  // ---------------------------------------------------------------------------------------------- live events
  function connect() {
    if (S.es) S.es.close();
    const es = new EventSource(`/api/events?t=${encodeURIComponent(O.TOKEN)}&since=${S.seq}`);
    S.es = es;
    es.onmessage = (m) => {
      let ev;
      try { ev = JSON.parse(m.data); } catch (_) { return; }
      S.seq = ev.seq || S.seq;
      handle(ev);
    };
    es.addEventListener("reset", () => reloadAll());
  }
  const FOR_VIEWS = new Set(["delta", "drop", "thinking", "item", "turn_end"]);
  function handle(ev) {
    if (ev.bot && FOR_VIEWS.has(ev.type)) {
      for (const v of W.views.values()) if (v.bot === ev.bot) v.handle(ev);
      if (ev.type === "turn_end") O.refreshDrawer("changes", ev.bot);
      return;
    }
    switch (ev.type) {
      case "bot": if (ev.bot_state) O.updateBot(ev.bot_state); break;
      case "bots": reloadBots(); break;
      case "memory_changed": refreshMemoryCount(); if (!ev.bot) O.refreshSettings("memory"); break;
      case "antibodies": O.refreshSettings("antibodies"); break;
      case "apps":
        if (ev.message) O.toast(ev.message, ev.ok ? 3000 : 6000);
        O.refreshSettings("apps");
        O.refreshDrawer("bot");
        break;
      case "notify": browserNotify(ev); break;
      case "ui": GET("ui").then((ui) => { O.remoteUi(ui); renderSide(); }).catch(() => {}); break;
      default:
        // schedules changed or ran: refresh the list if it's showing
        if (/schedule/.test(ev.type || "") && S.schedules && (!ev.bot || ev.bot === S.schedules.bot)) S.schedules.refresh();
        break;
    }
  }
  // In a browser (not the Mac app, which shows its own), one window shows notifications.
  function browserNotify(ev) {
    if (S.native.windows || O.WIN.kind !== "main" || !("Notification" in window) || Notification.permission !== "granted") return;
    const s = S.settings;
    if (ev.kind !== "test" && (!s.notify || s[`notify_${ev.kind}`] === false)) return;
    try {
      const n = new Notification(ev.title || "oomi", { body: ev.body || "", tag: `oomi-${ev.bot || ev.kind}` });
      n.onclick = () => { window.focus(); if (ev.bot) openBot(ev.bot); n.close(); };
    } catch (_) { /* not allowed here */ }
  }
  async function reloadBots() {
    try {
      const st = await GET("state");
      S.bots = st.bots;
      pruneAndRender();
    } catch (_) { /* the stream reconnects on its own */ }
  }
  function pruneAndRender() {
    if (W.mode === "single" && !bot(O.WIN.bot)) return O.botGone(O.WIN.bot);
    const ids = new Set(S.bots.map((b) => b.id));
    let t = W.layout;
    for (const id of L.allBots(t)) if (!ids.has(id)) t = L.remove(t, id);
    if (t !== W.layout) commit(t, { input: false });
    else {
      for (const v of W.views.values()) { v.renderHead(); if (!v.bot) v.renderEmpty(); }
      renderSide();
      renderStatus();
    }
    soon("over", O.renderOverview);
  }
  async function reloadAll() {
    try {
      const st = await GET("state");
      S.bots = st.bots; S.seq = st.seq; S.settings = st.settings; S.native = st.native || {};
      O.applyGhostColor();
      pruneAndRender();
      for (const v of W.views.values()) if (v.bot) v.loadThread();
    } catch (_) { /* retry on next reset */ }
  }
  async function refreshMemoryCount() {
    try { const m = await GET("memory"); S.memoryReview = m.unreviewed.length; O.renderFoot(); } catch (_) { /* ignore */ }
  }

  // Tell the app which bots this window shows and whether you're looking at it (so nothing you can see
  // counts as unread or pops up a notification).
  let lastReport = "";
  let reportTimer = null;
  function reportFocus(force = false) {
    clearTimeout(reportTimer);
    reportTimer = setTimeout(() => {
      const body = { client: O.CLIENT, bots: visibleNow(), focused: focusedBot(), active: document.hasFocus() && !document.hidden };
      const key = JSON.stringify(body);
      if (key === lastReport && !force) return;
      lastReport = key;
      POST("focus", body).catch(() => {});
    }, 120);
  }

  // ---------------------------------------------------------------------------------------------- keyboard
  let slotTimer = null;
  const hideSlots = () => { clearTimeout(slotTimer); $("#app").classList.remove("show-slots"); };
  function wireKeys() {
    document.addEventListener("keydown", (e) => {
      if (e.isComposing || e.keyCode === 229) return; // typing with an input method: its keys aren't ours
      const cmd = O.MAC ? e.metaKey : e.ctrlKey;
      if (e.key === (O.MAC ? "Meta" : "Control")) {
        clearTimeout(slotTimer);
        slotTimer = setTimeout(() => $("#app").classList.add("show-slots"), 450); // hold ⌘ to see the pane numbers
        return;
      }
      clearTimeout(slotTimer);
      if (e.key === "Escape") {
        if (O.menuOpen()) return O.closeMenu();
        if (O.paletteOpen()) return O.closePalette();
        if (O.modalOpen()) return O.closeModal();
        if (O.sheetOpen()) return O.closeSettings();
        if (O.overviewOpen()) return O.closeOverview();
        const v = focusedView();
        if (v && (v.planEdit || v.reply)) return v.escape();
        if (S.drawer) return O.closeDrawer();
        if (v && v.escape()) return;
        if (W.zoom) return toggleZoom();
        return;
      }
      if (e.ctrlKey && e.key === "Tab" && W.mode === "advanced") { e.preventDefault(); return cycleTab(e.shiftKey ? -1 : 1); }
      if (!cmd) return;
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      const run = (fn) => { e.preventDefault(); fn(); };
      if (e.altKey && k.startsWith("Arrow")) return W.mode === "advanced" ? run(() => moveFocus({ ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" }[k])) : null;
      if (e.code === "Backslash") return run(() => addPane(e.shiftKey ? "bottom" : "right"));
      if (e.shiftKey) {
        if (k === "Enter") return run(() => toggleZoom());
        if (k === "m") return run(() => W.mode === "advanced" && minimize());
        if (k === "f") return run(() => W.mode === "advanced" && commit(L.flip(W.layout)));
        if (k === "o") return run(() => { const id = focusedBot(); if (id && O.WIN.kind !== "bot") O.popOut(id); });
        return;
      }
      if (k === "k") return run(() => (O.paletteOpen() ? O.closePalette() : O.palette()));
      if (k === "n") return run(() => O.newBot());
      if (k === ",") return run(() => O.openSettings("providers"));
      if (k === ".") return run(O.toggleOverview);
      if (k === "/") return run(() => O.openSettings("shortcuts"));
      if (k === "b") return run(toggleSidebar);
      if (k === "w" && W.mode === "advanced") return run(() => { const id = focusedBot(); if (id) closeTab(id); else if (L.groups(W.layout).length > 1) closePane(W.focus); });
      if (/^[1-9]$/.test(k)) {
        const n = +k - 1;
        if (W.mode === "advanced") { const g = L.slots(W.layout)[n]; if (g) run(() => { if (W.zoom && W.zoom !== g.id) toggleZoom(); focus(g.id, { input: true }); }); }
        else if (W.mode === "simple" && W.sideOrder[n]) run(() => openBot(W.sideOrder[n]));
      }
    });
    document.addEventListener("keyup", (e) => { if (e.key === "Meta" || e.key === "Control") hideSlots(); });
    window.addEventListener("blur", hideSlots);
  }

  // ---------------------------------------------------------------------------------------------- start
  function wire() {
    $("#btn-new").addEventListener("click", () => O.newBot());
    $("#btn-search").addEventListener("click", () => O.palette());
    $("#btn-side-menu").addEventListener("click", (e) => sideMenu(e.currentTarget));
    $("#btn-me").addEventListener("click", () => O.openSettings("account"));
    $("#btn-settings").addEventListener("click", () => O.openSettings("providers"));
    wireDrops();
    wireKeys();
    window.addEventListener("focus", () => reportFocus());
    window.addEventListener("blur", () => reportFocus());
    document.addEventListener("visibilitychange", () => reportFocus());
    setInterval(() => reportFocus(true), 45000); // still here
    window.addEventListener("pagehide", () => { O.api("POST", "focus", { client: O.CLIENT, gone: true }, { keepalive: true }).catch(() => {}); });
    window.addEventListener("dragend", endDrag);
  }
  function start(botId) {
    const ids = S.bots.map((b) => b.id);
    const recent = recentIds()[0];
    W.mode = S.ui.mode === "advanced" ? "advanced" : "simple";
    if (O.WIN.kind === "extra") {
      W.layout = L.group();
    } else if (W.mode === "advanced") {
      W.layout = L.load(S.ui.layout, ids) || L.group();
      if (!L.allBots(W.layout).length && recent) W.layout = L.open(W.layout, recent);
    } else {
      const last = S.ui.simple_bot || O.store("bot");
      W.layout = L.group([last && bot(last) ? last : recent].filter(Boolean));
    }
    const saved = S.ui.focus && L.find(W.layout, S.ui.focus);
    W.focus = saved && !saved.minimized ? saved.id : (L.slots(W.layout)[0] || L.groups(W.layout)[0]).id;
    render();
    if (botId) openBot(botId);
    const v = focusedView(); if (v) v.focusInput();
  }
  function startSingle() {
    $("#app").classList.add("single");
    W.mode = "single";
    W.persist = false;
    const id = bot(O.WIN.bot) ? O.WIN.bot : null;
    W.layout = L.group(id ? [id] : []);
    W.focus = W.layout.id;
    render();
    if (!id) $("#workspace").replaceChildren(h("div", { class: "welcome-inner gone" }, h("h1", {}, "That bot isn't here any more."), h("p", {}, "You can close this window.")));
    const v = focusedView(); if (v) v.focusInput();
  }
  O.ws = {
    mode: () => W.mode, setMode, focusedBot, focus, openBot, openIn, closePane, tabStrip, paneMenu, render, start,
    shownBots: () => L.allBots(W.layout), visibleBots: visibleNow, paneCount: () => L.groups(W.layout).length,
    refreshHeads: () => { for (const v of W.views.values()) v.renderHead(); },
    reload: (id) => { for (const v of W.views.values()) if (v.bot === id) v.loadThread(); },
    refreshThreads: () => { for (const v of W.views.values()) v.refreshRows(); },
  };

  async function boot() {
    wire();
    let st;
    try { [st, S.ui] = await Promise.all([GET("state"), GET("ui")]); }
    catch (e) {
      document.body.append(h("div", { class: "welcome" }, h("div", { class: "welcome-inner" }, h("h1", {}, "oomi can't start"), h("p", {}, e.message))));
      return;
    }
    S.bots = st.bots; S.settings = st.settings; S.native = st.native || {}; S.seq = st.seq; S.home = st.home; S.version = st.version;
    O.applyGhostColor();
    GET("apps").then((r) => { S.appNames = Object.fromEntries(r.apps.map((x) => [x.id, x.name])); }).catch(() => {});
    O.applyPrefs();
    $("#app").classList.toggle("side-hidden", !!O.pref("sidebar_hidden"));
    connect();
    refreshMemoryCount();
    if (O.WIN.kind === "bot") return startSingle();
    if (!S.settings.has_key) { renderSide(); return O.showWelcome("key"); }
    if (!S.bots.length) { renderSide(); return O.showWelcome("bot"); }
    start();
  }

  window.OomiApp = { S, W, L, summarize: O.summarize, handle }; // for tests
  boot();
})();
