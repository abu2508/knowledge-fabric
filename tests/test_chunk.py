from knowledge_fabric.chunk import Document, chunk_documents, chunk_text


def test_chunk_text_basic():
    text = " ".join(f"word{i}" for i in range(10))
    chunks = chunk_text(text, chunk_size=4, overlap=1)
    assert chunks[0] == "word0 word1 word2 word3"
    # each chunk after the first overlaps the previous by 1 word
    assert chunks[1].split()[0] == chunks[0].split()[-1]


def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_rejects_bad_overlap():
    try:
        chunk_text("a b c", chunk_size=3, overlap=3)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for overlap >= chunk_size")


def test_chunk_documents_ids_are_stable():
    doc = Document(doc_id="a.txt", text="one two three four five", source="a.txt")
    chunks = chunk_documents([doc], chunk_size=2, overlap=0)
    assert [c.chunk_id for c in chunks] == ["a.txt#0", "a.txt#1", "a.txt#2"]
    assert all(c.doc_id == "a.txt" for c in chunks)
