"""Thin streaming wrapper around the Anthropic Messages API.

Everything above this layer works on plain dicts, so a scripted fake can stand in for tests.
"""

from __future__ import annotations

from typing import Callable, Optional


class ModelError(Exception):
    """A model call failed. kind: "auth" (key rejected), "unavailable" (overloaded, rate-limited, down,
    or no access to this model: worth trying another model), or "request" (a bad request: don't retry)."""

    def __init__(self, kind: str, model: str, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.kind, self.model, self.status = kind, model, status

    @property
    def short(self) -> str:
        return {529: "overloaded", 429: "rate-limited", 404: "not found", 403: "no access"}.get(
            self.status or 0, "unreachable" if self.status is None else f"HTTP {self.status}")


_RETRYABLE_BODY_TYPES = {"overloaded_error", "rate_limit_error", "api_error", "timeout_error", "service_unavailable"}


def _wrap(exc: Exception, model: str) -> ModelError:
    import anthropic

    status = getattr(exc, "status_code", None)
    if isinstance(exc, anthropic.AuthenticationError):
        return ModelError("auth", model, "The Anthropic API key was rejected.", status)
    retryable = tuple(c for c in (getattr(anthropic, n, None) for n in (
        "OverloadedError", "RateLimitError", "InternalServerError", "ServiceUnavailableError", "DeadlineExceededError",
        "APIConnectionError", "APITimeoutError", "NotFoundError", "PermissionDeniedError")) if isinstance(c, type))
    if isinstance(exc, retryable):
        return ModelError("unavailable", model, str(exc)[:300], status if status != 200 else 529)
    # Errors that arrive mid-stream come as a generic APIStatusError with HTTP 200; the body says what happened.
    body = getattr(exc, "body", None)
    err_type = body.get("error", {}).get("type") if isinstance(body, dict) and isinstance(body.get("error"), dict) else None
    if err_type in _RETRYABLE_BODY_TYPES or (isinstance(exc, anthropic.APIStatusError) and (status or 0) >= 500):
        return ModelError("unavailable", model, str(exc)[:300], 529 if err_type == "overloaded_error" else status)
    if err_type == "authentication_error":
        return ModelError("auth", model, "The Anthropic API key was rejected.", 401)
    return ModelError("request", model, str(exc)[:500], status)


def _transport_errors() -> tuple:
    """Connection drops mid-stream surface as the HTTP library's own errors, not the SDK's."""
    out = []
    for name in ("httpx2", "httpx"):
        try:
            mod = __import__(name)
            out.append(mod.TransportError)
        except (ImportError, AttributeError):
            pass
    return tuple(out)


class LLM:
    def __init__(self, api_key: str, max_retries: int = 4, http_client=None):
        import anthropic

        kwargs = {"http_client": http_client} if http_client is not None else {}
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=max_retries, **kwargs)
        self._current = None

    def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list,
        tools: list,
        max_tokens: int,
        effort: Optional[str] = None,
        show_thinking: bool = True,
        on_text: Optional[Callable[[str], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
        check: Optional[Callable[[], None]] = None,
        cache_ttl: str = "5m",
    ) -> dict:
        """check() runs on every streamed event; it may raise (e.g. KeyboardInterrupt) to stop the stream."""
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            # Automatic prompt caching: the API places and moves the breakpoint as history grows.
            cache_control={"type": "ephemeral", "ttl": "1h"} if cache_ttl == "1h" else {"type": "ephemeral"},
            thinking={"type": "adaptive", "display": "summarized" if show_thinking else "omitted"},
        )
        if tools:
            kwargs["tools"] = tools
        if effort:
            kwargs["output_config"] = {"effort": effort}

        import anthropic

        try:
            with self.client.messages.stream(**kwargs) as stream:
                self._current = stream
                for event in stream:
                    if check:
                        check()
                    if getattr(event, "type", None) != "content_block_delta":
                        continue
                    delta = event.delta
                    dtype = getattr(delta, "type", None)
                    if dtype == "text_delta" and on_text:
                        on_text(delta.text)
                    elif dtype == "thinking_delta" and on_thinking and delta.thinking:
                        on_thinking(delta.thinking)
                final = stream.get_final_message()
        except anthropic.APIError as e:
            raise _wrap(e, model) from e
        except _transport_errors() as e:
            raise ModelError("unavailable", model, f"connection dropped: {e}"[:300]) from e
        finally:
            self._current = None
        return _to_dict(final)

    def abort(self) -> None:
        """Close the response being streamed (called from another thread to stop a silent wait)."""
        stream = self._current
        if stream is not None:
            stream.close()

    def complete(self, *, model: str, system: str, prompt: str, max_tokens: int = 4000) -> dict:
        """One-shot, non-streaming call used for summaries. Returns the same dict shape."""
        import anthropic

        try:
            resp = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "low"},
            )
        except anthropic.APIError as e:
            raise _wrap(e, model) from e
        except _transport_errors() as e:
            raise ModelError("unavailable", model, f"connection dropped: {e}"[:300]) from e
        return _to_dict(resp)


def _to_dict(msg) -> dict:
    return {
        "model": msg.model,
        "stop_reason": msg.stop_reason,
        "content": [b.model_dump(mode="json", exclude_none=True) for b in msg.content],
        "usage": msg.usage.model_dump(mode="json", exclude_none=True) if msg.usage else {},
    }


def text_of(response: dict) -> str:
    return "".join(b.get("text", "") for b in response.get("content", []) if b.get("type") == "text")
