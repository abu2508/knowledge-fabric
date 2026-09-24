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
              graphstore.py         one Neptune graph, two logical
              (Amazon Neptune)      partitions ("doc" / "code")
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
  graphstore.py   Neptune/Gremlin storage - the doc and code graphs are
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
infra/            Terraform: VPC + Neptune Serverless cluster + IAM policy
                  (+ optional bastion - see infra/README.md)
tests/graphrag/    unit tests (chunking, Tree-sitter parsing - no AWS
                  needed) + Neptune-backed tests (skipped without a
                  configured cluster - see "Running it" below)
examples/graphrag_demo.py   end-to-end demo against tests/fixtures/
```

### What's real vs. what's a documented stand-in

| Piece | Status |
|---|---|
| Chunking, table extraction, Tree-sitter code parsing, graph construction, candidate generation, hop traversal logic | Real, tested (see "Running it") |
| **Neptune storage** (`graphstore.py`) | Real Gremlin + IAM SigV4 client code, written against Neptune's documented API. **Not yet exercised against a live cluster** - this build environment's network policy doesn't reach AWS service endpoints, and no cluster was provisioned from here. See `infra/README.md`. |
| **Laya** (`laya.py`) | Uses the real `laya` PyPI package (Convai Innovations' "System 1": input state + typed questions in, structured answer + calibrated probability out, one non-autoregressive forward pass) via `LayaClient`, the default everywhere. Written against the installed package's actual `Agent.predict`/`system_one` API and its `"noul"` question type (calibrated P(true) on a yes/no question). **Not yet exercised end to end**: `Agent(...)` downloads its checkpoint from Hugging Face on first use, and this environment's network policy blocks `huggingface.co`. `HaikuLayaClient` (same `score()` interface, a structured-output call to Claude Haiku 4.5) is a documented, explicitly-opt-in alternative for environments where the Laya checkpoint isn't reachable - pass it as `laya=HaikuLayaClient()` to `link()`/`query()`. |
| **Noun-phrase extraction** (`docgraph.extract_noun_phrases`) | The spec calls for spaCy-style grammar-based extraction. This environment's network policy blocks downloading a spaCy language model (spaCy the package installs fine from PyPI; `en_core_web_sm` does not), so it's a mechanical stopword-filtered word-run extractor instead - same "mechanical span extraction, not summarization" property, swappable via the same function signature. |
| **Real OpenProject benchmark data** | Not pulled. This environment's network policy blocks general web access (`openproject.org`, `github.com/opf/openproject`) and that repo isn't in this session's GitHub scope. `tests/fixtures/` has a small hand-written policy doc + Ruby service instead, used only for unit tests and the demo script - not a substitute for the real benchmark. See "Getting to the real benchmark" below. |
| SQL/schema store path | **Descoped**, per the spec - not built. |
| Evaluation methodology, cost-vs-baseline methodology | Open per the spec - not yet decided, so not built. |

### The manual-eyeball checkpoint

The spec calls for eyeballing the noun phrases the extractor pulls from
real OpenProject policy sentences before trusting Phase 2 on top of it.
That hasn't happened - there's no real OpenProject text to eyeball yet.
Run `knowledge_fabric.graphrag.docgraph.review_noun_phrases(text)` against
real policy text once it's available, and read the output, before relying
on linking-layer results.

### Getting to the real benchmark

Two things are blocking real OpenProject data and a live Neptune run, both
environment-level, not code-level:

1. **OpenProject data.** From a session whose network policy allows
   general web access (or with `opf/openproject` forked into a GitHub org
   this session can reach), pull the published GDPR/security/privacy
   policy pages as the document corpus, and the codebase modules
   implementing data deletion/retention as the code corpus - `index()`
   takes any directory of `.md`/`.txt` docs and `.rb` files, so no code
   changes are needed once the data is reachable.
2. **A live Neptune cluster.** `cd infra && terraform apply` (see
   `infra/README.md` - it also covers reaching Neptune from outside its
   VPC, which this build's own test environment can't do either).

### Running it

```bash
pip install -r requirements.txt   # or: pip install -e ".[graphrag]"

# Pure-logic tests - chunking, table extraction, Tree-sitter parsing.
# No AWS needed.
pytest tests/graphrag/

# Once a cluster exists (infra/) and credentials are set:
export NEPTUNE_ENDPOINT=<cluster endpoint>
export AWS_REGION=<region>
export ANTHROPIC_API_KEY=sk-ant-...
pytest tests/graphrag/                     # now runs the Neptune-backed tests too
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