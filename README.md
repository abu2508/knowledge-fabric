# Knowledge Fabric

Two things live in this repo:

1. **`knowledge_fabric.graphrag`** - the real target: a GraphRAG
   architecture (document graph + code graph + a thin cross-reference
   linking layer) built for compliance/policy-code drift detection,
   per the build spec. **Start with the [GraphRAG](#graphrag-compliancepolicy-code-drift-detection)
   section below** - it explains what's implemented, what's a documented
   stand-in, and what's still blocked in this build environment.
2. **`knowledge_fabric`** (top-level modules) - a small, dependency-light
   single-graph RAG starter kit that predates the spec: ingest documents,
   chunk them, retrieve by keyword overlap, answer via Claude. Kept as-is;
   unrelated to the GraphRAG work below.

## How the simple RAG kit fits together

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

## Roadmap ideas (simple RAG kit)

- Pluggable embedding backends (local model, hosted API, vector DB)
- Persistent storage (SQLite / on-disk index) instead of in-memory only
- Source citations with file + chunk offsets in answers
- Incremental re-indexing when source documents change

---

## GraphRAG: compliance/policy-code drift detection

Implements the build spec: index a document graph and a code graph
independently, confirm cross-references between them, then let a query
walk both graphs via the confirmed links - anchored on detecting drift
between what a policy document claims (e.g. "records deleted after 90
days") and what the code actually does (e.g. a hardcoded 30-day constant).

```
docs/*.md, *.txt          code/**/*.rb
      │                         │
      ▼                         ▼
  docgraph.py              codegraph.py
  chunk, extract tables,   Tree-sitter parse: file/
  noun-phrase entities     class/function, calls, imports
      │                         │
      └────────────┬────────────┘
                    ▼
              graphstore.py         one Neo4j graph, two logical
              (Neo4j / AuraDB)      partitions ("doc" / "code")
                    │
                    ▼
              linking.py            word-overlap candidates -> Laya
              (Phase 2)             confirmation -> LLM escalation for
                                    the ambiguous band -> links_to edges
                    │
                    ▼
              retrieval.py          router -> keyword seed nodes ->
              (Phase 3)             Laya-scored dynamic hop traversal
                                    (floor 0.50, hard cap 5 hops) ->
                                    LLM answers over the shortlist
```

### Package layout

```
knowledge_fabric/graphrag/
  graphstore.py   Neo4j storage (Cypher) - the doc and code graphs are
                  two logical partitions of one graph; links_to edges are
                  Phase 2's cross-reference layer, stored directly on it
  docgraph.py     Phase 1, document path: table extraction, recursive
                  paragraph/sentence/word chunking, noun-phrase extraction
  codegraph.py    Phase 1, code path: Tree-sitter (Ruby) parsing into
                  file/class/function nodes + calls/imports edges,
                  incremental diff-based reparsing
  laya.py         typed-decision scoring interface (see "Laya" below)
  linking.py      Phase 2: candidate generation + Laya/LLM confirmation
  retrieval.py    Phase 3: router + dynamic hop traversal + final answer
  pipeline.py     orchestration: index() -> link() -> query()
infra/            Deprecated Terraform for an earlier Neptune cluster -
                  kept only to `terraform destroy` it (see infra/README.md)
tests/graphrag/    unit tests (chunking, Tree-sitter parsing - no
                  database needed) + Neo4j-backed tests (skipped without
                  a configured database - see "Running it" below)
examples/graphrag_demo.py   end-to-end demo against tests/fixtures/
```

### Why Neo4j, not Neptune

The build spec originally called for NetworkX locally / Amazon Neptune on
AWS (and explicitly ruled out Neo4j). This build started there, provisioned
a real Neptune Serverless cluster via Terraform, and hit two real costs of
that choice: Neptune has no public endpoint (only reachable from inside
its VPC - needs a bastion or VPC peering for anything outside AWS, this
build environment included) and it bills continuously even idle (~1 NCU
floor, no scale-to-zero, roughly $80/month left running). Given that, the
project explicitly moved to **Neo4j** (a free AuraDB instance) instead -
same graph-database role, but a public endpoint and a real free tier. The
`infra/` Terraform for the abandoned Neptune cluster is kept only to tear
it down; see `infra/README.md`.

### What's real vs. what's a documented stand-in

| Piece | Status |
|---|---|
| Chunking, table extraction, Tree-sitter code parsing, graph construction, candidate generation, hop traversal logic | Real, tested (see "Running it") |
| **Neo4j storage** (`graphstore.py`) | Real driver + Cypher code, written against the official `neo4j` Python driver. Not yet exercised against a live AuraDB instance from this session - needs `NEO4J_URI`/`NEO4J_PASSWORD` for an instance you create at neo4j.com/cloud/aura (a manual signup step). |
| **Laya** (`laya.py`) | Uses the real `laya` PyPI package (Convai Innovations' "System 1": input state + typed questions in, structured answer + calibrated probability out, one non-autoregressive forward pass) via `LayaClient`, the default everywhere. **Verified working end to end**: the checkpoint was downloaded from Hugging Face and a real prediction ran successfully in this build environment. `HaikuLayaClient` (same `score()` interface, a structured-output call to Claude Haiku 4.5) remains as a documented, explicitly-opt-in alternative - pass it as `laya=HaikuLayaClient()` to `link()`/`query()`. |
| **Noun-phrase extraction** (`docgraph.extract_noun_phrases`) | The spec calls for spaCy-style grammar-based extraction. Implemented as a mechanical stopword-filtered word-run extractor instead of a spaCy pipeline (no model dependency) - same "mechanical span extraction, not summarization" property, swappable via the same function signature. |
| **Real OpenProject benchmark data** | Confirmed reachable (the real `opf/openproject` repo clones successfully, including its GDPR/security/privacy docs) but not yet pulled into this repo's benchmark data - see "Getting to the real benchmark" below. |
| SQL/schema store path | **Descoped**, per the spec - not built. |
| Evaluation methodology, cost-vs-baseline methodology | Open per the spec - not yet decided, so not built. |

### The manual-eyeball checkpoint

The spec calls for eyeballing the noun phrases the extractor pulls from
real OpenProject policy sentences before trusting Phase 2 on top of it.
That hasn't happened yet - there's no real OpenProject text ingested yet
(see "Getting to the real benchmark"). Run
`knowledge_fabric.graphrag.docgraph.review_noun_phrases(text)` against
real policy text once it's ingested, and read the output, before relying
on linking-layer results.

### Getting to the real benchmark

Network access to OpenProject's data is confirmed working - what's left is
pulling the specific GDPR/security/privacy policy pages as the document
corpus and the codebase modules implementing data deletion/retention as
the code corpus, then running `index()` on them (it takes any directory of
`.md`/`.txt` docs and `.rb` files, so no code changes are needed).

### Running it

```bash
pip install -r requirements.txt   # or: pip install -e ".[graphrag]"

# Pure-logic tests - chunking, table extraction, Tree-sitter parsing.
# No database needed.
pytest tests/graphrag/

# Once you have a Neo4j database (a free AuraDB instance works):
export NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=...
export ANTHROPIC_API_KEY=sk-ant-...
pytest tests/graphrag/                     # now runs the Neo4j-backed tests too
python examples/graphrag_demo.py           # indexes, links, and queries the fixture data end to end
```

### Laya thresholds (Phase 2 and Phase 3, shared)

| Laya confidence | Phase 2 (linking) | Phase 3 (traversal) |
|---|---|---|
| > 0.85 | auto-accept, no LLM | - |
| 0.50 - 0.85 | escalate to a full LLM for the final call | - |
| < 0.50 | auto-reject | stop traversing this edge |

Phase 3's hard ceiling is 5 hops regardless of confidence (`MAX_HOPS` in
`retrieval.py`), as a safety net against runaway traversal - the same 0.50
floor as Phase 2's auto-reject threshold, reused for consistency per the
spec rather than introducing a second scoring cutoff.

**Open per the spec, not yet validated:** whether 0.50 is the right
traversal-stopping threshold is empirical - tune it against real
OpenProject traversal behavior once the benchmark is running.