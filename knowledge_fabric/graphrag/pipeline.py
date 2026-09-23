"""End-to-end orchestration: index documents and code, link them, answer a query.

Ties together Phase 1 (docgraph, codegraph), Phase 2 (linking), and Phase 3
(retrieval) into the flow the build spec describes for the compliance/
policy-code drift use case: index the two graphs independently, confirm
cross-references between them, then let a query walk both via the linked
edges.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_fabric.graphrag.codegraph import build_code_graph
from knowledge_fabric.graphrag.docgraph import build_document_graph
from knowledge_fabric.graphrag.graphstore import GraphStore
from knowledge_fabric.graphrag.laya import LayaClient
from knowledge_fabric.graphrag.linking import LinkDecision, confirm_candidates, generate_candidates
from knowledge_fabric.graphrag.retrieval import answer_question
from knowledge_fabric.ingest import load_documents


def index(
    doc_path: str | Path,
    code_root: str | Path,
    *,
    store: GraphStore | None = None,
    code_extensions: tuple[str, ...] = (".rb",),
) -> GraphStore:
    """Phase 1: build the document graph and the code graph into one `GraphStore`."""
    store = store or GraphStore()

    documents = load_documents(doc_path, extensions=(".txt", ".md"))
    build_document_graph(documents, store)

    code_root = Path(code_root)
    files = {
        str(p.relative_to(code_root)): p.read_bytes()
        for p in sorted(code_root.rglob("*"))
        if p.is_file() and p.suffix in code_extensions
    }
    build_code_graph(files, store)

    return store


def link(
    store: GraphStore, *, laya: LayaClient | None = None, llm_client=None, llm_model: str = "claude-opus-5"
) -> list[LinkDecision]:
    """Phase 2: propose and confirm cross-reference links, writing them onto `store`."""
    laya = laya or LayaClient()
    candidates = generate_candidates(store)
    return confirm_candidates(candidates, store, laya=laya, llm_client=llm_client, llm_model=llm_model)


def query(
    question: str,
    store: GraphStore,
    *,
    laya: LayaClient | None = None,
    llm_client=None,
    llm_model: str = "claude-opus-5",
) -> str:
    """Phase 3: retrieve and answer."""
    laya = laya or LayaClient()
    return answer_question(question, store, laya=laya, llm_client=llm_client, llm_model=llm_model)


def build_and_query(
    doc_path: str | Path,
    code_root: str | Path,
    question: str,
    *,
    laya: LayaClient | None = None,
    llm_client=None,
    llm_model: str = "claude-opus-5",
) -> tuple[GraphStore, list[LinkDecision], str]:
    """Convenience: run the full pipeline once, end to end."""
    laya = laya or LayaClient()
    store = index(doc_path, code_root)
    decisions = link(store, laya=laya, llm_client=llm_client, llm_model=llm_model)
    answer = query(question, store, laya=laya, llm_client=llm_client, llm_model=llm_model)
    return store, decisions, answer
