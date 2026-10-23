// oomi — everything that opens over or beside the conversations: settings, the bot and changes drawers, the
// overview of every bot, the command palette, and making a bot.
(() => {
  "use strict";

  const O = window.Oomi;
  const { h, icon, avatar, $, S, bot, GET, POST } = O;

  // ---------------------------------------------------------------------------------------------- drawer
  // One drawer per window; it shows the bot in the pane you're using and follows you between panes.
  O.closeDrawer = function closeDrawer() {
    S.drawer = null;
    $("#drawer").classList.add("hidden");
    $("#app").classList.remove("drawer-open");
    O.ws.refreshHeads();
  };
  O.openDrawer = async function openDrawer(name) {
    const id = O.ws.focusedBot();
    if (!id) return O.closeDrawer();
    S.drawer = name;
    S.drawerBot = id;
    const d = $("#drawer");
    d.classList.remove("hidden");
    $("#app").classList.add("drawer-open");
    const b = bot(id);
    const titles = { changes: "Changes", bot: "Bot settings" };
    const body = h("div", { class: "drawer-body" });
    d.replaceChildren(h("div", { class: "drawer-head" }, avatar(b.avatar, "sm", b.color), h("div", { class: "grow" }, h("h3", {}, titles[name] || name), h("div", { class: "sub" }, b.name)),
      h("button", { type: "button", class: "icon-btn", title: "Close (Esc)", onclick: O.closeDrawer }, icon("x", 18))), body);
    O.ws.refreshHeads();
    try { await ({ changes: drawChanges, bot: drawBot }[name])(body, id); }
    catch (e) { body.append(h("div", { class: "err" }, e.message)); }
  };
  O.toggleDrawer = (name) => { if (S.drawer === name && S.drawerBot === O.ws.focusedBot()) O.closeDrawer(); else O.openDrawer(name); };
  // the pane you're using changed
  O.drawerFollow = () => { if (S.drawer && S.drawerBot !== O.ws.focusedBot()) O.openDrawer(S.drawer); };
  O.refreshDrawer = (name, id) => { if (S.drawer === name && (!id || S.drawerBot === id)) O.openDrawer(name); };

  async function drawChanges(body, id) {
    const b = bot(id);
    const data = await GET(`bots/${b.id}/changes`);
    if (!body.isConnected) return;
    if (!data.items.length && !data.snapshots.length) {
      body.append(h("p", { class: "empty-note", style: "padding:6px 0" }, `${b.name} hasn't changed any files in this conversation.`));
      return;
    }
    const diffBox = h("div");
    body.append(h("div", { class: "section" }, `Files (${data.items.length})`));
    body.append(h("div", { class: "list" }, data.items.map((c) => h("div", { class: "li" },
      h("span", { class: `tag${c.status === "created" ? " solid" : ""}` }, c.status),
      h("div", { class: "grow" }, h("div", { style: "font-family:var(--mono);font-size:12.5px" }, c.path), h("div", { class: "small" }, c.detail)),
      h("div", { class: "mini" }, c.detail.includes("+") ? h("button", { type: "button", onclick: () => showDiff(c.path) }, "Diff") : null)))));
    async function showDiff(path) {
      const r = await GET(`bots/${b.id}/diff?path=${encodeURIComponent(path)}`).catch((e) => ({ diff: e.message }));
      diffBox.replaceChildren(h("div", { class: "section" }, "Diff", h("button", { type: "button", class: "btn quiet", style: "height:26px", onclick: () => diffBox.replaceChildren() }, "Hide")), renderDiff(r.diff || "No differences."));
      diffBox.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (data.snapshots.length) {
      body.append(h("div", { class: "section" }, "Snapshots"), h("div", { class: "list" }, data.snapshots.map((s) => h("div", { class: "li" },
        icon("folder", 16), h("div", { class: "grow" }, `Before ${s.label}`, h("div", { class: "small" }, s.at ? O.sepTime(s.at) : ""))))));
    }
    const undoOne = h("button", { type: "button", class: "btn primary", onclick: () => undo(false) }, icon("undo", 16), "Undo last change");
    const undoAll = h("button", { type: "button", class: "btn", onclick: () => undo(true) }, "Undo everything");
    body.append(h("div", { class: "section" }, "Undo"), h("p", { class: "small", style: "color:var(--fg-3);font-size:12.5px;margin:0 0 10px" }, data.next ? `Next step would ${data.next}.` : "Nothing left to undo."),
      h("div", { class: "row-inline" }, undoOne, undoAll), diffBox);
    if (!data.next) { undoOne.disabled = true; undoAll.disabled = true; }
    async function undo(all, confirmed = false) {
      try {
        const r = await POST(`bots/${b.id}/undo`, { all, confirm: confirmed });
        if (r.needs_confirm) {
          O.confirmBox("Restore the snapshot?", `This will ${r.needs_confirm}, removing files created since then, including any you made yourself.`, "Restore", () => undo(all, true));
          return;
        }
        if (r.error) O.toast(`Stopped: ${r.error}`, 5000);
        else O.toast(r.done.length ? `Undid ${O.plural(r.done.length, "1 step", "N steps")}` : "Nothing to undo");
      } catch (e) { O.fail(e); }
      O.refreshDrawer("changes", b.id);
    }
  }
  function renderDiff(text) {
    const box = h("div", { class: "diff" });
    for (const line of text.split("\n")) {
      const cls = line.startsWith("+++") || line.startsWith("---") ? "file" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "";
      if (cls === "file" && line.startsWith("---")) continue;
      box.append(h("div", { class: cls }, cls === "file" ? line.replace(/^\+\+\+ b\//, "") : line || " "));
    }
    return box;
  }

  async function drawBot(body, id) {
    const b = bot(id);
    if (!b) return;
    const s = S.settings.models ? S.settings : (S.settings = await GET("settings"));
    const set = (patch) => O.setBot(patch, id);
    const redraw = () => O.refreshDrawer("bot", id);
    const name = h("input", { value: b.name, maxlength: "40" });
    name.addEventListener("change", () => set({ name: name.value }).then(() => O.toast("Renamed", 1000)).catch(O.fail));
    body.append(O.field("Name", name));
    body.append(O.field("Color", O.colorPicker(b.color || O.appColor(), (c) => set({ color: c }).then(redraw), { custom: true })));
    // A bot made without a folder keeps its own private one, so there is nothing to show or change
    if (!b.private_folder) body.append(O.field("Folder", h("div", { class: "row-inline" }, h("div", { class: "grow mono-line" }, O.tildify(b.folder)),
      S.native.pick_folder ? h("button", { type: "button", class: "btn", onclick: async () => {
        if (b.status !== "idle") return O.toast("Stop the bot first.");
        const r = await POST("native", { action: "pick_folder", start: b.folder }).catch(O.fail);
        if (r && r.result) O.confirmBox("Move to this folder?", `${b.name} will start a fresh conversation in ${O.tildify(r.result)}.`, "Move", () => set({ folder: r.result }).then(() => { redraw(); O.ws.reload(id); }));
      } }, "Change…") : null),
    "Changing the folder starts a fresh conversation."));
    body.append(O.field("Trust", h("div", { class: "choices" },
      Object.entries(O.TRUST).map(([k, v]) => h("button", { type: "button", class: `choice${b.trust === k ? " on" : ""}`, onclick: () => {
        if (k === "full" && b.trust !== "full") O.confirmBox("Turn off the sandbox?", "With full trust, commands run with your full access to files and the network. Risky ones still ask first.", "Use full trust", () => set({ trust: k }).then(redraw));
        else set({ trust: k }).then(redraw).catch(O.fail);
      } }, h("span", { class: "radio" }), h("span", {}, h("div", { class: "t" }, v.label), h("div", { class: "d" }, v.desc)))))));

    // standing instructions, read at the start of every conversation
    const instr = h("textarea", { class: "instr", rows: "5", maxlength: "4000", placeholder: "For example: Answer briefly. Use pnpm, never npm. Ask before adding a dependency." });
    instr.value = b.instructions || "";
    const instrSave = h("button", { type: "button", class: "btn", disabled: true, onclick: async () => {
      instrSave.disabled = true;
      try { await set({ instructions: instr.value }); O.toast("Saved. It follows them from its next message.", 2600); } catch (e) { O.fail(e); instrSave.disabled = false; }
    } }, "Save");
    instr.addEventListener("input", () => { instrSave.disabled = instr.value === (bot(id).instructions || ""); });
    body.append(h("div", { class: "field", dataset: { key: "instructions" } }, h("span", { class: "lab" }, "Instructions"), instr,
      h("div", { class: "row-inline" }, h("span", { class: "help grow" }, "Standing instructions this bot always follows. CLAUDE.md and AGENTS.md in its folder are read too (see Settings, Safety)."), instrSave)));

    body.append(O.field("Model", O.select([["", `App default (${O.modelName(s.model)})`]].concat(s.models.map((m) => [m, O.modelName(m)])), b.model, (v) => set({ model: v }))));
    body.append(O.field("Effort", O.select([["", `App default (${s.effort})`]].concat(s.efforts.map((e) => [e, e[0].toUpperCase() + e.slice(1)])), b.effort, (v) => set({ effort: v }))));
    const budget = h("input", { type: "number", min: "0", max: "1000", step: "0.5", value: b.budget ? String(b.budget) : "", placeholder: `App default ($${s.max_session_cost_usd})` });
    budget.addEventListener("change", () => set({ budget: Number(budget.value || 0) }).then(() => O.toast("Saved", 1000)).catch(O.fail));
    body.append(O.field("Spending limit ($)", budget, b.cost ? `${O.money(b.cost)} spent since you opened oomi.` : "Nothing spent since you opened oomi."));

    // sidebar
    body.append(h("div", { class: "section" }, "Sidebar"),
      O.toggleRow("Pinned", "Keep it at the top of the sidebar.", b.pinned, (v) => set({ pinned: v })));

    // commands it may run without asking
    if (b.allow && b.allow.length) {
      body.append(h("div", { class: "section", dataset: { key: "allow" } }, "Always allowed", h("button", { type: "button", class: "btn quiet", style: "height:26px", onclick: () => O.confirmBox("Ask again for everything?", `${b.name} will ask before running any of these commands again.`, "Remove all", () => set({ allow: [] }).then(redraw)) }, "Remove all")),
        h("p", { class: "small-note" }, "These exact commands run without asking, in every conversation with this bot."),
        h("div", { class: "list" }, b.allow.map((c) => h("div", { class: "li" }, h("div", { class: "grow mono-line" }, c),
          h("div", { class: "mini" }, h("button", { type: "button", onclick: () => set({ allow: b.allow.filter((x) => x !== c) }).then(redraw).catch(O.fail) }, "Remove"))))));
    }

    // what fills its conversation
    const ctxBox = h("div", { class: "ctx" }, h("div", { class: "small-note" }, "Measuring…"));
    body.append(h("div", { class: "section", dataset: { key: "context" } }, "Conversation size",
      h("button", { type: "button", class: "btn quiet", style: "height:26px", title: "Summarize older turns now to make room", onclick: async (e) => {
        if (bot(id).status !== "idle") return O.toast("Wait until it finishes.");
        e.currentTarget.disabled = true;
        try { const r = await POST(`bots/${id}/compact`); O.toast(r.compacted ? "Compacted older turns into a summary" : "Nothing old enough to compact yet"); paintCtx(r.context); }
        catch (er) { O.fail(er); }
        e.currentTarget.disabled = false;
      } }, "Compact now")), ctxBox);
    const paintCtx = (c) => {
      const total = c.parts.reduce((a, p) => a + p.tokens, 0) || 1;
      ctxBox.replaceChildren(
        h("div", { class: "ctx-bar" }, c.parts.map((p, i) => h("div", { class: `seg-${i % 6}`, style: `flex:${p.tokens}`, title: `${p.name}: about ${O.tok(p.tokens)} tokens` }))),
        h("div", { class: "ctx-legend" }, c.parts.map((p, i) => h("div", {}, h("i", { class: `seg-${i % 6}` }), h("span", { class: "grow" }, p.name), h("span", { class: "n" }, `${O.tok(p.tokens)} (${Math.round((p.tokens / total) * 100)}%)`)))),
        h("div", { class: "small-note" }, (c.actual ? `${O.tok(c.actual)} tokens at the last reply. ` : "Not started yet. ") + `Older turns are summarized at ${O.tok(c.compact_at)}. The split is an estimate.`));
    };
    GET(`bots/${id}/context`).then((c) => { if (ctxBox.isConnected) paintCtx(c); }).catch((e) => ctxBox.replaceChildren(h("div", { class: "err" }, e.message)));

    const sched = h("div", { class: "hidden", dataset: { key: "schedules" } }); // stays hidden unless the server has schedules
    const apps = h("div", { dataset: { key: "apps" } });
    body.append(sched, apps);
    drawSchedules(sched, b);
    drawBotApps(apps, b).catch(() => {});

    // what it has spent and learned; then its own memory and skills (the shared ones are in Settings)
    const usageBox = h("div", { class: "stat-grid three" });
    body.append(h("div", { class: "section" }, "Usage", h("button", { type: "button", class: "btn quiet", style: "height:26px", onclick: () => O.openSettings("status") }, "All bots")), usageBox);
    GET("usage").then((u) => {
      const mine = u.bots.find((x) => x.id === id);
      if (!mine || !usageBox.isConnected) return;
      const card = (label, x) => h("div", { class: "stat" }, h("div", { class: "v" }, O.money(x.usd)), h("div", { class: "k" }, label), h("div", { class: "t" }, x.calls ? O.tokens(x) : "No requests"));
      const count = (v, label, t) => h("div", { class: "stat" }, h("div", { class: "v" }, String(v)), h("div", { class: "k" }, label), h("div", { class: "t" }, t));
      const tried = mine.skill_wins + mine.skill_fails;
      usageBox.replaceChildren(card("Today", mine.today), card("Last 30 days", mine.month), card("All time", mine.all),
        count(mine.memories, mine.memories === 1 ? "Memory of its own" : "Memories of its own", `Plus ${u.general.memories} shared`),
        count(mine.skills, mine.skills === 1 ? "Skill of its own" : "Skills of its own",
          tried ? `Worked ${mine.skill_wins} of ${tried} times` : mine.skill_uses ? O.plural(mine.skill_uses, "Used once", "Used N times") : `Plus ${u.general.skills} shared`));
    }).catch(() => {});
    const mem = h("div", { dataset: { key: "memory" } }), skills = h("div", { dataset: { key: "skills" } });
    body.append(h("div", { class: "section" }, "This bot's memory"), mem, h("div", { class: "section" }, "This bot's skills"), skills);
    drawMemory(mem, id).catch((e) => mem.append(h("div", { class: "err" }, e.message)));
    drawSkills(skills, id).catch((e) => skills.append(h("div", { class: "err" }, e.message)));
    body.append(h("div", { class: "section" }, "This bot"), h("div", { class: "row-inline wrap" },
      h("button", { type: "button", class: "btn", onclick: () => O.freshBot(id) }, icon("fresh", 16), "Start fresh"),
      h("button", { type: "button", class: "btn", onclick: () => O.cloneBot(id) }, icon("copy", 16), "Duplicate"),
      h("button", { type: "button", class: "btn", onclick: () => O.exportBot(id) }, icon("download", 16), "Export"),
      O.WIN.kind !== "bot" ? h("button", { type: "button", class: "btn", onclick: () => O.popOut(id) }, icon("popout", 16), "Own window") : null,
      h("button", { type: "button", class: "btn", onclick: () => O.deleteBot(id) }, icon("trash", 16), "Delete")));
  }

  // Scheduled runs: the list, a switch and delete on each, and a form to add one. Any "schedule" event refreshes it.
  const stamp = (v) => {
    const t = typeof v === "number" ? (v > 1e11 ? v / 1000 : v) : Date.parse(v) / 1000;
    if (!t) return "";
    const d = new Date(t * 1000), n = O.dayDiff(d);
    return `${n === 0 ? "today" : n === -1 ? "tomorrow" : n === 1 ? "yesterday" : O.fmtFull.format(d)} at ${O.fmtTime.format(d)}`;
  };
  function drawSchedules(box, b) {
    const me = S.schedules = { bot: b.id, refresh: () => fill() };
    const path = `bots/${b.id}/schedules`;
    const list = h("div");
    const when = h("input", { class: "text-input", placeholder: "When — e.g. weekdays at 8:00", maxlength: "200", "aria-label": "When" });
    const what = h("input", { class: "text-input", placeholder: "What to do", maxlength: "2000", "aria-label": "What to do" });
    const err = h("div", { class: "err hidden" });
    const add = h("button", { class: "btn", type: "submit" }, "Add");
    const form = h("form", { class: "sched-add", autocomplete: "off", onsubmit: async (e) => {
      e.preventDefault(); err.classList.add("hidden"); add.disabled = true;
      try { await POST(path, { when: when.value.trim(), prompt: what.value.trim() }); when.value = ""; what.value = ""; await fill(); O.toast("Scheduled", 1200); }
      catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
      add.disabled = false;
    } }, when, what, err, add);
    const row = (sc) => {
      const on = sc.enabled !== false;
      const sw = h("button", { type: "button", class: `toggle${on ? " on" : ""}`, role: "switch", "aria-checked": String(on), title: on ? "Pause" : "Resume", onclick: async () => {
        const v = !sw.classList.contains("on");
        sw.classList.toggle("on", v); sw.setAttribute("aria-checked", String(v));
        try { await O.api("PATCH", `${path}/${sc.id}`, { enabled: v }); }
        catch (ex) { sw.classList.toggle("on", !v); sw.setAttribute("aria-checked", String(!v)); O.fail(ex); }
      } });
      const next = on ? (sc.next_run ? `Next ${stamp(sc.next_run)}` : "") : "Paused";
      const last = sc.last_run ? `Last ${stamp(sc.last_run)}${sc.last_status ? `, ${sc.last_status}` : ""}` : "";
      return h("div", { class: "routine" },
        h("div", { class: "grow" }, h("div", { class: "rt", title: sc.prompt }, O.clip(sc.prompt, 60)), h("div", { class: "rs" }, sc.describe || sc.when),
          next || last ? h("div", { class: "rs dim" }, [next, last].filter(Boolean).join(" · ")) : null),
        h("button", { type: "button", class: "icon-btn rdel", title: "Delete", "aria-label": "Delete routine",
          onclick: async () => { try { await O.api("DELETE", `${path}/${sc.id}`); await fill(); } catch (ex) { O.fail(ex); } } }, icon("trash", 15)),
        sw);
    };
    async function fill() {
      let data;
      try { data = await GET(path); }
      catch (ex) { if (ex.status === 404) box.classList.add("hidden"); return; } // an older server: no section
      if (S.schedules !== me) return; // the drawer moved on
      const items = data.schedules || [];
      list.replaceChildren(items.length ? h("div", { class: "routines" }, items.map(row)) : h("p", { class: "small-note" }, "No routines yet. A routine runs in this conversation while oomi is open; anything that would ask is declined."));
      box.classList.remove("hidden");
    }
    box.append(h("div", { class: "section" }, "Routines"), list, form);
    return fill();
  }

  // Which connected apps this bot may use (all of them unless you switch one off)
  async function drawBotApps(box, b) {
    const data = await GET("apps");
    const mine = data.apps.filter((a) => a.connected);
    if (!box.isConnected) return;
    box.append(h("div", { class: "section" }, "Apps", h("button", { type: "button", class: "btn quiet", style: "height:26px", onclick: () => O.openSettings("apps") }, "Manage")));
    if (!mine.length) { box.append(h("p", { class: "small-note" }, "No apps connected yet. Connect Notion, GitHub, Linear and more in Settings, Apps.")); return; }
    let off = new Set(b.apps_off || []);
    box.append(h("div", { class: "list" }, mine.map((a) => {
      const t = h("span", { class: `toggle${off.has(a.id) ? "" : " on"}` });
      return h("label", { class: "li", style: "align-items:center;cursor:pointer", onclick: async (e) => {
        e.preventDefault();
        const next = new Set(off);
        next.has(a.id) ? next.delete(a.id) : next.add(a.id);
        t.classList.toggle("on", !next.has(a.id));
        try { await O.setBot({ apps_off: [...next] }, b.id); off = next; } catch (ex) { t.classList.toggle("on", !off.has(a.id)); O.fail(ex); }
      } }, appLogo(a, "sm"), h("div", { class: "grow" }, a.name, h("div", { class: "small" }, O.plural(a.tools, "1 tool", "N tools"))), t);
    })));
  }
  const appLogo = (a, cls = "") => h("span", { class: `app-logo ${cls}`, style: `background:${a.color || "#444"}` }, a.name[0]);

  // ---------------------------------------------------------------------------------------------- bot actions
  O.setBot = async function setBot(patch, id) {
    const st = await O.api("PATCH", `bots/${id}`, patch);
    O.updateBot(st);
    return st;
  };
  O.sectionBox = function sectionBox(id, sections) {
    const b = bot(id);
    O.promptBox("Section", "Bots in the same section are listed together. Leave it empty for none.", b.section || "", "Save",
      (v) => O.setBot({ section: v }, id).then(() => O.refreshDrawer("bot", id)), { list: sections, placeholder: "Like Work or Clients", max: 40 });
  };
  O.freshBot = function freshBot(id) {
    const b = bot(id);
    O.confirmBox("Start fresh?", `${b.name} keeps its folder, instructions and what it learned about you, but starts a new conversation. Your messages stay visible above a divider.`, "Start fresh",
      async () => { O.updateBot(await POST(`bots/${id}/fresh`)); O.ws.reload(id); });
  };
  O.deleteBot = function deleteBot(id) {
    const b = bot(id);
    O.confirmBox(`Delete ${b.name}?`, `This removes the bot and its chat history. Its folder (${O.tildify(b.folder)}) and everything in it stays where it is.`, "Delete bot",
      async () => { await O.api("DELETE", `bots/${id}`); O.botGone(id); }, true);
  };
  O.cloneBot = async function cloneBot(id) {
    try {
      const st = await POST(`bots/${id}/clone`, {});
      O.updateBot(st, true);
      O.ws.openBot(st.id);
      O.toast(`Made ${st.name}`);
    } catch (e) { O.fail(e); }
  };
  O.exportBot = async function exportBot(id) {
    try {
      const data = await GET(`bots/${id}/export`);
      O.downloadJson(`${(data.name || "bot").replace(/[^\w.-]+/g, "-")}.oomi-bot.json`, data);
    } catch (e) { O.fail(e); }
  };
  O.importBot = async function importBot() {
    let data;
    try { data = await O.pickJson(); } catch (e) { return O.fail(e); }
    if (!data || data.oomi_bot !== 1) return O.toast("That isn't an oomi bot file.");
    const trust = data.trust === "read-only" ? "read-only" : "project"; // a file never brings full trust
    const instr = typeof data.instructions === "string" ? data.instructions.trim() : "";
    O.modal((box, close) => {
      const go = h("button", { type: "button", class: "btn primary", onclick: async () => {
        go.disabled = true;
        try {
          const st = await POST("bots/import", { data });
          close();
          O.updateBot(st, true);
          O.ws.openBot(st.id);
          O.toast(`Imported ${st.name}`);
        } catch (e) { go.disabled = false; O.fail(e); }
      } }, "Import");
      box.append(h("h3", {}, `Import ${O.clip(String(data.name || "this bot"), 40)}?`),
        h("div", { class: "list", style: "margin:10px 0" },
          h("div", { class: "li" }, h("div", { class: "grow" }, "Folder", h("div", { class: "small mono-line" }, data.folder ? `${O.tildify(String(data.folder))} (if it exists on this Mac; otherwise its own new folder)` : "Its own new folder in ~/Oomi"))),
          h("div", { class: "li" }, h("div", { class: "grow" }, "Trust", h("div", { class: "small" }, `${O.TRUST[trust].label}: ${O.TRUST[trust].desc}${data.trust === "full" ? " (the file asked for Full; change it yourself if you mean it)" : ""}`))),
          h("div", { class: "li" }, h("div", { class: "grow" }, "Instructions", h("div", { class: "small", style: "white-space:pre-wrap" }, instr ? O.clip(instr, 600) : "None")))),
        h("p", { class: "small-note" }, "Read the instructions: the bot will follow them. Commands it was always allowed to run aren't imported."),
        h("div", { class: "foot" }, h("button", { type: "button", class: "btn quiet", onclick: close }, "Cancel"), go));
    });
  };
  O.popOut = async function popOut(id) {
    const b = bot(id);
    if (!b) return;
    if (S.native.windows) {
      try { await POST("native", { action: "open_window", kind: "bot", bot: id, title: b.name }); }
      catch (e) { O.fail(e); }
      return;
    }
    const url = `/?t=${encodeURIComponent(O.TOKEN)}&w=bot&bot=${encodeURIComponent(id)}&name=bot-${id}`;
    const w = window.open(url, `oomi-bot-${id}`, "popup,width=460,height=720");
    if (!w) O.toast("Your browser blocked the new window.");
  };
  O.newWindow = async function newWindow() {
    if (S.native.windows) {
      try { await POST("native", { action: "open_window", kind: "extra", title: "oomi" }); } catch (e) { O.fail(e); }
      return;
    }
    const w = window.open(`/?t=${encodeURIComponent(O.TOKEN)}&w=extra&name=extra-${Date.now().toString(36)}`, "_blank");
    if (!w) O.toast("Your browser blocked the new window.");
  };

  // ---------------------------------------------------------------------------------------------- settings
  const TABS = [
    ["account", "Account", "person"], ["general", "General", "gear"], ["apps", "Apps", "apps"], ["antibodies", "Antibodies", "shield"], ["display", "Display", "eye"], ["notifications", "Notifications", "bell"],
    ["safety", "Safety and limits", "lock"], ["memory", "General memory", "memory"], ["skills", "General skills", "skills"],
    ["status", "Status", "pulse"], ["shortcuts", "Shortcuts", "keyboard"],
  ];
  // what settings search finds: [tab, key, label, more words]
  const INDEX = [
    ["account", "profile", "Your profile", "name email about me bio account personal"], ["account", "timezone", "Time zone", "clock local time"],
    ["account", "language", "Reply language", "english spanish french locale"], ["account", "units", "Units", "metric imperial miles kilometers"],
    ["account", "share", "Let bots know this", "privacy profile share"], ["account", "sync", "Cloud sync", "mongodb atlas database backup cloud mirror"],
    ["general", "key", "Anthropic API key", "token secret keychain"], ["general", "model", "Model", "sonnet opus haiku fable"],
    ["general", "effort", "Effort", "thinking reasoning"], ["general", "budget", "Spending limit per bot", "cost money dollars budget"],
    ["general", "trust", "Trust for new bots", "sandbox read-only full project permissions"],
    ["general", "fallback", "Fallback models", "overloaded busy retry order"], ["general", "small", "Background model", "learning summaries compaction cheap"],
    ["general", "web", "Web search", "internet browse fetch"], ["general", "learn", "Learn about me", "memory facts preferences"],
    ["general", "awake", "Keep the Mac awake", "sleep caffeinate power"], ["general", "color", "oomi's color", "ghost dock icon theme blue pink purple green peach"],
    ["antibodies", "", "Antibodies", "team fixes errors mongodb atlas shared proven cures vector search rerank"],
    ["apps", "", "Connected apps", "connectors mcp notion github linear jira sentry vercel supabase stripe integrations"], ["general", "file", "Export, import or reset settings", "backup restore defaults file"],
    ["display", "mode", "Simple or Advanced", "panes split layout tabs windows workspace"], ["display", "steps", "Tool steps", "activity commands detailed hidden"],
    ["display", "thinking", "Show what it's thinking", "reasoning typing"], ["display", "text", "Text size", "font bigger smaller zoom"],
    ["display", "density", "Density", "compact spacing"], ["display", "width", "Conversation width", "narrow wide full"],
    ["display", "stamps", "Timestamps", "time date"], ["display", "cost", "Cost under each reply", "price money seconds"],
    ["display", "dim", "Dim panes you're not using", "focus fade"],
    ["notifications", "notify", "Notifications", "alerts banner"], ["notifications", "kinds", "When to notify", "approval finished failed done problem"],
    ["notifications", "sound", "Sound", "chime ping glass"], ["notifications", "test", "Send a test notification", "try check"],
    ["safety", "block", "Block list", "deny forbid commands never run patterns"], ["safety", "protected", "Protected files", "claude.md agents.md env keys secrets ask"],
    ["safety", "redact", "Hide secrets from the model", "redact api keys tokens passwords"], ["safety", "timeout", "Approval timeout", "wait decline unanswered"],
    ["safety", "project", "Read CLAUDE.md and AGENTS.md", "project notes instructions"], ["safety", "time", "Time limit per request", "minutes long running"],
    ["safety", "steps", "Step limit per request", "tool calls loop"], ["safety", "compact", "Summarize older turns at", "context compaction tokens"],
    ["memory", "", "General memory", "remember facts forget review"], ["skills", "", "General skills", "procedures approve"],
    ["status", "", "Status and spending", "usage tokens cost statistics health"], ["shortcuts", "", "Keyboard shortcuts", "keys hotkeys cheat sheet"],
  ];
  let sheetEl = null;
  O.sheetOpen = () => !!sheetEl;
  O.closeSettings = function closeSettings() {
    if (sheetEl) { sheetEl.remove(); sheetEl = null; }
    S.sheet = null;
  };
  O.openSettings = async function openSettings(tab = "general", highlight = "") {
    const same = sheetEl && S.sheet === tab;
    const prev = sheetEl && $(".sheet-body", sheetEl);
    const keepScroll = same && prev && !highlight ? prev.scrollTop : 0;
    S.sheet = tab;
    if (!sheetEl) {
      const box = h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": "Settings" },
        h("nav", { class: "sheet-nav" }), h("section", { class: "sheet-main" }));
      sheetEl = h("div", { class: "scrim sheet-scrim", onmousedown: (e) => { if (e.target === sheetEl) O.closeSettings(); } }, box);
      document.body.append(sheetEl);
    }
    renderSheetNav();
    const body = h("div", { class: "drawer-body sheet-body" });
    $(".sheet-main", sheetEl).replaceChildren(
      h("div", { class: "sheet-head" }, h("h3", {}, TABS.find((t) => t[0] === tab)[1]),
        h("button", { type: "button", class: "icon-btn", title: "Close (Esc)", onclick: O.closeSettings }, icon("x", 18))), body);
    const draw = { account: drawAccount, general: drawGeneral, apps: drawApps, antibodies: drawAntibodies, display: drawDisplay, notifications: drawNotifications, safety: drawSafety, memory: drawMemory, skills: drawSkills, status: drawStatus, shortcuts: drawShortcuts }[tab];
    try { await draw(body); }
    catch (e) { body.append(h("div", { class: "err" }, e.message)); }
    if (keepScroll) body.scrollTop = keepScroll;
    if (highlight) {
      const el = body.querySelector(`[data-key="${highlight}"]`);
      if (el) { el.scrollIntoView({ block: "center" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1600); }
    }
  };
  function renderSheetNav() {
    if (!sheetEl) return;
    const nav = $(".sheet-nav", sheetEl);
    const old = $(".sheet-search input", nav);
    const q = old ? old.value : "";
    const search = h("input", { type: "search", placeholder: "Search settings", value: q, spellcheck: "false" });
    search.addEventListener("input", () => showSearch(search.value));
    search.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { const first = $(".sheet-body .hit", sheetEl); if (first) first.click(); }
      if (e.key === "Escape" && search.value) { e.stopPropagation(); search.value = ""; O.openSettings(S.sheet); }
    });
    nav.replaceChildren(h("div", { class: "sheet-title" }, "Settings"), h("div", { class: "sheet-search" }, search),
      ...TABS.map(([id, label, ic]) => h("button", { type: "button", class: `tab${id === S.sheet ? " on" : ""}`, onclick: () => { search.value = ""; O.openSettings(id); } },
        icon(ic, 17), h("span", {}, label), id === "memory" && S.memoryReview ? h("span", { class: "count", title: "New, to review" }, String(S.memoryReview)) : null)),
      h("div", { class: "sheet-version" }, `oomi ${S.version}`));
    if (q) setTimeout(() => { const i = $(".sheet-search input", nav); if (i) { i.focus(); i.setSelectionRange(q.length, q.length); } });
  }
  O.renderSheetNav = renderSheetNav;
  function showSearch(q) {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const body = $(".sheet-body", sheetEl);
    if (!words.length) return O.openSettings(S.sheet);
    const hits = INDEX.filter(([, , label, more]) => words.every((w) => `${label} ${more}`.toLowerCase().includes(w)));
    $(".sheet-head h3", sheetEl).textContent = "Search";
    body.replaceChildren(hits.length
      ? h("div", { class: "list" }, hits.map(([tab, key, label]) => h("button", { type: "button", class: "li hit", onclick: () => O.openSettings(tab, key) },
        h("div", { class: "grow" }, label, h("div", { class: "small" }, TABS.find((t) => t[0] === tab)[1])), icon("right", 14))))
      : h("p", { class: "empty-note" }, "No setting matches that."));
  }
  // Show a change in the tab that's open, without switching tabs.
  O.refreshSettings = (tab) => { if (sheetEl && S.sheet === tab && !$(".sheet-search input", sheetEl).value) O.openSettings(tab); };
  const save = async (patch) => { S.settings = await POST("settings", patch); return S.settings; };
  const numberField = (label, key, value, opts, help) => {
    const input = h("input", { type: "number", min: String(opts.min), max: String(opts.max), step: String(opts.step || 1), value: String(value) });
    input.addEventListener("change", async () => { try { await save({ [opts.name]: Number(input.value) }); O.toast("Saved", 1000); } catch (e) { O.fail(e); input.value = String(S.settings[opts.name]); } });
    return O.field(label, input, help, key);
  };

  async function drawGeneral(body) {
    const s = S.settings = await GET("settings");
    if (!body.isConnected) return;
    body.append(O.field("Anthropic API key", h("div", { class: "row-inline" }, h("div", { class: "grow mono-line" }, s.has_key ? s.key_hint || "Saved" : "Not set"),
      h("button", { type: "button", class: "btn", onclick: keyBox }, s.has_key ? "Replace" : "Add key")),
    s.key_from_env ? "Read from ANTHROPIC_API_KEY in your environment." : "Kept in your Mac's Keychain.", "key"));
    body.append(O.field("oomi's color", O.colorPicker(s.ghost_color, async (c) => { await save({ ghost_color: c }); O.applyGhostColor(); }),
      "For the Dock and for bots without a color of their own. Each bot's color is in its settings.", "color"));
    body.append(O.field("Model", O.select(s.models.map((m) => [m, O.modelName(m)]), s.model, (v) => save({ model: v }).then(() => O.refreshSettings("general"))),
      "Sonnet is quick and capable. Opus is stronger and costs more.", "model"));
    body.append(O.field("Effort", O.select(s.efforts.map((e) => [e, e[0].toUpperCase() + e.slice(1)]), s.effort, (v) => save({ effort: v })), "Higher effort thinks longer on hard problems.", "effort"));
    const budget = h("input", { type: "number", min: "0.5", max: "1000", step: "0.5", value: String(s.max_session_cost_usd) });
    budget.addEventListener("change", async () => { try { await save({ max_session_cost_usd: Number(budget.value) }); O.toast("Saved", 1000); } catch (e) { O.fail(e); } });
    body.append(O.field("Spending limit per bot ($)", budget, "Each bot stops and asks when it has spent this much since you opened oomi.", "budget"));
    body.append(O.field("Trust for new bots", O.select(s.trust_levels.map((t) => [t, O.TRUST[t].label + ": " + O.TRUST[t].desc]), s.trust, (v) => save({ trust: v })), "", "trust"));

    // fallback models, in the order they're tried
    const fb = h("div", { class: "list" });
    const paintFb = () => {
      const chosen = (S.settings.fallback_models || []).filter((m) => m !== S.settings.model);
      const rest = S.settings.models.filter((m) => m !== S.settings.model && !chosen.includes(m));
      const setFb = (list) => save({ fallback_models: list }).then(paintFb).catch(O.fail);
      fb.replaceChildren(...chosen.map((m, i) => h("div", { class: "li" }, h("span", { class: "n-badge" }, String(i + 1)), h("div", { class: "grow" }, O.modelName(m)),
        h("div", { class: "mini" },
          i ? h("button", { type: "button", title: "Try earlier", onclick: () => { const l = chosen.slice(); [l[i - 1], l[i]] = [l[i], l[i - 1]]; setFb(l); } }, "Up") : null,
          h("button", { type: "button", onclick: () => setFb(chosen.filter((x) => x !== m)) }, "Remove")))),
      ...rest.map((m) => h("div", { class: "li dim" }, h("div", { class: "grow" }, O.modelName(m)), h("div", { class: "mini" }, h("button", { type: "button", onclick: () => setFb(chosen.concat(m)) }, "Add")))));
    };
    paintFb();
    body.append(O.field("Fallback models", fb, `If ${O.modelName(s.model)} is overloaded or failing, these are tried in order.`, "fallback"));
    body.append(O.field("Background model", O.select(s.models.map((m) => [m, O.modelName(m)]), s.small_model, (v) => save({ small_model: v })),
      "Used for learning about you and summarizing long conversations.", "small"));
    body.append(
      O.toggleRow("Web search", "Let bots search and read the web.", s.web_search, (v) => save({ web_search: v }), "web"),
      O.toggleRow("Learn about me", "Pick up facts and preferences from what you say.", s.memory_learn, (v) => save({ memory_learn: v }), "learn"),
      O.toggleRow("Keep the Mac awake while a bot works", "So a long task isn't paused by sleep. The display can still turn off.", s.keep_awake, (v) => save({ keep_awake: v }), "awake"));
    body.append(h("div", { class: "section", dataset: { key: "file" } }, "Settings file"), h("div", { class: "row-inline wrap" },
      h("button", { type: "button", class: "btn", onclick: async () => { try { O.downloadJson("oomi-settings.json", await GET("settings/export")); } catch (e) { O.fail(e); } } }, icon("download", 16), "Export"),
      h("button", { type: "button", class: "btn", onclick: importSettings }, icon("upload", 16), "Import"),
      h("button", { type: "button", class: "btn quiet", onclick: () => O.confirmBox("Reset settings?", "Models, limits, safety lists, notifications and display go back to how oomi comes. Your bots, memory, skills and API key stay.", "Reset",
        async () => { S.settings = await POST("settings/reset"); O.remoteUi(await GET("ui")); O.ws.render(); O.ws.refreshThreads(); O.toast("Settings reset"); O.refreshSettings("general"); }, true) }, "Reset to defaults")),
    h("p", { class: "small-note" }, "The file has your choices, not your API key, bots or memory."));
  }
  async function importSettings() {
    let data;
    try { data = await O.pickJson(); } catch (e) { return O.fail(e); }
    if (!data || data.oomi_settings !== 1 || typeof data.config !== "object" || !data.config) return O.toast("That isn't an oomi settings file.");
    const cur = S.settings;
    const c = data.config;
    const looser = [];
    const dropped = (key) => (cur[key] || []).filter((x) => !(Array.isArray(c[key]) ? c[key] : cur[key]).includes(x));
    if (dropped("deny_commands").length) looser.push(`Takes ${O.plural(dropped("deny_commands").length, "1 command", "N commands")} off your block list: ${dropped("deny_commands").slice(0, 4).join(", ")}`);
    if (dropped("protected_write_names").length) looser.push(`Stops protecting ${dropped("protected_write_names").slice(0, 5).join(", ")}`);
    if (cur.redact_secrets && c.redact_secrets === false) looser.push("Stops hiding secrets from the model");
    if (c.trust === "full" && cur.trust !== "full") looser.push("Makes new bots start with Full trust (no sandbox)");
    if (cur.read_project_files === false && c.read_project_files === true) looser.push("Starts reading CLAUDE.md and AGENTS.md in bot folders");
    if (Number(c.max_session_cost_usd) > Number(cur.max_session_cost_usd)) looser.push(`Raises the spending limit per bot to $${Number(c.max_session_cost_usd)}`);
    const go = async () => {
      S.settings = await POST("settings/import", { data });
      O.remoteUi(await GET("ui"));
      O.ws.render(); O.ws.refreshThreads();
      O.toast("Settings imported");
      O.refreshSettings("general");
    };
    if (!looser.length) return O.confirmBox("Import these settings?", "Your models, limits, safety lists, notifications and display choices are replaced by the file's.", "Import", go);
    O.modal((box, close) => {
      const ok = h("button", { type: "button", class: "btn primary danger", onclick: async () => { ok.disabled = true; try { await go(); close(); } catch (e) { ok.disabled = false; O.fail(e); } } }, "Import anyway");
      box.append(h("h3", {}, "This file makes oomi less careful"), h("div", { class: "list", style: "margin:10px 0" }, looser.map((x) => h("div", { class: "li" }, O.icon("alert", 16), h("div", { class: "grow" }, x)))),
        h("p", { class: "small-note" }, "Only import settings from a file you made yourself."),
        h("div", { class: "foot" }, h("button", { type: "button", class: "btn quiet", onclick: close }, "Cancel"), ok));
    });
  }
  function keyBox() {
    O.modal((box, close) => {
      const input = h("input", { class: "text-input", type: "password", placeholder: "sk-ant-…", autocomplete: "off", spellcheck: "false" });
      const err = h("div", { class: "err hidden" });
      const ok = h("button", { type: "submit", class: "btn primary" }, "Save key");
      box.append(h("h3", {}, "Anthropic API key"), h("p", { class: "lead" }, "Create one at platform.claude.com, under API keys. It's checked, then kept in your Keychain."),
        h("form", { onsubmit: async (e) => {
          e.preventDefault(); ok.disabled = true; err.classList.add("hidden");
          try { const r = await POST("key", { key: input.value }); S.settings = r.settings; close(); O.toast(r.message, 3000); O.refreshSettings("general"); }
          catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); ok.disabled = false; }
        } }, input, err, h("div", { class: "foot" }, h("button", { type: "button", class: "btn quiet", onclick: close }, "Cancel"), ok)));
    });
  }

  async function drawDisplay(body) {
    const s = S.settings.models ? S.settings : (S.settings = await GET("settings"));
    const pref = (k) => (v) => { O.setPref(k, v); O.ws.render(); if (k === "tool_steps") O.ws.refreshThreads(); };
    body.append(O.field("Layout", O.segmented([["simple", "Simple"], ["advanced", "Advanced"]], O.ws.mode(), (v) => O.ws.setMode(v)),
      "Simple shows one conversation. Advanced lets you split the window into panes and tabs, so several bots work side by side.", "mode"));
    body.append(O.field("Tool steps", O.segmented([["simple", "Summary"], ["detailed", "Detailed"], ["hidden", "Hidden"]], O.pref("tool_steps"), pref("tool_steps")),
      "How much of what a bot did (commands, files) shows in the conversation. Hidden still shows what's running now.", "steps"));
    body.append(O.toggleRow("Show what it's thinking", "A line of its reasoning while it works.", s.show_thinking !== false, (v) => save({ show_thinking: v }), "thinking"));
    body.append(O.field("Text size", O.segmented([["small", "Small"], ["normal", "Normal"], ["large", "Large"]], O.pref("text_size"), pref("text_size")), "", "text"));
    body.append(O.field("Density", O.segmented([["comfortable", "Comfortable"], ["compact", "Compact"]], O.pref("density"), pref("density")), "", "density"));
    body.append(O.field("Conversation width", O.segmented([["narrow", "Narrow"], ["normal", "Normal"], ["wide", "Wide"], ["full", "Full"]], O.pref("chat_width"), pref("chat_width")), "", "width"));
    body.append(O.field("Timestamps", O.segmented([["separators", "Between gaps"], ["every", "Every message"], ["off", "Off"]], O.pref("timestamps"), pref("timestamps")), "", "stamps"));
    body.append(
      O.toggleRow("Cost under each reply", "What each answer cost and how long it took.", O.pref("reply_cost"), pref("reply_cost"), "cost"),
      O.toggleRow("Dim panes you're not using", "In Advanced layout, panes other than the one you're typing in fade a little.", O.pref("dim_unfocused"), pref("dim_unfocused"), "dim"));
  }

  async function drawNotifications(body) {
    const s = S.settings = await GET("settings");
    if (!body.isConnected) return;
    const kinds = h("div", { class: s.notify ? "" : "off" });
    body.append(O.toggleRow("Notifications", "When you're in another app or window. Nothing pops up for a bot you're looking at.", s.notify, async (v) => { await save({ notify: v }); kinds.classList.toggle("off", !v); }, "notify"));
    kinds.append(h("div", { class: "section", dataset: { key: "kinds" } }, "Tell me when a bot"),
      O.toggleRow("Needs my OK", "It's waiting to run something.", s.notify_approval, (v) => save({ notify_approval: v })),
      O.toggleRow("Finished", "A request is done.", s.notify_done, (v) => save({ notify_done: v })),
      O.toggleRow("Hit a problem", "It stopped with an error.", s.notify_failed, (v) => save({ notify_failed: v })));
    body.append(kinds);
    const sounds = [["", "None"]].concat(s.sounds.map((x) => [x, x]));
    const sel = O.select(sounds, s.notify_sound, async (v) => { await save({ notify_sound: v }); if (v && S.native.windows) POST("native", { action: "sound", name: v }).catch(() => {}); });
    body.append(O.field("Sound", sel, S.native.windows ? "Plays with each notification." : "Plays in the Mac app.", "sound"));
    body.append(h("div", { class: "field", dataset: { key: "test" } }, h("div", { class: "row-inline" },
      h("button", { type: "button", class: "btn", onclick: async () => {
        if (!S.native.windows && "Notification" in window && Notification.permission === "default") await Notification.requestPermission().catch(() => {});
        POST("notify/test").catch(O.fail);
      } }, icon("bell", 16), "Send a test"),
      h("span", { class: "help" }, S.native.windows ? "If nothing shows up, allow oomi in System Settings, Notifications." : "Your browser may ask first."))));
  }

  async function drawSafety(body) {
    const s = S.settings = await GET("settings");
    if (!body.isConnected) return;
    const listField = (key, name, label, help, placeholder) => {
      const ta = h("textarea", { class: "mono-area", rows: "5", spellcheck: "false", placeholder });
      ta.value = (s[name] || []).join("\n");
      const btn = h("button", { type: "button", class: "btn", disabled: true, onclick: async () => {
        btn.disabled = true;
        try { await save({ [name]: ta.value.split("\n").map((x) => x.trim()).filter(Boolean) }); O.toast("Saved", 1000); }
        catch (e) { O.fail(e); btn.disabled = false; }
      } }, "Save");
      ta.addEventListener("input", () => { btn.disabled = false; });
      return h("div", { class: "field", dataset: { key } }, h("span", { class: "lab" }, label), ta, h("div", { class: "row-inline" }, h("span", { class: "help grow" }, help), btn));
    };
    body.append(listField("block", "deny_commands", "Block list",
      "Commands bots may not run, even with full trust and your OK. One pattern per line; * matches anything. Every part of a chained command is checked, and spacing, quotes, capitals, sudo or env in front and bash -c are seen through. It's a guard rail, not a sandbox: a determined script can still hide a command.",
      "One per line, like:\ngit push*\n*terraform apply*"));
    body.append(listField("protected", "protected_write_names", "Protected files",
      "File names a bot must ask before changing, wherever they are. These hold instructions bots follow, or secrets.", "One per line, like:\nCLAUDE.md\n.env\n*.pem"));
    body.append(
      O.toggleRow("Hide secrets from the model", "API keys, tokens and passwords in command output and files are replaced before Claude sees them.", s.redact_secrets, (v) => save({ redact_secrets: v }), "redact"),
      O.toggleRow("Read CLAUDE.md and AGENTS.md", "Project notes in a bot's folder are read at the start of each conversation. They can't override your rules.", s.read_project_files, (v) => save({ read_project_files: v }), "project"));
    body.append(numberField("Approval timeout (minutes)", "timeout", s.approval_timeout_minutes, { name: "approval_timeout_minutes", min: 0, max: 1440 },
      "If nobody answers an approval card in this long, it's declined and the bot carries on. 0 waits for you."));
    body.append(h("div", { class: "section" }, "Limits for each request"));
    body.append(numberField("Time limit (minutes)", "time", s.max_task_minutes, { name: "max_task_minutes", min: 0, max: 1440 }, "A request that runs longer stops and says so. 0 means no limit."));
    body.append(numberField("Step limit", "steps", s.max_steps_per_turn, { name: "max_steps_per_turn", min: 5, max: 500 }, "Tool calls in one request before it wraps up."));
    body.append(numberField("Summarize older turns at (tokens)", "compact", s.compact_at_tokens, { name: "compact_at_tokens", min: 20000, max: 900000, step: 10000 }, "When a conversation gets this big, older turns become a summary."));
  }

  // Account: your profile (what bots know about you) and its copy in MongoDB Atlas
  async function drawAccount(body) {
    const a = await GET("account");
    if (!body.isConnected) return;
    const p = a.profile;
    const save = async (patch, ok = "Saved") => {
      try { await O.api("PATCH", "account", patch); O.toast(ok, 1000); } catch (e) { O.fail(e); O.refreshSettings("account"); }
    };
    const input = (key, attrs) => {
      const el = h("input", { class: "text-input", value: p[key] || "", ...attrs });
      el.addEventListener("change", () => save({ [key]: el.value }));
      return el;
    };
    const initials = (p.name || p.email || "?").split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
    body.append(h("div", { class: "acct-head" }, h("span", { class: "acct-badge" }, initials || "?"),
      h("div", {}, h("div", { class: "nm" }, p.name || "Your name"), h("div", { class: "small-note", style: "margin:0" }, p.email || "No email yet"))));

    body.append(O.field("Name", input("name", { maxlength: "80", placeholder: "What bots call you", autocomplete: "name" }), "", "profile"));
    body.append(O.field("Email", input("email", { type: "email", maxlength: "254", placeholder: "you@example.com", autocomplete: "email" })));
    const about = h("textarea", { class: "instr", rows: "4", maxlength: "1500", placeholder: "Your work, what you're into, how you like answers. A few lines is plenty." });
    about.value = p.about || "";
    const aboutSave = h("button", { type: "button", class: "btn", disabled: true, onclick: async () => { aboutSave.disabled = true; await save({ about: about.value }); } }, "Save");
    about.addEventListener("input", () => { aboutSave.disabled = about.value === (p.about || ""); });
    body.append(h("div", { class: "field" }, h("span", { class: "lab" }, "About you"), about,
      h("div", { class: "row-inline" }, h("span", { class: "help grow" }, "Bots read this at the start of each conversation."), aboutSave)));

    const here = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
    const tz = input("timezone", { list: "acct-zones", placeholder: here || "Europe/London", spellcheck: "false" });
    body.append(O.field("Time zone", h("div", { class: "row-inline" }, h("div", { class: "grow" }, tz, h("datalist", { id: "acct-zones" }, zones.map((z) => h("option", { value: z })))),
      here ? h("button", { type: "button", class: "btn", onclick: () => { tz.value = here; save({ timezone: here }); } }, "This Mac's") : null), "", "timezone"));
    body.append(O.field("Reply language", input("language", { maxlength: "40", placeholder: "The language you write in" }), "", "language"));
    body.append(O.field("Units", O.select([["", "No preference"], ["metric", "Metric"], ["imperial", "Imperial"]], p.units || "", (v) => save({ units: v })), "", "units"));
    body.append(O.toggleRow("Let bots know this", "Your profile is added to each new conversation. Off: bots don't see any of it.", p.share_with_bots, (v) => save({ share_with_bots: v }), "share"));

    // ---- cloud sync
    const s = a.sync;
    const when = (t) => (t ? O.sepTime(t) : "never");
    const sync = h("div", { dataset: { key: "sync" } });
    body.append(h("div", { class: "section" }, "Cloud sync"), sync);
    const run = async (btn, path, payload, ok) => {
      btn.disabled = true;
      try { await POST(path, payload); O.toast(ok, 1600); O.refreshSettings("account"); } catch (e) { O.fail(e); btn.disabled = false; }
    };
    if (s.provider) {
      const up = h("button", { type: "button", class: "btn", onclick: () => run(up, "account/sync/push", {}, "Uploaded") }, icon("upload", 15), "Upload now");
      const down = h("button", { type: "button", class: "btn", onclick: () => O.confirmBox("Replace your profile here?", "The profile in the database replaces the one on this Mac.", "Download", () => run(down, "account/sync/pull", {}, "Downloaded")) }, icon("download", 15), "Download");
      sync.append(
        h("div", { class: "list" },
          h("div", { class: "li" }, h("span", { class: "mark-txt" }, s.error ? "!" : "✓"), h("div", { class: "grow" }, `MongoDB Atlas · ${s.database}.${s.collection}`,
            h("div", { class: "small" }, `Uploaded ${when(s.last_push)} · downloaded ${when(s.last_pull)}`), s.error ? h("div", { class: "small", style: "color:var(--fg)" }, s.error) : null)),
          h("div", { class: "li" }, h("div", { class: "grow" }, "Account ID", h("div", { class: "small mono-line" }, a.id)),
            h("div", { class: "mini" }, h("button", { type: "button", onclick: () => O.copyText(a.id, "Account ID copied") }, "Copy")))),
        h("div", { class: "row-inline wrap", style: "margin-top:10px" }, up, down,
          h("button", { type: "button", class: "btn quiet", onclick: () => O.promptBox("Use another Mac's account", "Paste the account ID from the other Mac's Account settings. Its profile replaces the one here.", "", "Use it",
            (v) => POST("account/sync/pull", { account_id: v.trim() }).then(() => { O.toast("Switched account", 1600); O.refreshSettings("account"); }), { placeholder: "acct_…", max: 40 }) }, "Use another account…"),
          h("button", { type: "button", class: "btn quiet", onclick: () => O.confirmBox("Stop syncing?", "Your profile stays on this Mac and in the database; the connection string is removed from your Keychain.", "Stop syncing", () => POST("account/sync/disconnect").then(() => O.refreshSettings("account"))) }, "Disconnect")));
    } else {
      const uri = h("input", { class: "text-input", type: "password", placeholder: "mongodb+srv://user:password@cluster0.example.mongodb.net/", autocomplete: "off", spellcheck: "false" });
      const db = h("input", { class: "text-input", value: s.database || "oomi", spellcheck: "false", "aria-label": "Database" });
      const coll = h("input", { class: "text-input", value: s.collection || "accounts", spellcheck: "false", "aria-label": "Collection" });
      const err = h("div", { class: "err hidden" });
      const go = h("button", { type: "submit", class: "btn primary" }, "Connect");
      sync.append(h("p", { class: "small-note" }, "Keep a copy of your profile in your own MongoDB Atlas database, to use it on another Mac. Only the profile is synced: API keys, app sign-ins and conversations stay on this Mac."),
        h("form", { class: "stack", autocomplete: "off", onsubmit: async (e) => {
          e.preventDefault(); go.disabled = true; err.classList.add("hidden");
          try { await POST("account/sync/connect", { uri: uri.value, database: db.value.trim(), collection: coll.value.trim() }); O.toast("Connected to MongoDB Atlas", 1800); O.refreshSettings("account"); }
          catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); go.disabled = false; }
        } },
          h("span", { class: "lab" }, "Connection string"), uri,
          h("div", { class: "row-inline" }, h("div", { class: "grow" }, h("span", { class: "lab" }, "Database"), db), h("div", { class: "grow" }, h("span", { class: "lab" }, "Collection"), coll)),
          err, h("div", { class: "row-inline" }, h("span", { class: "small-note grow", style: "margin:0" }, "Kept in your Mac's Keychain. Needs pymongo: pip install 'oomi[cloud]'."), go)));
    }
  }

  // Antibodies: errors your team's bots have fixed, with the fix that was proven to work, shared through MongoDB Atlas
  async function drawAntibodies(body) {
    const a = await GET("antibodies");
    if (!body.isConnected) return;
    body.append(h("p", { class: "small-note", style: "margin:0 0 12px" }, "When a bot fixes an error and the failing check then passes, it writes the fix down. Once you approve it, every oomi on your team gets that fix first when it hits the same error, even worded differently in another project."));
    const again = () => O.refreshSettings("antibodies");
    if (!a.connected) {
      const retry = h("button", { type: "button", class: "btn", onclick: async () => { retry.disabled = true; try { await POST("antibodies/connect"); } catch (e) { O.fail(e); } again(); } }, "Try again");
      body.append(h("div", { class: "list" }, h("div", { class: "li" }, h("span", { class: "mark-txt" }, a.error ? "!" : "·"),
        h("div", { class: "grow" }, a.error ? "Couldn't reach MongoDB Atlas" : "Not connected to MongoDB Atlas",
          h("div", { class: "small" }, a.error || "Connect your cluster in Account (Cloud sync), or set MONGODB_URI. Your team shares one database; OOMI_TEAM names the team.")))),
        h("div", { class: "row-inline", style: "margin-top:10px" }, h("button", { type: "button", class: "btn primary", onclick: () => O.openSettings("account", "sync") }, "Connect in Account"), retry));
      return;
    }
    const mins = (s) => (s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);
    const card = (v, k, t) => h("div", { class: "stat" }, h("div", { class: "v" }, v), h("div", { class: "k" }, k), t ? h("div", { class: "t" }, t) : null);
    body.append(h("div", { class: "stat-grid four" },
      card(String(a.stats.antibodies), a.stats.antibodies === 1 ? "Proven fix" : "Proven fixes", `team ${a.team}`),
      card(String(a.stats.cures), a.stats.cures === 1 ? "Cure" : "Cures", "errors fixed with them"),
      card(mins(a.stats.saved_seconds || 0), "Time saved"), card(O.money(a.stats.saved_usd || 0), "Money saved")));
    const setup = h("button", { type: "button", class: "btn", onclick: async () => {
      setup.disabled = true; setup.textContent = "Setting up…";
      try { const r = await POST("antibodies/setup", { wait: 20 }); O.toast((r.report || []).join("\n") || "Done", 6000); } catch (e) { O.fail(e); }
      again();
    } }, "Set up search indexes");
    body.append(h("div", { class: "list", style: "margin-top:12px" }, h("div", { class: "li" }, h("span", { class: "mark-txt" }, a.search_error ? "!" : "✓"),
      h("div", { class: "grow" }, a.mode === "local" ? "Matching on this Mac" : `Matching with ${a.mode}`, h("div", { class: "small" }, a.search_error
        ? `Stepped down because: ${a.search_error}`
        : a.mode === "local" ? "Atlas Search is off (OOMI_ATLAS_SEARCH=0), so errors are compared word by word."
        : "Hybrid search ($rankFusion of vector and text), reranked by Voyage inside Atlas, when the cluster supports it.")),
      h("div", { class: "mini" }, setup))));

    const doc = (d, actions) => h("div", { class: "routine antibody" }, h("div", { class: "grow" },
      h("div", { class: "rt mono-line", title: d.error_text }, d.error_text),
      h("div", { class: "rs" }, d.fix || "(no description)"),
      h("div", { class: "rs dim" }, [d.author ? `by ${d.author}` : "", d.project ? `in ${d.project}` : "",
        d.proven_by && d.proven_by.command ? `proved by \`${d.proven_by.command}\`` : "",
        d.uses ? `cured ${d.cures || 0} of ${d.uses}` : "", d.seconds_to_fix ? `took ${mins(d.seconds_to_fix)}` : ""].filter(Boolean).join(" · "))), actions);
    const review = (id, approve) => POST(`antibodies/${id}/review`, { approve }).then(() => { O.toast(approve ? "Shared with your team" : "Discarded", 1500); again(); }).catch(O.fail);
    if (a.pending.length) {
      body.append(h("div", { class: "section" }, `Waiting for you (${a.pending.length})`), h("div", { class: "routines" }, a.pending.map((d) =>
        doc(d, h("div", { class: "row-inline", style: "flex:none" },
          h("button", { type: "button", class: "btn quiet", onclick: () => review(d.id, false) }, "Discard"),
          h("button", { type: "button", class: "btn primary", onclick: () => review(d.id, true) }, "Share"))))));
    }
    body.append(h("div", { class: "section" }, `Your team's fixes (${a.approved.length})`), a.approved.length
      ? h("div", { class: "routines" }, a.approved.map((d) => doc(d, null)))
      : h("p", { class: "small-note" }, "None yet. They appear here when a bot fixes an error and you share the fix."));

    // try a match: what would a bot be offered for this error?
    const err = h("textarea", { class: "instr", rows: "3", placeholder: "Paste an error, like: AttributeError: module 'collections' has no attribute 'Mapping'" });
    const out = h("div");
    const test = h("button", { type: "button", class: "btn", onclick: async () => {
      test.disabled = true;
      try {
        const r = await POST("antibodies/match", { error: err.value });
        out.replaceChildren(h("div", { class: "list", style: "margin-top:8px" }, r.match
          ? h("div", { class: "li" }, h("span", { class: "mark-txt" }, "✓"), h("div", { class: "grow" }, r.match.fix, h("div", { class: "small" }, `${r.match.how}, score ${r.match.score} · by ${r.match.author}`)))
          : h("div", { class: "li" }, h("span", { class: "mark-txt" }, "·"), h("div", { class: "grow" }, "Nothing sure enough to offer", h("div", { class: "small" }, `${r.how} search found ${r.candidates.length} candidates`)))));
      } catch (e) { O.fail(e); }
      test.disabled = false;
    } }, "Find a match");
    err.style.width = "100%";
    body.append(h("div", { class: "section" }, "Try it"), err, h("div", { class: "row-inline", style: "margin-top:8px;justify-content:flex-end" }, test), out);
  }

  // Apps: hosted MCP servers. Connected ones first, then the catalog by category, with search and filter chips.
  async function drawApps(body) {
    const data = await GET("apps");
    if (!body.isConnected) return;
    S.appNames = Object.fromEntries(data.apps.map((a) => [a.id, a.name]));
    const state = S.appsView || (S.appsView = { q: "", cat: "All" });
    const connected = data.apps.filter((a) => a.connected);
    body.append(h("div", { class: "apps-head" },
      h("p", { class: "small-note", style: "margin:0" }, "Let bots use your other apps. Each one asks before it changes anything there."),
      connected.length ? h("div", { class: "apps-stack", title: connected.map((a) => a.name).join(", ") },
        connected.slice(0, 4).map((a) => appLogo(a)), h("span", {}, `${connected.length} connected`)) : null));
    const search = h("label", { class: "apps-search" }, icon("search", 16),
      h("input", { type: "search", placeholder: "Search apps", value: state.q, "aria-label": "Search apps", oninput: (e) => { state.q = e.target.value; paint(); } }));
    const cats = ["All", ...data.categories.filter((c) => data.apps.some((a) => a.category === c))];
    const chips = h("div", { class: "apps-chips" });
    const list = h("div");
    body.append(h("div", { class: "row-inline", style: "margin-top:14px" }, h("div", { class: "grow" }, search),
      h("button", { type: "button", class: "btn", onclick: customBox }, icon("plus", 15), "Custom app")), chips, list);

    const row = (a) => h("div", { class: "app-row" }, appLogo(a),
      h("div", { class: "grow" }, h("div", { class: "nm" }, a.name),
        h("div", { class: "ds", title: a.desc }, a.connected ? `${O.plural(a.tools, "1 tool", "N tools")} · ${a.desc}` : a.desc)),
      a.connected
        ? h("button", { type: "button", class: "btn app-btn on", onclick: (e) => O.openMenu(e.currentTarget, [
            { label: "Reload its tools", icon: "fresh", run: () => act(`apps/${a.id}/refresh`, `${a.name}'s tools reloaded`) },
            "-",
            { label: "Disconnect", icon: "x", run: () => O.confirmBox(`Disconnect ${a.name}?`, "Bots stop using it and its sign-in is removed from your Keychain.", "Disconnect", () => act(`apps/${a.id}/disconnect`, `${a.name} disconnected`)) },
          ], { align: "right" }) }, icon("check", 14), "Connected")
        : h("button", { type: "button", class: "btn app-btn", onclick: (e) => connect(a, e.currentTarget) }, a.auth === "token" ? "Connect" : "Add"));

    function paint() {
      chips.replaceChildren(...cats.map((c) => h("button", { type: "button", class: `chip${state.cat === c ? " solid" : ""}`, onclick: () => { state.cat = c; paint(); } }, c)));
      const q = state.q.trim().toLowerCase();
      const hit = (a) => (!q || `${a.name} ${a.desc} ${a.category}`.toLowerCase().includes(q)) && (state.cat === "All" || a.category === state.cat);
      const groups = [];
      const mine = connected.filter(hit);
      if (mine.length && state.cat === "All") groups.push(["Connected", mine]);
      for (const c of cats.slice(1).concat(data.apps.some((a) => a.category === "Custom") ? ["Custom"] : [])) {
        const items = data.apps.filter((a) => a.category === c && hit(a) && !(state.cat === "All" && a.connected));
        if (items.length) groups.push([c, items]);
      }
      list.replaceChildren(...groups.map(([title, items]) => h("div", {}, h("div", { class: "section" }, title), h("div", { class: "app-grid" }, items.map(row)))));
      if (!groups.length) list.append(h("p", { class: "empty-note" }, "No apps match. Add any MCP server with Custom app."));
    }
    paint();

    async function act(path, done) {
      try { await POST(path); O.toast(done, 1500); O.refreshSettings("apps"); } catch (e) { O.fail(e); }
    }
    async function connect(a, btn) {
      if (a.auth === "token") return tokenBox(a);
      if (a.auth === "client") return clientBox(a);
      btn.disabled = true; btn.textContent = "Connecting…";
      try {
        const r = await POST(`apps/${a.id}/connect`);
        if (r.auth_url) {
          btn.textContent = "Waiting for you…";
          O.toast(`Sign in to ${a.name} in your browser, then come back.`, 6000);
        } else { O.toast(`${a.name} is connected`, 1800); O.refreshSettings("apps"); }
      } catch (e) { O.fail(e); btn.disabled = false; btn.textContent = "Add"; }
    }
    // An app that signs in with an OAuth client the user made (Gmail): its ID and secret, and the address to allow.
    function clientBox(a) {
      O.modal((box, close) => {
        const id = h("input", { class: "text-input", placeholder: "Client ID", autocomplete: "off", spellcheck: "false" });
        const secret = h("input", { class: "text-input", type: "password", placeholder: "Client secret", autocomplete: "off", spellcheck: "false" });
        const err = h("div", { class: "err hidden" });
        const save = h("button", { type: "submit", class: "btn primary" }, "Sign in");
        const uri = data.redirect_uri || "";
        box.append(h("h3", {}, `Connect ${a.name}`), h("p", { class: "lead" }, a.client_help || `${a.name} signs in with an OAuth client of yours.`),
          uri ? h("div", { class: "row-inline", style: "margin:4px 0 12px" }, h("div", { class: "grow mono-line", title: "Add this under Authorized redirect URIs" }, uri),
            h("button", { type: "button", class: "btn quiet", onclick: () => { navigator.clipboard?.writeText(uri); O.toast("Copied", 1000); } }, "Copy")) : null,
          h("form", { onsubmit: async (e) => {
            e.preventDefault(); save.disabled = true; err.classList.add("hidden");
            try {
              await POST(`apps/${a.id}/connect`, { client_id: id.value, client_secret: secret.value });
              close(); O.toast(`Sign in to ${a.name} in your browser, then come back.`, 6000);
            } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); save.disabled = false; }
          } }, h("div", { style: "display:grid;gap:8px" }, id, secret), err, h("div", { class: "foot" }, h("button", { type: "button", class: "btn quiet", onclick: close }, "Cancel"), save)));
        setTimeout(() => id.focus(), 30);
      });
    }
    function tokenBox(a) {
      O.modal((box, close) => {
        const input = h("input", { class: "text-input", type: "password", placeholder: "Token", autocomplete: "off", spellcheck: "false" });
        const err = h("div", { class: "err hidden" });
        const save = h("button", { type: "submit", class: "btn primary" }, "Connect");
        box.append(h("h3", {}, `Connect ${a.name}`), h("p", { class: "lead" }, a.token_help || `Paste a token for ${a.name}. It's kept in your Keychain.`),
          h("form", { onsubmit: async (e) => {
            e.preventDefault(); save.disabled = true; err.classList.add("hidden");
            try { await POST(`apps/${a.id}/connect`, { token: input.value }); close(); O.toast(`${a.name} is connected`, 1800); O.refreshSettings("apps"); }
            catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); save.disabled = false; }
          } }, input, err, h("div", { class: "foot" }, h("button", { type: "button", class: "btn quiet", onclick: close }, "Cancel"), save)));
        setTimeout(() => input.focus(), 30);
      });
    }
    function customBox() {
      O.modal((box, close) => {
        const name = h("input", { class: "text-input", placeholder: "Name, like “Company wiki”", maxlength: "40" });
        const url = h("input", { class: "text-input", placeholder: "https://example.com/mcp", spellcheck: "false" });
        const token = h("input", { class: "text-input", type: "password", placeholder: "Token (only if it uses one)", autocomplete: "off" });
        const err = h("div", { class: "err hidden" });
        const save = h("button", { type: "submit", class: "btn primary" }, "Add");
        box.append(h("h3", {}, "Custom app"), h("p", { class: "lead" }, "Any MCP server that speaks Streamable HTTP. If it needs a sign-in, your browser opens."),
          h("form", { class: "stack", onsubmit: async (e) => {
            e.preventDefault(); save.disabled = true; err.classList.add("hidden");
            try {
              const r = await POST("apps/custom", { name: name.value, url: url.value, token: token.value });
              close();
              if (r.auth_url) O.toast("Sign in in your browser, then come back.", 6000);
              else { O.toast("Connected", 1500); O.refreshSettings("apps"); }
            } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); save.disabled = false; }
          } }, name, url, token, err, h("div", { class: "foot" }, h("button", { type: "button", class: "btn quiet", onclick: close }, "Cancel"), save)));
        setTimeout(() => name.focus(), 30);
      });
    }
  }

  // bot: one bot's own memory (in its settings); without it, what every bot shares (the Memory tab)
  async function drawMemory(body, botId = "") {
    const qs = botId ? `?bot=${encodeURIComponent(botId)}` : "";
    const data = await GET("memory" + (botId ? qs : "?all=1")); // Settings shows who holds each memory; a bot's page is only its own
    if (!body.isConnected) return;
    const again = () => { if (botId) { body.replaceChildren(); drawMemory(body, botId); } else O.refreshSettings("memory"); };
    if (!botId) { S.memoryReview = data.unreviewed.length; O.renderFoot(); }
    const editFact = (f, row) => {
      const ta = h("textarea", { class: "mem-edit", style: "min-height:64px", spellcheck: "false", maxlength: "1000" });
      ta.value = f.text;
      const save = async () => { if (ta.value.trim() && ta.value.trim() !== f.text) await act("edit", { id: f.id, text: ta.value }, f.owner); else again(); };
      row.replaceChildren(h("div", { class: "grow" }, ta, h("div", { class: "row-inline", style: "margin-top:8px;justify-content:flex-end" },
        h("button", { type: "button", class: "btn quiet", onclick: again }, "Cancel"),
        h("button", { type: "button", class: "btn primary", onclick: save }, "Save"))));
      ta.focus();
    };
    // Who holds a memory (Settings only): "Every bot" for the shared ones, otherwise the bot's name.
    const ownerTag = (f) => botId ? null : h("span", { class: `tag owner${f.owner ? "" : " shared"}`, title: f.owner ? `Only ${f.owner_name} sees this` : "Every bot sees this" }, f.owner ? f.owner_name : "Every bot");
    const factRow = (f, review) => {
      const row = h("div", { class: "li" });
      row.append(
        h("div", { class: "grow" }, f.text, h("div", { class: "small" },
          ownerTag(f),
          f.tags ? f.tags.split(",").map((t) => h("span", { class: "tag" }, t)) : null,
          f.expires_at ? `until ${O.fmtDate.format(new Date(f.expires_at * 1000))}` : "", !f.reviewed && !review ? " not yet confirmed" : "")),
        h("div", { class: "mini" },
          review ? h("button", { type: "button", onclick: () => act("keep", { ids: [f.id] }, f.owner) }, "Keep") : null,
          h("button", { type: "button", onclick: () => editFact(f, row) }, "Edit"),
          h("button", { type: "button", onclick: () => act("forget", { id: f.id }, f.owner) }, "Forget")));
      return row;
    };
    // bot: whose memory the change is made in (a row's owner; "" is the shared memory)
    async function act(what, payload, bot = botId) {
      try { await POST("memory/" + what, { ...payload, bot }); again(); } catch (e) { O.fail(e); }
    }
    const view = S.memOwner && (S.memOwner === "shared" || data.facts.concat(data.unreviewed).some((f) => f.owner === S.memOwner)) ? S.memOwner : "";
    const shown = (list) => botId || !view ? list : list.filter((f) => (view === "shared" ? !f.owner : f.owner === view));
    const facts = shown(data.facts), unreviewed = shown(data.unreviewed);
    const keepAll = async () => {
      try {
        for (const owner of new Set(unreviewed.map((f) => f.owner))) await POST("memory/keep", { ids: unreviewed.filter((f) => f.owner === owner).map((f) => f.id), bot: owner });
        again();
      } catch (e) { O.fail(e); }
    };
    body.append(h("p", { class: "small-note" }, botId
      ? "Only this bot sees these."
      : "Every memory, and who holds it. “Every bot” ones are seen by all bots; the others belong to the bot named, and only it sees them. You can also manage a bot's own in its settings."));
    const owners = botId ? [] : [...new Map(data.facts.concat(data.unreviewed).filter((f) => f.owner).map((f) => [f.owner, f.owner_name])).entries()];
    if (owners.length) {
      const chip = (id, label) => {
        const n = (id === "" ? data.facts : data.facts.filter((f) => (id === "shared" ? !f.owner : f.owner === id))).length;
        return h("button", { type: "button", class: `chip${view === id ? " solid" : ""}`, onclick: () => { S.memOwner = id; again(); } }, `${label} ${n}`);
      };
      body.append(h("div", { class: "apps-chips", style: "margin-bottom:10px" }, chip("", "All"), chip("shared", "Every bot"), ...owners.map(([id, name]) => chip(id, name))));
    }
    if (unreviewed.length) {
      body.append(h("div", { class: "section" }, `New, to review (${unreviewed.length})`,
        h("button", { type: "button", class: "btn quiet", style: "height:26px", onclick: keepAll }, "Keep all")),
      h("div", { class: "list" }, unreviewed.map((f) => factRow(f, true))));
    }
    const files = h("input", { type: "file", multiple: true, class: "hidden", accept: ".txt,.md,.markdown,.csv,.json,.log,.yaml,.yml,text/*" });
    files.addEventListener("change", async () => {
      let saved = 0, skipped = 0, failed = "";
      for (const file of [...files.files]) {
        try {
          const r = await POST("memory/file", { name: file.name, text: await file.text(), bot: botId });
          saved += r.result.saved; skipped += r.result.skipped;
        } catch (e) { failed = e.message; }
      }
      files.value = "";
      if (failed) O.toast(failed, 4000);
      else O.toast(`Remembered ${saved} ${saved === 1 ? "note" : "notes"}` + (skipped ? `, skipped ${skipped}` : ""));
      again();
    });
    const addIn = h("input", { class: "text-input", placeholder: botId ? "Teach this bot something, like “Deploys go to staging first”" : "Teach it something, like “I use pnpm, not npm”", maxlength: "1000" });
    body.append(h("form", { class: "row-inline", style: "margin-top:14px", onsubmit: async (e) => { e.preventDefault(); if (addIn.value.trim()) await act("add", { text: addIn.value.trim() }); } },
      h("div", { class: "grow" }, addIn), h("button", { class: "btn", type: "submit" }, "Add"),
      h("button", { class: "btn", type: "button", title: "Remember what's in text files (notes, markdown, CSV…)", onclick: () => files.click() }, icon("plus", 15), "Add files")));
    const editBtn = h("button", { type: "button", class: "btn quiet", style: "height:26px", onclick: () => editAsText() }, botId ? "Edit as text" : "Edit shared as text");
    body.append(h("div", { class: "section" }, `${botId ? "Its own memories" : "All memories"} (${view ? facts.length : data.count})`, editBtn));
    const listBox = h("div", { class: "list" }, facts.length ? facts.map((f) => factRow(f, false)) : h("div", { class: "li small" },
      botId ? "Nothing of its own yet. It saves notes about its work here as it goes." : "Nothing yet. It learns from what you tell it, a few turns at a time."));
    body.append(listBox);
    if (shown(data.history).length) {
      const hist = h("div", { class: "list hidden" }, shown(data.history).map((f) => h("div", { class: "li" }, h("div", { class: "grow", style: "color:var(--fg-3)" }, f.text, h("div", { class: "small" }, ownerTag(f), f.reason)))));
      body.append(h("div", { class: "section" }, "Retired", h("button", { type: "button", class: "btn quiet", style: "height:26px", onclick: (e) => { hist.classList.toggle("hidden"); e.currentTarget.textContent = hist.classList.contains("hidden") ? "Show" : "Hide"; } }, "Show")), hist);
    }
    async function editAsText() {
      const t = await GET("memory/text" + qs);
      const ta = h("textarea", { class: "mem-edit", spellcheck: "false" });
      ta.value = t.text;
      listBox.replaceWith(h("div", {}, ta, h("div", { class: "row-inline", style: "margin-top:8px;justify-content:flex-end" },
        h("button", { type: "button", class: "btn quiet", onclick: again }, "Cancel"),
        h("button", { type: "button", class: "btn primary", onclick: async () => {
          try {
            const r = await POST("memory/text", { text: ta.value, ids: t.ids, bot: botId });
            const res = r.result;
            O.toast(`Updated ${res.updated}, added ${res.added}, forgot ${res.forgotten}` + (res.skipped.length ? `, skipped ${res.skipped.length}` : ""));
            again();
          } catch (e) { O.fail(e); }
        } }, "Save"))));
      editBtn.remove();
      ta.focus();
    }
  }

  // bot: that bot's own skills (in its settings); without it, the skills every bot can use (the Skills tab)
  async function drawSkills(body, botId = "") {
    const data = await GET("skills" + (botId ? `?bot=${encodeURIComponent(botId)}` : ""));
    if (!body.isConnected) return;
    const again = () => { if (botId) { body.replaceChildren(); drawSkills(body, botId); } else O.refreshSettings("skills"); };
    if (!data.skills.length) {
      body.append(h("p", { class: "small-note" }, botId
        ? "No skills of its own yet. When it works out how to do something it will need again, it offers to save it here."
        : "No shared skills yet. When you ask for something you've asked for before, oomi offers to save how it did it, first for that bot. Share it from the bot's settings to give it to every bot."));
      return;
    }
    body.append(h("p", { class: "small-note" }, botId ? "Only this bot uses these. Share one to give it to every bot." : "Every bot can use these. Certified skills come with oomi; save one with the same name to replace it. Each bot also has its own, in its settings."));
    const act = async (what, name) => { try { await POST("skills/" + what, { name, bot: botId }); again(); if (what === "share") O.toast(`Every bot can use ${name} now`); } catch (e) { O.fail(e); } };
    const LABEL = { certified: "certified", verified: "checked", approved: "approved", candidate: "waiting for you", imported: "imported", retired: "retired" };
    body.append(h("div", { class: "list" }, data.skills.map((s) => {
      const detail = h("pre", { class: "hidden skill-body" }, s.body);
      return h("div", { class: "li" }, h("div", { class: "grow" },
        h("div", { style: "font-weight:600" }, s.name, " ", h("span", { class: `tag${s.status === "candidate" ? " solid" : ""}` }, LABEL[s.status] || s.status), s.certified && s.status !== "certified" ? h("span", { class: "tag" }, "certified") : null, s.unreliable ? h("span", { class: "tag" }, "unreliable") : null),
        h("div", { class: "small" }, s.description),
        h("div", { class: "small" }, s.successes + s.failures ? `${O.plural(s.successes, "Worked once", "Worked N times")}, ${O.plural(s.failures, "failed once", "failed N times")}` : s.uses ? O.plural(s.uses, "Used once", "Used N times") : "Not used yet"),
        detail),
      h("div", { class: "mini" },
        h("button", { type: "button", onclick: () => detail.classList.toggle("hidden") }, "Show"),
        s.status === "candidate" || s.status === "imported" ? h("button", { type: "button", onclick: () => act("approve", s.name) }, "Approve") : null,
        botId && s.status !== "candidate" ? h("button", { type: "button", title: "Let every bot use it", onclick: () => act("share", s.name) }, "Share") : null,
        s.certified ? null : h("button", { type: "button", onclick: () => O.confirmBox(`Remove ${s.name}?`, "oomi won't use this skill again.", "Remove", () => act("remove", s.name)) }, "Remove")));
    })));
  }

  async function drawStatus(body) {
    const [s, u] = await Promise.all([GET("status"), GET("usage")]);
    if (!body.isConnected) return;
    const card = (label, x) => h("div", { class: "stat" }, h("div", { class: "v" }, O.money(x.usd)), h("div", { class: "k" }, label),
      h("div", { class: "t" }, x.calls ? O.tokens(x) : "No requests"));
    body.append(h("div", { class: "section" }, "Spending"), h("div", { class: "stat-grid four" },
      card("Today", u.totals.today), card("Last 7 days", u.totals.week), card("Last 30 days", u.totals.month), card("All time", u.totals.all)));
    const byDay = Object.fromEntries(u.days.map((d) => [d.day, d]));
    const days = [];
    for (let i = 29; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86400000);
      days.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`);
    }
    const max = Math.max(0.0001, ...days.map((d) => (byDay[d] ? byDay[d].usd : 0)));
    body.append(h("div", { class: "bars", title: "Spending per day, last 30 days" }, days.map((d) => {
      const x = byDay[d];
      return h("div", { class: x ? "" : "zero", style: `height:${Math.max(3, ((x ? x.usd : 0) / max) * 100)}%`,
        title: x ? `${d}: ${O.money(x.usd)}, ${O.tokens(x)}, ${O.plural(x.calls, "1 request", "N requests")}` : `${d}: nothing` });
    })), h("div", { class: "bars-x" }, h("span", {}, "30 days ago"), h("span", {}, "Today")));
    const row = (who, x) => h("tr", {},
      h("td", {}, who),
      h("td", {}, O.money(x.today.usd)), h("td", {}, O.money(x.week.usd)), h("td", {}, O.money(x.all.usd)),
      h("td", {}, x.all.calls ? h("div", {}, O.tok(x.all.in + x.all.out), h("div", { class: "sub" }, O.tokens(x.all))) : "None"),
      h("td", {}, String(x.all.calls)),
      h("td", { title: "Memories and skills of its own" }, x.memories === undefined ? "" : `${x.memories} · ${x.skills}`));
    const live = Object.fromEntries((s.bots || []).map((b) => [b.id, b]));
    const rows = u.bots.map((b) => {
      const now = live[b.id];
      const sub = [b.last ? `last used ${O.sideTime(b.last).toLowerCase()}` : "not used yet",
        now && now.cost ? `${O.money(now.cost)} since you opened oomi` : "", now && now.context_tokens ? `${O.tok(now.context_tokens)} tokens of context now` : ""].filter(Boolean).join(", ");
      return row(h("div", { class: "who" }, avatar(b.avatar, "", b.color), h("div", {}, h("div", { class: "nm" }, b.name), h("div", { class: "sub" }, sub))), b);
    });
    if (u.other.all.calls) rows.push(row(h("div", { class: "who" }, h("span", { class: "avatar term" }, icon("terminal", 14)), h("div", {}, h("div", { class: "nm" }, "Terminal and other"), h("div", { class: "sub" }, "The oomi command and deleted bots"))), u.other));
    body.append(h("div", { class: "section" }, "By bot"), h("div", { class: "table-wrap" }, h("table", { class: "utable" },
      h("thead", {}, h("tr", {}, ["Bot", "Today", "7 days", "All time", "Tokens", "Requests", "Learned"].map((t) => h("th", {}, t)))),
      h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: "7" }, "No bots yet."))))));
    body.append(h("p", { class: "note-small" }, `Learned: each bot's own memories · skills. Shared by every bot: ${O.plural(u.general.memories, "1 memory", "N memories")} and ${O.plural(u.general.skills, "1 skill", "N skills")}.`));
    if (u.models.length) {
      body.append(h("div", { class: "section" }, "By model, last 30 days"), h("div", { class: "table-wrap" }, h("table", { class: "utable" },
        h("thead", {}, h("tr", {}, ["Model", "Requests", "Tokens in", "Tokens out", "Cost"].map((t) => h("th", {}, t)))),
        h("tbody", {}, u.models.map((m) => h("tr", {}, h("td", {}, O.modelName(m.model)), h("td", {}, String(m.calls)), h("td", {}, O.tok(m.in)), h("td", {}, O.tok(m.out)), h("td", {}, O.money(m.usd))))))));
    }
    body.append(h("p", { class: "note-small" }, "Tokens in include cached input, which costs a tenth of fresh input. Costs use Anthropic's published prices."));
    const mark = (ok) => h("span", { class: "mark-txt" }, ok === true ? "✓" : ok === false ? "✕" : "!");
    const keyText = { ok: "API key accepted", rejected: "API key rejected. Replace it in General.", missing: "No API key yet", unused: "API key not used yet" }[s.key.status] || s.key.status;
    body.append(h("div", { class: "section" }, "Health"), h("div", { class: "list health" },
      h("div", { class: "li" }, mark(s.key.status === "ok" ? true : s.key.status === "rejected" ? false : null), h("div", { class: "grow" }, keyText, s.key.when ? h("div", { class: "small" }, O.sepTime(s.key.when)) : null)),
      h("div", { class: "li" }, mark(true), h("div", { class: "grow" }, `${O.modelName(s.model)} for conversations`, h("div", { class: "small" }, s.fallback.length ? `Falls back to ${s.fallback.map(O.modelName).join(", ")}; background work on ${O.modelName(s.background)}` : `No fallback; background work on ${O.modelName(s.background)}`))),
      h("div", { class: "li" }, mark(s.failures_24h ? null : true), h("div", { class: "grow" }, s.failures_24h ? `${O.plural(s.failures_24h, "1 model failure", "N model failures")} in 24 hours; fell back ${s.fallbacks_24h}×` : "No model failures in 24 hours", s.latest_failure ? h("div", { class: "small" }, s.latest_failure) : null)),
      h("div", { class: "li" }, mark(s.sandbox.available ? true : null), h("div", { class: "grow" }, s.sandbox.available ? "Sandbox working" : "Sandbox unavailable", !s.sandbox.available && s.sandbox.reason ? h("div", { class: "small" }, s.sandbox.reason) : null)),
      h("div", { class: "li" }, mark(true), h("div", { class: "grow" }, `${O.plural(s.memory.count, "1 memory", "N memories")}` + (s.memory.unreviewed ? `, ${s.memory.unreviewed} to review` : ""))),
      h("div", { class: "li" }, mark(s.skills.unreliable ? null : true), h("div", { class: "grow" }, `${O.plural(s.skills.count, "1 skill", "N skills")}` + (s.skills.candidates ? `, ${s.skills.candidates} waiting for you` : "") + (s.skills.unreliable ? `, ${s.skills.unreliable} unreliable` : "") + (s.skills.retired ? `, ${s.skills.retired} retired` : "")))));
  }

  const SHORTCUTS = [
    ["Everywhere", [
      ["⌘K", "Find a bot, a message or a command"], ["⌘N", "New bot"], ["⌘.", "Overview of every bot"], ["⌘,", "Settings"],
      ["⌘/", "These shortcuts"], ["⌘B", "Show or hide the sidebar"], ["Esc", "Close, cancel, or stop the bot you're using"],
    ]],
    ["Advanced layout", [
      ["⌘1 … ⌘9", "Go to pane 1 to 9 (hold ⌘ to see the numbers)"], ["⌘\\", "Split right"], ["⌘⇧\\", "Split down"],
      ["⌘⌥← → ↑ ↓", "Go to the pane in that direction"], ["⌃Tab, ⌃⇧Tab", "Next or previous tab in a pane"],
      ["⌘W", "Close the tab"], ["⌘⇧M", "Minimize or restore the pane"], ["⌘⇧↩", "Zoom the pane, or zoom back out"],
      ["⌘⇧F", "Flip rows and columns"], ["⌘⇧O", "Open the bot in its own window"],
    ]],
    ["Simple layout", [["⌘1 … ⌘9", "Open bot 1 to 9 in the sidebar"]]],
    ["Writing", [["↩", "Send"], ["⇧↩", "New line"], ["Paste or drop", "Attach images, PDFs and text files"]]],
  ];
  function drawShortcuts(body) {
    for (const [title, rows] of SHORTCUTS) {
      body.append(h("div", { class: "section" }, title), h("div", { class: "list" }, rows.map(([k, what]) => h("div", { class: "li" },
        h("div", { class: "grow" }, what), h("kbd", {}, O.keys(k))))));
    }
    body.append(h("p", { class: "note-small" }, "Drag a bot from the sidebar or a tab onto a pane: the middle adds a tab, an edge splits the pane. Drag the line between panes to resize; double-click it to even them out."));
  }

  // ---------------------------------------------------------------------------------------------- overview
  // Every bot at a glance: what it's doing, what it needs from you, and a way to answer without opening it.
  let overEl = null;
  O.overviewOpen = () => !!overEl;
  let overOrder = [];
  const overSeen = new Set(); // approvals you've scrolled to the end of here
  O.closeOverview = () => { if (overEl) { for (const g of overGates) g.stop(); overGates = []; overEl.remove(); overEl = null; } };
  let overGates = [];
  O.toggleOverview = () => (overEl ? O.closeOverview() : O.openOverview());
  O.openOverview = function openOverview() {
    if (!overEl) {
      // sorted once, when it opens: cards (and their buttons) don't move under your pointer while it's open
      const rank = { waiting: 0, working: 1, unread: 2, "": 3 };
      overOrder = S.bots.slice().sort((a, b) => rank[O.statusOf(a)] - rank[O.statusOf(b)] || (b.last_at || 0) - (a.last_at || 0)).map((b) => b.id);
      overEl = h("div", { class: "scrim over-scrim", onmousedown: (e) => { if (e.target === overEl) O.closeOverview(); } },
        h("div", { class: "overview", role: "dialog", "aria-modal": "true", "aria-label": "Overview" }));
      document.body.append(overEl);
    }
    O.renderOverview();
  };
  O.renderOverview = function renderOverview() {
    if (!overEl) return;
    const box = $(".overview", overEl);
    const scroll = $(".over-grid", box) ? $(".over-grid", box).scrollTop : 0;
    for (const b of S.bots) if (!overOrder.includes(b.id)) overOrder.push(b.id);
    const bots = overOrder.map(bot).filter(Boolean);
    for (const g of overGates) g.stop();
    overGates = [];
    const busy = S.bots.filter((b) => b.status !== "idle").length;
    const waiting = S.bots.filter((b) => b.status === "waiting").length;
    const unread = S.bots.filter((b) => b.unread).length;
    const shown = new Set(O.ws.shownBots());
    const grid = h("div", { class: "over-grid" }, bots.map((b) => {
      const st = O.statusOf(b);
      const a = b.approval;
      // As in the conversation: the thing to approve is shown whole (confirm questions show their details), and
      // Allow waits until it's on screen and, if it scrolls, read to the end. Long ones are opened instead.
      const confirm = a && a.tool === "confirm";
      const body = a ? (confirm ? a.reason || "" : a.summary) : "";
      const inline = a && !a.too_long && (confirm || (a.length || body.length) <= 400) && body.length <= 2000;
      let gate = null;
      const answer = async (choice, el) => {
        if (gate) gate.busy(true);
        el.closest(".over-ask").querySelectorAll("button").forEach((x) => (x.disabled = true));
        try { await POST(`bots/${b.id}/approve`, { id: a.id, choice }); } catch (e) { O.fail(e); }
      };
      const what = inline ? h("div", { class: "what", tabindex: "0" }, O.visible(body)) : null;
      const allowBtn = inline ? h("button", { type: "button", class: "btn primary", onclick: (e) => answer("yes", e.currentTarget) }, confirm ? "Yes" : "Allow") : null;
      const note = h("div", { class: "gate small-note hidden" }, "Scroll to the end of it to allow.");
      if (inline) { gate = O.gateAllow(what, [allowBtn], note, { seen: overSeen.has(a.id), onSeen: () => overSeen.add(a.id) }); overGates.push(gate); }
      return h("div", { class: `over-card ${st}${shown.has(b.id) ? " shown" : ""}` },
        h("div", { class: "over-top" }, h("span", { class: "avatar-wrap sm" }, avatar(b.avatar, "", b.color), O.mark(b, "on-avatar")),
          h("div", { class: "grow" }, h("div", { class: "nm" }, b.name), h("div", { class: "sub" }, O.tildify(b.folder))),
          h("span", { class: "time" }, O.sideTime(b.last_at))),
        h("div", { class: `over-state ${st}` }, st === "waiting" ? "Needs your OK" : st === "working" ? (b.step || "Working") : b.unread ? O.plural(b.unread, "1 new message", "N new messages") : O.clip(O.plain(b.preview), 140) || "Nothing yet"),
        a ? h("div", { class: `over-ask${a.danger ? " danger" : ""}` },
          h("div", { class: "q" }, a.danger ? `Careful. ${O.askTitle(a)}` : confirm ? O.clip(a.summary, 200) : O.askTitle(a)),
          inline ? what : h("div", { class: "small-note" }, "Too long to show here; open it to read it all."),
          inline && !confirm ? h("div", { class: "why" }, `Why it's asking: ${a.reason}`) : null,
          note,
          h("div", { class: "btns" },
            allowBtn,
            h("button", { type: "button", class: "btn", onclick: (e) => answer("no", e.currentTarget) }, confirm ? "No" : "Don't allow"),
            h("button", { type: "button", class: "btn quiet", onclick: () => { O.closeOverview(); O.ws.openBot(b.id); } }, "Open"))) : null,
        h("div", { class: "over-foot" },
          b.cost ? h("span", { class: "small-note" }, `${O.money(b.cost)} since opening`) : h("span"),
          h("div", { class: "row-inline" },
            b.status !== "idle" ? h("button", { type: "button", class: "btn quiet sm", onclick: () => POST(`bots/${b.id}/stop`).catch(O.fail) }, icon("stop", 14), "Stop") : null,
            O.WIN.kind !== "bot" ? h("button", { type: "button", class: "btn quiet sm", title: "Own window", onclick: () => O.popOut(b.id) }, icon("popout", 14)) : null,
            h("button", { type: "button", class: "btn sm", onclick: () => { O.closeOverview(); O.ws.openBot(b.id); } }, shown.has(b.id) ? "Go to" : "Open"))));
    }));
    box.replaceChildren(
      h("div", { class: "over-head" }, h("h3", {}, "Overview"),
        h("span", { class: "sub" }, [busy ? `${busy} working` : "", waiting ? `${waiting} waiting for you` : "", unread ? `${unread} unread` : ""].filter(Boolean).join(", ") || "All quiet"),
        h("div", { class: "row-inline" },
          unread ? h("button", { type: "button", class: "btn quiet", onclick: () => POST("bots/read-all").catch(O.fail) }, "Mark all read") : null,
          busy ? h("button", { type: "button", class: "btn", onclick: () => O.confirmBox("Stop every bot?", `${O.plural(busy, "1 bot is", "N bots are")} working. Everything so far is kept.`, "Stop all", () => POST("bots/stop-all")) }, icon("stop", 14), "Stop all") : null,
          h("button", { type: "button", class: "icon-btn", title: "Close (Esc)", onclick: O.closeOverview }, icon("x", 18)))),
      S.bots.length ? grid : h("p", { class: "empty-note" }, "No bots yet."));
    $(".over-grid", box) && ($(".over-grid", box).scrollTop = scroll);
  };

  // ---------------------------------------------------------------------------------------------- palette
  // ⌘K: bots, messages and commands in one list. With `pick`, it chooses a bot for something (a new pane…).
  let palEl = null;
  O.paletteOpen = () => !!palEl;
  O.closePalette = () => { if (palEl) { palEl.remove(); palEl = null; } };
  O.palette = function palette(opts = {}) {
    O.closePalette();
    const input = h("input", { type: "text", placeholder: opts.placeholder || "Find a bot, a message or a command", spellcheck: "false", autocomplete: "off" });
    const list = h("div", { class: "pal-list", role: "listbox" });
    const box = h("div", { class: "palette", role: "dialog", "aria-modal": "true" }, h("div", { class: "pal-in" }, icon("search", 18), input), list);
    palEl = h("div", { class: "scrim pal-scrim", onmousedown: (e) => { if (e.target === palEl) O.closePalette(); } }, box);
    document.body.append(palEl);
    let items = [];
    let sel = 0;
    let searchTimer = null;
    const run = (it) => { O.closePalette(); it.run(); };
    const paint = () => {
      list.replaceChildren(...items.map((it, i) => it.head
        ? h("div", { class: "pal-head" }, it.head)
        : h("button", { type: "button", class: `pal-item${i === sel ? " sel" : ""}`, role: "option", onmousemove: () => { if (sel !== i) { sel = i; paint(); } }, onclick: () => run(it) },
          it.bot ? h("span", { class: "avatar-wrap xs" }, avatar(it.bot.avatar, "", it.bot.color), O.mark(it.bot, "on-avatar")) : icon(it.icon || "right", 16),
          h("span", { class: "grow" }, it.label, it.sub ? h("span", { class: "d" }, it.sub) : null),
          it.keys ? h("kbd", {}, O.keys(it.keys)) : null)));
      const cur = list.querySelector(".sel");
      if (cur) cur.scrollIntoView({ block: "nearest" });
    };
    const build = (q, hits = []) => {
      const ql = q.toLowerCase();
      const match = (s) => !ql || s.toLowerCase().includes(ql);
      const out = [];
      const bots = S.bots.filter((b) => match(b.name) || match(b.section || "") || match(O.tildify(b.folder)));
      if (bots.length) {
        out.push({ head: opts.pick ? "Bots" : "Go to" });
        for (const b of bots.slice(0, opts.pick ? 30 : 9)) out.push({ bot: b, label: b.name, sub: O.statusOf(b) === "waiting" ? "Needs your OK" : b.step || O.tildify(b.folder), run: () => (opts.pick ? opts.pick(b.id) : O.ws.openBot(b.id)) });
      }
      if (!opts.pick) {
        const cmds = O.commands().filter((c) => match(c.label + " " + (c.words || "")));
        if (cmds.length) { out.push({ head: "Commands" }); out.push(...cmds.slice(0, q ? 12 : 8)); }
        if (hits.length) {
          out.push({ head: "Messages" });
          for (const r of hits) {
            const b = bot(r.bot);
            if (b) out.push({ bot: b, label: O.clip(r.snippet, 90), sub: `${b.name}, ${(r.role === "user" ? "you, " : "") + O.sideTime(r.ts)}`, run: () => O.ws.openBot(r.bot, r.item) });
          }
        }
      } else if (opts.allowNew) {
        out.push({ head: "" }, { icon: "plus", label: "New bot…", run: () => O.newBot((st) => opts.pick(st.id)) });
      }
      if (!out.some((x) => !x.head)) out.push({ head: q ? "Nothing matches." : "No bots yet." });
      items = out;
      sel = items.findIndex((x) => !x.head);
      paint();
    };
    input.addEventListener("input", () => {
      const q = input.value.trim();
      build(q);
      clearTimeout(searchTimer);
      if (opts.pick || q.length < 2) return;
      searchTimer = setTimeout(async () => {
        try {
          const data = await GET("search?q=" + encodeURIComponent(q));
          if (palEl && input.value.trim() === q) build(q, data.results.slice(0, 12));
        } catch (_) { /* the list still works */ }
      }, 200);
    });
    input.addEventListener("keydown", (e) => {
      const step = (d) => {
        if (!items.some((x) => !x.head)) return;
        do { sel = (sel + d + items.length) % items.length; } while (items[sel].head);
        paint();
      };
      if (e.key === "ArrowDown") { e.preventDefault(); step(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); step(-1); }
      else if (e.key === "Enter") { e.preventDefault(); if (items[sel] && !items[sel].head) run(items[sel]); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); O.closePalette(); }
    });
    build("");
    input.focus(); // at once, so nothing typed straight after ⌘K lands in the conversation
  };

  // ---------------------------------------------------------------------------------------------- new bot & welcome
  function botForm(onDone, opts = {}) {
    const form = h("form", { autocomplete: "off" });
    const name = h("input", { class: "text-input", value: opts.name || "", placeholder: "Name, like Website or Taxes", maxlength: "40" });
    let folder = "";
    let specific = false; // off: it gets its own new folder in ~/Oomi
    let trust = S.settings.trust || "project";
    const folderLine = h("div", { class: "grow mono-line" });
    const pathIn = h("input", { class: "text-input", placeholder: "Folder path, like ~/code/my-app" });
    const err = h("div", { class: "err hidden" });
    const paintFolder = () => { folderLine.textContent = folder ? O.tildify(folder) : "No folder chosen yet"; };
    paintFolder();
    const pick = async () => {
      try { const r = await POST("native", { action: "pick_folder" }); if (r.result) { folder = r.result; paintFolder(); if (!name.value.trim()) name.value = O.basename(folder); } } catch (e) { O.fail(e); }
    };
    const folderBox = h("div", { class: "hidden", style: "margin-top:8px" }, S.native.pick_folder
      ? h("div", { class: "row-inline" }, folderLine, h("button", { type: "button", class: "btn", onclick: pick }, "Choose folder…"))
      : pathIn);
    const folderSwitch = O.toggleRow("Work in a specific folder", "Off: it gets its own new folder in ~/Oomi.", false, (v) => {
      specific = v;
      folderBox.classList.toggle("hidden", !v);
      if (v && S.native.pick_folder && !folder) pick();
      else if (v) setTimeout(() => pathIn.focus(), 30);
    });
    const choices = h("div", { class: "choices" });
    const paintTrust = () => {
      choices.replaceChildren(...Object.entries(O.TRUST).map(([k, v]) => h("button", { type: "button", class: `choice${trust === k ? " on" : ""}`, onclick: () => { trust = k; paintTrust(); } },
        h("span", { class: "radio" }), h("span", {}, h("div", { class: "t" }, v.label), h("div", { class: "d" }, v.desc)))));
    };
    paintTrust();
    const create = h("button", { type: "submit", class: `btn primary${opts.big ? " big" : ""}` }, opts.cta || "Create bot");
    form.append(
      h("div", { class: "field" }, h("label", {}, "Name"), name),
      h("div", { class: "field" }, h("span", { class: "lab" }, "Folder"), folderSwitch, folderBox),
      h("div", { class: "field" }, h("span", { class: "lab" }, "How much it may do"), choices),
      err, opts.big ? create : h("div", { class: "foot" }, h("button", { type: "button", class: "btn quiet", onclick: opts.cancel }, "Cancel"), create));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.classList.add("hidden");
      create.disabled = true;
      const f = !specific ? "" : S.native.pick_folder ? folder : pathIn.value.trim();
      if (specific && !f) {
        err.textContent = "Choose a folder, or turn off Work in a specific folder.";
        err.classList.remove("hidden"); create.disabled = false;
        return;
      }
      try {
        const st = await POST("bots", { name: name.value.trim() || (f ? O.basename(f) : ""), folder: f, trust });
        O.updateBot(st, true);
        onDone(st);
      } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); create.disabled = false; }
    });
    setTimeout(() => name.focus(), 30);
    return form;
  }
  O.newBot = function newBot(onDone) {
    O.modal((box, close) => {
      box.append(h("h3", {}, "New bot"), h("p", { class: "lead" }, "Each bot works in one folder and keeps one long conversation."),
        botForm((st) => { close(); (onDone || ((x) => O.ws.openBot(x.id)))(st); }, { cancel: close }));
    });
  };
  O.showWelcome = function showWelcome(step) {
    let w = $("#welcome");
    if (!w) { w = h("div", { class: "welcome", id: "welcome" }); document.body.append(w); }
    const inner = h("div", { class: "welcome-inner" });
    w.replaceChildren(inner);
    const ghost = h("div", { class: "ghost" }, O.ghostImg());
    if (step === "key") {
      const input = h("input", { class: "text-input", type: "password", placeholder: "Paste your Anthropic API key (sk-ant-…)", autocomplete: "off", spellcheck: "false" });
      const err = h("div", { class: "err hidden" });
      const go = h("button", { type: "submit", class: "btn primary big" }, "Continue");
      inner.append(h("div", { class: "steps" }, h("i", { class: "on" }), h("i")), ghost, h("h1", {}, "Hi, I'm oomi."),
        h("p", {}, "I work in your folders with Claude, keep track of everything I change, and learn how you like things done."),
        h("form", { onsubmit: async (e) => {
          e.preventDefault(); go.disabled = true; err.classList.add("hidden"); go.textContent = "Checking…";
          try { const r = await POST("key", { key: input.value }); S.settings = r.settings; if (S.bots.length) { w.remove(); O.ws.start(); } else O.showWelcome("bot"); }
          catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); go.disabled = false; go.textContent = "Continue"; }
        } }, input, err, go),
        h("div", { class: "small" }, "Get a key at ", h("a", { href: "https://platform.claude.com/settings/keys", target: "_blank", rel: "noopener noreferrer" }, "platform.claude.com"), ". It's kept in your Mac's Keychain."));
      setTimeout(() => input.focus(), 50);
    } else {
      inner.append(h("div", { class: "steps" }, h("i", { class: "on" }), h("i", { class: "on" })), ghost, h("h1", {}, "Make your first bot"),
        h("p", {}, "A bot works in one folder and keeps one long conversation. Make one per project."),
        botForm((st) => { w.remove(); O.ws.start(st.id); }, { name: "oomi", cta: "Create and start", big: true }));
    }
  };
})();
