// A small Markdown renderer for model output. Every character of input is HTML-escaped first; the only markup in
// the result is the fixed set of tags produced here, and links are limited to http(s) and mailto. Model output can
// never inject HTML or script.
(function (root) {
  "use strict";

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ESC[c]); }

  const UNESC = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" };
  function safeUrl(raw) {
    const url = raw.trim().replace(/&(amp|lt|gt|quot|#39);/g, (m) => UNESC[m]);
    if (!/^(https?:\/\/|mailto:)/i.test(url)) return null;
    return esc(url);
  }

  const LONG = 20000; // a paragraph longer than this is shown without inline formatting (keeps rendering fast)

  // Inline formatting on already-escaped text. Code spans are pulled out first so nothing inside them is formatted.
  function inline(text) {
    if (text.length > LONG) return esc(text).replace(/\n/g, "<br>");
    const slots = [];
    const keep = (html) => "\u0000" + (slots.push(html) - 1) + "\u0000";
    let s = esc(text);
    s = s.replace(/(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g, (_, __, code) => keep("<code>" + code.trim() + "</code>"));
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (m, alt, url) => {
      const u = safeUrl(url);
      return u ? keep(`<a href="${u}" target="_blank" rel="noopener noreferrer">${alt || "image"}</a>`) : m;
    });
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (m, label, url) => {
      const u = safeUrl(url);
      return u ? keep(`<a href="${u}" target="_blank" rel="noopener noreferrer">${label}</a>`) : m;
    });
    s = s.replace(/(^|[\s(])((?:https?:\/\/)[^\s<]+[^\s<.,:;"')\]!?])/g, (m, pre, url) => {
      const u = safeUrl(url);
      return u ? pre + keep(`<a href="${u}" target="_blank" rel="noopener noreferrer">${url}</a>`) : m;
    });
    s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__(?=\S)([\s\S]*?\S)__(?!\w)/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^_\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?![_\w])/g, "$1<em>$2</em>");
    s = s.replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>");
    s = s.replace(/\n/g, "<br>");
    // Slots can hold other slots (a code span inside a link); expand until none are left.
    for (let n = 0; n < 8 && s.includes("\u0000"); n++) s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => slots[+i]);
    return s.replace(/\u0000/g, "");
  }

  const reFence = /^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/;
  // Written so that no pattern can backtrack badly on long lines.
  const reHead = /^ {0,3}(#{1,6})(?:[ \t]+(.*))?$/;
  function headText(rest) {
    let t = (rest || "").trimEnd();
    const k = t.search(/#+$/);
    if (k >= 0 && (k === 0 || t[k - 1] === " " || t[k - 1] === "\t")) t = t.slice(0, k).trimEnd();
    return t.trim();
  }
  function isHr(line) {
    if (/^ {4}/.test(line)) return false;
    const t = line.replace(/[ \t]/g, "");
    return t.length >= 3 && (/^-+$/.test(t) || /^\*+$/.test(t) || /^_+$/.test(t));
  }
  const reQuote = /^ {0,3}> ?(.*)$/;
  const reList = /^( *)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  function isTableSep(line) {
    const t = line.replace(/[ \t]/g, "");
    return t.length <= 2000 && /^\|?:?-{2,}:?(\|:?-{2,}:?)*\|?$/.test(t);
  }
  const MAX_DEPTH = 12; // quotes and lists nested deeper than this are shown as plain text

  function splitRow(line) {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
  }

  function renderList(lines, start, depth) {
    // Collect consecutive list items (and their indented continuation lines) into a nested list.
    const items = [];
    let i = start;
    const base = reList.exec(lines[start]);
    const indent = base[1].length;
    const ordered = /\d/.test(base[2]);
    while (i < lines.length) {
      const line = lines[i];
      const m = reList.exec(line);
      if (m && m[1].length === indent && /\d/.test(m[2]) === ordered) {
        items.push({ text: [m[3]], sub: [] });
        i++;
        continue;
      }
      if (m && m[1].length > indent && items.length) {
        items[items.length - 1].sub.push(line);
        i++;
        continue;
      }
      if (line.trim() && items.length && /^\s+\S/.test(line) && !m) {
        const last = items[items.length - 1];
        (last.sub.length ? last.sub : last.text).push(line.trim());
        i++;
        continue;
      }
      break;
    }
    const tag = ordered ? "ol" : "ul";
    const first = ordered ? parseInt(base[2], 10) : 1;
    let html = `<${tag}${ordered && first !== 1 ? ` start="${first}"` : ""}>`;
    for (const it of items) {
      let body = inline(it.text.join("\n"));
      if (it.sub.length) {
        const dedent = Math.min(...it.sub.filter((l) => l.trim()).map((l) => l.match(/^ */)[0].length));
        body += blocks(it.sub.map((l) => l.slice(dedent)), depth + 1);
      }
      html += `<li>${body}</li>`;
    }
    return [html + `</${tag}>`, i];
  }

  function blocks(lines, depth) {
    if (depth > MAX_DEPTH) return `<p>${esc(lines.join("\n")).replace(/\n/g, "<br>")}</p>`;
    let html = "";
    let para = [];
    const flush = () => {
      if (para.length) html += `<p>${inline(para.join("\n"))}</p>`;
      para = [];
    };
    for (let i = 0; i < lines.length; ) {
      const line = lines[i];
      let m;
      if ((m = reFence.exec(line))) {
        flush();
        const fence = m[1], lang = m[2];
        const code = [];
        i++;
        while (i < lines.length && !(lines[i].trim().startsWith(fence[0].repeat(fence.length)) && lines[i].trim().replace(/[`~]/g, "") === "")) {
          code.push(lines[i]);
          i++;
        }
        i++; // closing fence (or end of a still-streaming block)
        html += `<div class="code"><div class="code-head"><span>${esc(lang || "code")}</span><button type="button" data-copy-code>Copy</button></div>` +
          `<pre><code>${esc(code.join("\n"))}</code></pre></div>`;
        continue;
      }
      if (!line.trim()) { flush(); i++; continue; }
      if ((m = reHead.exec(line))) {
        flush();
        const level = Math.min(4, m[1].length);
        html += `<h${level}>${inline(headText(m[2]))}</h${level}>`;
        i++;
        continue;
      }
      if (!para.length && isHr(line)) { flush(); html += "<hr>"; i++; continue; }
      if (reQuote.test(line)) {
        flush();
        const q = [];
        while (i < lines.length && (m = reQuote.exec(lines[i]))) { q.push(m[1]); i++; }
        html += `<blockquote>${blocks(q, depth + 1)}</blockquote>`;
        continue;
      }
      if (reList.test(line) && (!para.length || /^ {0,3}([-*+]|1[.)])\s/.test(line))) {
        flush();
        const [out, next] = renderList(lines, i, depth);
        html += out;
        i = next;
        continue;
      }
      if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        flush();
        const head = splitRow(line);
        const aligns = splitRow(lines[i + 1]).map((c) => (c.endsWith(":") ? (c.startsWith(":") ? "center" : "right") : ""));
        i += 2;
        let t = "<table><thead><tr>" + head.map((c, j) => `<th${aligns[j] ? ` style="text-align:${aligns[j]}"` : ""}>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          const cells = splitRow(lines[i]);
          t += "<tr>" + head.map((_, j) => `<td${aligns[j] ? ` style="text-align:${aligns[j]}"` : ""}>${inline(cells[j] || "")}</td>`).join("") + "</tr>";
          i++;
        }
        html += t + "</tbody></table>";
        continue;
      }
      para.push(line);
      i++;
    }
    flush();
    return html;
  }

  function render(text) {
    const src = String(text || "");
    if (src.length > 400000) return `<p>${esc(src).replace(/\n/g, "<br>")}</p>`;
    return blocks(src.replace(/\r\n?/g, "\n").split("\n"), 0);
  }

  root.OomiMarkdown = { render, escape: esc };
})(typeof window !== "undefined" ? window : globalThis);
