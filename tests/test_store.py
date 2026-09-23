from knowledge_fabric.chunk import Chunk
from knowledge_fabric.store import KnowledgeStore


def _chunk(chunk_id: str, text: str) -> Chunk:
    return Chunk(chunk_id=chunk_id, text=text, doc_id=chunk_id, source=f"{chunk_id}.txt")


def test_search_ranks_relevant_chunk_first():
    store = KnowledgeStore()
    store.add(
        [
            _chunk("c1", "The quick brown fox jumps over the lazy dog"),
            _chunk("c2", "Knowledge Fabric stores document chunks for retrieval"),
            _chunk("c3", "Bananas are a good source of potassium"),
        ]
    )

    results = store.search("How does the fabric store chunks?", k=2)

    assert results
    assert results[0].chunk_id == "c2"


def test_search_empty_store_returns_empty():
    store = KnowledgeStore()
    assert store.search("anything") == []


def test_search_no_matching_terms_returns_empty():
    store = KnowledgeStore()
    store.add([_chunk("c1", "apples and oranges")])
    assert store.search("zzzznonexistentterm") == []
