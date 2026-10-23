// oomi — shared pieces for every window: state, the local API, DOM and formatting helpers, menus and dialogs.
// Plain scripts, no build step: each file adds to window.Oomi.
(() => {
  "use strict";

  const O = (window.Oomi = window.Oomi || {});
  const Q = new URLSearchParams(location.search);
  O.TOKEN = Q.get("t") || "";
  // which window this is: the main one, a single bot in a window of its own, or another full window
  O.WIN = { kind: ["bot", "extra"].includes(Q.get("w")) ? Q.get("w") : "main", bot: Q.get("bot") || "", name: Q.get("name") || "" };
  O.CLIENT = `${O.WIN.name || O.WIN.kind}-${Math.random().toString(36).slice(2, 8)}`;
  const $ = (O.$ = (sel, el = document) => el.querySelector(sel));
  O.$$ = (sel, el = document) => [...el.querySelectorAll(sel)];

  // ---------------------------------------------------------------------------------------------- DOM
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v === null || v === undefined) continue;
      if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = v; // only ever trusted markup (icons, rendered Markdown)
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  O.h = h;

  const P = {
    apps: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.8"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.8"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.8"/><path d="M16.75 13.5v6.5M13.5 16.75H20"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>',
    more: '<circle cx="5" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="19" cy="12" r="1.3" fill="currentColor"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    right: '<path d="m9 6 6 6-6 6"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2.5"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    reply: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    smile: '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5s1.3 2 3.5 2 3.5-2 3.5-2"/><path d="M9 9.5h.01M15 9.5h.01" stroke-width="2.6"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
    up: '<path d="M12 19V5M5.5 11.5 12 5l6.5 6.5"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
    clip: '<path d="m20.5 11.5-8.1 8.1a5.2 5.2 0 0 1-7.4-7.4l8.5-8.5a3.5 3.5 0 0 1 4.9 4.9l-8.5 8.5a1.7 1.7 0 0 1-2.5-2.5l7.8-7.8"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z"/>',
    terminal: '<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="m7 9.5 3 2.5-3 2.5M12.5 15H17"/>',
    fresh: '<path d="M4 12a8 8 0 0 1 13.7-5.6L20 8.5"/><path d="M20 4v4.5h-4.5"/><path d="M20 12a8 8 0 0 1-13.7 5.6L4 15.5"/><path d="M4 20v-4.5h4.5"/>',
    trash: '<path d="M4.5 7h15M9.5 7V4.8h5V7M6.5 7l1 12.5h9l1-12.5"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    alert: '<path d="M12 4 2.8 19.5h18.4Z"/><path d="M12 10v4.2M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5h.01"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    shield: '<path d="M12 3.5 5 6v5.5c0 4.4 3 8 7 9 4-1 7-4.6 7-9V6Z"/>',
    changes: '<path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5Z"/><path d="M14 3.5v5h5M12 11v5M9.5 13.5h5"/>',
    plan: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h.01M4 12h.01M4 18h.01" stroke-width="2.6"/>',
    memory: '<path d="M12 3.5a5.5 5.5 0 0 0-3.2 10V16h6.4v-2.5A5.5 5.5 0 0 0 12 3.5Z"/><path d="M9.5 19h5M10.5 21.5h3"/>',
    skills: '<path d="m13 2.5-8 11h6.5L10 21.5l9-12h-6.5Z"/>',
    pulse: '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>',
    person: '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
    wifi: '<path d="M2.5 9a14 14 0 0 1 19 0M5.5 12.5a9.5 9.5 0 0 1 13 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01"/><path d="M3 3l18 18"/>',
    gear2: '<circle cx="12" cy="12" r="2.5"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    file: '<path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5Z"/><path d="M14 3.5v5h5"/>',
    img: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.7"/><path d="m20.5 16-5-5-8.5 8.5"/>',
    // the workspace
    splitR: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M12 4.5v15"/>',
    // the layout presets, each drawn in its own shape (the 62/38 split of "One big, two small" included)
    lay1: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/>',
    lay2c: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M12 4.5v15"/>',
    lay2r: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 12h17"/>',
    lay3c: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.2 4.5v15M14.8 4.5v15"/>',
    layMain: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M14 4.5v15M14 12h6.5"/>',
    lay4: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M12 4.5v15M3.5 12h17"/>',
    lay6: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.2 4.5v15M14.8 4.5v15M3.5 12h17"/>',
    splitD: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 12h17"/>',
    grid: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.8"/>',
    minimize: '<path d="M6 12h12"/>',
    popout: '<path d="M13.5 4.5h6v6M19.5 4.5l-8 8"/><path d="M17.5 14v4a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2h4"/>',
    zoom: '<path d="M4.5 9V4.5H9M15 4.5h4.5V9M19.5 15v4.5H15M9 19.5H4.5V15"/>',
    flip: '<path d="M7 4.5v15M7 4.5 4 7.5M7 4.5l3 3M17 19.5v-15M17 19.5l-3-3M17 19.5l3-3"/>',
    pin: '<path d="M9.5 3.5h5l-.8 5.5 3.3 3v1.5h-10V12l3.3-3Z"/><path d="M12 13.5v7"/>',
    bell: '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15Z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>',
    keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M8 14h8"/>',
    lock: '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    download: '<path d="M12 4v11M7 10.5l5 5 5-5M4.5 19.5h15"/>',
    upload: '<path d="M12 15.5v-11M7 9.5l5-5 5 5M4.5 19.5h15"/>',
    layers: '<path d="m12 3.5 8.5 4.5-8.5 4.5L3.5 8Z"/><path d="m3.5 12 8.5 4.5 8.5-4.5M3.5 16l8.5 4.5 8.5-4.5"/>',
    side: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15"/>',
    text: '<path d="M5 6.5V5h14v1.5M12 5v14M9 19h6"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9.5 2.5V6M14.5 2.5V6M9.5 18v3.5M14.5 18v3.5M2.5 9.5H6M2.5 14.5H6M18 9.5h3.5M18 14.5h3.5"/>',
    coffee: '<path d="M4.5 9h12v5a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5Z"/><path d="M16.5 10.5h1.5a2.5 2.5 0 0 1 0 5h-1.5M8 3.5v2.5M12 3.5v2.5"/>',
  };
  O.P = P;
  O.icon = function icon(name, size = 18, sw = 1.8) {
    return h("span", {
      class: "ic", "aria-hidden": "true",
      html: `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round">${P[name] || ""}</svg>`,
    });
  };

  // Colors: a bot wears its own; everything else (and bots from before colors) follows oomi's color in Settings.
  // Every ghost is cut out on transparency, so it sits straight on whatever is behind it. A custom color (#rrggbb)
  // is the white ghost repainted on a canvas, keeping its shading and glossy highlights.
  O.appColor = () => O.S.settings.ghost_color || "white";
  const isHex = (O.isHex = (c) => /^#[0-9a-f]{6}$/i.test(c || ""));
  const ghostSrc = (O.ghostSrc = (c) => `/static/ghosts/${c || O.appColor()}.png`);
  const tints = new Map();
  function tint(hex) {
    if (!tints.has(hex)) tints.set(hex, new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        const cv = document.createElement("canvas");
        cv.width = img.naturalWidth; cv.height = img.naturalHeight;
        const g = cv.getContext("2d");
        g.drawImage(img, 0, 0);
        const d = g.getImageData(0, 0, cv.width, cv.height), p = d.data;
        const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
        for (let i = 0; i < p.length; i += 4) {
          const L = (p[i] + p[i + 1] + p[i + 2]) / 765; // the white ghost's brightness is its shading
          const shade = Math.min(1, L / 0.85), hi = Math.min(1, Math.max(0, (L - 0.9) / 0.1)) ** 1.5; // highlights stay white
          for (let k = 0; k < 3; k++) p[i + k] = rgb[k] * shade * (1 - hi) + 255 * hi;
        }
        g.putImageData(d, 0, 0);
        resolve(cv.toDataURL());
      };
      img.onerror = () => resolve(ghostSrc("white"));
      img.src = "/static/ghosts/white.png";
    }));
    return tints.get(hex);
  }
  // class "cut" pads the ghost to the size the app was designed around (see .cut in app.css)
  O.ghostImg = function ghostImg(color = "") {
    if (!isHex(color)) return h("img", { src: ghostSrc(color), alt: "", class: "cut", "data-ghost": !color });
    const img = h("img", { alt: "", class: "cut" });
    tint(color.toLowerCase()).then((src) => { img.src = src; });
    return img;
  };
  O.applyGhostColor = function applyGhostColor() {
    O.$$("img[data-ghost]").forEach((img) => { img.src = ghostSrc(); });
  };

  const POSES = (O.POSES = 4); // avatars: the ghost on black, facing either way, upright or leaning
  // Poses are switched off for now: every ghost faces you, whatever pose a bot has stored (only the color varies).
  O.avatar = function avatar(n, cls = "", color = "") {
    const v = 0;
    return h("span", { class: `avatar av-${v} ${cls}` }, O.ghostImg(color));
  };

  // The six ghost colors; with custom: true, also the colors added with + (kept in Settings) and the + itself.
  O.colorPicker = function colorPicker(selected, onPick, { custom = false } = {}) {
    const box = h("div", { class: "swatches", role: "radiogroup", "aria-label": "Color" });
    const norm = (c) => (isHex(c) ? c.toLowerCase() : c);
    const pick = async (c) => { try { await onPick(c); } catch (e) { return O.fail(e); } selected = c; render(); };
    const keep = async (list) => { O.S.settings = await O.POST("settings", { custom_colors: list }); };
    function render() {
      const saved = custom ? O.S.settings.custom_colors || [] : [];
      const worn = custom && isHex(selected) && !saved.includes(norm(selected)) ? [norm(selected)] : []; // a bot's color from before +
      const swatches = [...(O.S.settings.ghost_colors || []), ...saved, ...worn].map((c) => {
        const on = c === norm(selected);
        const b = h("button", {
          type: "button", class: `swatch${on ? " on" : ""}`, role: "radio", "aria-checked": String(on),
          title: isHex(c) ? c.toUpperCase() : c[0].toUpperCase() + c.slice(1), "aria-label": c, onclick: () => pick(c),
        }, O.ghostImg(c));
        if (saved.includes(c)) b.addEventListener("contextmenu", (e) => {
          e.preventDefault();
          O.openMenu(b, [{ label: "Remove color", icon: "trash", run: () => keep(saved.filter((x) => x !== c)).then(render, O.fail) }]);
        });
        return b;
      });
      if (custom) swatches.push(h("button", {
        type: "button", class: "swatch add", title: "Add a color", "aria-label": "Add a color",
        onclick: (e) => colorPop(e.currentTarget, isHex(selected) ? norm(selected) : "#ff7a59", async (c) => {
          try { await keep([...new Set([...saved, ...worn, c])]); } catch (err) { return O.fail(err); } // keep the color it wore, too
          pick(c);
        }),
      }, O.icon("plus", 18, 2.2)));
      box.replaceChildren(...swatches);
    }
    render();
    return box;
  };
  function hexToHsv(hex) {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    const mx = Math.max(r, g, b), d = mx - Math.min(r, g, b);
    const hue = d === 0 ? 0 : mx === r ? ((g - b) / d + 6) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [hue * 60, mx ? d / mx : 0, mx];
  }
  function hsvToHex(hue, sat, val) {
    const f = (n) => { const k = (n + hue / 60) % 6; return val - val * sat * Math.max(0, Math.min(k, 4 - k, 1)); };
    return "#" + [f(5), f(3), f(1)].map((x) => Math.round(x * 255).toString(16).padStart(2, "0")).join("");
  }
  // The picker behind +, drawn in the app's own style (the system one is a grey panel that ignores it)
  function colorPop(anchor, start, onAdd) {
    let [hue, sat, val] = hexToHsv(start);
    const fieldDot = h("span", { class: "cp-dot" }), hueDot = h("span", { class: "cp-dot" });
    const field = h("div", { class: "cp-field" }, fieldDot), hueBar = h("div", { class: "cp-hue" }, hueDot);
    const preview = h("span", { class: "avatar" });
    const hex = h("input", { class: "text-input cp-hex", maxlength: "7", spellcheck: "false", "aria-label": "Hex color" });
    const add = h("button", { type: "button", class: "btn primary", onclick: () => { O.closeMenu(); onAdd(cur()); } }, "Add");
    const cur = () => hsvToHex(hue, sat, val);
    let shown = "", wait = 0;
    function paint(typed = false) {
      const c = cur();
      field.style.setProperty("--hue", `hsl(${hue} 100% 50%)`);
      fieldDot.style.cssText = `left:${sat * 100}%;top:${(1 - val) * 100}%;background:${c}`;
      hueDot.style.cssText = `left:${(hue / 360) * 100}%;background:hsl(${hue} 100% 50%)`;
      if (!typed) hex.value = c.toUpperCase();
      clearTimeout(wait); // repainting the ghost is slow-ish, so only once the color settles
      wait = setTimeout(() => {
        if (c === shown) return;
        shown = c;
        tint(c).then((src) => { if (shown === c) preview.replaceChildren(h("img", { src, alt: "", class: "cut" })); });
      }, shown ? 120 : 0);
    }
    const drag = (el, move) => el.addEventListener("pointerdown", (e) => {
      const at = (ev) => {
        const r = el.getBoundingClientRect(), clamp = (v) => Math.min(1, Math.max(0, v));
        move(clamp((ev.clientX - r.left) / r.width), clamp((ev.clientY - r.top) / r.height));
        paint();
      };
      el.setPointerCapture(e.pointerId);
      at(e);
      el.onpointermove = at;
      el.onpointerup = () => { el.onpointermove = null; };
    });
    drag(field, (x, y) => { sat = x; val = 1 - y; });
    drag(hueBar, (x) => { hue = Math.min(x * 360, 359.9); });
    hex.addEventListener("input", () => {
      const v = hex.value.trim().replace(/^#?/, "#").toLowerCase();
      if (isHex(v)) { [hue, sat, val] = hexToHsv(v); paint(true); }
    });
    hex.addEventListener("keydown", (e) => { if (e.key === "Enter") add.click(); });
    const m = O.openMenu(anchor, [{ node: h("div", { class: "cp" }, field, hueBar, h("div", { class: "cp-row" }, preview, hex, add)) }]);
    m.setAttribute("role", "dialog"); m.setAttribute("aria-label", "Add a color");
    paint();
    setTimeout(() => hex.select(), 30);
  }

  // ---------------------------------------------------------------------------------------------- state
  O.S = {
    bots: [], settings: {}, native: {}, home: "", version: "", seq: 0, es: null,
    ui: {}, // saved window state (ui.json): mode, layout, saved layouts, display and sidebar preferences
    memoryReview: 0,
    drawer: null, sheet: null,
    drag: null, // what's being dragged: {bot, from}
  };
  O.bot = (id) => O.S.bots.find((b) => b.id === id);
  O.isBusy = (b) => !!b && b.status !== "idle";

  // ---------------------------------------------------------------------------------------------- API
  class ApiErr extends Error {
    constructor(msg, status, data) { super(msg); this.status = status; this.data = data; }
  }
  O.api = async function api(method, path, body, opts = {}) {
    const r = await fetch("/api/" + path, {
      method,
      keepalive: !!opts.keepalive,
      headers: { "X-Oomi-Token": O.TOKEN, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    let data = {};
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) throw new ApiErr(data.error || `Request failed (${r.status})`, r.status, data);
    return data;
  };
  O.GET = (p) => O.api("GET", p);
  O.POST = (p, b = {}) => O.api("POST", p, b);

  O.store = function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem("oomi." + key);
      if (value === null) localStorage.removeItem("oomi." + key);
      else localStorage.setItem("oomi." + key, value);
    } catch (_) { return null; }
    return null;
  };

  // ---------------------------------------------------------------------------------------------- preferences
  O.PREFS = {
    tool_steps: "simple", // simple | detailed | hidden
    text_size: "normal", // small | normal | large
    density: "comfortable", // comfortable | compact
    chat_width: "normal", // narrow | normal | wide | full
    timestamps: "separators", // separators | every | off
    reply_cost: false,
    dim_unfocused: false, // panes you are not using stay as bright as the one you are
    side_group: "section", // section | status | folder | none
    side_sort: "recent", // recent | name | cost | manual
    sidebar_hidden: false,
    side_collapsed: [],
  };
  const PREF_CHOICES = {
    tool_steps: ["simple", "detailed", "hidden"], text_size: ["small", "normal", "large"], density: ["comfortable", "compact"],
    chat_width: ["narrow", "normal", "wide", "full"], timestamps: ["separators", "every", "off"],
    side_group: ["section", "status", "folder", "none"], side_sort: ["recent", "name", "cost", "manual"],
  };
  // a saved preference, or the default if it's missing or not something this version understands
  O.pref = (k) => {
    const p = O.S.ui.prefs && typeof O.S.ui.prefs === "object" ? O.S.ui.prefs : {};
    const d = O.PREFS[k];
    const v = p[k];
    if (v === undefined || v === null) return d;
    if (Array.isArray(d)) return Array.isArray(v) ? v.filter((x) => typeof x === "string") : d;
    if (typeof v !== typeof d) return d;
    if (PREF_CHOICES[k] && !PREF_CHOICES[k].includes(v)) return d;
    return v;
  };
  O.savedLayouts = () => (Array.isArray(O.S.ui.layouts) ? O.S.ui.layouts.filter((x) => x && typeof x.name === "string" && x.shape && typeof x.shape === "object") : []);
  O.applyPrefs = function applyPrefs() {
    const d = document.documentElement;
    d.dataset.text = O.pref("text_size");
    d.dataset.density = O.pref("density");
    d.dataset.width = O.pref("chat_width");
    d.dataset.steps = O.pref("tool_steps");
    d.dataset.stamps = O.pref("timestamps");
    d.classList.toggle("dim-unfocused", !!O.pref("dim_unfocused"));
    d.classList.toggle("reply-cost", !!O.pref("reply_cost"));
  };
  // Window state is saved on the server (so every window and the next launch see it), a moment after it changes.
  let uiTimer = null;
  let uiPending = {};
  O.saveUi = function saveUi(patch, now = false) {
    Object.assign(O.S.ui, patch);
    Object.assign(uiPending, patch);
    clearTimeout(uiTimer);
    const go = () => {
      const body = uiPending;
      uiPending = {};
      if (Object.keys(body).length) O.POST("ui", body).catch(() => {});
    };
    if (now) go(); else uiTimer = setTimeout(go, 400);
  };
  // Preferences are sent one by one and merged on the server, so two windows don't undo each other's changes.
  let prefPending = {};
  let prefTimer = null;
  O.setPref = function setPref(k, v) {
    O.S.ui.prefs = { ...(O.S.ui.prefs && typeof O.S.ui.prefs === "object" ? O.S.ui.prefs : {}), [k]: v };
    prefPending[k] = v;
    clearTimeout(prefTimer);
    prefTimer = setTimeout(() => { const body = prefPending; prefPending = {}; O.POST("ui", { prefs: body }).catch(() => {}); }, 300);
    O.applyPrefs();
  };
  // another window changed preferences or saved layouts
  O.remoteUi = function remoteUi(ui) {
    O.S.ui.prefs = { ...(ui.prefs && typeof ui.prefs === "object" ? ui.prefs : {}), ...prefPending };
    O.S.ui.layouts = ui.layouts;
    O.applyPrefs();
  };

  // ---------------------------------------------------------------------------------------------- formatting
  const fmtTime = (O.fmtTime = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }));
  const fmtDay = new Intl.DateTimeFormat(undefined, { weekday: "short" });
  O.fmtDate = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
  const fmtFull = (O.fmtFull = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" }));
  O.dayDiff = function dayDiff(d) {
    const a = new Date(d); a.setHours(0, 0, 0, 0);
    const b = new Date(); b.setHours(0, 0, 0, 0);
    return Math.round((b - a) / 86400000);
  };
  O.sideTime = function sideTime(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000), n = O.dayDiff(d);
    if (n === 0) return fmtTime.format(d);
    if (n === 1) return "Yesterday";
    if (n < 7) return fmtDay.format(d);
    return O.fmtDate.format(d);
  };
  O.sepTime = function sepTime(ts) {
    const d = new Date(ts * 1000), n = O.dayDiff(d);
    const t = fmtTime.format(d);
    if (n === 0) return `Today ${t}`;
    if (n === 1) return `Yesterday ${t}`;
    return `${fmtFull.format(d)}, ${t}`;
  };
  O.money = (v) => (v >= 10 ? `$${v.toFixed(0)}` : v >= 0.995 ? `$${v.toFixed(2)}` : v > 0 ? `$${v.toFixed(v < 0.01 ? 3 : 2)}` : "$0");
  O.plural = (n, one, many) => (n === 1 ? one : many.replace("N", n));
  O.tildify = (p) => (O.S.home && p && p.startsWith(O.S.home) ? "~" + p.slice(O.S.home.length) : p || "");
  O.basename = (p) => String(p || "").replace(/\/+$/, "").split("/").pop();
  // previews: Markdown markers removed
  O.plain = (s) => String(s || "").replace(/```[\s\S]*?(```|$)/g, " ").replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`~#>|]+/g, " ").replace(/(^|\s)[-:]{3,}(?=\s|$)/g, " ").replace(/\s+/g, " ").trim();
  O.clip = (s, n) => { s = String(s || "").replace(/\s+/g, " "); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
  O.tok = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n));
  O.tokens = (u) => `${O.tok(u.in)} in, ${O.tok(u.out)} out`;
  const MODEL_NAMES = { "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5", "claude-haiku-5-5": "Haiku 5.5", "claude-fable-5-1": "Fable 5.1" };
  O.modelName = (m) => MODEL_NAMES[m] || m;
  O.TRUST = {
    "read-only": { label: "Read-only", desc: "Looks, doesn't touch. Only read-only commands." },
    project: { label: "Project", desc: "Edits this folder. Commands run in the macOS sandbox." },
    full: { label: "Full", desc: "No sandbox. Risky commands still ask first." },
  };
  const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  O.MAC = MAC;
  // ⌘ on a Mac, Ctrl elsewhere
  O.keys = (s) => (MAC ? s : s.replace(/⌘/g, "Ctrl+").replace(/⌥/g, "Alt+").replace(/⇧/g, "Shift+").replace(/⌃/g, "Ctrl+"));

  // A bot's state as a mark: working, waiting for you, or something new to read.
  O.statusOf = (b) => (!b ? "" : b.status === "waiting" ? "waiting" : b.status === "working" || b.status === "stopping" ? "working" : b.unread ? "unread" : "");
  O.mark = function mark(b, cls = "") {
    const s = O.statusOf(b);
    const title = { waiting: "Needs your OK", working: b && b.step ? b.step : "Working", unread: "New messages" }[s];
    return s ? h("span", { class: `mark ${s} ${cls}`, title }) : null;
  };

  // What will run, shown in full: long gaps, tabs and invisible or direction-changing characters are marked.
  O.visible = function visible(text) {
    const out = [];
    const re = /( {4,}|\t+|[\u0000-\u0008\u000b-\u001f\u007f-\u009f­͏؜ᅟᅠ឴឵᠎​-‏‪-‮⁠-⁯ㅤ︀-️﻿ﾠ￰-￻])/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      out.push(text.slice(last, m.index));
      const t = m[0];
      const label = t[0] === " " ? `${t.length} spaces` : t[0] === "\t" ? O.plural(t.length, "tab", "N tabs") : `U+${t.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")}`;
      out.push(h("span", { class: "ws", title: "Hidden or unusual characters" }, label));
      last = m.index + t.length;
    }
    out.push(text.slice(last));
    return out;
  };

  // ---------------------------------------------------------------------------------------------- toast
  let toastTimer = null;
  O.toast = function toast(msg, ms = 2400) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), ms);
  };
  O.fail = (e) => O.toast(e && e.message ? e.message : String(e), 4200);
  O.copyText = async function copyText(text, msg = "Copied") {
    try { await navigator.clipboard.writeText(text); }
    catch (_) {
      const ta = h("textarea", { style: "position:fixed;opacity:0" });
      ta.value = text; document.body.append(ta); ta.select();
      try { document.execCommand("copy"); } catch (__) { /* ignore */ }
      ta.remove();
    }
    O.toast(msg, 1400);
  };
  O.downloadJson = function downloadJson(name, data) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = h("a", { href: url, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  };
  O.pickJson = function pickJson() {
    return new Promise((resolve, reject) => {
      const input = h("input", { type: "file", accept: "application/json,.json", style: "display:none" });
      input.addEventListener("change", () => {
        const f = input.files[0];
        input.remove();
        if (!f) return;
        if (f.size > 2_000_000) return reject(new Error("That file is too big."));
        f.text().then((t) => { try { resolve(JSON.parse(t)); } catch (_) { reject(new Error("That isn't a JSON file.")); } }, reject);
      });
      document.body.append(input);
      input.click();
    });
  };

  // ---------------------------------------------------------------------------------------------- menus & dialogs
  let menuEl = null;
  O.menuOpen = () => !!menuEl;
  O.closeMenu = function closeMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } };
  // entries: {label, icon, desc, on, keys, run, disabled} | "-" | {node} | {head}
  O.openMenu = function openMenu(anchor, entries, opts = {}) {
    O.closeMenu();
    const m = h("div", { class: "menu", role: "menu" });
    for (const e of entries.filter(Boolean)) {
      if (e === "-") { m.append(h("hr")); continue; }
      if (e.node) { m.append(e.node); continue; }
      if (e.head) { m.append(h("div", { class: "menu-head" }, e.head)); continue; }
      m.append(h("button", {
        type: "button", role: "menuitem", class: e.on ? "on" : "", disabled: !!e.disabled,
        onclick: (ev) => { ev.stopPropagation(); O.closeMenu(); e.run(); },
      }, e.icon ? O.icon(e.icon, 16) : h("span", { class: "ic-gap" }), h("span", { class: "grow" }, e.label, e.desc ? h("span", { class: "d" }, e.desc) : null),
      e.keys ? h("kbd", {}, O.keys(e.keys)) : null));
    }
    document.body.append(m);
    const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: opts.x || 0, right: opts.x || 0, top: opts.y || 0, bottom: opts.y || 0, width: 0 };
    const mw = m.offsetWidth, mh = m.offsetHeight;
    let left = opts.align === "right" ? r.right - mw : opts.align === "center" ? r.left + r.width / 2 - mw / 2 : r.left;
    let top = opts.up ? r.top - mh - 6 : r.bottom + 6;
    if (top + mh > innerHeight - 8) top = r.top - mh - 6;
    if (top < 8) top = 8;
    left = Math.max(8, Math.min(left, innerWidth - mw - 8));
    m.style.left = left + "px"; m.style.top = top + "px";
    menuEl = m;
    const first = m.querySelector("button");
    if (first && opts.focus) first.focus();
    return m;
  };
  O.contextMenu = (e, entries) => { e.preventDefault(); O.openMenu(null, entries, { x: e.clientX, y: e.clientY }); };
  document.addEventListener("mousedown", (e) => { if (menuEl && !menuEl.contains(e.target)) O.closeMenu(); });

  let modalEl = null;
  O.modalOpen = () => !!modalEl;
  O.closeModal = function closeModal() { if (modalEl) { modalEl.remove(); modalEl = null; } };
  O.modal = function modal(build, cls = "") {
    O.closeModal();
    const box = h("div", { class: `modal ${cls}`, role: "dialog", "aria-modal": "true" });
    const scrim = h("div", { class: "scrim", onmousedown: (e) => { if (e.target === scrim) O.closeModal(); } }, box);
    build(box, O.closeModal);
    document.body.append(scrim);
    modalEl = scrim;
    const f = box.querySelector("input, textarea, select, button.primary");
    if (f) setTimeout(() => f.focus(), 30);
    return box;
  };
  O.confirmBox = function confirmBox(title, text, okLabel, onOk, danger = false) {
    O.modal((box, close) => {
      const ok = h("button", { class: "btn primary", type: "button", onclick: async () => {
        ok.disabled = true;
        try { await onOk(); close(); } catch (e) { ok.disabled = false; O.fail(e); }
      } }, okLabel);
      box.append(h("h3", {}, title), h("p", { class: "lead" }, text),
        h("div", { class: "foot" }, h("button", { class: "btn quiet", type: "button", onclick: close }, "Cancel"), ok));
      if (danger) ok.classList.add("danger");
    });
  };
  O.promptBox = function promptBox(title, lead, value, okLabel, onOk, opts = {}) {
    O.modal((box, close) => {
      const input = h("input", { class: "text-input", value: value || "", placeholder: opts.placeholder || "", maxlength: String(opts.max || 60) });
      if (opts.list) { const id = "dl-" + Math.random().toString(36).slice(2); input.setAttribute("list", id); box.append(h("datalist", { id }, opts.list.map((v) => h("option", { value: v })))); }
      const err = h("div", { class: "err hidden" });
      const ok = h("button", { class: "btn primary", type: "submit" }, okLabel);
      box.append(h("h3", {}, title), lead ? h("p", { class: "lead" }, lead) : null, h("form", { onsubmit: async (e) => {
        e.preventDefault(); ok.disabled = true;
        try { await onOk(input.value.trim()); close(); } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); ok.disabled = false; }
      } }, input, err, h("div", { class: "foot" }, h("button", { class: "btn quiet", type: "button", onclick: close }, "Cancel"), ok)));
      setTimeout(() => input.select(), 40);
    });
  };

  // Approve-type buttons stay off until what they approve has been seen whole: it's measured once it's actually on
  // screen (a card made in a minimized or hidden pane can't be), and if it scrolls, only after scrolling to the end.
  O.gateAllow = function gateAllow(what, buttons, note, opts = {}) {
    buttons = buttons.filter(Boolean);
    let seenEnd = !!opts.seen;
    let busy = false;
    const set = (locked, showNote) => {
      if (!busy) buttons.forEach((b) => (b.disabled = locked));
      if (note) note.classList.toggle("hidden", !showNote);
    };
    const check = () => {
      if (busy) return;
      if (!what.isConnected || !what.clientHeight) return set(true, false);
      const overflow = what.scrollHeight > what.clientHeight + 4;
      what.classList.toggle("scrolls", overflow);
      if (!overflow || what.scrollTop + what.clientHeight >= what.scrollHeight - 4) {
        if (!seenEnd && opts.onSeen) opts.onSeen();
        seenEnd = true;
      }
      set(overflow && !seenEnd, overflow && !seenEnd);
    };
    set(true, false);
    const ro = new ResizeObserver(check);
    ro.observe(what);
    what.addEventListener("scroll", check);
    return { stop: () => ro.disconnect(), busy: (v) => { busy = v; if (!v) check(); } };
  };

  // Markdown, with escaped plain text if the renderer ever fails
  O.renderMd = function renderMd(text) {
    try { return window.OomiMarkdown.render(text); }
    catch (_) { return `<p style="white-space:pre-wrap">${window.OomiMarkdown.escape(text)}</p>`; }
  };

  // small form controls shared by the panels
  O.toggleRow = function toggleRow(title, help, on, onChange, key) {
    const t = h("span", { class: `toggle${on ? " on" : ""}`, role: "switch", "aria-checked": on ? "true" : "false" });
    return h("label", { class: "switch", dataset: key ? { key } : {}, onclick: async () => {
      const v = !t.classList.contains("on");
      t.classList.toggle("on", v); t.setAttribute("aria-checked", v ? "true" : "false");
      try { await onChange(v); } catch (e) { t.classList.toggle("on", !v); O.fail(e); }
    } }, h("span", {}, h("div", { class: "t" }, title), help ? h("div", { class: "help" }, help) : null), t);
  };
  O.select = function select(options, value, onChange) {
    const sel = h("select", { onchange: async () => { try { await onChange(sel.value); O.toast("Saved", 1000); } catch (e) { O.fail(e); } } },
      options.map(([v, label]) => h("option", { value: v, selected: v === value }, label)));
    return sel;
  };
  // A row of buttons, one of which is chosen
  O.segmented = function segmented(options, value, onChange) {
    const box = h("div", { class: "seg", role: "radiogroup" });
    const paint = (v) => box.replaceChildren(...options.map(([k, label]) => h("button", {
      type: "button", role: "radio", class: k === v ? "on" : "", "aria-checked": k === v ? "true" : "false",
      onclick: async () => { paint(k); try { await onChange(k); } catch (e) { paint(v); O.fail(e); } },
    }, label)));
    paint(value);
    return box;
  };
  O.field = (label, control, help, key) => h("div", { class: "field", dataset: key ? { key } : {} },
    h("span", { class: "lab" }, label), control, help ? h("span", { class: "help" }, help) : null);
})();
