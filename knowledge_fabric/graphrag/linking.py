"""Phase 2: the thin cross-reference linking layer between the document
graph and the code graph.

Two-step pipeline per candidate pair:

  1. Cheap match (near-free, no model call): flag a pair when the doc
     entity and the code entity share at least one meaningful word, after
     stripping a stopword/generic-term list. Only fall back to embedding
     similarity for a doc entity that matched nothing by word overlap.
  2. Laya confirmation: every flagged candidate is scored for same-concept
     confidence (`knowledge_fabric.graphrag.laya.LayaClient`).

     | confidence   | outcome                                    |
     |--------------|---------------------------------------------|
     | > 0.85       | auto-accept - confirmed link, no LLM needed |
     | 0.50 - 0.85  | escalate to a full LLM for a final call     |
     | < 0.50       | auto-reject - treated as noise              |

Confirmed links are written directly onto the shared `GraphStore` as
`links_to` edges (see `graphstore.py`) - `(entity, links-to, entity,
confidence, timestamp)` - bidirectionally.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from knowledge_fabric.graphrag.graphstore import GraphStore, make_link
from knowledge_fabric.graphrag.laya import LayaAnswer, LayaClient

# Generic terms that don't make two entities "the same concept" just by
# being shared - stripped before computing word overlap for candidate
# generation. Examples straight from the spec plus a few obvious siblings.
STOPWORDS = {
    "system", "data", "user", "process", "processing", "service", "record",
    "records", "policy", "manager", "handler", "controller", "model",
    "object", "item", "items", "value", "info", "information", "default",
    "base", "core", "main", "helper", "util", "utils", "module",
}

AUTO_ACCEPT_THRESHOLD = 0.85
AUTO_REJECT_THRESHOLD = 0.50
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class Candidate:
    doc_entity_id: str
    doc_entity_name: str
    code_entity_id: str
    code_entity_name: str
    source: str  # "word_overlap" | "embedding_fallback"


@dataclass
class LinkDecision:
    candidate: Candidate
    outcome: str  # "auto_accept" | "escalated_accept" | "escalated_reject" | "auto_reject"
    confidence: float
    reasoning: str


def _significant_words(name: str) -> set[str]:
    return {w for w in _WORD_RE.findall(name.lower()) if w not in STOPWORDS and len(w) > 2}


def _bow_cosine(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    if intersection == 0:
        return 0.0
    return intersection / math.sqrt(len(a) * len(b))


def _code_entity_names(store: GraphStore) -> list[tuple[str, str]]:
    """(node_id, name) for every code-side entity a doc mention could link to."""
    pairs = []
    for node_type in ("file", "class", "function"):
        for node_id in store.nodes(node_type=node_type, partition="code"):
            pairs.append((node_id, store.get_node(node_id).get("name", "")))
    return pairs


def generate_candidates(store: GraphStore, *, embedding_top_k: int = 3) -> list[Candidate]:
    """Step 1: propose (doc entity, code entity) candidate pairs.

    Word overlap first; embedding-similarity fallback (bag-of-words cosine
    over significant words - documented as a stand-in for a real embedding
    model, same interface either way) only for doc entities that matched
    nothing by word overlap.
    """
    code_entities = _code_entity_names(store)
    code_word_sets = [(node_id, name, _significant_words(name)) for node_id, name in code_entities]

    candidates: list[Candidate] = []
    for doc_entity_id in store.nodes(node_type="doc_entity", partition="doc"):
        doc_name = store.get_node(doc_entity_id).get("name", "")
        doc_words = _significant_words(doc_name)
        if not doc_words:
            continue

        overlap_matches = [
            (node_id, name)
            for node_id, name, code_words in code_word_sets
            if doc_words & code_words
        ]

        if overlap_matches:
            for node_id, name in overlap_matches:
                candidates.append(
                    Candidate(doc_entity_id, doc_name, node_id, name, source="word_overlap")
                )
            continue

        scored = [
            (_bow_cosine(doc_words, code_words), node_id, name)
            for node_id, name, code_words in code_word_sets
        ]
        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda s: s[0], reverse=True)
        for _score, node_id, name in scored[:embedding_top_k]:
            candidates.append(
                Candidate(doc_entity_id, doc_name, node_id, name, source="embedding_fallback")
            )

    return candidates


def _candidate_input_state(candidate: Candidate, store: GraphStore) -> str:
    # Pull one example chunk that mentions this doc entity, for context.
    example_chunk = None
    for node_id in store.predecessors(candidate.doc_entity_id, edge_type="mentions"):
        if store.get_node(node_id).get("type") == "doc_chunk":
            example_chunk = store.get_node(node_id).get("text", "")[:300]
            break

    code_node = store.get_node(candidate.code_entity_id)
    return (
        f"Document entity: \"{candidate.doc_entity_name}\""
        + (f'\nExample usage in policy text: "{example_chunk}"' if example_chunk else "")
        + f"\n\nCode entity: \"{candidate.code_entity_name}\" ({code_node.get('type')} in {code_node.get('file_path', '?')})"
    )


def confirm_candidates(
    candidates: list[Candidate],
    store: GraphStore,
    *,
    laya: LayaClient | None = None,
    llm_client=None,
    llm_model: str = "claude-opus-5",
) -> list[LinkDecision]:
    """Step 2: score every candidate with Laya and resolve per the thresholds table."""
    laya = laya or LayaClient()
    decisions: list[LinkDecision] = []

    for candidate in candidates:
        input_state = _candidate_input_state(candidate, store)
        question = "Do the document entity and the code entity refer to the same real-world concept?"
        result: LayaAnswer = laya.score(input_state, question)

        if result.confidence > AUTO_ACCEPT_THRESHOLD:
            decisions.append(
                LinkDecision(candidate, "auto_accept", result.confidence, result.reasoning)
            )
            store.add_link(
                make_link(
                    candidate.doc_entity_id,
                    candidate.code_entity_id,
                    result.confidence,
                    reason=f"laya:{result.reasoning}",
                )
            )
        elif result.confidence < AUTO_REJECT_THRESHOLD:
            decisions.append(
                LinkDecision(candidate, "auto_reject", result.confidence, result.reasoning)
            )
        else:
            final_confidence, final_reason, accepted = _escalate_to_llm(
                candidate, input_state, client=llm_client, model=llm_model
            )
            outcome = "escalated_accept" if accepted else "escalated_reject"
            decisions.append(LinkDecision(candidate, outcome, final_confidence, final_reason))
            if accepted:
                store.add_link(
                    make_link(
                        candidate.doc_entity_id,
                        candidate.code_entity_id,
                        final_confidence,
                        reason=f"llm:{final_reason}",
                    )
                )

    return decisions


_ESCALATION_SCHEMA = {
    "type": "object",
    "properties": {
        "same_concept": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["same_concept", "confidence", "reasoning"],
    "additionalProperties": False,
}


def _escalate_to_llm(
    candidate: Candidate, input_state: str, *, client=None, model: str
) -> tuple[float, str, bool]:
    """Ambiguous-band final call: a full LLM judges same-concept, no shortcuts."""
    import anthropic

    if client is None:
        client = anthropic.Anthropic()

    response = client.messages.create(
        model=model,
        max_tokens=512,
        system=(
            "You are confirming whether a document entity (from a policy/spec "
            "document) and a code entity (a class, function, or file) refer to "
            "the same real-world concept, for a compliance-drift-detection tool. "
            "A cheap first-pass scorer was unsure, so make the final call."
        ),
        messages=[{"role": "user", "content": input_state}],
        output_config={"format": {"type": "json_schema", "schema": _ESCALATION_SCHEMA}},
    )

    if response.stop_reason == "refusal":
        return 0.0, "llm refused", False

    import json

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        return 0.0, "no text response", False
    data = json.loads(text)
    confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    return confidence, str(data.get("reasoning", "")), bool(data.get("same_concept", False))
