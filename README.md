# Knowledge Fabric

A small, dependency-light Retrieval-Augmented Generation (RAG) starter kit:
ingest documents, chunk them, retrieve the most relevant chunks for a
question, and get an answer grounded in that context via the Claude API.

It's built to grow: the retriever, chunker, and store are all small,
swappable pieces rather than a single framework you have to fight.

## How it fits together

```
docs/*.txt, *.md
      │
      ▼
  ingest.py        walk a directory, read text files
      │
      ▼
  chunk.py          split each document into overlapping chunks
      │
      ▼
  store.py          in-memory store + lightweight keyword retriever
      │
      ▼
  qa.py             ask Claude to answer using the retrieved chunks
```

## Install

```bash
pip install -r requirements.txt
```

`anthropic` is only needed for `qa.py` (answer generation). Ingestion,
chunking, and retrieval have no external dependencies.

## Quick start

```bash
export ANTHROPIC_API_KEY=sk-ant-...

python -m knowledge_fabric.cli examples/docs "What does the fabric store?"
```

Or use it as a library:

```python
from knowledge_fabric.ingest import load_documents
from knowledge_fabric.chunk import chunk_documents
from knowledge_fabric.store import KnowledgeStore
from knowledge_fabric.qa import answer_question

docs = load_documents("examples/docs")
chunks = chunk_documents(docs)

store = KnowledgeStore()
store.add(chunks)

top_chunks = store.search("What does the fabric store?", k=4)
answer = answer_question(
    "What does the fabric store?",
    [c.text for c in top_chunks],
)
print(answer)
```

## Design notes

- **Retriever is intentionally simple.** `store.py` ships a keyword/overlap
  scorer with no ML dependencies, so the project runs out of the box. Swap
  in real embeddings (e.g. `sentence-transformers`, an embeddings API, or a
  vector DB) by implementing the same `add()` / `search()` interface.
- **`qa.py` is optional.** Only that module imports `anthropic`. Everything
  else works without an API key or network access.
- **Model:** defaults to `claude-opus-5`. Override with the
  `KNOWLEDGE_FABRIC_MODEL` environment variable.

## Project layout

```
knowledge_fabric/
  ingest.py    load documents from disk
  chunk.py     split documents into overlapping text chunks
  store.py     in-memory store + retrieval
  qa.py        answer a question from retrieved chunks via Claude
  cli.py       glue: ingest -> chunk -> store -> retrieve -> answer
tests/         unit tests for chunking and retrieval
examples/docs/ sample documents for the quick start
```

## Roadmap ideas

- Pluggable embedding backends (local model, hosted API, vector DB)
- Persistent storage (SQLite / on-disk index) instead of in-memory only
- Source citations with file + chunk offsets in answers
- Incremental re-indexing when source documents change