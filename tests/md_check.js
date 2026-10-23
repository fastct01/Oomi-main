// Checks for oomi/gui/static/md.js, run by tests/test_markdown.py (needs node).
// Model output must never produce markup outside the renderer's fixed set, and must never make it slow or crash.
"use strict";
const path = require("path");
require(path.join(__dirname, "..", "oomi", "gui", "static", "md.js"));
const { render } = globalThis.OomiMarkdown;

let failures = 0;
function check(name, ok, detail = "") {
  if (!ok) { failures++; console.log(`FAIL ${name} ${detail}`); }
}
function timed(name, text, ms = 400) {
  const t0 = Date.now();
  let out = "";
  try { out = render(text); } catch (e) { check(name, false, `threw ${e}`); return ""; }
  const took = Date.now() - t0;
  check(name, took < ms, `took ${took}ms`);
  return out;
}

const ALLOWED_TAGS = new Set(["p", "br", "strong", "em", "del", "code", "pre", "a", "h1", "h2", "h3", "h4", "ul", "ol",
  "li", "blockquote", "hr", "table", "thead", "tbody", "tr", "th", "td", "div", "span", "button"]);
const ALLOWED_ATTRS = new Set(["href", "target", "rel", "class", "type", "data-copy-code", "start", "style"]);
function audit(name, html) {
  const tagRe = /<\/?([a-zA-Z0-9]+)([^>]*)>/g;
  let m;
  while ((m = tagRe.exec(html))) {
    check(`${name}: tag`, ALLOWED_TAGS.has(m[1].toLowerCase()), m[0].slice(0, 80));
    const attrRe = /([a-zA-Z-]+)(?:="([^"]*)")?/g;
    let a;
    while ((a = attrRe.exec(m[2]))) {
      check(`${name}: attr`, ALLOWED_ATTRS.has(a[1].toLowerCase()), a[0].slice(0, 80));
      if (a[1] === "href") check(`${name}: href`, /^(https?:\/\/|mailto:)/i.test(a[2] || ""), a[2]);
      if (a[1] === "style") check(`${name}: style`, /^text-align:(left|right|center)$|^white-space:pre-wrap$/.test(a[2] || ""), a[2]);
    }
  }
  check(`${name}: no NUL`, !html.includes("\u0000"));
}

// Injection attempts
const attacks = [
  "<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "[x](javascript:alert(1))", "[x](JaVaScRiPt:alert(1))",
  "[x](data:text/html,<script>alert(1)</script>)", "![x](javascript:alert(1))", '[x](https://a.com" onmouseover="alert(1))',
  "`<b>`", "```\n</code></pre><script>alert(1)</script>\n```", "| a | b |\n|---|---|\n| <i>x</i> | [y](vbscript:z) |",
  "**<svg onload=alert(1)>**", "https://example.com/<script>", "> <iframe src=//evil>", "- <style>*{}</style>",
  "[a](https://x.com/'onclick='alert(1))", "<a href=\"javascript:alert(1)\">x</a>", "&lt;script&gt;", "\u0000<b>\u0000",
];
for (const a of attacks) audit(JSON.stringify(a).slice(0, 40), render(a));
// Random mixtures of markdown punctuation and HTML-ish fragments
const parts = ["<", ">", "\"", "'", "&", "[", "]", "(", ")", "`", "*", "_", "~", "#", "|", "-", ">", "\n", " ", "!",
  "javascript:", "https://a.b/", "mailto:x@y.z", "<script>", "onerror=", "\u0000", "‮", "1. ", "```", "---"];
let seed = 7;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
for (let i = 0; i < 20000; i++) {
  let s = "";
  for (let j = 0, n = 1 + rnd(40); j < n; j++) s += parts[rnd(parts.length)];
  audit("fuzz", render(s));
}

// Things the review found
timed("deep quotes", ">".repeat(8000) + " x");
timed("deep lists", Array.from({ length: 3000 }, (_, i) => " ".repeat(i * 2) + "- x").join("\n"), 1500);
timed("heading with long spaces", "# a" + " ".repeat(5000) + "x");
timed("hr-like line", "- ".repeat(5000) + "x");
timed("table separator-like", "| a |\n" + "|--".repeat(3000) + "x");
timed("many bold openers", "**a ".repeat(6000), 1500);
timed("many code ticks", "`a ".repeat(6000), 1500);
const big = timed("long reply", ("Some **bold** text with `code` and a [link](https://example.com).\n\n").repeat(2000), 1500);
check("long reply rendered", big.includes("<strong>bold</strong>"));
const nested = render("[![badge](https://img.shields.io/x.svg)](https://example.com)");
audit("nested link", nested);
check("nested link expanded", nested.includes('href="https://example.com"') && !nested.includes("\u0000"), nested);
const quoted = render("[x](https://example.com/it's)");
check("quote in url", quoted.includes("href=\"https://example.com/it&#39;s\"") && !quoted.includes("&amp;#39;"), quoted);
check("basic heading", render("## Title ##") === "<h2>Title</h2>", render("## Title ##"));
check("hash inside heading kept", render("# C# tips").includes("C# tips"));
check("hr", render("---") === "<hr>" && render("* * *") === "<hr>");
check("table", render("| a | b |\n|:--|--:|\n| 1 | 2 |").includes("<td style=\"text-align:right\">2</td>"));

if (failures) { console.log(`${failures} failure(s)`); process.exit(1); }
console.log("md ok");
