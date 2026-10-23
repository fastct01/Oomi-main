// oomi — the workspace layout as data: splits (rows or columns) of groups, each group a stack of tabs (one bot
// per tab). Every change returns a new, normalized tree; nothing here touches the page.
(function (root) {
  "use strict";

  const MAX_GROUPS = 9; // ⌘1–⌘9
  let counter = 0;
  const newId = () => "g" + Date.now().toString(36).slice(-5) + (++counter).toString(36);

  function group(tabs = [], active) {
    tabs = tabs.filter(Boolean);
    return { type: "group", id: newId(), tabs, active: active || tabs[0] || null, minimized: false };
  }
  function split(dir, children, sizes) {
    return { type: "split", dir, children, sizes: sizes || children.map(() => 1 / children.length) };
  }
  const clone = (n) => JSON.parse(JSON.stringify(n));

  // ---------------------------------------------------------------------------------------------- reading
  function groups(node, out = []) {
    if (!node) return out;
    if (node.type === "group") out.push(node);
    else node.children.forEach((c) => groups(c, out));
    return out;
  }
  const find = (rootNode, id) => groups(rootNode).find((g) => g.id === id) || null;
  const groupOfBot = (rootNode, bot) => groups(rootNode).find((g) => g.tabs.includes(bot)) || null;
  const allBots = (rootNode) => groups(rootNode).flatMap((g) => g.tabs);
  // panes you can see right now (empty ones too), in reading order: these are the ⌘1–⌘9 slots
  const slots = (rootNode) => groups(rootNode).filter((g) => !g.minimized);
  const visibleBots = (rootNode) => slots(rootNode).map((g) => g.active);

  // ---------------------------------------------------------------------------------------------- tidying
  function normalize(node) {
    if (!node || typeof node !== "object" || node.drop) return group();
    if (node.type === "group") {
      const seen = new Set();
      node.tabs = (Array.isArray(node.tabs) ? node.tabs : []).filter((t) => typeof t === "string" && !seen.has(t) && seen.add(t));
      if (!node.tabs.includes(node.active)) node.active = node.tabs[0] || null;
      node.minimized = !!node.minimized && node.tabs.length > 0;
      if (!node.id) node.id = newId();
      delete node.drop;
      return node;
    }
    const kids = [];
    const sizes = [];
    const given = Array.isArray(node.sizes) && node.sizes.length === node.children.length ? node.sizes : node.children.map(() => 1);
    node.children.forEach((c, i) => {
      if (!c || c.drop) return; // closed panes go; empty ones stay as places to put a bot
      const n = normalize(c);
      const s = Number(given[i]) > 0 ? Number(given[i]) : 1 / node.children.length;
      if (n.type === "split" && n.dir === node.dir) { // a row inside a row is just a longer row
        const t = n.sizes.reduce((a, b) => a + b, 0) || 1;
        n.children.forEach((cc, j) => { kids.push(cc); sizes.push((s * n.sizes[j]) / t); });
        return;
      }
      kids.push(n); sizes.push(s);
    });
    if (!kids.length) return group();
    if (kids.length === 1) return kids[0];
    const total = sizes.reduce((a, b) => a + b, 0) || 1;
    return { type: "split", dir: node.dir === "col" ? "col" : "row", children: kids, sizes: sizes.map((s) => s / total) };
  }
  // Take a bot out of whichever pane has it; a pane left with nothing in it closes (unless it's `keep`).
  function takeOut(t, bot, keep) {
    for (const g of groups(t)) {
      const i = g.tabs.indexOf(bot);
      if (i < 0) continue;
      g.tabs.splice(i, 1);
      if (g.active === bot) g.active = g.tabs[Math.min(i, g.tabs.length - 1)] || null;
      if (!g.tabs.length && g !== keep && groups(t).length > 1) g.drop = true;
    }
  }
  // At least one pane must stay open: if every pane is minimized, open the first.
  function ensureOpen(rootNode) {
    const gs = groups(rootNode);
    if (gs.length && gs.every((g) => g.minimized)) gs[0].minimized = false;
    return rootNode;
  }
  const done = (rootNode) => ensureOpen(normalize(rootNode));

  // ---------------------------------------------------------------------------------------------- changes
  // Show a bot: if it's already in the layout, bring its tab forward; otherwise add it as a tab of `target`.
  function open(rootNode, bot, target) {
    const t = clone(rootNode);
    const have = groupOfBot(t, bot);
    if (have) { have.active = bot; have.minimized = false; return done(t); }
    const g = (target && find(t, target)) || slots(t).find((x) => !x.tabs.length) || slots(t)[0] || groups(t)[0];
    g.tabs.push(bot); g.active = bot; g.minimized = false;
    return done(t);
  }
  // Replace what a pane shows (Simple mode, or "open here").
  function replace(rootNode, bot, target) {
    let t = clone(rootNode);
    const g = (target && find(t, target)) || slots(t)[0] || groups(t)[0];
    const old = groupOfBot(t, bot);
    if (old && old !== g) takeOut(t, bot, g);
    const i = g.tabs.indexOf(g.active);
    if (!g.tabs.includes(bot)) { if (i >= 0) g.tabs[i] = bot; else g.tabs.push(bot); }
    g.active = bot;
    g.minimized = false;
    t = done(t);
    return t;
  }
  function remove(rootNode, bot) {
    const t = clone(rootNode);
    takeOut(t, bot);
    return done(t);
  }
  function closeGroup(rootNode, id) {
    const t = clone(rootNode);
    const g = find(t, id);
    if (!g) return rootNode;
    if (groups(t).length === 1) { g.tabs = []; g.active = null; g.minimized = false; return done(t); }
    g.drop = true;
    return done(t);
  }
  // A new empty pane beside `id`, to pick a bot for.
  function addEmpty(rootNode, id, side = "right") {
    const t = clone(rootNode);
    const target = find(t, id) || groups(t)[0];
    if (groups(t).length >= MAX_GROUPS) return rootNode;
    return place(t, target, side, group());
  }
  function place(t, target, side, fresh) {
    const dir = side === "left" || side === "right" ? "row" : "col";
    const pair = side === "left" || side === "top" ? [fresh, target] : [target, fresh];
    const replacement = split(dir, pair);
    if (t === target) return done(replacement);
    (function swap(node) {
      if (node.type !== "split") return false;
      const i = node.children.indexOf(target);
      if (i >= 0) { node.children[i] = replacement; return true; }
      return node.children.some(swap);
    })(t);
    return done(t);
  }
  function activate(rootNode, id, bot) {
    const t = clone(rootNode);
    const g = find(t, id);
    if (g && g.tabs.includes(bot)) { g.active = bot; g.minimized = false; }
    return done(t);
  }

  // Put `bot` in a new pane on one side of pane `id`. Returns the tree unchanged if that makes no sense.
  function splitAt(rootNode, id, side, bot) {
    if (!["left", "right", "top", "bottom"].includes(side)) return rootNode;
    const t = clone(rootNode);
    const target = find(t, id);
    if (!target) return rootNode;
    const from = groupOfBot(t, bot);
    if (from === target && target.tabs.length === 1) return rootNode; // splitting a pane from itself
    const frees = from && from !== target && from.tabs.length === 1; // moving its only tab closes the old pane
    if (groups(t).length >= MAX_GROUPS && !frees) return rootNode;
    if (from) takeOut(t, bot, target);
    return place(t, target, side, group([bot]));
  }
  // Drag and drop: the middle of a pane adds a tab, its edges split.
  function move(rootNode, bot, id, zone, index) {
    if (zone !== "center") return splitAt(rootNode, id, zone, bot);
    const t = clone(rootNode);
    const target = find(t, id);
    if (!target) return rootNode;
    const from = groupOfBot(t, bot);
    if (from && from !== target) takeOut(t, bot, target);
    const at = target.tabs.indexOf(bot);
    if (at >= 0) target.tabs.splice(at, 1);
    const i = Number.isInteger(index) ? Math.max(0, Math.min(index - (at >= 0 && at < index ? 1 : 0), target.tabs.length)) : target.tabs.length;
    target.tabs.splice(i, 0, bot);
    target.active = bot;
    target.minimized = false;
    return done(t);
  }
  function toggleMinimize(rootNode, id) {
    const t = clone(rootNode);
    const g = find(t, id);
    if (!g || !g.tabs.length) return rootNode;
    if (!g.minimized && slots(t).length <= 1) return rootNode; // keep one pane open
    g.minimized = !g.minimized;
    return done(t);
  }
  // Rows become columns and columns rows.
  function flip(rootNode) {
    const t = clone(rootNode);
    (function walk(n) { if (n.type === "split") { n.dir = n.dir === "row" ? "col" : "row"; n.children.forEach(walk); } })(t);
    return done(t);
  }
  // path: child indexes from the root down to the split being resized
  function setSizes(rootNode, path, sizes) {
    const t = clone(rootNode);
    let n = t;
    for (const i of path) { if (!n || n.type !== "split") return rootNode; n = n.children[i]; }
    if (!n || n.type !== "split" || sizes.length !== n.children.length) return rootNode;
    n.sizes = sizes.slice();
    return done(t);
  }
  function equalize(rootNode) {
    const t = clone(rootNode);
    (function walk(n) { if (n.type === "split") { n.sizes = n.children.map(() => 1 / n.children.length); n.children.forEach(walk); } })(t);
    return done(t);
  }

  // ---------------------------------------------------------------------------------------------- shapes
  // A layout's shape without its bots: what presets and saved layouts are.
  function geometry(rootNode) {
    const t = clone(rootNode);
    for (const g of groups(t)) { g.tabs = []; g.active = null; g.minimized = false; delete g.id; }
    return t;
  }
  const g0 = () => ({ type: "group", tabs: [] });
  const PRESETS = {
    single: { name: "One pane", shape: () => g0() },
    columns2: { name: "Two side by side", shape: () => split("row", [g0(), g0()]) },
    rows2: { name: "Two stacked", shape: () => split("col", [g0(), g0()]) },
    columns3: { name: "Three columns", shape: () => split("row", [g0(), g0(), g0()]) },
    main_side: { name: "One big, two small", shape: () => split("row", [g0(), split("col", [g0(), g0()])], [0.62, 0.38]) },
    grid4: { name: "Grid of four", shape: () => split("col", [split("row", [g0(), g0()]), split("row", [g0(), g0()])]) },
    grid6: { name: "Grid of six", shape: () => split("col", [split("row", [g0(), g0(), g0()]), split("row", [g0(), g0(), g0()])]) },
  };
  const shapeGroups = (shape) => groups(shape);
  // Pour the current panes (with their tabs) into a shape. Extra panes get `more` bots (most recent first);
  // panes that don't fit hand their tabs to the last pane, so nothing you had open disappears.
  function applyShape(rootNode, shape, more = []) {
    const t = clone(shape);
    const target = groups(t);
    if (!target.length) return rootNode;
    const old = groups(rootNode).filter((g) => g.tabs.length);
    const shown = new Set(allBots(rootNode));
    const extra = more.filter((b) => !shown.has(b));
    target.forEach((g, i) => {
      g.id = newId(); g.minimized = false;
      if (old[i]) { g.tabs = old[i].tabs.slice(); g.active = old[i].active; }
      else { const b = extra.shift(); g.tabs = b ? [b] : []; g.active = b || null; }
    });
    const last = target.filter((g) => g.tabs.length).pop() || target[0];
    for (const g of old.slice(target.length)) for (const b of g.tabs) if (!last.tabs.includes(b)) last.tabs.push(b);
    if (!last.active) last.active = last.tabs[0] || null;
    return done(t);
  }

  // ---------------------------------------------------------------------------------------------- storage
  // Accept only well-formed trees made of known bots; anything odd is dropped rather than trusted.
  // A pane whose bots have all been deleted closes; a pane that was saved empty stays empty.
  function load(data, knownBots, opts = {}) {
    const known = knownBots instanceof Set ? knownBots : new Set(knownBots || []);
    function walk(n, d) {
      if (!n || typeof n !== "object" || d > 8) return null;
      if (n.type === "group") {
        const list = Array.isArray(n.tabs) && !opts.shape ? n.tabs : [];
        const tabs = list.filter((b) => typeof b === "string" && known.has(b)).slice(0, 50);
        return { type: "group", id: typeof n.id === "string" && /^[\w-]{1,40}$/.test(n.id) ? n.id : newId(), tabs,
          active: tabs.includes(n.active) ? n.active : tabs[0] || null, minimized: !!n.minimized && !opts.shape,
          drop: list.length > 0 && !tabs.length };
      }
      if (n.type === "split" && Array.isArray(n.children) && n.children.length) {
        const kids = [];
        const sizes = [];
        n.children.slice(0, 12).forEach((c, i) => {
          const k = walk(c, d + 1);
          if (!k) return;
          kids.push(k);
          const s = Array.isArray(n.sizes) ? Number(n.sizes[i]) : NaN;
          sizes.push(Number.isFinite(s) && s > 0 ? Math.min(s, 1e6) : 1);
        });
        return kids.length ? { type: "split", dir: n.dir === "col" ? "col" : "row", children: kids, sizes } : null;
      }
      return null;
    }
    const t = walk(data, 0);
    if (!t) return null;
    if (groups(t).every((g) => g.drop)) return group(); // everything in it was deleted
    const ids = new Set();
    for (const g of groups(t)) { if (ids.has(g.id)) g.id = newId(); ids.add(g.id); }
    let out = done(t);
    for (let gs = groups(out); gs.length > MAX_GROUPS; gs = groups(out)) out = closeGroup(out, gs[gs.length - 1].id);
    return out;
  }
  function loadShape(data) {
    const t = load(data, null, { shape: true });
    return t ? geometry(t) : null;
  }

  const L = {
    MAX_GROUPS, PRESETS, group, split, groups, find, groupOfBot, allBots, slots, visibleBots, normalize,
    open, replace, remove, closeGroup, addEmpty, activate, splitAt, move, toggleMinimize, flip, setSizes, equalize,
    geometry, applyShape, shapeGroups, load, loadShape,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = L;
  else root.OomiLayout = L;
})(typeof window !== "undefined" ? window : globalThis);
