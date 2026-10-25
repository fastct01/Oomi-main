// oomi — one conversation on screen: header, thread, approval cards and composer. The workspace makes one of
// these per pane; a pane shows one bot at a time (its tabs switch which).
(() => {
  "use strict";

  const O = window.Oomi;
  const { h, icon, avatar, $, S, bot, POST, GET } = O;

  const KIND = {
    run_shell: ["Command", "Ran a command", "Ran N commands"],
    read_file: ["Read", "Read N", "Read N files"],
    write_file: ["Write", "Wrote N", "Wrote N files"],
    edit_file: ["Edit", "Edited N", "Edited N files"],
    list_dir: ["Folder", "Looked in a folder", "Looked in N folders"],
    grep: ["Search", "Searched the code", "Searched the code N times"],
    web_search: ["Web", "Searched the web", "Searched the web N times"],
    web_fetch: ["Page", "Read a web page", "Read N web pages"],
    remember: ["Memory", "Saved a memory", "Saved N memories"],
    recall: ["Memory", "Checked its memory", "Checked its memory N times"],
    forget: ["Memory", "Forgot a memory", "Forgot N memories"],
    load_skill: ["Skill", "Used a skill", "Used N skills"],
    find_skill: ["Skill", "Looked for a skill", "Looked for skills N times"],
    propose_skill: ["Skill", "Proposed a skill", "Proposed N skills"],
    skill_feedback: ["Skill", "Noted how a skill went", "Noted how skills went"],
    search_history: ["History", "Searched past conversations", "Searched past conversations N times"],
  };
  // Slash commands typed in the composer: [name, argument hint, what it does]. They're handled here without a
  // model call, except /init and /review, which send a prompt. Any other /name goes to the bot as before
  // (a quick command from config.json, or just a message).
  const SLASH = [
    ["help", "", "Every slash command"],
    ["clear", "", "Start a fresh conversation (also /new, /reset)"],
    ["stop", "", "Stop what it's doing"],
    ["plan", "[task]", "Look around without changing anything, then propose steps"],
    ["go", "[changes]", "Approve the plan waiting above"],
    ["compact", "", "Summarize older turns now to free up room"],
    ["changes", "", "Files it changed, with diffs"],
    ["undo", "[all]", "Undo the last change, or everything"],
    ["model", "[name]", "Switch this bot's model"],
    ["effort", "[level]", "low, medium, high, xhigh or max"],
    ["provider", "[name]", "Switch this bot's provider"],
    ["trust", "[level]", "read-only, project or full"],
    ["budget", "[dollars]", "This bot's spending limit (0: the app default)"],
    ["cost", "", "What this bot has spent today"],
    ["status", "", "Health, spending and usage"],
    ["memory", "", "What oomi remembers"],
    ["remember", "<fact>", "Teach it something"],
    ["skills", "", "Saved skills"],
    ["taint", "", "Why commands are offline"],
    ["untaint", "", "Turn the network back on for commands"],
    ["settings", "", "This bot's settings"],
    ["init", "", "Write a CLAUDE.md describing this folder"],
    ["review", "", "Review the uncommitted changes in this folder"],
  ];
  const SLASH_ALIAS = { new: "clear", reset: "clear", config: "settings", usage: "cost" };
  const SLASH_PROMPTS = {
    init: "Look through this folder and write a CLAUDE.md at its top level: what the project is, how to build, run and test it, how it's laid out, and the conventions worth knowing. Keep it short. If there's one already, improve it rather than replace it.",
    review: "Review the uncommitted changes in this folder (git diff, including new files). List bugs, risks and anything unclear, most important first. Don't change any files.",
  };

  // A connected app's tool is "<app>__<tool>"; show the app's name
  function appLabel(name) {
    const i = name.indexOf("__");
    if (i < 0) return name;
    const id = name.slice(0, i).replace(/_/g, "-");
    return (S.appNames && S.appNames[id]) || id.replace(/(^|-)(\w)/g, (_, d, c) => (d ? " " : "") + c.toUpperCase());
  }
  function summarize(tools) {
    const counts = new Map();
    for (const t of tools) counts.set(t.name, (counts.get(t.name) || []).concat(t));
    const parts = [];
    for (const [name, ts] of counts) {
      const k = KIND[name];
      if (!k) { const who = appLabel(name); parts.push(ts.length === 1 ? `Used ${who}` : `Used ${who} ${ts.length} times`); continue; }
      if (ts.length === 1 && ["read_file", "write_file", "edit_file"].includes(name)) parts.push(k[1].replace("N", O.basename(ts[0].summary.split(" (")[0]) || "a file"));
      else parts.push(ts.length === 1 ? k[1] : k[2].replace("N", ts.length));
    }
    let s = parts.map((p, i) => (i ? p.charAt(0).toLowerCase() + p.slice(1) : p)).join(", ");
    const bad = tools.filter((t) => t.state === "error").length;
    if (bad) s += ` (${bad} failed)`;
    return s;
  }
  O.summarize = summarize;

  function askTitle(it) {
    if (it.tool === "confirm") return it.summary;
    return {
      run_shell: "Run this command?", write_file: "Write this file?", edit_file: "Edit this file?",
      remember: "Save this to memory?", forget: "Forget this memory?", propose_skill: "Save this skill?",
      read_file: "Read this file?", list_dir: "Look in this folder?", grep: "Search here?",
    }[it.tool] || `Allow ${it.tool}?`;
  }
  O.askTitle = askTitle;
  const ANSWERED = { allowed: "Allowed", always: "Always allowed", "always for this bot": "Always allowed for this bot",
    declined: "Declined", stopped: "Stopped before answering", "timed out": "Nobody answered" };

  class ChatView {
    // mode: "simple" (one pane, name in the middle), "advanced" (tabs in the header), "single" (a window of its own)
    constructor(gid, mode) {
      this.gid = gid;
      this.mode = mode;
      this.bot = undefined; // nothing shown yet (null: an empty pane)
      this.rendered = new Map(); this.order = [];
      this.lastTs = 0; this.lastKind = null; this.live = null; this.typingWhat = "";
      this.openRows = new Set(); this.closedRows = new Set(); this.openOutputs = new Set();
      this.stick = true; this.attachments = []; this.reply = null; this.plan = false; this.planEdit = false; this.hideSlash();
      this.loading = null; this.lastTurn = null; this.savedScroll = null;
      this.build();
    }

    // ---------------------------------------------------------------------------------------- DOM
    build() {
      const q = (cls) => this.el.querySelector(cls);
      this.el = h("div", { class: "chat" },
        h("header", { class: "chat-head" },
          h("div", { class: "head-left" }),
          h("button", { class: "name-pill hidden", type: "button", title: "Bot menu" }),
          h("div", { class: "head-right" })),
        h("div", { class: "scroller" }, h("div", { class: "thread", "aria-live": "polite" })),
        h("button", { class: "jump hidden", type: "button" }, "Jump to latest"),
        h("div", { class: "composer-wrap" },
          h("div", { class: "composer-extras" }),
          h("form", { class: "composer", autocomplete: "off" },
            h("button", { class: "icon-btn attach", type: "button", title: "Attach files or plan first", "aria-label": "Add" }, icon("plus", 20)),
            h("textarea", { rows: "1", placeholder: "Message oomi", "aria-label": "Message" }),
            h("button", { class: "icon-btn mic", type: "button", title: "Dictate", "aria-label": "Dictate" }, icon("mic", 19)),
            h("button", { class: "send", type: "submit", title: "Send (Enter)", "aria-label": "Send", disabled: true }, icon("up", 18, 2.2)),
            h("button", { class: "send stop hidden", type: "button", title: "Stop (Esc)", "aria-label": "Stop" }, icon("stop", 16))),
          h("div", { class: "hint" })),
        h("div", { class: "dropzone hidden" }, "Drop files to attach"),
        h("div", { class: "empty-pane hidden" }));
      this.head = q(".chat-head"); this.headLeft = q(".head-left"); this.pill = q(".name-pill"); this.headRight = q(".head-right");
      this.scroller = q(".scroller"); this.thread = q(".thread"); this.jump = q(".jump");
      this.extras = q(".composer-extras"); this.form = q(".composer"); this.input = q("textarea"); this.hint = q(".hint");
      this.sendBtn = q(".send:not(.stop)"); this.stopBtn = q(".send.stop"); this.micBtn = q(".mic");
      this.dropzone = q(".dropzone"); this.emptyEl = q(".empty-pane"); this.composerWrap = q(".composer-wrap");
      this.typing = h("div", { class: "typing hidden" }, h("div", { class: "dots" }, h("i"), h("i"), h("i")), h("div", { class: "what" }));
      this.thread.append(this.typing);
      if (!S.native.dictate && !(window.SpeechRecognition || window.webkitSpeechRecognition)) this.micBtn.classList.add("hidden");

      this.pill.addEventListener("click", (e) => this.bot && O.botMenu(e.currentTarget, this.bot, this));
      q(".attach").addEventListener("click", (e) => this.plusMenu(e.currentTarget));
      this.micBtn.addEventListener("click", () => this.dictate());
      this.stopBtn.addEventListener("click", () => this.stop());
      this.form.addEventListener("submit", (e) => { e.preventDefault(); this.send(); });
      this.jump.addEventListener("click", () => { this.stick = true; this.scrollDown(true); });
      this.input.addEventListener("input", () => { this.autosize(); this.paintSlash(); if (this.bot) O.store("draft." + this.bot, this.input.value); });
      this.input.addEventListener("keydown", (e) => {
        if (this.slashKey(e)) return;
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.send(); }
      });
      this.input.addEventListener("blur", () => setTimeout(() => this.hideSlash(), 120));
      this.slashEl = h("div", { class: "slash-menu hidden", role: "listbox", "aria-label": "Commands", onmousedown: (e) => e.preventDefault() });
      this.composerWrap.prepend(this.slashEl);
      this.input.addEventListener("paste", (e) => {
        const files = [...(e.clipboardData ? e.clipboardData.files : [])];
        if (files.length) { e.preventDefault(); this.addFiles(files); }
      });
      this.scroller.addEventListener("scroll", () => {
        const sc = this.scroller;
        this.stick = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80;
        if (this.stick) this.jump.classList.add("hidden");
      });
      this.thread.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-copy-code]");
        if (btn) { const pre = btn.closest(".code").querySelector("pre"); O.copyText(pre.textContent); }
      });
      // files dropped on the conversation become attachments (bots dragged here are the workspace's business)
      let depth = 0;
      const files = (e) => !S.drag && e.dataTransfer && [...e.dataTransfer.types].includes("Files");
      this.el.addEventListener("dragenter", (e) => { if (files(e) && this.bot) { depth++; this.dropzone.classList.remove("hidden"); e.preventDefault(); } });
      this.el.addEventListener("dragover", (e) => { if (files(e) && this.bot) e.preventDefault(); });
      this.el.addEventListener("dragleave", (e) => { if (files(e)) { depth = Math.max(0, depth - 1); if (!depth) this.dropzone.classList.add("hidden"); } });
      this.el.addEventListener("drop", (e) => {
        if (!files(e)) return;
        e.preventDefault(); depth = 0; this.dropzone.classList.add("hidden");
        if (this.bot && e.dataTransfer.files.length) this.addFiles([...e.dataTransfer.files]);
      });
    }
    setMode(mode) { if (mode !== this.mode) { this.mode = mode; this.renderHead(); } }
    destroy() {
      if (this.live && this.live.raf) clearTimeout(this.live.raf);
      if (this.recog) this.recog.stop();
      this.dropThumbs(this.attachments);
      for (const r of this.rendered.values()) if (r.el._gate) r.el._gate.stop();
      this.el.remove();
    }
    dropThumbs(list) { for (const a of list || []) if (a.thumb) URL.revokeObjectURL(a.thumb); }
    focusInput() { if (this.bot && !this.input.disabled) this.input.focus({ preventScroll: true }); }
    // a moved pane loses its scroll position; keep it
    saveScroll() { this.savedScroll = this.stick ? null : this.scroller.scrollTop; }
    restoreScroll() {
      if (this.savedScroll === null || this.savedScroll === undefined) this.scrollDown(true);
      else this.scroller.scrollTop = this.savedScroll;
      this.savedScroll = null;
    }

    // ---------------------------------------------------------------------------------------- opening
    async open(id, focusItem) {
      const changed = this.bot !== id;
      if (changed && this.bot) O.store("draft." + this.bot, this.input.value);
      this.bot = id && bot(id) ? id : null;
      this.el.classList.toggle("is-empty", !this.bot);
      this.emptyEl.classList.toggle("hidden", !!this.bot);
      if (!this.bot) {
        this.thread.replaceChildren(this.typing);
        this.rendered.clear(); this.order = []; this.live = null;
        this.renderHead(); this.renderEmpty();
        return;
      }
      if (changed) {
        this.dropThumbs(this.attachments);
        this.attachments = []; this.reply = null; this.plan = false; this.planEdit = false; this.renderExtras();
        this.input.value = O.store("draft." + id) || ""; this.autosize();
        this.openRows.clear(); this.closedRows.clear(); this.openOutputs.clear();
      }
      this.renderHead();
      this.renderComposerState();
      if (changed || focusItem) await this.loadThread(focusItem);
    }

    renderEmpty() {
      const shown = new Set(O.ws ? O.ws.visibleBots() : []); // bots in background tabs can move here
      const others = S.bots.filter((b) => !shown.has(b.id)).slice(0, 8);
      this.emptyEl.replaceChildren(h("div", { class: "empty-inner" },
        h("div", { class: "ghost" }, O.ghostImg()),
        h("h3", {}, S.bots.length ? "Pick a bot for this pane" : "No bots yet"),
        h("p", {}, S.bots.length ? "Or drag one here from the sidebar." : "Make one to get started."),
        h("div", { class: "pick-list" }, others.map((b) => h("button", { type: "button", class: "pick", onclick: () => O.ws.openIn(this.gid, b.id) },
          avatar(b.avatar, "", b.color), h("span", { class: "nm" }, b.name), O.mark(b)))),
        h("div", { class: "row-inline", style: "justify-content:center;margin-top:12px" },
          h("button", { type: "button", class: "btn", onclick: () => O.newBot((st) => O.ws.openIn(this.gid, st.id)) }, icon("plus", 16), "New bot"),
          O.ws && O.ws.paneCount() > 1 ? h("button", { type: "button", class: "btn quiet", onclick: () => O.ws.closePane(this.gid) }, "Close pane") : null)));
    }

    async loadThread(focusItem) {
      const id = this.bot;
      const loading = { bot: id, buffer: [] };
      this.loading = loading; // events for this bot that arrive meanwhile are held, then replayed if newer
      let data;
      try { data = await GET(`bots/${id}/timeline`); }
      catch (e) { if (this.loading === loading) this.loading = null; return O.fail(e); }
      if (this.loading === loading) this.loading = null;
      if (id !== this.bot) return;
      O.updateBot(data.state, false, true);
      for (const r of this.rendered.values()) if (r.el._gate) r.el._gate.stop();
      this.thread.replaceChildren(this.typing);
      this.rendered.clear(); this.order = []; this.lastTs = 0; this.lastKind = null; this.live = null;
      const items = data.items;
      if (!items.some((i) => i.type === "user" || i.type === "assistant")) this.thread.append(this.helloEl());
      for (const it of items) this.appendItem(it);
      const live = data.live || {};
      if (live.text_id && live.text && !this.rendered.has(live.text_id)) this.startLive(live.text_id, live.text);
      this.typingWhat = live.thinking || "";
      for (const ev of loading.buffer) if (ev.seq > data.seq) this.handle(ev);
      this.renderTyping();
      this.refreshPlanActions();
      this.stick = true;
      if (focusItem && this.rendered.has(focusItem)) {
        const el = this.rendered.get(focusItem).el;
        el.scrollIntoView({ block: "center" });
        el.animate([{ opacity: 0.3 }, { opacity: 1 }], { duration: 900, iterations: 2 });
        this.stick = false;
      } else this.scrollDown(true);
    }

    helloEl() {
      const b = bot(this.bot);
      const ideas = b.private_folder
        ? ["Plan my week", "Draft an email", "Explain something to me"]
        : ["What's in this folder?", "Find and fix a bug", "Tidy up the README"];
      return h("div", { class: "hello" },
        h("div", { class: "ghost" }, O.ghostImg(b.color)),
        h("h2", {}, `What should ${b.name} work on?`),
        h("p", {}, b.private_folder ? `It has its own folder at ${O.tildify(b.folder)}. Drop files here or just ask.` : `It works in ${O.tildify(b.folder)}.`),
        h("div", { class: "ideas" }, ideas.map((t) => h("button", { type: "button", class: "chip", onclick: () => { this.input.value = t; this.autosize(); this.input.focus(); } }, t))));
    }

    // ---------------------------------------------------------------------------------------- header
    renderHead() {
      const b = bot(this.bot);
      const adv = this.mode === "advanced";
      this.head.classList.toggle("adv", adv);
      this.headLeft.replaceChildren();
      this.headRight.replaceChildren();
      this.pill.classList.toggle("hidden", adv || !b);
      const chips = [];
      if (b) {
        chips.push(h("button", { type: "button", class: "chip trust-chip", title: "How much this bot may do", onclick: (e) => this.trustMenu(e.currentTarget) },
          icon("shield", 14), O.TRUST[b.trust] ? O.TRUST[b.trust].label : b.trust));
        if (b.tainted) chips.push(h("button", { type: "button", class: "chip", title: "Commands run offline", onclick: (e) => this.taintMenu(e.currentTarget) }, icon("wifi", 14), "Offline"));
      }
      if (adv) {
        this.headLeft.append(O.ws.tabStrip(this.gid));
        this.headRight.append(...chips);
      } else {
        this.headLeft.append(...chips);
        if (b) this.pill.replaceChildren(avatar(b.avatar, "", b.color), h("span", { class: "label" }, b.name), icon("down", 16));
      }
      if (b) {
        const changes = h("button", { type: "button", class: `icon-btn changes-btn${S.drawer === "changes" && O.ws.focusedBot() === b.id ? " on" : ""}`, title: "Changes", "aria-label": "Changes",
          onclick: () => { O.ws.focus(this.gid); O.toggleDrawer("changes"); } }, icon("changes"),
        b.changes ? h("span", { class: "badge" }, String(b.changes)) : null);
        this.headRight.append(changes, h("button", { type: "button", class: "icon-btn more-btn", title: "More", "aria-label": "More", onclick: (e) => O.botMenu(e.currentTarget, b.id, this) }, icon("more")));
      }
      if (this.mode === "single" && S.native.windows) {
        this.headRight.append(h("button", { type: "button", class: `icon-btn${O.floating ? " on" : ""}`, title: "Keep on top of other windows", "aria-label": "Keep on top",
          onclick: async (e) => { O.floating = !O.floating; e.currentTarget.classList.toggle("on", O.floating); await POST("native", { action: "float", name: O.WIN.name, on: O.floating }).catch(O.fail); } }, icon("pin")));
      }
      this.composerWrap.classList.toggle("hidden", !b);
    }

    trustMenu(anchor) {
      const b = bot(this.bot);
      if (!b) return;
      O.openMenu(anchor, Object.entries(O.TRUST).map(([k, v]) => ({
        label: v.label, desc: v.desc, on: b.trust === k,
        run: async () => {
          if (k === "full" && b.trust !== "full") {
            O.confirmBox("Turn off the sandbox?", "With full trust, commands run with your full access to files and the network. Risky ones still ask first.", "Use full trust", () => O.setBot({ trust: k }, b.id));
          } else await O.setBot({ trust: k }, b.id).catch(O.fail);
        },
      })), { align: "left" });
    }
    taintMenu(anchor) {
      const b = bot(this.bot);
      const src = (b.taint_sources || []).slice(-4);
      const node = h("div", { style: "padding:8px 10px 6px;font-size:12.5px;color:var(--fg-2);max-width:300px" },
        "Outside content came into this conversation" + (src.length ? ` (${src.join(", ")})` : "") + ", so commands run offline unless you approve network use.");
      O.openMenu(anchor, [{ node }, "-", { label: "Turn network back on", icon: "globe", run: async () => { try { O.updateBot(await POST(`bots/${b.id}/untaint`)); O.toast("Network allowed again"); } catch (e) { O.fail(e); } } }]);
    }

    // the bot's state changed (status, name, trust, …)
    botChanged(before, st) {
      this.renderHead();
      this.renderComposerState();
      this.refreshPlanActions();
      if (before && before.status !== "idle" && st.status === "idle") this.typingWhat = "";
      this.renderTyping();
    }

    // ---------------------------------------------------------------------------------------- the thread
    needsSep(ts) { return !this.lastTs || ts - this.lastTs > 3600 || O.dayDiff(new Date(ts * 1000)) !== O.dayDiff(new Date(this.lastTs * 1000)); }
    appendItem(it) {
      const hello = this.thread.querySelector(".hello");
      if (hello && (it.type === "user" || it.type === "assistant")) hello.remove();
      if (it.ts && this.needsSep(it.ts) && it.type !== "divider") this.thread.insertBefore(h("div", { class: "sep" }, O.sepTime(it.ts)), this.typing);
      const el = this.renderItem(it);
      const kind = it.type === "user" ? "user" : it.type === "assistant" ? "bot" : "row";
      if ((kind === "user" || kind === "bot") && this.lastKind !== kind) el.classList.add("first");
      this.thread.insertBefore(el, this.typing);
      this.rendered.set(it.id, { el, item: it });
      this.order.push(it.id);
      if (it.ts) this.lastTs = it.ts;
      this.lastKind = kind;
    }
    upsertItem(it) {
      const cur = this.rendered.get(it.id);
      if (this.live && this.live.id === it.id) { this.finishLive(it); return; }
      if (cur) {
        if (cur.el._gate) cur.el._gate.stop();
        const el = this.renderItem(it);
        if (cur.el.classList.contains("first")) el.classList.add("first");
        cur.el.replaceWith(el);
        this.rendered.set(it.id, { el, item: it });
      } else {
        this.appendItem(it);
        if (this.live) this.thread.insertBefore(this.live.wrap, this.typing); // the streaming reply stays last
      }
      this.refreshPlanActions();
      this.renderTyping();
      this.scrollDown();
    }
    renderItem(it) {
      switch (it.type) {
        case "user": return this.userEl(it);
        case "assistant": return this.botEl(it);
        case "activity": return this.activityEl(it);
        case "approval": return this.askEl(it);
        case "divider": return h("div", { class: "row divider", dataset: { id: it.id } }, h("span", {}, it.text));
        default: return this.noticeEl(it);
      }
    }
    msgActions(it, role) {
      const btn = (name, title, fn) => h("button", { type: "button", title, "aria-label": title, onclick: (e) => { e.stopPropagation(); fn(e.currentTarget); } }, icon(name, 16));
      const text = () => (it.shown !== undefined ? it.shown : it.text) || "";
      return h("div", { class: "actions" },
        btn("smile", "React", (a) => this.reactMenu(a, it)),
        btn("reply", "Reply", () => this.startReply(it)),
        btn("copy", "Copy", () => O.copyText(text())),
        btn("more", "More", (a) => O.openMenu(a, [
          { label: "Remember this…", icon: "memory", run: () => O.rememberBox(text()) },
          role === "user" ? { label: "Send again", icon: "up", run: () => { this.input.value = text(); this.autosize(); this.input.focus(); } } : null,
        ])));
    }
    reactMenu(anchor, it) {
      const node = h("div", { class: "emojis" }, ["👍", "❤️", "😂", "😮", "🙏", "👎"].map((e) => h("button", {
        type: "button", onclick: async () => { O.closeMenu(); try { await POST(`bots/${this.bot}/react`, { item: it.id, emoji: e }); } catch (er) { O.fail(er); } },
      }, e)));
      O.openMenu(anchor, [{ node }]);
    }
    stamp(it) { return it.ts ? h("span", { class: "at" }, O.fmtTime.format(new Date(it.ts * 1000))) : null; }
    userEl(it) {
      const shown = it.shown !== undefined ? it.shown : it.text;
      const atts = it.attachments || [];
      return h("div", { class: `msg user${it.reaction ? " has-reaction" : ""}`, dataset: { id: it.id } },
        atts.length ? h("div", { class: "atts" }, atts.map((a) => a.kind === "image"
          ? h("img", { src: `/api/attachment/${a.id}?t=${encodeURIComponent(O.TOKEN)}`, alt: a.name, title: a.name })
          : h("span", { class: "att-file" }, icon("file", 16), h("span", {}, a.name)))) : null,
        shown ? h("div", { class: "bubble" }, it.reply ? h("div", { class: "quote" }, it.reply.text) : null, shown) : null,
        it.while_working ? h("div", { class: "sub" }, "Sent while it was working") : null,
        this.stamp(it),
        it.reaction ? h("span", { class: "reaction" }, it.reaction) : null,
        this.msgActions(it, "user"));
    }
    botEl(it) {
      const bubble = h("div", { class: "bubble md", html: O.renderMd(it.text) });
      const cost = it.cost !== undefined ? h("span", { class: "cost" }, `${O.money(it.cost)}${it.secs ? `, ${Math.round(it.secs)}s` : ""}`) : null;
      return h("div", { class: `msg bot${it.reaction ? " has-reaction" : ""}`, dataset: { id: it.id } }, bubble,
        it.ts || cost ? h("div", { class: "meta-line" }, this.stamp(it), cost) : null,
        it.reaction ? h("span", { class: "reaction" }, it.reaction) : null,
        this.msgActions(it, "assistant"));
    }

    // streaming reply
    startLive(id, text = "") {
      const bubble = h("div", { class: "bubble md cursor" });
      const wrap = h("div", { class: `msg bot${this.lastKind !== "bot" ? " first" : ""}`, dataset: { id } }, bubble);
      const hello = this.thread.querySelector(".hello");
      if (hello) hello.remove();
      this.thread.insertBefore(wrap, this.typing);
      this.live = { id, text, wrap, bubble, raf: 0 };
      this.lastKind = "bot";
      this.paintLive();
      this.renderTyping();
    }
    paintLive() {
      const L = this.live;
      if (!L || L.raf) return;
      const wait = L.text.length > 20000 ? 500 : L.text.length > 4000 ? 120 : 0; // long replies: repaint less often
      L.raf = setTimeout(() => requestAnimationFrame(() => {
        L.raf = 0;
        if (this.live !== L) return;
        L.bubble.innerHTML = O.renderMd(L.text);
        this.scrollDown();
      }), wait);
    }
    finishLive(it) {
      const L = this.live;
      this.live = null;
      if (L.raf) clearTimeout(L.raf);
      const el = this.botEl(it);
      if (L.wrap.classList.contains("first")) el.classList.add("first");
      L.wrap.replaceWith(el);
      this.rendered.set(it.id, { el, item: it });
      this.order.push(it.id);
      this.lastTs = it.ts || this.lastTs;
      this.refreshPlanActions();
      this.renderTyping();
      this.scrollDown();
    }

    // tool activity rows
    refreshRows() { for (const r of [...this.rendered.values()]) if (r.item.type === "activity") this.upsertItem(r.item); }
    rowOpen(id) { return O.pref("tool_steps") === "detailed" ? !this.closedRows.has(id) : this.openRows.has(id); }
    toggleRow(id) {
      const open = this.rowOpen(id);
      if (O.pref("tool_steps") === "detailed") { if (open) this.closedRows.add(id); else this.closedRows.delete(id); }
      else if (open) this.openRows.delete(id); else this.openRows.add(id);
      this.upsertItem(this.rendered.get(id).item);
    }
    activityEl(it) {
      const tools = it.tools || [];
      const running = tools.find((t) => t.state === "running");
      const open = this.rowOpen(it.id);
      const label = running
        ? (running.name === "run_shell" ? `Running ${O.clip(running.summary, 70)}` : `${(KIND[running.name] || [appLabel(running.name)])[0]}: ${O.clip(running.summary, 70)}`)
        : summarize(tools);
      const row = h("div", { class: `row activity${open ? " open" : ""}${running ? " running" : ""}`, dataset: { id: it.id } },
        h("button", { type: "button", class: "row-line", onclick: () => this.toggleRow(it.id) },
          running ? h("span", { class: "spin" }) : icon(tools.some((t) => t.name.startsWith("web")) ? "globe" : "gear2", 14), label, h("span", { class: "chev" }, icon("right", 14))));
      if (open) {
        row.append(h("div", { class: "tools" }, tools.map((t, i) => {
          const key = it.id + ":" + i;
          const showOut = this.openOutputs.has(key);
          const tool = h("div", { class: `tool ${t.state}` },
            h("button", { type: "button", class: "tool-head", onclick: () => { if (showOut) this.openOutputs.delete(key); else this.openOutputs.add(key); this.upsertItem(this.rendered.get(it.id).item); } },
              t.state === "running" ? h("span", { class: "spin" }) : icon(t.state === "error" ? "x" : "check", 14, 2.2),
              h("span", { class: "kind" }, (KIND[t.name] || [appLabel(t.name)])[0]),
              h("span", { class: "sum", title: t.summary }, t.summary || ""),
              h("span", { class: "secs" }, t.secs >= 1 ? `${t.secs}s` : "")));
          if (showOut && t.out) tool.append(h("pre", {}, t.out));
          return tool;
        })));
      }
      return row;
    }
    noticeEl(it) {
      const ACT = {
        budget: ["Allow more", async () => { try { O.updateBot(await POST(`bots/${this.bot}/budget`, {})); O.toast("Budget raised"); } catch (e) { O.fail(e); } }],
        memory: ["Review", () => O.openSettings("memory")],
        status: ["See status", () => O.openSettings("status")],
        settings: ["Open settings", () => O.openSettings("providers")],
      };
      // older notices say "…ready to share with your team: review in Settings, Antibodies." and get the button too
      if (it.action === "antibody" || /ready to share with your team/.test(it.text || "")) {
        it = { ...it, text: it.text.replace(/:\s*review in Settings, Antibodies\.?$/, ".") };
        // a fix this bot proved: Share sends it to the team (deleting it is in Settings, Antibodies)
        const share = async (btn) => {
          btn.disabled = true;
          try {
            const r = await POST(`bots/${this.bot}/share`, { item: it.id });
            O.toast(r.shared ? "Shared with your team" : "Already shared or deleted in Settings, Antibodies", 2200);
          } catch (e) { O.fail(e); btn.disabled = false; }
        };
        return h("div", { class: "row notice info", dataset: { id: it.id } },
          h("div", { class: "row-line" }, icon("antibody", 14), h("span", {}, it.closed ? (it.shared ? "Shared with your team." : "This fix was already shared or deleted.") : it.text),
            it.closed ? null : h("button", { type: "button", class: "act-btn", onclick: (e) => share(e.currentTarget) }, "Share")));
      }
      const a = ACT[it.action];
      return h("div", { class: `row notice ${it.level || "info"}`, dataset: { id: it.id } },
        h("div", { class: "row-line" }, icon(it.level === "error" || it.level === "warn" ? "alert" : it.text.startsWith("Memory:") ? "memory" : "info", 14),
          h("span", {}, it.text), a ? h("button", { type: "button", class: "act-btn", onclick: a[1] }, a[0]) : null));
    }

    // approval cards
    askEl(it) {
      if (it.state !== "pending") {
        const verb = ANSWERED[it.state] || it.state;
        return h("div", { class: "row notice", dataset: { id: it.id } }, h("div", { class: "row-line" },
          icon(["declined", "stopped", "timed out"].includes(it.state) ? "x" : "check", 14, 2.2),
          h("span", {}, `${verb}: ${O.clip(it.summary, 90)}${it.note ? ` (“${O.clip(it.note, 80)}”)` : ""}`)));
      }
      const answer = async (choice, note = "") => {
        gate.busy(true);
        card.querySelectorAll("button").forEach((b) => (b.disabled = true));
        try { await POST(`bots/${this.bot}/approve`, { id: it.id, choice, note }); }
        catch (e) { O.fail(e); card.querySelectorAll("button").forEach((b) => (b.disabled = false)); gate.busy(false); }
      };
      const confirm = it.tool === "confirm";
      const noteRow = h("form", { class: "note hidden", onsubmit: (e) => { e.preventDefault(); const v = noteIn.value.trim(); if (v) answer("no", v); } });
      const noteIn = h("input", { class: "text-input", placeholder: "What should it do instead?", maxlength: "600" });
      noteRow.append(noteIn, h("button", { class: "btn", type: "submit" }, "Send"));
      const body = confirm ? it.reason : it.summary;
      const what = h("div", { class: "what", tabindex: "0" }, O.visible(body));
      const lines = body.split("\n").length;
      const big = (it.length || body.length) > 300 || lines > 8;
      const allow = h("button", { type: "button", class: "btn primary", onclick: () => answer("yes") }, confirm ? "Yes" : "Allow");
      const always = !confirm && !it.danger ? h("button", { type: "button", class: "btn", title: "Allow this exact action for the rest of this conversation", onclick: () => answer("always") }, "Always allow") : null;
      // only commands can be remembered: the same command, word for word, runs without asking in later conversations too
      const forBot = !confirm && !it.danger && it.tool === "run_shell"
        ? h("button", { type: "button", class: "btn", title: "Allow this exact command from now on, in every conversation with this bot. You can remove it in Bot settings.", onclick: () => answer("bot") }, "Always for this bot") : null;
      const gateNote = h("div", { class: "gate hidden" }, "Scroll to the end of it to allow.");
      const card = h("div", { class: `ask${it.danger ? " danger" : ""}`, dataset: { id: it.id } },
        h("h4", {}, it.danger ? `Careful. ${askTitle(it)}` : confirm ? O.clip(it.summary, 200) : askTitle(it)),
        big ? h("div", { class: "size" }, `${(it.length || body.length).toLocaleString()} characters, ${O.plural(lines, "1 line", "N lines")}`) : null,
        what,
        confirm ? null : h("div", { class: "why" }, `Why it's asking: ${it.reason}`),
        it.too_long ? h("div", { class: "err" }, "This is too long to show in full, so it can't be approved here.") : null,
        gateNote,
        h("div", { class: "btns" },
          it.too_long ? null : allow, it.too_long ? null : always, it.too_long ? null : forBot,
          h("button", { type: "button", class: "btn", onclick: () => answer("no") }, confirm ? "No" : "Don't allow"),
          confirm ? null : h("button", { type: "button", class: "btn quiet", onclick: () => { noteRow.classList.toggle("hidden"); noteIn.focus(); } }, "Tell it why")),
        noteRow);
      // Allow waits until the card is on screen and, if what it approves doesn't fit, scrolled to the end.
      const gate = O.gateAllow(what, [allow, always, forBot], gateNote);
      card._gate = gate;
      return card;
    }

    // the plan you can approve
    refreshPlanActions() {
      this.thread.querySelectorAll(".plan-actions").forEach((e) => e.remove());
      const b = bot(this.bot);
      if (!b || !b.plan_pending || b.status !== "idle") return;
      let last = null;
      for (let i = this.order.length - 1; i >= 0; i--) {
        const r = this.rendered.get(this.order[i]);
        if (!r) continue;
        if (r.item.type === "assistant") { last = r; break; }
        if (r.item.type === "user") break;
      }
      if (!last) return;
      last.el.append(h("div", { class: "plan-actions" },
        h("button", { type: "button", class: "btn primary", onclick: () => this.goPlan("") }, "Go ahead"),
        h("button", { type: "button", class: "btn", onclick: () => { this.planEdit = b.id; this.renderComposerState(); this.input.focus(); } }, "Change the plan")));
    }
    async goPlan(edits) { try { await POST(`bots/${this.bot}/go`, { edits }); } catch (e) { O.fail(e); } }

    renderTyping() {
      const t = this.typing;
      const b = bot(this.bot);
      const show = b && (b.status === "working" || b.status === "stopping") && !this.live && !this.thread.querySelector(".ask");
      t.classList.toggle("hidden", !show);
      if (show) {
        const what = (S.settings.show_thinking === false ? "" : this.typingWhat.trim()) || "";
        $(".what", t).textContent = b.status === "stopping" ? "Stopping…" : what ? O.clip(what.split("\n").filter(Boolean).pop(), 160) : b.step || "";
        if (t.parentNode !== this.thread || t.nextSibling) this.thread.append(t);
      }
    }
    scrollDown(force) {
      const sc = this.scroller;
      if (force || this.stick) { sc.scrollTop = sc.scrollHeight; this.jump.classList.add("hidden"); }
      else this.jump.classList.remove("hidden");
    }

    // ---------------------------------------------------------------------------------------- composer
    autosize() {
      const ta = this.input;
      ta.style.height = "auto";
      ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
      this.renderSendButton();
    }
    renderSendButton() {
      const b = bot(this.bot);
      const busy = b && b.status !== "idle";
      const has = this.input.value.trim() || this.attachments.some((a) => a.id);
      const uploading = this.attachments.some((a) => !a.id);
      this.sendBtn.classList.toggle("hidden", !!(busy && !has));
      this.stopBtn.classList.toggle("hidden", !(busy && !has));
      this.sendBtn.disabled = !has || uploading;
    }
    renderComposerState() {
      const b = bot(this.bot);
      if (!b) return;
      const busy = b.status !== "idle";
      this.input.placeholder = this.planEdit ? "What should change in the plan?" : busy ? `Add to what ${b.name} is doing…` : `Message ${b.name}`;
      const left = h("span", { class: "left" });
      if (b.status === "waiting") left.append("Waiting for your OK above");
      else if (b.status === "stopping") left.append("Stopping…");
      else if (busy) left.append(b.step ? `${b.step}. ` : "Working. ", h("b", {}, "Esc"), " stops it; anything you send now is added to the task.");
      else if (this.planEdit) left.append("Your changes go with the plan. ", h("b", {}, "Esc"), " cancels.");
      else if (this.plan) left.append("Plan first: it looks around without changing anything, then proposes steps.");
      else left.append(`${O.TRUST[b.trust] ? O.TRUST[b.trust].label : b.trust} in ${O.tildify(b.folder)}`);
      const lt = this.lastTurn;
      const right = h("span", {}, lt && lt.bot === b.id && Date.now() - lt.at < 15000
        ? `${O.money(lt.cost)}, ${Math.round(lt.seconds)}s`
        : b.cost ? `${O.money(b.cost)} today` : "");
      this.hint.replaceChildren(left, right);
      this.renderExtras();
      this.renderSendButton();
    }
    renderExtras() {
      const box = this.extras;
      box.replaceChildren();
      if (this.reply) box.append(h("div", { class: "reply-chip" }, icon("reply", 14), h("span", {}, "Replying to: " + O.clip(this.reply.text, 120)),
        h("button", { type: "button", class: "icon-btn", style: "width:24px;height:24px", title: "Cancel reply", onclick: () => { this.reply = null; this.renderExtras(); } }, icon("x", 14))));
      if (this.plan) box.append(h("button", { type: "button", class: "chip plan-on", title: "Turn off plan first", onclick: () => { this.plan = false; this.renderComposerState(); } }, icon("plan", 14), "Plan first", icon("x", 12)));
      if (this.planEdit) box.append(h("button", { type: "button", class: "chip plan-on", onclick: () => { this.planEdit = false; this.renderComposerState(); } }, icon("plan", 14), "Changing the plan", icon("x", 12)));
      for (const a of this.attachments) {
        box.append(h("span", { class: `pending-att${a.id ? "" : " busy"}`, title: a.name },
          a.thumb ? h("img", { src: a.thumb, alt: "" }) : icon(a.kind === "image" ? "img" : "file", 16),
          h("span", {}, a.name), a.id ? null : h("span", { class: "spin" }),
          h("button", { type: "button", title: "Remove", onclick: () => { this.dropThumbs([a]); this.attachments = this.attachments.filter((x) => x !== a); this.renderExtras(); this.renderSendButton(); } }, icon("x", 12))));
      }
    }
    startReply(it) {
      this.reply = { id: it.id, text: (it.shown !== undefined ? it.shown : it.text) || "", role: it.type };
      this.renderExtras();
      this.input.focus();
    }
    // ---------------------------------------------------------------------------------------- slash commands
    slashMatches() {
      const m = this.input.value.match(/^\/([a-z-]*)$/i); // the command name, before any argument
      if (!m || !this.bot) return [];
      const w = m[1].toLowerCase();
      return SLASH.filter(([name]) => name.startsWith(w));
    }
    paintSlash() {
      const items = this.slashMatches();
      if (!items.length) return this.hideSlash();
      this.slashSel = Math.min(this.slashSel || 0, items.length - 1);
      this.slashEl.replaceChildren(...items.map(([name, arg, desc], i) => h("button", { type: "button", role: "option", class: i === this.slashSel ? "on" : "",
        "aria-selected": String(i === this.slashSel), onclick: () => this.pickSlash(items[i]) },
        h("span", { class: "nm" }, "/" + name), arg ? h("span", { class: "arg" }, arg) : null, h("span", { class: "d" }, desc))));
      this.slashEl.classList.remove("hidden");
      const on = this.slashEl.querySelector(".on");
      if (on) on.scrollIntoView({ block: "nearest" });
    }
    hideSlash() { if (this.slashEl) { this.slashEl.classList.add("hidden"); this.slashSel = 0; } }
    // A command without an argument runs straight away; one that takes an argument waits for it.
    pickSlash([name, arg]) {
      this.input.value = "/" + name + (arg ? " " : "");
      this.hideSlash(); this.autosize(); this.input.focus();
      if (!arg) this.send();
    }
    slashKey(e) {
      if (!this.slashEl || this.slashEl.classList.contains("hidden") || e.isComposing) return false;
      const items = this.slashMatches();
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        this.slashSel = (this.slashSel + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
        this.paintSlash();
      } else if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        this.pickSlash(items[this.slashSel] || items[0]);
      } else if (e.key === "Escape") {
        this.hideSlash();
        e.stopPropagation(); // close the menu, don't stop the bot
      } else return false;
      e.preventDefault();
      return true;
    }
    // True when the command was handled here, a string to send to the bot instead, or false for an unknown /name.
    async slash(name, arg, b) {
      name = SLASH_ALIAS[name] || name;
      if (SLASH_PROMPTS[name]) return SLASH_PROMPTS[name];
      if (!SLASH.some(([n]) => n === name)) return false;
      const s = S.settings.models ? S.settings : (S.settings = await GET("settings"));
      const set = async (patch, done) => { await O.setBot(patch, b.id); O.toast(done, 1800); };
      const idle = () => b.status === "idle" || (O.toast(`Wait until ${b.name} finishes, or stop it first (Esc).`, 2600), false);
      switch (name) {
        case "help":
          O.modal((box, close) => box.append(h("h3", {}, "Slash commands"),
            h("div", { class: "list slash-help" }, SLASH.map(([n, a, d]) => h("div", { class: "li" }, h("span", { class: "nm" }, `/${n}${a ? " " + a : ""}`), h("div", { class: "grow small" }, d)))),
            h("p", { class: "small-note", style: "margin-top:10px" }, "Type / in the message box to pick one. Quick commands from your config.json work too."),
            h("div", { class: "foot" }, h("button", { type: "button", class: "btn primary", onclick: close }, "Done"))));
          break;
        case "clear":
          if (!idle()) break;
          O.updateBot(await POST(`bots/${b.id}/fresh`)); O.ws.reload(b.id); O.toast("Fresh conversation", 1500);
          break;
        case "stop": await this.stop(); break;
        case "plan":
          this.plan = true; this.renderComposerState();
          if (arg) return arg; // send the task with plan first
          break;
        case "go": await this.goPlan(arg); break;
        case "compact": {
          if (!idle()) break;
          O.toast("Summarizing older turns…", 2000);
          const r = await POST(`bots/${b.id}/compact`);
          O.toast(r.compacted ? "Older turns summarized" : "Nothing to summarize yet", 2000);
          break;
        }
        case "changes": O.openDrawer("changes"); break;
        case "undo": {
          if (!idle()) break;
          const undo = async (confirm) => {
            const r = await POST(`bots/${b.id}/undo`, { all: arg === "all", confirm });
            if (r.needs_confirm) return O.confirmBox("Restore the snapshot?", `This will ${r.needs_confirm}, removing files created since then, including any you made yourself.`, "Restore", () => undo(true).catch(O.fail));
            if (r.error) O.toast(`Stopped: ${r.error}`, 5000);
            else O.toast(r.done.length ? `Undid ${O.plural(r.done.length, "1 step", "N steps")}` : "Nothing to undo");
            O.refreshDrawer("changes", b.id);
          };
          await undo(false);
          break;
        }
        case "model": {
          if (!arg) { O.openDrawer("bot"); break; }
          const w = arg.toLowerCase();
          const m = w === "default" ? "" : s.models.find((x) => x === w) || s.models.find((x) => O.modelName(x).toLowerCase().includes(w) || x.includes(w));
          if (m === undefined) { O.toast(`No model called ${arg}. Try: ${s.models.map(O.modelName).join(", ")}, default.`, 4000); break; }
          await set({ model: m }, `${b.name} now uses ${m ? O.modelName(m) : `the app default (${O.modelName(s.model)})`}`);
          break;
        }
        case "effort": {
          const e = arg.toLowerCase();
          if (!arg) { O.openDrawer("bot"); break; }
          if (e !== "default" && !s.efforts.includes(e)) { O.toast(`Effort is one of: ${s.efforts.join(", ")}, default.`, 3000); break; }
          await set({ effort: e === "default" ? "" : e }, `Effort: ${e}`);
          break;
        }
        case "provider": {
          const ps = s.providers || [];
          if (!arg) { O.toast(`${b.name} uses ${(ps.find((p) => p.id === b.provider) || { name: "Anthropic" }).name}. Options: Anthropic${ps.map((p) => ", " + p.name).join("")}.`, 4000); break; }
          const w = arg.toLowerCase();
          const p = ["anthropic", "default"].includes(w) ? { id: "", name: "Anthropic" } : ps.find((x) => x.name.toLowerCase().startsWith(w));
          if (!p) { O.toast(`No provider called ${arg}. Add one in Settings, Providers.`, 3000); break; }
          await set({ provider: p.id }, `${b.name} now uses ${p.name}`);
          break;
        }
        case "trust": {
          const t = arg.toLowerCase();
          if (!arg) { O.toast(`Trust is ${O.TRUST[b.trust] ? O.TRUST[b.trust].label : b.trust}. Options: read-only, project, full.`, 3000); break; }
          if (!O.TRUST[t]) { O.toast("Trust is one of: read-only, project, full.", 3000); break; }
          if (t === "full" && b.trust !== "full") O.confirmBox("Turn off the sandbox?", "With full trust, commands run with your full access to files and the network. Risky ones still ask first.", "Use full trust", () => set({ trust: t }, "Trust: Full").catch(O.fail));
          else await set({ trust: t }, `Trust: ${O.TRUST[t].label}`);
          break;
        }
        case "budget": {
          const v = Number(arg.replace(/^\$/, ""));
          if (!arg || !Number.isFinite(v) || v < 0 || v > 1000) { O.toast("Give an amount in dollars, like /budget 5 (0 uses the app default).", 3000); break; }
          await set({ budget: v }, v ? `Spending limit: ${O.money(v)}` : "Spending limit: app default");
          break;
        }
        case "cost": O.toast(`${b.name} has spent ${O.money(b.cost || 0)} today${b.limit ? ` of its ${O.money(b.limit)} limit` : ""}.`, 3500); break;
        case "status": O.openSettings("status"); break;
        case "memory": O.openSettings("memory"); break;
        case "remember":
          if (!arg) { O.toast("Say what to remember, like /remember I use pnpm, never npm.", 3000); break; }
          await POST("memory/add", { text: arg }); O.toast("Remembered", 1500);
          break;
        case "skills": O.openSettings("skills"); break;
        case "taint":
          O.toast(b.tainted ? `Commands are offline because untrusted content came in: ${(b.taint_sources || []).join("; ") || "a web page or command"}. /untaint turns the network back on.` : "Commands have network access.", 5000);
          break;
        case "untaint": O.updateBot(await POST(`bots/${b.id}/untaint`)); O.toast("Network allowed again", 1500); break;
        case "settings": O.openDrawer("bot"); break;
      }
      return true;
    }
    async send() {
      const b = bot(this.bot);
      if (!b) return;
      const ta = this.input;
      let text = ta.value.trim();
      this.hideSlash();
      const cmd = !this.planEdit && !this.attachments.length && text.match(/^\/([a-z-]+)(?:\s+([\s\S]*))?$/i);
      if (cmd) {
        let r;
        try { r = await this.slash(cmd[1].toLowerCase(), (cmd[2] || "").trim(), b); } catch (e) { O.fail(e); r = true; }
        if (r === true) { ta.value = ""; this.autosize(); O.store("draft." + b.id, null); this.renderComposerState(); return; }
        if (typeof r === "string") text = r;
      }
      if (this.attachments.some((a) => !a.id)) return O.toast("Still uploading…");
      if (!text && !this.attachments.length) return;
      if (this.planEdit && this.planEdit !== b.id) this.planEdit = false;
      if (this.planEdit) {
        this.planEdit = false; ta.value = ""; this.autosize(); O.store("draft." + b.id, null);
        this.renderComposerState();
        return this.goPlan(text);
      }
      const body = { text, attachments: this.attachments.map((a) => a.id), plan: this.plan };
      if (this.reply) body.reply = this.reply;
      const saved = { text: ta.value, attachments: this.attachments, reply: this.reply, plan: this.plan };
      ta.value = ""; this.attachments = []; this.reply = null; this.plan = false;
      this.autosize(); this.renderExtras(); O.store("draft." + b.id, null);
      this.stick = true;
      try { await POST(`bots/${b.id}/send`, body); this.dropThumbs(saved.attachments); }
      catch (e) {
        ta.value = saved.text; this.attachments = saved.attachments; this.reply = saved.reply; this.plan = saved.plan;
        this.autosize(); this.renderExtras(); O.fail(e);
      }
      this.renderComposerState();
    }
    async stop() { if (this.bot) { try { await POST(`bots/${this.bot}/stop`); } catch (e) { O.fail(e); } } }
    // Esc in this pane: back out of whatever is half-done, or stop the bot. True if it did something.
    escape() {
      if (this.slashEl && !this.slashEl.classList.contains("hidden")) { this.hideSlash(); return true; }
      if (this.planEdit) { this.planEdit = false; this.renderComposerState(); return true; }
      if (this.reply) { this.reply = null; this.renderExtras(); return true; }
      const b = bot(this.bot);
      if (b && (b.status === "working" || b.status === "waiting")) { this.stop(); return true; }
      return false;
    }

    addFiles(files) {
      const target = this.bot;
      for (const f of files) {
        const kind = f.type.startsWith("image/") ? "image" : f.type === "application/pdf" ? "pdf" : "text";
        const a = { name: f.name || "pasted image", kind, thumb: kind === "image" ? URL.createObjectURL(f) : null, id: null };
        this.attachments.push(a);
        this.renderExtras(); this.renderSendButton();
        const reader = new FileReader();
        reader.onload = async () => {
          const data = String(reader.result).split(",")[1] || "";
          try {
            const meta = await POST("upload", { bot: target, name: a.name, type: f.type, data });
            a.id = meta.id; a.kind = meta.kind;
          } catch (e) {
            this.attachments = this.attachments.filter((x) => x !== a);
            O.fail(e);
          }
          this.renderExtras(); this.renderSendButton();
        };
        reader.onerror = () => { this.attachments = this.attachments.filter((x) => x !== a); this.renderExtras(); O.toast(`Couldn't read ${a.name}`); };
        reader.readAsDataURL(f);
      }
    }
    plusMenu(anchor) {
      O.openMenu(anchor, [
        { label: "Attach files", icon: "clip", desc: "Images, PDFs and text files", run: () => O.pickFiles((files) => this.addFiles(files)) },
        { label: "Plan first", icon: "plan", desc: "Look around, then propose steps to approve", on: this.plan, run: () => { this.plan = !this.plan; this.renderComposerState(); this.input.focus(); } },
      ], { up: true });
    }
    // dictation: macOS Dictation in the app, the Web Speech API in a browser that has it
    async dictate() {
      const ta = this.input;
      ta.focus();
      if (S.native.dictate) {
        try { await POST("native", { action: "dictate" }); } catch (e) { O.fail(e); }
        return;
      }
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) return O.toast("Dictation isn't available here.");
      if (this.recog) { this.recog.stop(); return; }
      const recog = (this.recog = new SR());
      recog.interimResults = false;
      recog.continuous = true;
      this.micBtn.classList.add("on");
      recog.onresult = (e) => {
        for (let i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) ta.value += (ta.value && !/\s$/.test(ta.value) ? " " : "") + e.results[i][0].transcript.trim();
        this.autosize();
      };
      recog.onend = () => { this.recog = null; this.micBtn.classList.remove("on"); };
      recog.onerror = () => { O.toast("Dictation stopped."); };
      recog.start();
    }

    // ---------------------------------------------------------------------------------------- live events
    handle(ev) {
      if (this.loading && ["item", "delta", "drop", "thinking"].includes(ev.type)) { this.loading.buffer.push(ev); return; }
      switch (ev.type) {
        case "delta":
          if (!this.live || this.live.id !== ev.id) this.startLive(ev.id, "");
          this.live.text += ev.text;
          this.paintLive();
          break;
        case "drop":
          if (this.live && this.live.id === ev.id) { this.live.wrap.remove(); this.live = null; this.renderTyping(); }
          break;
        case "thinking":
          this.typingWhat = (this.typingWhat + ev.text).slice(-600); this.renderTyping();
          break;
        case "item":
          this.upsertItem(ev.item);
          break;
        case "turn_end":
          this.lastTurn = { bot: ev.bot, cost: ev.cost, seconds: ev.seconds, at: Date.now() };
          this.renderComposerState();
          setTimeout(() => this.renderComposerState(), 15500);
          break;
        default:
          break;
      }
    }
  }
  O.ChatView = ChatView;

  // shared: a hidden file picker
  O.pickFiles = function pickFiles(onFiles) {
    const input = $("#file-input");
    input.onchange = (e) => { const files = [...e.target.files]; e.target.value = ""; if (files.length) onFiles(files); };
    input.click();
  };
  O.rememberBox = function rememberBox(text) {
    O.modal((box, close) => {
      const ta = h("textarea", { class: "mem-edit", style: "min-height:110px;font-family:var(--font);font-size:14px", maxlength: "1000" });
      ta.value = text.slice(0, 1000);
      const err = h("div", { class: "err hidden" });
      const save = h("button", { type: "button", class: "btn primary", onclick: async () => {
        save.disabled = true;
        try { await POST("memory/add", { text: ta.value }); close(); O.toast("Remembered"); }
        catch (e) { err.textContent = e.message; err.classList.remove("hidden"); save.disabled = false; }
      } }, "Remember");
      box.append(h("h3", {}, "Remember this"), h("p", { class: "lead" }, "One short fact works best. Every bot can use it."), ta, err,
        h("div", { class: "foot" }, h("button", { type: "button", class: "btn quiet", onclick: close }, "Cancel"), save));
    });
  };
})();
