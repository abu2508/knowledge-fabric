"""Laya-style typed-decision scoring: a cheap, fast, calibrated-probability
answer to a yes/no question about some input state - no free-form prose to
parse.

Laya itself (Convai Innovations' "System 1" decision model: input state +
typed questions in, structured answers + calibrated probabilities out, in
one fast forward pass) isn't available as a callable service in this
environment. `LayaClient` stands in for it with the same interface -
`score(input_state, question) -> LayaAnswer(confidence, answer, reasoning)`
- implemented as a single structured-output call to Claude Haiku 4.5
(cheap, fast, no thinking needed for a single typed judgment). Swap the
class body for a real Laya call later; every caller in this package
(`linking.py`, `retrieval.py`) only depends on this interface.

Used for two jobs in the spec:
  - Phase 2 linking: confirm whether a candidate (doc entity, code entity)
    pair refers to the same concept.
  - Phase 3 retrieval: at each traversal hop, confirm whether continuing is
    still relevant to the query.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL = os.environ.get("KNOWLEDGE_FABRIC_LAYA_MODEL", "claude-haiku-4-5")

_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "boolean"},
        "confidence": {
            "type": "number",
            "description": "Calibrated probability, 0.0-1.0, that `answer` is correct.",
        },
        "reasoning": {"type": "string", "description": "One short sentence."},
    },
    "required": ["answer", "confidence", "reasoning"],
    "additionalProperties": False,
}


@dataclass
class LayaAnswer:
    answer: bool
    confidence: float
    reasoning: str = ""


class LayaClient:
    """Typed-decision scorer. See module docstring for what stands behind it."""

    def __init__(self, *, model: str = DEFAULT_MODEL, client=None) -> None:
        self.model = model
        self._client = client

    def _anthropic_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def score(self, input_state: str, question: str) -> LayaAnswer:
        """Answer a single typed yes/no `question` about `input_state`.

        `input_state` is any short text description of what's being judged
        (an email, a ticket, or here: a candidate entity pair or a
        traversal frontier). Returns a calibrated confidence in [0, 1].
        """
        client = self._anthropic_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=256,
            system=(
                "You are a fast, calibrated typed-decision scorer. Given an input "
                "state and a yes/no question, answer with a boolean, a calibrated "
                "confidence between 0 and 1, and one short reason. Be honest about "
                "uncertainty - a well-calibrated 0.55 is more useful than a "
                "falsely confident 0.95."
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"Input state:\n{input_state}\n\nQuestion: {question}",
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )

        if response.stop_reason == "refusal":
            return LayaAnswer(answer=False, confidence=0.0, reasoning="refused")

        import json

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            return LayaAnswer(answer=False, confidence=0.0, reasoning="no text response")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return LayaAnswer(answer=False, confidence=0.0, reasoning="unparseable response")

        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        return LayaAnswer(
            answer=bool(data.get("answer", False)),
            confidence=confidence,
            reasoning=str(data.get("reasoning", "")),
        )
