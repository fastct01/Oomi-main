"""Reranking over HTTP, for clusters where Atlas's native $rerank stage isn't switched on.

Uses the MongoDB Embedding and Reranking API (keys made in the Atlas UI) by default. A key from the
Voyage AI dashboard works too: set OOMI_RERANK_URL=https://api.voyageai.com/v1/rerank.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_URL = "https://ai.mongodb.com/v1/rerank"


class Reranker:
    def __init__(self, api_key: str = "", model: str = "rerank-2.5", url: str = DEFAULT_URL, timeout: float = 8):
        self.api_key, self.model, self.url, self.timeout = api_key, model, url or DEFAULT_URL, timeout
        self.last_error = ""

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def rank(self, query: str, documents: list) -> list:
        """[(index into documents, relevance 0..1)], best first. Empty on any failure."""
        if not self.available or not documents:
            return []
        body = json.dumps({"query": query, "documents": documents, "model": self.model,
                           "top_k": min(len(documents), 5), "return_documents": False, "truncation": True}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            self.last_error = str(e)
            return []
        out = [(int(d["index"]), float(d["relevance_score"])) for d in data.get("data", [])]
        return sorted(out, key=lambda x: x[1], reverse=True)
