"""The Atlas pipelines themselves can only run on Atlas; these tests check their shape and the step-down logic."""

from pymongo.errors import OperationFailure

from oomi import atlas
from oomi.antibodies import Store


def stages(pipeline):
    return [next(iter(s)) for s in pipeline]


def test_hybrid_rerank_pipeline_shape():
    p = atlas.Searcher("rerank-2.5", "voyage-4").pipeline("hybrid+rerank", "team-a", "ValueError: boom")
    assert stages(p)[:2] == ["$rankFusion", "$limit"] and "$rerank" in stages(p)
    fusion = p[0]["$rankFusion"]["input"]["pipelines"]
    vec = fusion["vector"][0]["$vectorSearch"]
    assert vec["query"] == {"text": "ValueError: boom"} and vec["index"] == atlas.VECTOR_INDEX
    assert {"team": {"$eq": "team-a"}} in vec["filter"]["$and"]
    text = fusion["text"][0]["$search"]["compound"]
    assert {"equals": {"path": "team", "value": "team-a"}} in text["filter"]
    rerank = p[stages(p).index("$rerank")]["$rerank"]
    assert rerank["model"] == "rerank-2.5" and rerank["path"] == ["error_text"]


def test_index_definitions_declare_the_filters_they_need():
    vec = atlas.vector_index_definition("voyage-4")["fields"]
    assert {"type": "autoEmbed", "modality": "text", "path": "error_text", "model": "voyage-4"} in vec
    assert {"type": "filter", "path": "team"} in vec and {"type": "filter", "path": "status"} in vec
    fields = atlas.text_index_definition()["mappings"]["fields"]
    assert fields["team"]["type"] == "token" and fields["status"]["type"] == "token"


class FakeCollection:
    """Fails the way an older cluster does when a stage isn't supported."""

    def __init__(self, unsupported, docs):
        self.unsupported, self.docs, self.calls = unsupported, docs, []

    def aggregate(self, pipeline):
        names = stages(pipeline) + [next(iter(s)) for s in pipeline[0].get("$rankFusion", {}).get("input", {})]
        self.calls.append(stages(pipeline)[0])
        if any(n in self.unsupported for n in names):
            raise OperationFailure("Unrecognized pipeline stage name", details={"errmsg": "unsupported"})
        return iter(self.docs)


def test_searcher_steps_down_to_what_the_cluster_supports():
    col = FakeCollection({"$rerank", "$rankFusion"}, [{"error_text": "x", "_score": 0.9}])
    found = atlas.Searcher().candidates(col, "t", "x")
    assert found.how == "vector" and not found.reranked and col.calls == ["$rankFusion", "$rankFusion", "$vectorSearch"]
    col.calls.clear()
    atlas_searcher = atlas.Searcher()
    atlas_searcher.candidates(col, "t", "x")
    atlas_searcher.candidates(col, "t", "x")
    assert col.calls.count("$vectorSearch") == 2 and col.calls.count("$rankFusion") == 2  # remembers the mode


class StubSearcher:
    def __init__(self, docs):
        self.docs = docs

    def candidates(self, collection, team, error_text, limit=8):
        return atlas.Found(self.docs, "hybrid+rerank", True)


def test_native_rerank_score_decides_whether_to_offer(db):
    doc = {"_id": 1, "error_text": "AttributeError: module 'collections' has no attribute 'Mapping'",
           "fix": "Use collections.abc.Mapping.", "author": "Ivan"}
    sure = Store(db, "t", use_atlas_search=True, searcher=StubSearcher([{**doc, "_rerank": 0.91}]))
    m = sure.match("AttributeError: module 'collections' has no attribute 'Iterable'")
    assert m is not None and m.how == "hybrid+rerank" and m.fix == "Use collections.abc.Mapping."
    unsure = Store(db, "t", use_atlas_search=True, searcher=StubSearcher([{**doc, "_rerank": 0.12}]))
    assert unsure.match("TypeError: something unrelated") is None
