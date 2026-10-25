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
    body.append(O.field("ID", h("div", { class: "row-inline" }, h("div", { class: "grow mono-line" }, b.id),
      h("button", { type: "button", class: "btn quiet", onclick: () => O.copyText(b.id) }, "Copy")),
    `Shown as ${O.shortId(b.id)} next to its name, to tell it apart from bots with the same name or color.`));
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

    // Anthropic is always there; with another provider added, each bot picks one
    if (s.providers && s.providers.length) body.append(O.field("Provider",
      O.select([["", "Anthropic"]].concat(s.providers.map((p) => [p.id, p.name])), b.provider || "", (v) => set({ provider: v }).then(() => O.toast("Used from its next message.", 1600)).catch(O.fail)),
      "Where its requests go. Manage providers in Settings, Providers."));
    body.append(O.field("Model", O.select([["", `App default (${O.modelName(s.model)})`]].concat(s.models.map((m) => [m, O.modelName(m)])), b.model, (v) => set({ model: v }))));
    body.append(O.field("Effort", O.select([["", `App default (${s.effort})`]].concat(s.efforts.map((e) => [e, e[0].toUpperCase() + e.slice(1)])), b.effort, (v) => set({ effort: v }))));
    const budget = h("input", { type: "number", min: "0", max: "1000", step: "0.5", value: b.budget ? String(b.budget) : "", placeholder: `App default ($${s.max_session_cost_usd})` });
    budget.addEventListener("change", () => set({ budget: Number(budget.value || 0) }).then(() => O.toast("Saved", 1000)).catch(O.fail));
    body.append(O.field("Daily spending limit ($)", budget, b.cost ? `${O.money(b.cost)} spent today.` : "Nothing spent today."));

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
      h("button", { type: "button", class: "btn delete", onclick: () => O.deleteBot(id) }, icon("trash", 16), "Delete")));
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
  // Brand marks for the catalog apps (Simple Icons, CC0). Apps without one show their initial; custom apps a plug.
  const APP_LOGOS = {
    notion: "M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L17.86 1.968c-.42-.326-.981-.7-2.055-.607L3.01 2.295c-.466.046-.56.28-.374.466zm.793 3.08v13.904c0 .747.373 1.027 1.214.98l14.523-.84c.841-.046.935-.56.935-1.167V6.354c0-.606-.233-.933-.748-.887l-15.177.887c-.56.047-.747.327-.747.933zm14.337.745c.093.42 0 .84-.42.888l-.7.14v10.264c-.608.327-1.168.514-1.635.514-.748 0-.935-.234-1.495-.933l-4.577-7.186v6.952L12.21 19s0 .84-1.168.84l-3.222.186c-.093-.186 0-.653.327-.746l.84-.233V9.854L7.822 9.76c-.094-.42.14-1.026.793-1.073l3.456-.233 4.764 7.279v-6.44l-1.215-.139c-.093-.514.28-.887.747-.933zM1.936 1.035l13.31-.98c1.634-.14 2.055-.047 3.082.7l4.249 2.986c.7.513.934.653.934 1.213v16.378c0 1.026-.373 1.634-1.68 1.726l-15.458.934c-.98.047-1.448-.093-1.962-.747l-3.129-4.06c-.56-.747-.793-1.306-.793-1.96V2.667c0-.839.374-1.54 1.447-1.632z",
    linear: "M2.886 4.18A11.982 11.982 0 0 1 11.99 0C18.624 0 24 5.376 24 12.009c0 3.64-1.62 6.903-4.18 9.105L2.887 4.18ZM1.817 5.626l16.556 16.556c-.524.33-1.075.62-1.65.866L.951 7.277c.247-.575.537-1.126.866-1.65ZM.322 9.163l14.515 14.515c-.71.172-1.443.282-2.195.322L0 11.358a12 12 0 0 1 .322-2.195Zm-.17 4.862 9.823 9.824a12.02 12.02 0 0 1-9.824-9.824Z",
    atlassian: "M7.12 11.084a.683.683 0 00-1.16.126L.075 22.974a.703.703 0 00.63 1.018h8.19a.678.678 0 00.63-.39c1.767-3.65.696-9.203-2.406-12.52zM11.434.386a15.515 15.515 0 00-.906 15.317l3.95 7.9a.703.703 0 00.628.388h8.19a.703.703 0 00.63-1.017L12.63.38a.664.664 0 00-1.196.006z",
    gmail: "M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 0 1 0 19.366V5.457c0-2.023 2.309-3.178 3.927-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 5.457z",
    intercom: "M21 0H3C1.343 0 0 1.343 0 3v18c0 1.658 1.343 3 3 3h18c1.658 0 3-1.342 3-3V3c0-1.657-1.342-3-3-3zm-5.801 4.399c0-.44.36-.8.802-.8.44 0 .8.36.8.8v10.688c0 .442-.36.801-.8.801-.443 0-.802-.359-.802-.801V4.399zM11.2 3.994c0-.44.357-.799.8-.799s.8.359.8.799v11.602c0 .44-.357.8-.8.8s-.8-.36-.8-.8V3.994zm-4 .405c0-.44.359-.8.799-.8.443 0 .802.36.802.8v10.688c0 .442-.36.801-.802.801-.44 0-.799-.359-.799-.801V4.399zM3.199 6c0-.442.36-.8.802-.8.44 0 .799.358.799.8v7.195c0 .441-.359.8-.799.8-.443 0-.802-.36-.802-.8V6zM20.52 18.202c-.123.105-3.086 2.593-8.52 2.593-5.433 0-8.397-2.486-8.521-2.593-.335-.288-.375-.792-.086-1.128.285-.334.79-.375 1.125-.09.047.041 2.693 2.211 7.481 2.211 4.848 0 7.456-2.186 7.479-2.207.334-.289.839-.25 1.128.086.289.336.25.84-.086 1.128zm.281-5.007c0 .441-.36.8-.801.8-.441 0-.801-.36-.801-.8V6c0-.442.361-.8.801-.8.441 0 .801.357.801.8v7.195z",
    github: "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12",
    sentry: "M13.91 2.505c-.873-1.448-2.972-1.448-3.844 0L6.904 7.92a15.478 15.478 0 0 1 8.53 12.811h-2.221A13.301 13.301 0 0 0 5.784 9.814l-2.926 5.06a7.65 7.65 0 0 1 4.435 5.848H2.194a.365.365 0 0 1-.298-.534l1.413-2.402a5.16 5.16 0 0 0-1.614-.913L.296 19.275a2.182 2.182 0 0 0 .812 2.999 2.24 2.24 0 0 0 1.086.288h6.983a9.322 9.322 0 0 0-3.845-8.318l1.11-1.922a11.47 11.47 0 0 1 4.95 10.24h5.915a17.242 17.242 0 0 0-7.885-15.28l2.244-3.845a.37.37 0 0 1 .504-.13c.255.14 9.75 16.708 9.928 16.9a.365.365 0 0 1-.327.543h-2.287c.029.612.029 1.223 0 1.831h2.297a2.206 2.206 0 0 0 1.922-3.31z",
    vercel: "M24 22.525H0l12-21.05 12 21.05z",
    supabase: "M11.9 1.036c-.015-.986-1.26-1.41-1.874-.637L.764 12.05C-.33 13.427.65 15.455 2.409 15.455h9.579l.113 7.51c.014.985 1.259 1.408 1.873.636l9.262-11.653c1.093-1.375.113-3.403-1.645-3.403h-9.642z",
    cloudflare: "M16.5088 16.8447c.1475-.5068.0908-.9707-.1553-1.3154-.2246-.3164-.6045-.499-1.0615-.5205l-8.6592-.1123a.1559.1559 0 0 1-.1333-.0713c-.0283-.042-.0351-.0986-.021-.1553.0278-.084.1123-.1484.2036-.1562l8.7359-.1123c1.0351-.0489 2.1601-.8868 2.5537-1.9136l.499-1.3013c.0215-.0561.0293-.1128.0147-.168-.5625-2.5463-2.835-4.4453-5.5499-4.4453-2.5039 0-4.6284 1.6177-5.3876 3.8614-.4927-.3658-1.1187-.5625-1.794-.499-1.2026.119-2.1665 1.083-2.2861 2.2856-.0283.31-.0069.6128.0635.894C1.5683 13.171 0 14.7754 0 16.752c0 .1748.0142.3515.0352.5273.0141.083.0844.1475.1689.1475h15.9814c.0909 0 .1758-.0645.2032-.1553l.12-.4268zm2.7568-5.5634c-.0771 0-.1611 0-.2383.0112-.0566 0-.1054.0415-.127.0976l-.3378 1.1744c-.1475.5068-.0918.9707.1543 1.3164.2256.3164.6055.498 1.0625.5195l1.8437.1133c.0557 0 .1055.0263.1329.0703.0283.043.0351.1074.0214.1562-.0283.084-.1132.1485-.204.1553l-1.921.1123c-1.041.0488-2.1582.8867-2.5527 1.914l-.1406.3585c-.0283.0713.0215.1416.0986.1416h6.5977c.0771 0 .1474-.0489.169-.126.1122-.4082.1757-.837.1757-1.2803 0-2.6025-2.125-4.727-4.7344-4.727",
    huggingface: "M1.4446 11.5059c0 1.1021.1673 2.1585.4847 3.1563-.0378-.0028-.0691-.0058-.1058-.0058-.4209 0-.8015.16-1.0704.4512-.3454.3737-.4984.8335-.4316 1.293a1.576 1.576 0 0 0 .2148.5978c-.2319.1864-.4018.4456-.4844.7578-.0646.2448-.131.7543.2149 1.2794a1.4552 1.4552 0 0 0-.0625.1055c-.208.3923-.2207.8372-.0371 1.25.2783.6258.9696 1.1175 2.3126 1.6467.8356.3292 1.5988.5411 1.6056.543 1.1046.2847 2.104.4277 2.969.4277 1.4173 0 2.4754-.3849 3.1525-1.1446 1.538.2651 2.791.1403 3.592.006.6773.7555 1.7332 1.1387 3.1467 1.1387.8649 0 1.8643-.143 2.969-.4278.0068-.0019.77-.2138 1.6056-.543 1.343-.5292 2.0343-1.0208 2.3126-1.6466.1836-.4129.171-.8577-.037-1.25a1.4685 1.4685 0 0 0-.0626-.1056c.346-.525.2795-1.0346.2149-1.2793-.0826-.3122-.2525-.5714-.4844-.7579.11-.1816.1831-.3788.2148-.5977.0669-.4595-.0862-.9193-.4316-1.293-.2688-.2913-.6495-.4513-1.0704-.4513-.0209 0-.0376.0008-.0588.0018.3162-.9966.4846-2.0518.4846-3.1523 0-5.807-4.7362-10.5144-10.5789-10.5144-5.8426 0-10.5788 4.7073-10.5788 10.5144Zm10.5788-9.4831c5.2727 0 9.5476 4.246 9.5476 9.483a9.4201 9.4201 0 0 1-.2696 2.2365c-.0039-.0047-.0079-.011-.0117-.0156-.274-.3255-.6679-.5059-1.1075-.5059-.352 0-.714.1155-1.0763.3438-.2403.1517-.5058.422-.7793.7598-.2534-.3492-.608-.5832-1.0137-.6465a1.5174 1.5174 0 0 0-.2344-.0176c-.9263 0-1.4828.7993-1.6935 1.5177-.1046.2426-.6065 1.3482-1.3614 2.0978-1.1681 1.1601-1.4458 2.3534-.8396 3.6382-.843.1029-1.5836.0927-2.365-.006.5906-1.212.3626-2.4388-.8426-3.6322-.755-.7496-1.2568-1.8552-1.3614-2.0978-.2107-.7184-.7673-1.5177-1.6935-1.5177-.078 0-.1568.0054-.2344.0176-.4057.0633-.7604.2973-1.0137.6465-.2735-.3379-.539-.6081-.7794-.7598-.3622-.2283-.7243-.3438-1.0762-.3438-.4266 0-.8094.171-1.0821.4786a9.4208 9.4208 0 0 1-.2598-2.1936c0-5.237 4.2749-9.483 9.5475-9.483zM8.6443 7.0036c-.4838.0043-.9503.2667-1.1934.7227-.3536.6633-.1006 1.4873.5645 1.84.351.1862.4883-.5261.836-.6485.3107-.1095.841.399 1.0078.086.3536-.6634.1025-1.4874-.5625-1.84a1.3659 1.3659 0 0 0-.6524-.1602Zm6.8403 0c-.2199-.002-.4426.05-.6504.1602-.665.3526-.9181 1.1766-.5645 1.84.1669.313.6971-.1955 1.0079-.086.3476.1224.4867.8347.838.6485.6649-.3527.916-1.1767.5624-1.84-.243-.456-.7096-.7184-1.1934-.7227Zm-9.7565 1.418a.8768.8768 0 0 0-.877.877c0 .4846.3925.877.877.877a.8768.8768 0 0 0 .877-.877.8768.8768 0 0 0-.877-.877zm12.6434 0c-.4845 0-.879.3925-.879.877 0 .4846.3945.877.879.877a.8768.8768 0 0 0 .877-.877.8768.8768 0 0 0-.877-.877zM8.7927 11.459c-.179-.003-.2793.1107-.2793.416 0 .8097.3874 2.125 1.4279 2.924.207-.7123 1.3453-1.2832 1.5079-1.2012.2315.1167.2191.4417.6074.7266.3884-.285.374-.6098.6056-.7266.1627-.082 1.3009.4889 1.5079 1.2012 1.0404-.799 1.4278-2.1144 1.4278-2.924 0-1.2212-1.583.6402-3.5413.6485-1.4686-.0061-2.7266-1.0558-3.2639-1.0645zM4.312 14.4768c.5792.365 1.6964 2.2751 2.1056 3.0177.1371.2488.371.3536.582.3536.4188 0 .7465-.4138.0391-.9395-1.0636-.791-.6914-2.0846-.1836-2.1642a.4302.4302 0 0 1 .0664-.004c.4616 0 .666.7892.666.7892s.5959 1.4898 1.6213 2.508c.942.9356 1.062 1.703.4961 2.6661-.0164-.004-.0159.0236-.1484.2149-.1853.2673-.4322.4688-.7188.6152-.5062.2269-1.1397.2696-1.7833.2696-1.037 0-2.1017-.1824-2.6975-.336-.0293-.0075-3.6505-.9567-3.1916-1.8224.0771-.1454.2033-.2031.3633-.2031.6463 0 1.823.9551 2.3283.9551.113 0 .196-.0865.2285-.2031.2249-.8045-3.2787-1.0522-2.9846-2.1642.0519-.1967.193-.2757.3907-.2754.854 0 2.7704 1.4923 3.172 1.4923.0307 0 .0525-.0085.0645-.0274.2012-.3227.1096-.5865-1.3087-1.4395-1.4182-.8533-2.4315-1.329-1.8653-1.9416.0651-.0707.1574-.1015.2695-.1015.8611.0002 2.8948 1.84 2.8948 1.84s.5487.5683.8809.5683c.0762 0 .1416-.0315.1855-.1054.2355-.3946-2.1858-2.2183-2.3224-2.971-.0926-.51.0641-.7676.3555-.7676-.0006.008.1701-.0285.4942.1759zm16.2257.5918c-.1366.7526-2.5579 2.5764-2.3224 2.9709.044.074.1092.1055.1855.1055.3321 0 .881-.5684.881-.5684s2.0336-1.8397 2.8947-1.84c.1121 0 .2044.0308.2695.1016.5662.6125-.447 1.0882-1.8653 1.9415-1.4183.853-1.51 1.1168-1.3087 1.4396.012.0188.0337.0273.0644.0273.4016 0 2.3181-1.4923 3.1721-1.4923.1977-.0002.3388.0787.3907.2754.294 1.112-3.2095 1.3597-2.9846 2.1642.0325.1166.1156.2032.2285.2032.5054 0 1.682-.9552 2.3283-.9552.16 0 .2862.0577.3633.2032.459.8656-3.1623 1.8149-3.1916 1.8224-.5958.1535-1.6605.336-2.6975.336-.6351 0-1.261-.0409-1.7638-.2599-.2949-.1472-.5488-.3516-.7383-.625-.0411-.0682-.1026-.1476-.1426-.205-.5726-.9679-.455-1.7371.4903-2.676 1.0254-1.0182 1.6212-2.508 1.6212-2.508s.2044-.7891.666-.7891a.4318.4318 0 0 1 .0665.0039c.5078.0796.88 1.3732-.1836 2.1642-.7074.5257-.3797.9395.039.9395.211 0 .445-.1047.5821-.3535.4092-.7426 1.5264-2.6527 2.1056-3.0178.5588-.3524.99-.1816.8497.5918z",
    figma: "M15.852 8.981h-4.588V0h4.588c2.476 0 4.49 2.014 4.49 4.49s-2.014 4.491-4.49 4.491zM12.735 7.51h3.117c1.665 0 3.019-1.355 3.019-3.019s-1.355-3.019-3.019-3.019h-3.117V7.51zm0 1.471H8.148c-2.476 0-4.49-2.014-4.49-4.49S5.672 0 8.148 0h4.588v8.981zm-4.587-7.51c-1.665 0-3.019 1.355-3.019 3.019s1.354 3.02 3.019 3.02h3.117V1.471H8.148zm4.587 15.019H8.148c-2.476 0-4.49-2.014-4.49-4.49s2.014-4.49 4.49-4.49h4.588v8.98zM8.148 8.981c-1.665 0-3.019 1.355-3.019 3.019s1.355 3.019 3.019 3.019h3.117V8.981H8.148zM8.172 24c-2.489 0-4.515-2.014-4.515-4.49s2.014-4.49 4.49-4.49h4.588v4.441c0 2.503-2.047 4.539-4.563 4.539zm-.024-7.51a3.023 3.023 0 0 0-3.019 3.019c0 1.665 1.365 3.019 3.044 3.019 1.705 0 3.093-1.376 3.093-3.068v-2.97H8.148zm7.704 0h-.098c-2.476 0-4.49-2.014-4.49-4.49s2.014-4.49 4.49-4.49h.098c2.476 0 4.49 2.014 4.49 4.49s-2.014 4.49-4.49 4.49zm-.097-7.509c-1.665 0-3.019 1.355-3.019 3.019s1.355 3.019 3.019 3.019h.098c1.665 0 3.019-1.355 3.019-3.019s-1.355-3.019-3.019-3.019h-.098z",
    canva: "M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zM6.962 7.68c.754 0 1.337.549 1.405 1.2.069.583-.171 1.097-.822 1.406-.343.171-.48.172-.549.069-.034-.069 0-.137.069-.206.617-.514.617-.926.548-1.508-.034-.378-.308-.618-.583-.618-1.2 0-2.914 2.674-2.674 4.629.103.754.549 1.646 1.509 1.646.308 0 .65-.103.96-.24.5-.264.799-.47 1.097-.8-.073-.885.704-2.046 1.851-2.046.515 0 .926.205.96.583.068.514-.377.582-.514.582s-.378-.034-.378-.17c-.034-.138.309-.07.275-.378-.035-.206-.24-.274-.446-.274-.72 0-1.131.994-1.029 1.611.035.275.172.549.447.549.205 0 .514-.31.617-.755.068-.308.343-.514.583-.514.102 0 .17.034.205.171v.138c-.034.137-.137.548-.102.651 0 .069.034.171.17.171.092 0 .436-.18.777-.459.117-.59.253-1.298.253-1.357.034-.24.137-.48.617-.48.103 0 .171.034.205.171v.138l-.136.617c.445-.583 1.097-.994 1.508-.994.172 0 .309.102.309.274 0 .103 0 .274-.069.446-.137.377-.309.96-.412 1.474 0 .137.035.274.207.274.171 0 .685-.206 1.096-.754l.007-.004c-.002-.068-.007-.134-.007-.202 0-.411.035-.754.104-.994.068-.274.411-.514.617-.514.103 0 .205.069.205.171 0 .035 0 .103-.034.137-.137.446-.24.857-.24 1.269 0 .24.034.582.102.788 0 .034.035.069.07.069.068 0 .548-.445.89-1.028-.308-.206-.48-.549-.48-.96 0-.72.446-1.097.858-1.097.343 0 .617.24.617.72 0 .308-.103.65-.274.96h.102a.77.77 0 0 0 .584-.24.293.293 0 0 1 .134-.117c.335-.425.83-.74 1.41-.74.48 0 .924.205.959.582.068.515-.378.618-.515.618l-.002-.002c-.138 0-.377-.035-.377-.172 0-.137.309-.068.274-.376-.034-.206-.24-.275-.446-.275-.686 0-1.13.891-1.028 1.611.034.275.171.583.445.583.206 0 .515-.308.652-.754.068-.274.343-.514.583-.514.103 0 .17.034.205.171 0 .069 0 .206-.137.652-.17.308-.171.48-.137.617.034.274.171.48.309.583.034.034.068.102.068.102 0 .069-.034.138-.137.138-.034 0-.068 0-.103-.035-.514-.205-.72-.548-.789-.891-.205.24-.445.377-.72.377-.445 0-.89-.411-.96-.926a1.609 1.609 0 0 1 .075-.649c-.203.13-.422.203-.623.203h-.17c-.447.652-.927 1.098-1.27 1.303a.896.896 0 0 1-.377.104c-.068 0-.171-.035-.205-.104-.095-.152-.156-.392-.193-.667-.481.527-1.145.805-1.453.805-.343 0-.548-.206-.582-.55v-.376c.102-.754.377-1.2.377-1.337a.074.074 0 0 0-.069-.07c-.24 0-1.028.824-1.166 1.373l-.103.445c-.068.309-.377.515-.582.515-.103 0-.172-.035-.206-.172v-.137l.046-.233c-.435.31-.87.508-1.075.508-.308 0-.48-.172-.514-.412-.206.274-.445.412-.754.412-.352 0-.696-.24-.862-.593-.244.275-.523.553-.852.764-.48.309-1.028.549-1.68.549-.582 0-1.097-.309-1.371-.583-.412-.377-.651-.96-.686-1.509-.205-1.68.823-3.84 2.4-4.8.378-.205.755-.343 1.132-.343zm9.77 3.291c-.104 0-.172.172-.172.343 0 .274.137.583.309.755a1.74 1.74 0 0 0 .102-.583c0-.343-.137-.515-.24-.515z",
    stripe: "M13.976 9.15c-2.172-.806-3.356-1.426-3.356-2.409 0-.831.683-1.305 1.901-1.305 2.227 0 4.515.858 6.09 1.631l.89-5.494C18.252.975 15.697 0 12.165 0 9.667 0 7.589.654 6.104 1.872 4.56 3.147 3.757 4.992 3.757 7.218c0 4.039 2.467 5.76 6.476 7.219 2.585.92 3.445 1.574 3.445 2.583 0 .98-.84 1.545-2.354 1.545-1.875 0-4.965-.921-6.99-2.109l-.9 5.555C5.175 22.99 8.385 24 11.714 24c2.641 0 4.843-.624 6.328-1.813 1.664-1.305 2.525-3.236 2.525-5.732 0-4.128-2.524-5.851-6.594-7.305h.003z",
    paypal: "M7.016 19.198h-4.2a.562.562 0 0 1-.555-.65L5.093.584A.692.692 0 0 1 5.776 0h7.222c3.417 0 5.904 2.488 5.846 5.5-.006.25-.027.5-.066.747A6.794 6.794 0 0 1 12.071 12H8.743a.69.69 0 0 0-.682.583l-.325 2.056-.013.083-.692 4.39-.015.087zM19.79 6.142c-.01.087-.01.175-.023.261a7.76 7.76 0 0 1-7.695 6.598H9.007l-.283 1.795-.013.083-.692 4.39-.134.843-.014.088H6.86l-.497 3.15a.562.562 0 0 0 .555.65h3.612c.34 0 .63-.249.683-.585l.952-6.031a.692.692 0 0 1 .683-.584h2.126a6.793 6.793 0 0 0 6.707-5.752c.306-1.95-.466-3.744-1.89-4.906z",
    posthog: "M9.854 14.5 5 9.647.854 5.5A.5.5 0 0 0 0 5.854V8.44a.5.5 0 0 0 .146.353L5 13.647l.147.146L9.854 18.5l.146.147v-.049c.065.03.134.049.207.049h2.586a.5.5 0 0 0 .353-.854L9.854 14.5zm0-5-4-4a.487.487 0 0 0-.409-.144.515.515 0 0 0-.356.21.493.493 0 0 0-.089.288V8.44a.5.5 0 0 0 .147.353l9 9a.5.5 0 0 0 .853-.354v-2.585a.5.5 0 0 0-.146-.354l-5-5zm1-4a.5.5 0 0 0-.854.354V8.44a.5.5 0 0 0 .147.353l4 4a.5.5 0 0 0 .853-.354V9.854a.5.5 0 0 0-.146-.354l-4-4zm12.647 11.515a3.863 3.863 0 0 1-2.232-1.1l-4.708-4.707a.5.5 0 0 0-.854.354v6.585a.5.5 0 0 0 .5.5H23.5a.5.5 0 0 0 .5-.5v-.6c0-.276-.225-.497-.499-.532zm-5.394.032a.8.8 0 1 1 0-1.6.8.8 0 0 1 0 1.6zM.854 15.5a.5.5 0 0 0-.854.354v2.293a.5.5 0 0 0 .5.5h2.293c.222 0 .39-.135.462-.309a.493.493 0 0 0-.109-.545L.854 15.501zM5 14.647.854 10.5a.5.5 0 0 0-.854.353v2.586a.5.5 0 0 0 .146.353L4.854 18.5l.146.147h2.793a.5.5 0 0 0 .353-.854L5 14.647z",
  };
  APP_LOGOS["cloudflare-docs"] = APP_LOGOS.cloudflare;
  // not a catalog app: shown where oomi talks to your Atlas cluster (Account, Cloud sync)
  APP_LOGOS.mongodb = "M17.193 9.555c-1.264-5.58-4.252-7.414-4.573-8.115-.28-.394-.53-.954-.735-1.44-.036.495-.055.685-.523 1.184-.723.566-4.438 3.682-4.74 10.02-.282 5.912 4.27 9.435 4.888 9.884l.07.05A73.49 73.49 0 0 1 11.91 24h.481c.114-1.032.284-2.056.51-3.07.417-.296.604-.463.85-.693a11.342 11.342 0 0 0 3.639-8.464c.01-.814-.103-1.662-.197-2.218zm-5.336 8.195s0-8.291.275-8.29c.213 0 .49 10.695.49 10.695-.381-.045-.765-1.76-.765-2.405z";
  const ATLAS = { id: "mongodb", name: "MongoDB Atlas", color: "#00684A" };
  const appLogo = (a, cls = "") => {
    const d = APP_LOGOS[a.id];
    const glyph = d ? `<svg viewBox="0 0 24 24" fill="currentColor"><path d="${d}"/></svg>`
      : a.custom ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${O.P.plug}</svg>` : null;
    const rgb = /^#[0-9a-f]{6}$/i.test(a.color || "") ? parseInt(a.color.slice(1), 16) : 0;
    const light = 0.299 * (rgb >> 16) + 0.587 * ((rgb >> 8) & 255) + 0.114 * (rgb & 255) > 170;
    return h("span", { class: `app-logo ${cls}${light ? " light" : ""}`, style: `background:${a.color || "#444"}`, title: a.name },
      glyph ? h("span", { class: "glyph", "aria-hidden": "true", html: glyph }) : a.name[0]);
  };

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
      async () => { await O.api("DELETE", `bots/${id}`); O.botGone(id); }, "delete");
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
    ["account", "Account", "person"], ["providers", "Providers", "gear"], ["apps", "Apps", "apps"], ["antibodies", "Antibodies", "antibody"], ["display", "Display", "eye"], ["notifications", "Notifications", "bell"],
    ["safety", "Safety and limits", "lock"], ["memory", "General memory", "memory"], ["skills", "General skills", "skills"],
    ["status", "Status", "pulse"], ["shortcuts", "Shortcuts", "keyboard"],
  ];
  // what settings search finds: [tab, key, label, more words]
  const INDEX = [
    ["account", "profile", "Your profile", "name email about me bio account personal"], ["account", "timezone", "Time zone", "clock local time"],
    ["account", "language", "Reply language", "english spanish french locale"], ["account", "units", "Units", "metric imperial miles kilometers"],
    ["account", "share", "Let bots know this", "privacy profile share"], ["account", "sync", "Cloud sync", "mongodb atlas database backup cloud mirror"],
    ["account", "backup", "Back up everything", "mongodb atlas backup restore bots conversations memories another mac"],
    ["providers", "key", "Anthropic API key", "token secret keychain"], ["providers", "model", "Model", "sonnet opus haiku fable"],
    ["providers", "effort", "Effort", "thinking reasoning"], ["providers", "budget", "Daily spending limit per bot", "cost money dollars budget day"],
    ["providers", "others", "Other providers", "add provider gateway proxy base url openrouter litellm bedrock vertex key"],
    ["providers", "fallback", "Fallback models", "overloaded busy retry order"], ["providers", "small", "Background model", "learning summaries compaction cheap"],
    ["providers", "web", "Web search", "internet browse fetch"], ["providers", "learn", "Learn about me", "memory facts preferences"],
    ["providers", "awake", "Keep the Mac awake", "sleep caffeinate power"], ["providers", "color", "oomi's color", "ghost dock icon theme blue pink purple green peach"],
    ["antibodies", "", "Antibodies", "team fixes errors mongodb atlas shared proven cures vector search rerank"],
    ["antibodies", "test", "Test antibodies, step by step", "try check prompt broken demo share how"],
    ["antibodies", "autoshare", "Share fixes automatically", "antibodies approve auto share without asking"],
    ["apps", "", "Connected apps", "connectors mcp notion github linear jira sentry vercel supabase stripe integrations"], ["providers", "file", "Export, import or reset settings", "backup restore defaults file"],
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
  O.openSettings = async function openSettings(tab = "providers", highlight = "") {
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
    const draw = { account: drawAccount, providers: drawProviders, apps: drawApps, antibodies: drawAntibodies, display: drawDisplay, notifications: drawNotifications, safety: drawSafety, memory: drawMemory, skills: drawSkills, status: drawStatus, shortcuts: drawShortcuts }[tab];
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

  async function drawProviders(body) {
    const s = S.settings = await GET("settings");
    if (!body.isConnected) return;
    body.append(O.field("Anthropic API key", h("div", { class: "row-inline" }, h("div", { class: "grow mono-line" }, s.has_key ? s.key_hint || "Saved" : "Not set"),
      h("button", { type: "button", class: "btn", onclick: keyBox }, s.has_key ? "Replace" : "Add key")),
    s.key_from_env ? "Read from ANTHROPIC_API_KEY in your environment." : "Kept in your Mac's Keychain.", "key"));
    const others = h("div", { class: "list" }, s.providers.map((p) => h("div", { class: "li" },
      h("div", { class: "grow" }, p.name, h("div", { class: "small mono-line" }, p.has_key ? p.base_url : `${p.base_url} · key missing`)),
      h("div", { class: "mini" }, h("button", { type: "button", onclick: () => {
        const using = S.bots.filter((b) => b.provider === p.id).map((b) => b.name);
        O.confirmBox(`Remove ${p.name}?`, `Its key is deleted from your Keychain.${using.length ? ` ${using.join(", ")} will use Anthropic again.` : ""}`, "Remove",
          async () => { S.settings = await O.api("DELETE", `providers/${p.id}`); O.refreshSettings("providers"); }, true);
      } }, "Remove")))));
    body.append(O.field("Other providers", h("div", {}, s.providers.length ? others : null,
      h("button", { type: "button", class: "btn", style: s.providers.length ? "margin-top:8px" : "", onclick: providerBox }, icon("plus", 16), "Add provider")),
    "Gateways and proxies that speak Anthropic's Messages API, or a second Anthropic account. With more than one provider, each bot's settings choose which it uses.", "others"));
    body.append(O.field("oomi's color", O.colorPicker(s.ghost_color, async (c) => { await save({ ghost_color: c }); O.applyGhostColor(); }),
      "For the Dock and for bots without a color of their own. Each bot's color is in its settings.", "color"));
    body.append(O.field("Model", O.select(s.models.map((m) => [m, O.modelName(m)]), s.model, (v) => save({ model: v }).then(() => O.refreshSettings("providers"))),
      "Sonnet is quick and capable. Opus is stronger and costs more.", "model"));
    body.append(O.field("Effort", O.select(s.efforts.map((e) => [e, e[0].toUpperCase() + e.slice(1)]), s.effort, (v) => save({ effort: v })), "Higher effort thinks longer on hard problems.", "effort"));
    const budget = h("input", { type: "number", min: "0.5", max: "1000", step: "0.5", value: String(s.max_session_cost_usd) });
    budget.addEventListener("change", async () => { try { await save({ max_session_cost_usd: Number(budget.value) }); O.toast("Saved", 1000); } catch (e) { O.fail(e); } });
    body.append(O.field("Daily spending limit per bot ($)", budget, "Each bot stops and asks when it has spent this much today. The limit starts over at midnight.", "budget"));

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
        async () => { S.settings = await POST("settings/reset"); O.remoteUi(await GET("ui")); O.ws.render(); O.ws.refreshThreads(); O.toast("Settings reset"); O.refreshSettings("providers"); }, true) }, "Reset to defaults")),
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
    if (cur.read_project_files === false && c.read_project_files === true) looser.push("Starts reading CLAUDE.md and AGENTS.md in bot folders");
    if (Number(c.max_session_cost_usd) > Number(cur.max_session_cost_usd)) looser.push(`Raises the daily spending limit per bot to $${Number(c.max_session_cost_usd)}`);
    const go = async () => {
      S.settings = await POST("settings/import", { data });
      O.remoteUi(await GET("ui"));
      O.ws.render(); O.ws.refreshThreads();
      O.toast("Settings imported");
      O.refreshSettings("providers");
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
          try { const r = await POST("key", { key: input.value }); S.settings = r.settings; close(); O.toast(r.message, 3000); O.refreshSettings("providers"); }
          catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); ok.disabled = false; }
        } }, input, err, h("div", { class: "foot" }, h("button", { type: "button", class: "btn quiet", onclick: close }, "Cancel"), ok)));
    });
  }
  function providerBox() {
    O.modal((box, close) => {
      const name = h("input", { class: "text-input", placeholder: "Name, like Work gateway", maxlength: "40", spellcheck: "false" });
      const url = h("input", { class: "text-input", placeholder: "https://gateway.example.com", spellcheck: "false", autocomplete: "off" });
      const key = h("input", { class: "text-input", type: "password", placeholder: "API key", autocomplete: "off", spellcheck: "false" });
      const err = h("div", { class: "err hidden" });
      const ok = h("button", { type: "submit", class: "btn primary" }, "Add provider");
      box.append(h("h3", {}, "Add a provider"), h("p", { class: "lead" }, "Any service that speaks Anthropic's Messages API. Its address is the one you'd give the Anthropic SDK as base_url. The key is kept in your Keychain."),
        h("form", { onsubmit: async (e) => {
          e.preventDefault(); ok.disabled = true; err.classList.add("hidden");
          try { S.settings = await POST("providers", { name: name.value, base_url: url.value, key: key.value }); close(); O.toast(`${name.value.trim()} added. Choose it in a bot's settings.`, 3000); O.refreshSettings("providers"); }
          catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); ok.disabled = false; }
        } }, h("div", { class: "field" }, h("label", {}, "Name"), name), h("div", { class: "field" }, h("label", {}, "Address"), url),
        h("div", { class: "field" }, h("label", {}, "API key"), key), err,
        h("div", { class: "foot" }, h("button", { type: "button", class: "btn quiet", onclick: close }, "Cancel"), ok)));
      setTimeout(() => name.focus(), 30);
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
      const d = a.data;
      const size = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
      // Uploading replaces the backup; if another Mac made it, say so first
      const upload = async () => {
        if (d.enabled) {
          let r = null;
          try { r = (await GET("account/data/remote")).remote; } catch (e) { return O.fail(e); }
          if (r && !r.this_mac) return O.confirmBox(`Replace the backup from ${r.host}?`, `The backup in the database was made on ${r.host}, ${when(r.updated)}. Uploading replaces it with this Mac's bots, conversations and memories.`, "Upload", () => run(up, "account/sync/push", {}, "Uploaded"), true);
        }
        run(up, "account/sync/push", {}, "Uploaded");
      };
      const restore = async () => {
        let r = null;
        try { r = (await GET("account/data/remote")).remote; } catch (e) { return O.fail(e); }
        if (!r) return O.toast("There's no backup in the database for this account yet.", 3000);
        O.confirmBox(`Restore the backup from ${r.host}?`, `Your bots, conversations, memories, skills and settings here are replaced by the backup from ${r.host}, ${when(r.updated)} (${r.files} files, ${size(r.bytes)}). What's replaced is kept in ~/.oomi/backups. It finishes when you quit and reopen oomi.`, "Download",
          async () => { await POST("account/data/restore"); O.toast("Downloaded. Quit and reopen oomi to finish.", 4000); O.refreshSettings("account"); }, true);
      };
      const up = h("button", { type: "button", class: "btn", onclick: upload }, icon("upload", 15), "Upload now");
      const down = h("button", { type: "button", class: "btn", onclick: () => O.confirmBox("Replace your profile here?", "The profile in the database replaces the one on this Mac.", "Download", () => run(down, "account/sync/pull", {}, "Downloaded")) }, icon("download", 15), "Download profile");
      sync.append(...[ // DOM append writes null as text
        h("div", { class: "list" },
          h("div", { class: "li" }, appLogo(ATLAS, "sm"), h("div", { class: "grow" }, `MongoDB Atlas · ${s.database}.${s.collection}`,
            h("div", { class: "small" }, `Uploaded ${when(s.last_push)} · downloaded ${when(s.last_pull)}`), s.error ? h("div", { class: "small", style: "color:var(--fg)" }, s.error) : null),
            h("span", { class: "mark-txt", title: s.error ? "Last sync failed" : "Connected" }, s.error ? "!" : "✓")),
          h("div", { class: "li" }, h("div", { class: "grow" }, "Account ID", h("div", { class: "small mono-line" }, a.id)),
            h("div", { class: "mini" }, h("button", { type: "button", onclick: () => O.copyText(a.id, "Account ID copied") }, "Copy")))),
        O.toggleRow("Back up everything else", "Bots and their settings, conversations, memories, skills, schedules, usage and attachments, every 15 minutes and when you quit. API keys and app sign-ins stay in your Keychain.",
          d.enabled, (v) => POST("account/data/enabled", { on: v }).then(() => O.refreshSettings("account")), "backup"),
        d.enabled ? h("div", { class: "small-note" }, d.last_push ? `Backed up ${when(d.last_push)} · ${d.files} files, ${size(d.bytes)}` : "Not backed up yet.",
          d.skipped.length ? ` ${d.skipped.length} ${d.skipped.length === 1 ? "file was" : "files were"} too big to back up (over 64 MB).` : "",
          d.error ? h("div", { style: "color:var(--fg)" }, d.error) : null) : null,
        d.other && !d.pending_restore ? h("p", { class: "small-note", style: "color:var(--fg)" }, `The backup in the database is from ${d.other.host} (${when(d.other.updated)}), so automatic backups from this Mac are paused. Restore it here, or upload this Mac's to replace it.`) : null,
        d.pending_restore ? h("div", { class: "row-inline" }, h("p", { class: "small-note grow", style: "color:var(--fg);margin:0" }, "A backup is downloaded. Quit and reopen oomi to finish restoring it."),
          h("button", { type: "button", class: "btn quiet", onclick: () => POST("account/data/cancel_restore").then(() => O.refreshSettings("account"), O.fail) }, "Cancel restore")) : null,
        h("div", { class: "row-inline wrap", style: "margin-top:10px" }, up,
          h("button", { type: "button", class: "btn", onclick: restore }, icon("download", 15), "Restore everything…"), down,
          h("button", { type: "button", class: "btn quiet", onclick: () => O.promptBox("Use another Mac's account", "Paste the account ID from the other Mac's Account settings. Its profile replaces the one here.", "", "Use it",
            (v) => POST("account/sync/pull", { account_id: v.trim() }).then(() => { O.toast("Switched account", 1600); O.refreshSettings("account"); }), { placeholder: "acct_…", max: 40 }) }, "Use another account…"),
          h("button", { type: "button", class: "btn quiet", onclick: () => O.confirmBox("Stop syncing?", "Your profile and backup stay on this Mac and in the database; the connection string is removed from your Keychain.", "Stop syncing", () => POST("account/sync/disconnect").then(() => O.refreshSettings("account"))) }, "Disconnect"))].filter(Boolean));
    } else {
      const uri = h("input", { class: "text-input", type: "password", placeholder: "mongodb+srv://user:password@cluster0.example.mongodb.net/", autocomplete: "off", spellcheck: "false" });
      const db = h("input", { class: "text-input", value: s.database || "oomi", spellcheck: "false", "aria-label": "Database" });
      const coll = h("input", { class: "text-input", value: s.collection || "accounts", spellcheck: "false", "aria-label": "Collection" });
      const err = h("div", { class: "err hidden" });
      const go = h("button", { type: "submit", class: "btn primary" }, "Connect");
      sync.append(h("div", { class: "row-inline", style: "margin-bottom:10px" }, appLogo(ATLAS),
        h("p", { class: "small-note grow", style: "margin:0" }, "Keep a copy of your profile, bots, conversations and memories in your own MongoDB Atlas database, to back them up or move to another Mac. API keys and app sign-ins stay in your Keychain.")),
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
    // Atlas can take a few seconds to answer (up to 8 when it's unreachable)
    const loading = h("div", { class: "list" }, h("div", { class: "li" }, appLogo(ATLAS, "sm"),
      h("div", { class: "grow" }, "Loading antibodies…", h("div", { class: "small" }, "Asking MongoDB Atlas for your team's fixes.")), h("span", { class: "spin" })));
    body.append(loading);
    let a;
    let s;
    try { [a, s] = await Promise.all([GET("antibodies"), GET("settings")]); } finally { loading.remove(); }
    S.settings = s;
    if (!body.isConnected) return;
    const again = () => O.refreshSettings("antibodies");
    const mins = (s) => (s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);

    // the connection, always first; the driver's own error (often a page long) is under Details
    if (!a.connected) {
      const retry = h("button", { type: "button", class: "btn", onclick: async () => { retry.disabled = true; try { await POST("antibodies/connect"); } catch (e) { O.fail(e); } again(); } }, "Try again");
      const why = !a.error ? "Connect your cluster in Account (Cloud sync), or set MONGODB_URI. Your team shares one database; OOMI_TEAM names the team."
        : /timed? ?out|ServerSelection/i.test(a.error) ? "The cluster didn't answer. Check your internet connection, and that this network's IP address is allowed in Atlas, Network Access."
        : /auth/i.test(a.error) ? "Atlas turned down the username or password in the connection string."
        : O.clip(a.error, 160);
      body.append(h("div", { class: "list" }, h("div", { class: "li" }, appLogo(ATLAS, "sm"),
        h("div", { class: "grow" }, a.error ? "Couldn't reach MongoDB Atlas" : "Not connected to MongoDB Atlas",
          h("div", { class: "small" }, why),
          a.error ? h("details", { class: "err-details" }, h("summary", {}, "Details"), h("pre", {}, a.error)) : null),
        h("span", { class: "mark-txt" }, a.error ? "!" : "·"))),
        h("div", { class: "row-inline", style: "margin-top:10px" }, h("button", { type: "button", class: "btn primary", onclick: () => O.openSettings("account", "sync") }, "Connect in Account"), retry),
        howItWorks(), testGuide(false));
      return;
    }
    const setup = h("button", { type: "button", onclick: async () => {
      setup.disabled = true; setup.textContent = "Setting up…";
      try { const r = await POST("antibodies/setup", { wait: 20 }); O.toast((r.report || []).join("\n") || "Done", 6000); } catch (e) { O.fail(e); }
      again();
    } }, "Set up search indexes");
    const searchLine = a.search_error ? `Search stepped down to ${a.mode}: ${a.search_error}`
      : a.mode === "local" ? "Matching on this Mac, word by word (OOMI_ATLAS_SEARCH=0)" : `Matching with ${a.mode} search`;
    body.append(h("div", { class: "list" }, h("div", { class: "li" }, appLogo(ATLAS, "sm"),
      h("div", { class: "grow" }, `MongoDB Atlas · team ${a.team}`, h("div", { class: "small" }, `Sharing as ${a.user}`),
        h("div", { class: "small", style: a.search_error ? "color:var(--fg)" : "", title: "Hybrid search ($rankFusion of vector and text), reranked by Voyage inside Atlas, when the cluster supports it." }, searchLine)),
      h("div", { class: "mini" }, setup), h("span", { class: "mark-txt" }, a.search_error ? "!" : "✓"))),
      O.toggleRow("Share fixes automatically", s.antibody_auto_share
        ? "When a bot proves a fix, it's shared in memory straight away. There's no Share button in the chat."
        : "Off: when a bot proves a fix, the chat shows a Share button and nothing is shared until you click it.",
      s.antibody_auto_share, (v) => save({ antibody_auto_share: v }).then(() => { O.toast(v ? "Fixes are shared automatically" : "Fixes wait for Share in the chat", 2000); again(); }), "autoshare"));

    const empty = !a.approved.length && !a.pending.length;
    if (empty) body.append(howItWorks());
    else {
      const card = (v, k, t) => h("div", { class: "stat" }, h("div", { class: "v" }, v), h("div", { class: "k" }, k), t ? h("div", { class: "t" }, t) : null);
      body.append(h("div", { class: "stat-grid four", style: "margin-top:12px" },
        card(String(a.stats.antibodies), a.stats.antibodies === 1 ? "Proven fix" : "Proven fixes", `team ${a.team}`),
        card(String(a.stats.cures), a.stats.cures === 1 ? "Cure" : "Cures", "errors fixed with them"),
        card(mins(a.stats.saved_seconds || 0), "Time saved"), card(O.money(a.stats.saved_usd || 0), "Money saved")));
    }

    // Each antibody is a card titled by its error's type ("ModuleNotFoundError"), or its first line when it has none
    const titleOf = (d) => {
      const lines = (d.error_text || "").split("\n").map((l) => l.trim().replace(/^E\s{2,}/, "")).filter(Boolean); // pytest marks error lines "E   "
      const m = (lines[0] || "").match(/^(?:\w+\.)*([A-Z]\w*(?:Error|Exception|Timeout|Warning|Exit|Interrupt|Failure)\w*):?\s*([\s\S]*)$/);
      if (m) return { name: m[1], detail: [m[2], ...lines.slice(1)].filter(Boolean).join("\n") };
      return { name: O.clip(lines[0] || "An error", 90), detail: lines.slice(1).join("\n") };
    };
    const doc = (d, actions) => { const t = titleOf(d); return h("div", { class: "routine antibody" }, h("div", { class: "grow" },
      h("div", { class: "ab-title", title: d.error_text }, t.name),
      t.detail ? h("div", { class: "ab-error", title: "Show all", onclick: (e) => e.currentTarget.classList.toggle("open") }, h("div", { class: "ab-clamp" }, t.detail)) : null,
      h("div", { class: "rs" }, h("span", { class: "ab-k" }, "Fix "), d.fix || "(no description)"),
      h("div", { class: "rs dim" }, [d.author ? `by ${d.author}` : "", d.project ? `in ${d.project}` : "",
        d.proven_by && d.proven_by.command ? `proved by \`${d.proven_by.command}\`` : "",
        d.uses ? `cured ${d.cures || 0} of ${d.uses}` : "", d.seconds_to_fix ? `took ${mins(d.seconds_to_fix)}` : ""].filter(Boolean).join(" · "))), actions); };
    const remove = (d) => O.confirmBox("Delete this fix?", "It's removed from MongoDB Atlas, so nobody's bots are offered it any more. This can't be undone.", "Delete",
      () => O.api("DELETE", `antibodies/${d.id}`).then(() => { O.toast("Fix deleted", 1500); again(); }, (e) => { again(); throw e; }), true);
    if (a.pending.length) {
      body.append(h("div", { class: "section" }, `Waiting for you (${a.pending.length})`),
        h("p", { class: "small-note" }, "Fixes your bots proved that aren't shared yet. To share one, click Share on its line in that bot's chat. Your team only gets the ones you share."),
        h("div", { class: "routines" }, a.pending.map((d) =>
          doc(d, h("button", { type: "button", class: "btn quiet", style: "flex:none", onclick: () => remove(d) }, "Delete")))));
    }
    if (!empty) {
      const list = h("div", { class: "routines" });
      const paint = (q = "") => {
        const w = q.trim().toLowerCase();
        const items = a.approved.filter((d) => !w || `${d.error_text} ${d.fix} ${d.author} ${d.project}`.toLowerCase().includes(w));
        list.replaceChildren(...(items.length ? items.map((d) => doc(d, h("button", { type: "button", class: "btn quiet", style: "flex:none", onclick: () => remove(d) }, "Delete")))
          : [h("p", { class: "empty-note" }, a.approved.length ? "No fix matches that." : "None shared yet. Click Share on a fix's line in the bot's chat.")]));
      };
      paint();
      body.append(h("div", { class: "section" }, `Your team's fixes (${a.approved.length})`), ...[a.approved.length > 5
        ? h("input", { type: "search", class: "text-input", placeholder: "Filter by error, fix, person or project", style: "margin-bottom:8px", oninput: (e) => paint(e.target.value) })
        : null, list].filter(Boolean));
    }

    // a fix you already know works, typed in by hand
    const fType = h("input", { class: "text-input", placeholder: "AttributeError", maxlength: "80", spellcheck: "false" });
    const fMsg = h("textarea", { class: "instr", rows: "3", placeholder: "module 'collections' has no attribute 'Mapping'", maxlength: "4000", spellcheck: "false" });
    const fFix = h("textarea", { class: "instr", rows: "3", placeholder: "Import Mapping from collections.abc instead of collections; it moved there in Python 3.10.", maxlength: "500" });
    const fCmd = h("input", { class: "text-input", placeholder: "pytest -q", maxlength: "300", spellcheck: "false" });
    const fProj = h("input", { class: "text-input", placeholder: "invoices", maxlength: "80" });
    for (const t of [fMsg, fFix]) t.style.width = "100%";
    const addErr = h("div", { class: "err hidden" });
    const addBtn = h("button", { type: "submit", class: "btn primary" }, "Save fix");
    body.append(h("div", { class: "section" }, "Add a fix by hand"),
      h("p", { class: "small-note" }, "For an error you've already fixed yourself. It's shared in memory straight away."),
      h("form", { class: "stack", autocomplete: "off", onsubmit: async (e) => {
        e.preventDefault(); addBtn.disabled = true; addBtn.textContent = "Saving…"; addErr.classList.add("hidden");
        try {
          await POST("antibodies/add", { error_type: fType.value, error_message: fMsg.value, fix: fFix.value, command: fCmd.value, project: fProj.value });
          O.toast("Fix saved and shared in memory", 2000); again();
        } catch (ex) { addErr.textContent = ex.message; addErr.classList.remove("hidden"); addBtn.disabled = false; addBtn.textContent = "Save fix"; }
      } },
        O.field("Error type", fType, "Optional, like AttributeError or ModuleNotFoundError."),
        O.field("Error message", fMsg),
        O.field("Fix", fFix, "What change made the error go away, in a sentence or two."),
        h("div", { class: "row-inline" }, h("div", { class: "grow" }, O.field("Command that proves it", fCmd, "Optional. It passes once the fix is in.")),
          h("div", { class: "grow" }, O.field("Project", fProj, "Optional."))),
        addErr, h("div", { class: "row-inline", style: "justify-content:flex-end" }, addBtn)),
      testGuide(true));

    function step(n, t, d) {
      return h("div", { class: "li" }, h("span", { class: "n-badge" }, String(n)), h("div", { class: "grow" }, t, d ? h("div", { class: "small" }, d) : null));
    }
    // Always at the end of the page: a known error a bot can break and fix on purpose, to check the whole loop.
    function testGuide(connected) {
      const prompt = "Create broken.py containing these two lines:\nimport collections\nprint(collections.Mapping)\nRun `python3 broken.py` and show me the error. Fix broken.py, then run exactly `python3 broken.py` again to prove it passes. Finally, delete broken.py.";
      const steps = [
        ["Copy the test prompt", h("div", {}, h("pre", { class: "prompt-box", style: "margin-top:6px" }, prompt),
          h("button", { type: "button", class: "btn", style: "margin-top:8px", onclick: () => O.copyText(prompt, "Prompt copied") }, icon("copy", 15), "Copy prompt"))],
        ["Send it to any bot", "Paste it into a bot's message box and press Enter. If it asks to run python3 broken.py, click Allow."],
        ["Click Share in the chat", s.antibody_auto_share
          ? "Share fixes automatically is on, so there's no button: when it's done, the chat says “Shared a fix in memory automatically.”"
          : "When it's done, the chat says “A fix is ready to share in memory.” Click Share next to it."],
        ["Check it here", "Come back to this page. The fix is listed under Your team's fixes."],
        ["Check your team gets it", "Send the same prompt to a different bot. The chat says the error matches a fix proven by you, and the bot tries your fix first. On this page, the fix then shows “cured 1 of 1”."],
        ["Clean up", "Click Delete on the fix if you don't want to keep it."],
      ];
      if (!connected) steps.unshift(["Connect MongoDB Atlas", "Click Connect in Account above. The test needs a connection to save and share the fix."]);
      return h("div", { dataset: { key: "test" } }, h("div", { class: "section" }, "Test it, step by step"),
        h("p", { class: "small-note" }, "Have a bot break something on purpose and fix it, to check that fixes are saved, shared and reused."),
        h("div", { class: "list" }, steps.map(([t, d], i) => step(i + 1, t, d))),
        h("p", { class: "small-note", style: "margin-top:8px" }, "No Share button after step 3? Check the top of this page says MongoDB Atlas is connected, and that the bot ran exactly python3 broken.py both times. Piping it through tail or head hides the failure. If your team already has this fix, there's nothing new to share: the bot uses the fix straight away, as in step 5. Delete it first to test from the start."));
    }
    function howItWorks() {
      return h("div", {}, h("div", { class: "section" }, "How it works"),
        h("div", { class: "list" },
          step(1, "A bot fixes an error", "A command fails, the bot changes something, and the same command then passes."),
          step(2, "You share the fix", "It waits here until you click Share. Then it's stored in your team's shared brain on MongoDB Atlas, the one place every teammate's oomi learns from."),
          step(3, "Your team gets it first", "Any teammate's bot that hits a matching error, even worded differently, finds your fix in the shared brain and tries it first.")));
    }
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
    // Who holds a memory (Settings only): "Every bot" for the shared ones, otherwise the bot: its ghost, name and id.
    const ownerLabel = (id, name) => {
      if (!id) return [icon("layers", 12), "Every bot"];
      const b = O.bot(id);
      return [b ? O.avatar(0, "tag-av", b.color) : null, name, h("span", { class: "tag-id" }, O.shortId(id))];
    };
    const ownerTag = (f) => botId ? null : h("span", { class: `tag owner${f.owner ? "" : " shared"}`, title: f.owner ? `Only ${f.owner_name} (${f.owner}) sees this` : "Every bot sees this" }, ...ownerLabel(f.owner, f.owner_name));
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
        return h("button", { type: "button", class: `chip${view === id ? " solid" : ""}`, onclick: () => { S.memOwner = id; again(); } }, ...label, h("span", {}, String(n)));
      };
      body.append(h("div", { class: "apps-chips", style: "margin-bottom:10px" }, chip("", ["All"]), chip("shared", ownerLabel("", "")),
        ...owners.map(([id, name]) => chip(id, ownerLabel(id, name)))));
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
        now && now.cost ? `${O.money(now.cost)} today` : "", now && now.context_tokens ? `${O.tok(now.context_tokens)} tokens of context now` : ""].filter(Boolean).join(", ");
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
    const keyText = { ok: "API key accepted", rejected: "API key rejected. Replace it in Providers.", missing: "No API key yet", unused: "API key not used yet" }[s.key.status] || s.key.status;
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
          b.cost ? h("span", { class: "small-note" }, `${O.money(b.cost)} today`) : h("span"),
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
    let trust = "project";
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
