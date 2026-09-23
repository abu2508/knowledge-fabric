from knowledge_fabric.graphrag.docgraph import (
    build_document_graph,
    detect_tables,
    extract_noun_phrases,
    recursive_split,
)
from knowledge_fabric.ingest import Document


def test_detect_tables_extracts_intact_and_removes_from_prose():
    text = (
        "Intro paragraph.\n\n"
        "| A | B |\n"
        "| --- | --- |\n"
        "| 1 | 2 |\n\n"
        "Outro paragraph."
    )
    tables, remaining = detect_tables(text)
    assert len(tables) == 1
    assert "| 1 | 2 |" in tables[0].markdown
    assert "Table:" in tables[0].caption
    assert "| 1 | 2 |" not in remaining
    assert "Intro paragraph." in remaining
    assert "Outro paragraph." in remaining


def test_recursive_split_prefers_paragraph_boundaries():
    text = "Para one." + "\n\n" + "Para two." + "\n\n" + "Para three."
    chunks = recursive_split(text, chunk_size=1000)
    assert chunks == [text]  # fits in one chunk, no splitting needed

    small = recursive_split(text, chunk_size=15)
    assert len(small) >= 2
    assert all(len(c) <= 15 or " " not in c for c in small)


def test_recursive_split_never_returns_empty_pieces():
    assert recursive_split("") == []
    assert recursive_split("   ") == []


def test_extract_noun_phrases_finds_multiword_terms():
    phrases = extract_noun_phrases("The data retention period is ninety days for user records.")
    lowered = [p.lower() for p in phrases]
    assert "data retention period" in lowered
    assert "user records" in lowered


def test_build_document_graph_creates_chunks_entities_and_co_occurs_edges(store, ns):
    doc = Document(
        doc_id=f"policy-{ns}.md",
        text="The data retention period applies to user records under policy rules.",
        source=f"policy-{ns}.md",
    )
    chunks = build_document_graph([doc], store, chunk_size=1000)

    assert len(chunks) == 1
    # `store` is a real, shared Neptune cluster - scope every query to this
    # test's namespace so it isn't looking at other runs' leftover data.
    doc_chunks = [n for n in store.nodes(node_type="doc_chunk", partition="doc") if ns in n]
    assert len(doc_chunks) == 1

    entities = store.nodes(node_type="doc_entity", partition="doc")
    chunk_entities = store.neighbors(doc_chunks[0], edge_type="mentions")
    assert any("retention" in e for e in chunk_entities)

    # every entity extracted from the single chunk should co-occur with at
    # least one other entity from that same chunk
    if len(chunk_entities) > 1:
        e = chunk_entities[0]
        co_occurring = store.neighbors(e, edge_type="co_occurs")
        assert co_occurring, f"expected co_occurs edges from {e}"
    assert set(chunk_entities) <= set(entities)


def test_build_document_graph_tables_become_their_own_chunk(store, ns):
    doc = Document(
        doc_id=f"policy-{ns}.md",
        text="Intro.\n\n| Type | Days |\n| --- | --- |\n| User | 90 |\n\nOutro.",
        source=f"policy-{ns}.md",
    )
    chunks = build_document_graph([doc], store)
    assert any(c.is_table for c in chunks)
