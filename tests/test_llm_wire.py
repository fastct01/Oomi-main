"""Exercise the real Anthropic SDK path over a mocked HTTP transport (no network, no key)."""

import json

from oomi.memory import http_module

httpx = http_module()

from oomi.llm import LLM


def _sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


STREAM = [
    {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
                                          "content": [], "stop_reason": None, "stop_sequence": None,
                                          "usage": {"input_tokens": 1200, "output_tokens": 1,
                                                    "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Need to look at the file."}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SIG"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Checking "}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "it."}},
    {"type": "content_block_stop", "index": 1},
    {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {}}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "{\"path\": \"a.py\"}"}},
    {"type": "content_block_stop", "index": 2},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 42}},
    {"type": "message_stop"},
]


def test_stream_request_shape_and_parsing():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(STREAM))

    llm = LLM("test-key", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    texts, thoughts = [], []
    out = llm.stream(model="claude-sonnet-5-5", system="sys", messages=[{"role": "user", "content": "hi"}],
                     tools=[{"name": "read_file", "description": "d", "input_schema": {"type": "object", "properties": {}}}],
                     max_tokens=16000, effort="medium", show_thinking=True, on_text=texts.append, on_thinking=thoughts.append)

    body = seen["body"]
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "medium"}
    assert body["stream"] is True and body["tools"][0]["name"] == "read_file"
    assert seen["headers"]["x-api-key"] == "test-key"

    assert "".join(texts) == "Checking it." and thoughts == ["Need to look at the file."]
    assert out["stop_reason"] == "tool_use" and out["model"] == "claude-sonnet-5-5"
    kinds = [b["type"] for b in out["content"]]
    assert kinds == ["thinking", "text", "tool_use"]
    assert out["content"][0]["signature"] == "SIG"
    assert out["content"][2]["input"] == {"path": "a.py"}
    assert out["usage"]["output_tokens"] == 42 and out["usage"]["cache_read_input_tokens"] == 900
    # Round-trip: the dicts we store must be valid request content for the next call.
    json.dumps({"role": "assistant", "content": out["content"]})


def test_complete_shape():
    def handler(request):
        body = json.loads(request.content)
        assert body["output_config"] == {"effort": "low"} and "stream" not in body
        return httpx.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": "{\"summary\": \"x\"}"}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}})

    llm = LLM("k", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    r = llm.complete(model="claude-haiku-5-5", system="s", prompt="p")
    assert r["content"][0]["text"] == "{\"summary\": \"x\"}"
