"""Phase 3: query-time retrieval.

Routing: user query -> router (Laya-style typed decision) -> fans out to
graph+vector search / code-graph-RAG as needed -> LLM reasons over the
shortlist, including cross-reference hops -> answer.

Traversal: a fixed hop cap was rejected in the spec (it silently drops
valid multi-hop chains like policy -> config -> code, but too high a cap
adds noise faster than signal). Instead: at each hop, Laya scores
confidence that continuing is still relevant to the original query;
traversal stops once that confidence drops below 0.50 (the same floor used
for the linking layer's auto-reject, for consistency). A hard ceiling of 5
hops applies regardless, as a safety net against runaway traversal. This
reuses Laya as the same cheap decision component already used in Phase 2,
rather than introducing a second scoring mechanism.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from knowledge_fabric.graphrag.graphstore import GraphStore
from knowledge_fabric.graphrag.laya import LayaClient

HOP_RELEVANCE_FLOOR = 0.50
MAX_HOPS = 5
_WORD_RE = re.compile(r"[a-z0-9]+")

_ALL_STRUCTURAL_EDGE_TYPES = (
    "mentions", "co_occurs", "contains", "calls", "imports", "links_to",
)


@dataclass
class HopStep:
    frontier: str
    reached: str
    edge_type: str
    confidence: float
    reasoning: str


@dataclass
class RetrievalResult:
    seed_nodes: list[str]
    shortlist: list[str]  # node ids reached during traversal, seeds first
    path: list[HopStep]
    route: str  # "doc" | "code" | "both"


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def route_query(query: str, laya: LayaClient | None = None) -> str:
    """Decide whether the query needs the doc graph alone, or both graphs.

    A cheap Laya-style typed decision rather than a full LLM call, per the
    spec's router step. The drift-detection use case almost always needs
    the linked policy text alongside any code it touches, so this only ever
    narrows to "doc" (pure policy questions) or widens to "both" - it never
    routes code-only. Falls back to "both" on any scoring failure, since
    over-searching is cheaper than under-answering.
    """
    laya = laya or LayaClient()
    try:
        result = laya.score(
            f"User query: {query}",
            "Does answering this query require looking at source code "
            "(as opposed to only policy/document text)?",
        )
    except Exception:
        return "both"
    return "doc" if result.confidence < 0.4 else "both"


def _seed_nodes(query: str, store: GraphStore, *, k: int = 5, route: str = "both") -> list[str]:
    """Keyword-match entry points: doc entities/chunks and, when `route` is
    "both", code files/classes/functions whose name or text overlaps the query."""
    query_words = _tokenize(query)
    if not query_words:
        return []

    doc_types = {"doc_chunk", "doc_entity"}
    code_types = {"class", "function", "file", "external_call", "external_module"}
    allowed_types = doc_types if route == "doc" else doc_types | code_types

    scored: list[tuple[float, str]] = []
    for node_id in store.nodes():
        node = store.get_node(node_id)
        node_type = node.get("type")
        if node_type not in allowed_types:
            continue
        words = _tokenize(node.get("text", "")) if node_type == "doc_chunk" else _tokenize(node.get("name", ""))
        overlap = len(query_words & words)
        if overlap:
            scored.append((overlap / max(len(words), 1), node_id))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [node_id for _score, node_id in scored[:k]]


def dynamic_hop_traversal(
    query: str,
    seed_nodes: list[str],
    store: GraphStore,
    *,
    laya: LayaClient | None = None,
    floor: float = HOP_RELEVANCE_FLOOR,
    max_hops: int = MAX_HOPS,
) -> tuple[list[str], list[HopStep]]:
    """Expand outward from `seed_nodes`, asking Laya at each hop whether to continue.

    Breadth-first: all frontier nodes reachable at hop N are scored before
    moving to hop N+1. A node already in the shortlist is never re-added or
    re-expanded, so the traversal terminates even on a cyclic graph
    regardless of the hop cap.
    """
    laya = laya or LayaClient()
    shortlist: list[str] = list(dict.fromkeys(seed_nodes))
    visited = set(shortlist)
    path: list[HopStep] = []

    frontier = list(seed_nodes)
    for _hop in range(max_hops):
        if not frontier:
            break
        next_frontier: list[str] = []
        for node_id in frontier:
            for _src, target, data in store.edges(node_id):
                if target in visited:
                    continue
                edge_type = data.get("edge_type", "")
                if edge_type not in _ALL_STRUCTURAL_EDGE_TYPES:
                    continue

                target_desc = _describe_node(store, target)
                result = laya.score(
                    f"Original query: {query}\n"
                    f"Currently examining: {_describe_node(store, node_id)}\n"
                    f"Would follow a '{edge_type}' edge to: {target_desc}",
                    "Is it still worth following this edge to answer the original query?",
                )
                path.append(
                    HopStep(
                        frontier=node_id,
                        reached=target,
                        edge_type=edge_type,
                        confidence=result.confidence,
                        reasoning=result.reasoning,
                    )
                )
                if result.confidence >= floor:
                    visited.add(target)
                    shortlist.append(target)
                    next_frontier.append(target)
        frontier = next_frontier

    return shortlist, path


def _describe_node(store: GraphStore, node_id: str) -> str:
    node = store.get_node(node_id)
    node_type = node.get("type", "?")
    if node_type == "doc_chunk":
        return f"document chunk: {node.get('text', '')[:200]}"
    if node_type == "doc_entity":
        return f"document entity: {node.get('name')}"
    return f"{node_type}: {node.get('name', node_id)}"


def retrieve(query: str, store: GraphStore, *, laya: LayaClient | None = None) -> RetrievalResult:
    """Full Phase 3 pipeline: route, seed, and traverse."""
    laya = laya or LayaClient()
    route = route_query(query, laya)
    seeds = _seed_nodes(query, store, route=route)
    shortlist, path = dynamic_hop_traversal(query, seeds, store, laya=laya)
    return RetrievalResult(seed_nodes=seeds, shortlist=shortlist, path=path, route=route)


def format_context(result: RetrievalResult, store: GraphStore) -> str:
    """Render the shortlist (in traversal order) as context for the final LLM call."""
    lines = []
    for i, node_id in enumerate(result.shortlist, start=1):
        node = store.get_node(node_id)
        node_type = node.get("type", "?")
        if node_type == "doc_chunk":
            lines.append(f"[{i}] (doc chunk, {node.get('source')}) {node.get('text')}")
        elif node_type == "doc_entity":
            lines.append(f"[{i}] (doc entity) {node.get('name')}")
        else:
            lines.append(
                f"[{i}] (code {node_type}, {node.get('file_path', '?')}"
                f":{node.get('start_line', '?')}) {node.get('name')}"
            )
    if result.path:
        lines.append("\nCross-reference hops followed:")
        for step in result.path:
            if step.reached in result.shortlist:
                lines.append(
                    f"  {step.frontier} --{step.edge_type}--> {step.reached} "
                    f"(confidence {step.confidence:.2f}: {step.reasoning})"
                )
    return "\n".join(lines)


def answer_question(
    query: str,
    store: GraphStore,
    *,
    laya: LayaClient | None = None,
    llm_client=None,
    llm_model: str = "claude-opus-5",
) -> str:
    """Retrieve, then have the LLM reason over the shortlist and cross-reference
    hops to answer `query` - e.g. "does the code match what the policy claims?"
    """
    import anthropic

    result = retrieve(query, store, laya=laya)
    context = format_context(result, store)
    if not result.shortlist:
        return "No relevant document or code entities were found for this query."

    client = llm_client or anthropic.Anthropic()
    response = client.messages.create(
        model=llm_model,
        max_tokens=2048,
        system=(
            "You are a compliance-drift analyst. You are given retrieved policy "
            "document chunks, code entities, and the cross-reference links "
            "between them. Answer the question using only this context. If the "
            "code contradicts what the policy claims (e.g. a hardcoded value "
            "differs from a stated retention period), say so explicitly and "
            "name the drift. Cite chunk/entity numbers like [1], [2]."
        ),
        messages=[{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"}],
    )

    for block in response.content:
        if block.type == "text":
            return block.text
    return ""
