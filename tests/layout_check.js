// The workspace layout model (oomi/gui/static/layout.js), run under node by test_layout.py.
"use strict";
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "oomi", "gui", "static", "layout.js"));

let n = 0;
function check(name, fn) { fn(); n++; }
const shape = (t) => (t.type === "group" ? `[${t.tabs.join(",")}${t.minimized ? " min" : ""}]` : `${t.dir}(${t.children.map(shape).join(" ")})`);
const sum = (a) => a.reduce((x, y) => x + y, 0);
function sane(t) {
  const ids = new Set();
  const bots = new Set();
  (function walk(x) {
    if (x.type === "group") {
      assert(!ids.has(x.id), "duplicate pane id"); ids.add(x.id);
      for (const b of x.tabs) { assert(!bots.has(b), `bot ${b} shown twice`); bots.add(b); }
      assert(x.active === null ? !x.tabs.length : x.tabs.includes(x.active), "active tab missing");
      assert(!("drop" in x), "drop flag left behind");
      return;
    }
    assert(x.children.length >= 2, "split with fewer than two panes");
    assert.strictEqual(x.children.length, x.sizes.length);
    assert(Math.abs(sum(x.sizes) - 1) < 1e-9, "sizes don't add up");
    for (const c of x.children) { assert(!(c.type === "split" && c.dir === x.dir), "nested same-direction split"); walk(c); }
  })(t);
  return t;
}

check("open adds a tab, or brings an open one forward", () => {
  let t = L.group();
  t = L.open(t, "a"); t = L.open(t, "b");
  assert.strictEqual(shape(t), "[a,b]");
  assert.strictEqual(t.active, "b");
  t = L.open(t, "a");
  assert.strictEqual(t.active, "a");
  sane(t);
});

check("splitting moves a bot to a new pane beside the target", () => {
  let t = L.open(L.open(L.group(), "a"), "b");
  const g = t.id;
  t = L.splitAt(t, g, "right", "b");
  assert.strictEqual(shape(t), "row([a] [b])");
  t = L.splitAt(t, L.groupOfBot(t, "b").id, "bottom", "c");
  assert.strictEqual(shape(t), "row([a] col([b] [c]))");
  t = L.splitAt(t, L.groupOfBot(t, "a").id, "left", "d");
  assert.strictEqual(shape(t), "row([d] [a] col([b] [c]))"); // flattened, not row(row(...))
  sane(t);
  // the two panes that came from one keep that pane's share between them
  const t2 = L.splitAt(L.open(L.group(), "a"), L.open(L.group(), "a").id, "right", "a");
  assert.strictEqual(shape(t2), "[a]");
});

check("a pane can't be split from itself, and there are at most nine", () => {
  const one = L.open(L.group(), "a");
  assert.strictEqual(L.splitAt(one, one.id, "right", "a"), one);
  let t = one;
  for (const b of "bcdefghi") t = L.splitAt(t, L.groups(t).pop().id, "right", b);
  assert.strictEqual(L.groups(t).length, 9);
  assert.strictEqual(L.splitAt(t, L.groups(t)[0].id, "bottom", "z"), t);
  sane(t);
});

check("moving the last tab out of a pane closes that pane", () => {
  let t = L.splitAt(L.open(L.open(L.group(), "a"), "b"), L.open(L.open(L.group(), "a"), "b").id, "right", "b");
  t = L.move(t, "b", L.groupOfBot(t, "a").id, "center");
  assert.strictEqual(shape(t), "[a,b]");
  sane(t);
});

check("dropping in the middle adds a tab at the place you dropped it", () => {
  let t = L.open(L.open(L.open(L.group(), "a"), "b"), "c");
  t = L.move(t, "c", t.id, "center", 0);
  assert.deepStrictEqual(t.tabs, ["c", "a", "b"]);
  t = L.move(t, "c", t.id, "center", 3);
  assert.deepStrictEqual(t.tabs, ["a", "b", "c"]);
  t = L.move(t, "z", t.id, "center", 1);
  assert.deepStrictEqual(t.tabs, ["a", "z", "b", "c"]);
  sane(t);
});

check("removing a bot (deleted, or its tab closed)", () => {
  let t = L.split("row", [L.group(["a", "b"], "b"), L.group(["c"])]);
  t = L.remove(t, "b");
  assert.strictEqual(shape(t), "row([a] [c])");
  t = L.remove(t, "c");
  assert.strictEqual(shape(t), "[a]");
  t = L.remove(t, "a");
  assert.strictEqual(shape(t), "[]"); // the last pane stays, empty
  sane(t);
});

check("empty panes stay until closed, and new bots go to them first", () => {
  let t = L.addEmpty(L.open(L.group(), "a"), null, "right");
  assert.strictEqual(shape(t), "row([a] [])");
  t = L.open(t, "b");
  assert.strictEqual(shape(t), "row([a] [b])");
  t = L.addEmpty(t, L.groupOfBot(t, "b").id, "bottom");
  const empty = L.groups(t).find((g) => !g.tabs.length);
  t = L.closeGroup(t, empty.id);
  assert.strictEqual(shape(t), "row([a] [b])");
  sane(t);
});

check("minimizing keeps at least one pane open", () => {
  let t = L.split("row", [L.group(["a"]), L.group(["b"])]);
  const [ga, gb] = L.groups(t);
  t = L.toggleMinimize(t, ga.id);
  assert.strictEqual(shape(t), "row([a min] [b])");
  assert.strictEqual(L.toggleMinimize(t, gb.id), t);
  assert.deepStrictEqual(L.visibleBots(t), ["b"]);
  t = L.open(t, "a"); // opening a minimized bot brings its pane back
  assert.strictEqual(shape(t), "row([a] [b])");
  sane(t);
});

check("flip turns rows into columns", () => {
  const t = L.flip(L.split("row", [L.group(["a"]), L.split("col", [L.group(["b"]), L.group(["c"])])]));
  assert.strictEqual(shape(t), "col([a] row([b] [c]))");
  sane(t);
});

check("resizing and equalizing", () => {
  let t = L.split("row", [L.group(["a"]), L.split("col", [L.group(["b"]), L.group(["c"])])]);
  t = L.setSizes(t, [], [0.7, 0.3]);
  assert.deepStrictEqual(t.sizes, [0.7, 0.3]);
  t = L.setSizes(t, [1], [0.25, 0.75]);
  assert.deepStrictEqual(t.children[1].sizes, [0.25, 0.75]);
  assert.strictEqual(L.setSizes(t, [0], [1]), t); // not a split
  t = L.equalize(t);
  assert.deepStrictEqual(t.sizes, [0.5, 0.5]);
  sane(t);
});

check("presets keep what was open and fill new panes with recent bots", () => {
  let t = L.split("row", [L.group(["a", "x"], "x"), L.group(["b"])]);
  t = L.applyShape(t, L.PRESETS.grid4.shape(), ["b", "c", "d", "e"]);
  assert.strictEqual(shape(t), "col(row([a,x] [b]) row([c] [d]))");
  assert.strictEqual(L.groups(t)[0].active, "x");
  sane(t);
  t = L.applyShape(t, L.PRESETS.single.shape(), []);
  assert.strictEqual(shape(t), "[a,x,b,c,d]");
  sane(t);
  t = L.applyShape(L.open(L.group(), "a"), L.PRESETS.columns3.shape(), []);
  assert.strictEqual(shape(t), "row([a] [] [])"); // not enough bots: empty panes to fill
  sane(t);
});

check("saved layouts keep the shape, not the bots", () => {
  const t = L.split("row", [L.group(["a"]), L.split("col", [L.group(["b"]), L.group(["c"])], [0.3, 0.7])], [0.6, 0.4]);
  const g = L.loadShape(JSON.parse(JSON.stringify(L.geometry(t))));
  assert.strictEqual(shape(g), "row([] col([] []))");
  assert.deepStrictEqual(g.children[1].sizes, [0.3, 0.7]);
  assert(!JSON.stringify(g).includes('"a"'));
});

check("stored layouts are checked: unknown bots, junk and duplicates go", () => {
  const stored = { type: "split", dir: "row", sizes: [1, 1, 1], children: [
    { type: "group", id: "p1", tabs: ["a", "gone", "a", 7], active: "gone" },
    { type: "group", id: "p1", tabs: ["gone"] }, // its bot was deleted: the pane closes
    { type: "group", id: "p3", tabs: [] }, // saved empty: it stays
    { type: "evil", children: [] }, "<img src=x onerror=alert(1)>",
  ] };
  const t = L.load(stored, ["a", "b"]);
  assert.strictEqual(shape(t), "row([a] [])");
  assert.strictEqual(L.groups(t)[0].active, "a");
  sane(t);
  assert.strictEqual(L.load(null, ["a"]), null);
  assert.strictEqual(L.load({ type: "split", dir: "row", children: [] }, ["a"]), null);
  assert.strictEqual(shape(L.load({ type: "group", tabs: ["gone"] }, ["a"])), "[]");
  let deep = { type: "group", tabs: ["a"] };
  for (let i = 0; i < 40; i++) deep = { type: "split", dir: i % 2 ? "row" : "col", children: [deep, { type: "group", tabs: [] }] };
  const td = L.load(deep, ["a"]); // too deep to be real: the deep part goes
  assert(!L.allBots(td).includes("a") && L.groups(td).length <= L.MAX_GROUPS);
  sane(td);
  const many = { type: "split", dir: "row", children: Array.from({ length: 12 }, (_, i) => ({ type: "group", tabs: [`b${i}`] })) };
  const tm = L.load(many, Array.from({ length: 12 }, (_, i) => `b${i}`));
  assert.strictEqual(L.groups(tm).length, 9);
  sane(tm);
});

check("random edits never leave a broken tree", () => {
  let seed = 7;
  const rnd = (k) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % k; };
  const bots = "abcdefghijkl".split("");
  const sides = ["left", "right", "top", "bottom", "center"];
  let t = L.group();
  for (let i = 0; i < 4000; i++) {
    const gs = L.groups(t);
    const g = gs[rnd(gs.length)];
    const b = bots[rnd(bots.length)];
    switch (rnd(10)) {
      case 0: t = L.open(t, b, g.id); break;
      case 1: t = L.splitAt(t, g.id, sides[rnd(4)], b); break;
      case 2: t = L.move(t, b, g.id, sides[rnd(5)], rnd(4)); break;
      case 3: t = L.remove(t, b); break;
      case 4: t = L.toggleMinimize(t, g.id); break;
      case 5: t = L.flip(t); break;
      case 6: t = L.addEmpty(t, g.id, sides[rnd(4)]); break;
      case 7: if (rnd(4) === 0) t = L.closeGroup(t, g.id); break;
      case 8: t = L.replace(t, b, g.id); break;
      default: t = L.load(JSON.parse(JSON.stringify(t)), bots); break;
    }
    sane(t);
    assert(L.groups(t).length <= L.MAX_GROUPS, "too many panes");
    assert(L.slots(t).length >= 1, "every pane minimized");
  }
});

console.log(`layout: ${n} checks passed`);
