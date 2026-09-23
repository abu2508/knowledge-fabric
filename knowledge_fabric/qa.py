"""Answer a question from retrieved context chunks, via the Claude API.

This module is optional: only import it (and install `anthropic`) if you
want LLM-generated answers. Ingestion, chunking, and retrieval work fine
without it.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

# Override via the KNOWLEDGE_FABRIC_MODEL env var if needed.
DEFAULT_MODEL = os.environ.get("KNOWLEDGE_FABRIC_MODEL", "claude-opus-5")

SYSTEM_PROMPT = (
    "You are a precise research assistant. Answer the user's question using "
    "only the provided context chunks. If the context does not contain the "
    "answer, say so plainly instead of guessing. Cite chunks you rely on "
    "like [1], [2]."
)


def _build_context_block(chunks: Sequence[str]) -> str:
    return "\n\n".join(f"[{i + 1}] {chunk}" for i, chunk in enumerate(chunks))


def answer_question(
    question: str,
    chunks: Sequence[str],
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2048,
    client=None,
) -> str:
    """Answer `question` using `chunks` as retrieved context.

    Requires the `anthropic` package and a resolvable credential
    (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or an `ant auth login`
    profile). Pass `client` to reuse an existing `anthropic.Anthropic()`
    instance (e.g. for connection pooling or custom retry settings).
    """
    import anthropic  # imported lazily so this module stays optional

    if not chunks:
        return "No context was retrieved for this question, so I can't answer it."

    if client is None:
        client = anthropic.Anthropic()

    context_block = _build_context_block(chunks)
    user_message = f"Context:\n{context_block}\n\nQuestion: {question}"

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    for block in response.content:
        if block.type == "text":
            return block.text
    return ""
