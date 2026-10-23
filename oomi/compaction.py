"""Context compaction that keeps recall high and the API happy.

* Runs only between turns (never inside a tool loop), cutting at user-message boundaries so every
  tool_use keeps its tool_result.
* Before older turns are summarised, durable facts in them are extracted into long-term memory, so
  compaction can't silently lose them; memory retrieval brings them back when relevant.
* The summary is merged into the first kept user message (no fake assistant turn), and thinking
  blocks in kept turns are dropped, because the history before them changed.
"""

from __future__ import annotations

import json
import re

SUMMARY_SYSTEM = (
    "You compress an AI agent's working conversation so it can continue the task. "
    "Return only a JSON object, no prose before or after."
)

SUMMARY_PROMPT = """Below is the older part of a conversation between a user and a terminal agent on their Mac.
Write a JSON object with two keys:

"summary": Markdown with these sections (omit empty ones):
  ## Goal  (what the user is trying to achieve, in their words where possible)
  ## Done so far  (concrete results: files changed with paths, commands that worked, answers given)
  ## Decisions & constraints  (choices made, things the user ruled out, preferences stated)
  ## Open threads  (unfinished steps, errors still unresolved, questions waiting on the user)
  ## Key details  (exact names, paths, versions, URLs, IDs, numbers needed to continue)
  Be specific; keep exact identifiers. Under 900 words.

"durable_facts": a list of short standalone facts worth remembering across future sessions — the user's
preferences, people, projects, environment details. Only things the user stated or confirmed, not task
progress, not guesses, and nothing secret (no keys, passwords or tokens). Max 8. Can be empty.

<transcript>
{transcript}
</transcript>"""


def is_turn_start(msg: dict) -> bool:
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return True
    return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def turn_starts(messages: list) -> list:
    return [i for i, m in enumerate(messages) if is_turn_start(m)]


def strip_thinking(messages: list) -> list:
    out = []
    for m in messages:
        if m.get("role") == "assistant" and isinstance(m.get("content"), list):
            blocks = [b for b in m["content"] if b.get("type") not in ("thinking", "redacted_thinking")]
            if not blocks:
                blocks = [{"type": "text", "text": "(working)"}]
            m = {**m, "content": blocks}
        out.append(m)
    return out


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n // 2] + f" … [{len(s) - n} chars cut] … " + s[-n // 2:]


def render_transcript(messages: list, max_chars: int = 120_000) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "?").upper()
        content = m.get("content")
        if isinstance(content, str):
            parts.append(f"{role}: {_clip(content, 4000)}")
            continue
        for b in content:
            t = b.get("type")
            if t == "text":
                text = b.get("text", "")
                if text.startswith("<memory_context>") or text.startswith("<conversation_summary>"):
                    if text.startswith("<conversation_summary>"):
                        parts.append(f"EARLIER SUMMARY: {_clip(text, 6000)}")
                    continue
                parts.append(f"{role}: {_clip(text, 4000)}")
            elif t == "tool_use":
                parts.append(f"TOOL CALL {b.get('name')}: {_clip(json.dumps(b.get('input', {})), 600)}")
            elif t == "tool_result":
                c = b.get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                flag = " (error)" if b.get("is_error") else ""
                parts.append(f"TOOL RESULT{flag}: {_clip(str(c), 700)}")
            elif t == "server_tool_use":
                parts.append(f"{b.get('name', 'server tool').upper()}: {_clip(json.dumps(b.get('input', {})), 300)}")
            elif t in ("web_search_tool_result", "web_fetch_tool_result"):
                parts.append(f"[{t.replace('_tool_result', '').replace('_', ' ')} results omitted]")
    text = "\n".join(parts)
    return _clip(text, max_chars)


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def compact(messages: list, summarize, keep_recent_turns: int, memory=None) -> tuple:
    """Return (new_messages, summary_text, facts_saved, usage_dict or None).

    `summarize(prompt) -> dict` is an LLM call returning the response dict.
    """
    starts = turn_starts(messages)
    if len(starts) <= keep_recent_turns:
        return messages, "", 0, None
    cut = starts[-keep_recent_turns]
    old, recent = messages[:cut], messages[cut:]

    response = summarize(SUMMARY_PROMPT.format(transcript=render_transcript(old)))
    raw = "".join(b.get("text", "") for b in response.get("content", []) if b.get("type") == "text")
    data = _parse_json(raw)
    summary = (data.get("summary") or "").strip()
    if not summary:
        summary = "## Earlier conversation (summary unavailable, excerpt)\n" + render_transcript(old, 6000)

    saved = 0
    if memory is not None:
        for fact in (data.get("durable_facts") or [])[:8]:
            if isinstance(fact, str) and fact.strip() and not re.search(r"(api[_ -]?key|password|token|secret)\s*[:=]", fact, re.I):
                try:
                    memory.add(fact, tags=["from-compaction"], source="compaction")
                    saved += 1
                except ValueError:
                    pass

    recent = strip_thinking(recent)
    first = dict(recent[0])
    content = first["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    note = (f"<conversation_summary>\n{summary}\n</conversation_summary>\n"
            "(Earlier turns were compacted into the summary above. Continue from here.)")
    first["content"] = [{"type": "text", "text": note}] + list(content)
    return [first] + recent[1:], summary, saved, response.get("usage")
