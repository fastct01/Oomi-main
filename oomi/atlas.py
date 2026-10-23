"""MongoDB Atlas: connecting, collections and indexes, and the hybrid search that matches antibodies.

Matching an error against the team's antibodies runs as one aggregation pipeline:

    $rankFusion
      ├─ vector: $vectorSearch on error_text — Automated Embedding, so Atlas embeds both the stored
      │          errors and the new one with a Voyage model; finds the same bug worded differently
      └─ text:   $search on error_text — exact words matter in errors (module names, error codes)
    $rerank      Voyage reranker inside Atlas scores each candidate against the new error
    $limit 3

Not every cluster has every feature (hybrid search needs MongoDB 8.0+, native rerank is newer and
must be switched on in Project Settings), so the searcher steps down to the best mode that works:
hybrid+rerank → hybrid → vector → text, and tries the top mode again every few minutes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

TEXT_INDEX = "antibodies_text"
VECTOR_INDEX = "antibodies_vector"
MODES = ("hybrid+rerank", "hybrid", "vector", "text")
RETRY_TOP_MODE_AFTER = 300  # seconds


def connect(uri: str, db_name: str = "oomi"):
    client = MongoClient(uri, appname="oomi", tz_aware=True, serverSelectionTimeoutMS=8000)
    client.admin.command("ping")
    return client[db_name]


def ensure_collections(db) -> None:
    """Regular indexes. Cheap and safe to run on every start."""
    ab = db["antibodies"]
    ab.create_index([("team", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)], name="by_team")
    # One approved antibody per exact error per team; saving it again counts as a confirmation.
    ab.create_index([("team", ASCENDING), ("signature", ASCENDING)], name="one_per_error",
                    unique=True, partialFilterExpression={"status": "approved"})
    db["events"].create_index([("team", ASCENDING), ("ts", DESCENDING)], name="by_team_time")


def text_index_definition() -> dict:
    return {"mappings": {"dynamic": False, "fields": {
        "error_text": {"type": "string", "analyzer": "lucene.standard"},
        "fix": {"type": "string", "analyzer": "lucene.standard"},
        "command": {"type": "string", "analyzer": "lucene.standard"},
        "team": {"type": "token"},     # `equals` filters need token fields
        "status": {"type": "token"},
    }}}


def vector_index_definition(model: str = "voyage-4") -> dict:
    return {"fields": [
        # Automated Embedding: Atlas embeds error_text itself, no embedding code or vectors in documents.
        {"type": "autoEmbed", "modality": "text", "path": "error_text", "model": model},
        {"type": "filter", "path": "team"},
        {"type": "filter", "path": "status"},
    ]}


def setup(db, embed_model: str = "voyage-4", wait_s: int = 180) -> list:
    """Create collections, regular indexes and both search indexes. Returns lines to show the person."""
    report = []
    if "antibodies" not in db.list_collection_names():
        db.create_collection("antibodies")
    if "events" not in db.list_collection_names():
        db.create_collection("events")
    ensure_collections(db)
    report.append("✓ collections antibodies and events, with their indexes")
    ab = db["antibodies"]
    existing = {i["name"]: i for i in ab.list_search_indexes()}
    wanted = [
        SearchIndexModel(definition=text_index_definition(), name=TEXT_INDEX, type="search"),
        SearchIndexModel(definition=vector_index_definition(embed_model), name=VECTOR_INDEX, type="vectorSearch"),
    ]
    for model in wanted:
        name = model.document["name"]
        if name in existing:
            report.append(f"✓ search index {name} already exists")
            continue
        try:
            ab.create_search_index(model=model)
            report.append(f"… creating search index {name}")
        except OperationFailure as e:
            report.append(f"✗ couldn't create {name}: {e.details.get('errmsg', e) if e.details else e}\n"
                          f"  Create it in the Atlas UI (Search → Create Index → JSON editor) with atlas/{name}.json")
    deadline = time.time() + wait_s
    while time.time() < deadline:
        state = {i["name"]: i.get("queryable", False) for i in ab.list_search_indexes()}
        if all(state.get(n) for n in (TEXT_INDEX, VECTOR_INDEX)):
            report.append("✓ both search indexes are ready to query")
            return report
        time.sleep(5)
    report.append("… search indexes are still building; run `oomi setup-atlas` again in a minute to check")
    return report


@dataclass
class Found:
    docs: list
    how: str
    reranked: bool  # docs carry a `_rerank` score from Atlas's $rerank stage


class Searcher:
    """Finds the antibodies closest to an error, with the best search mode this cluster supports."""

    def __init__(self, rerank_model: str = "rerank-2.5", embed_model: str = "voyage-4"):
        self.rerank_model = rerank_model
        self.embed_model = embed_model
        self.mode_index = 0
        self.downgraded_at = 0.0
        self.last_error = ""

    def candidates(self, collection, team: str, error_text: str, limit: int = 8) -> Found:
        if self.mode_index and time.time() - self.downgraded_at > RETRY_TOP_MODE_AFTER:
            self.mode_index = 0
        while self.mode_index < len(MODES):
            mode = MODES[self.mode_index]
            try:
                docs = list(collection.aggregate(self.pipeline(mode, team, error_text, limit)))
                return Found(docs, mode, mode == "hybrid+rerank")
            except OperationFailure as e:
                self.last_error = f"{mode}: {e.details.get('errmsg', e) if e.details else e}"
                self.mode_index += 1
                self.downgraded_at = time.time()
        self.mode_index = len(MODES) - 1  # keep trying plain text search next time
        raise RuntimeError(f"Atlas Search isn't working on this cluster ({self.last_error}). "
                           "Run `oomi setup-atlas`, or set OOMI_ATLAS_SEARCH=0.")

    def _vector(self, team: str, error_text: str, limit: int) -> dict:
        return {"$vectorSearch": {
            "index": VECTOR_INDEX, "path": "error_text", "query": {"text": error_text}, "model": self.embed_model,
            "numCandidates": max(50, limit * 10), "limit": limit,
            "filter": {"$and": [{"team": {"$eq": team}}, {"status": {"$eq": "approved"}}]},
        }}

    def _text(self, team: str, error_text: str) -> dict:
        return {"$search": {"index": TEXT_INDEX, "compound": {
            "must": [{"text": {"query": error_text, "path": "error_text"}}],
            "should": [{"text": {"query": error_text, "path": ["fix", "command"]}}],
            "filter": [{"equals": {"path": "team", "value": team}},
                       {"equals": {"path": "status", "value": "approved"}}],
        }}}

    def pipeline(self, mode: str, team: str, error_text: str, limit: int = 8) -> list:
        if mode == "vector":
            return [self._vector(team, error_text, limit),
                    {"$addFields": {"_score": {"$meta": "vectorSearchScore"}}}]
        if mode == "text":
            return [self._text(team, error_text), {"$limit": limit},
                    {"$addFields": {"_score": {"$meta": "searchScore"}}}]
        fused = {"$rankFusion": {
            "input": {"pipelines": {
                "vector": [self._vector(team, error_text, limit * 2)],
                "text": [self._text(team, error_text), {"$limit": limit * 2}],
            }},
            "combination": {"weights": {"vector": 0.6, "text": 0.4}},
        }}
        stages = [fused, {"$limit": limit}, {"$addFields": {"_score": {"$meta": "score"}}}]
        if mode == "hybrid+rerank":
            stages += [
                {"$rerank": {"query": {"text": error_text}, "path": ["error_text"],
                             "numDocsToRerank": limit, "model": self.rerank_model}},
                {"$addFields": {"_rerank": {"$meta": "score"}}},
                {"$limit": 3},
            ]
        return stages
