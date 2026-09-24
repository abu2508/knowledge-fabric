"""Laya: Convai Innovations' "System 1" decision model - non-autoregressive,
calibrated typed decisions in a single forward pass. Used for two jobs in
the spec:

  - Phase 2 linking: confirm whether a candidate (doc entity, code entity)
    pair refers to the same concept.
  - Phase 3 retrieval: at each traversal hop, confirm whether continuing is
    still relevant to the query.

`laya` is a real PyPI package (`pip install laya`, `laya.Agent`). Both
`LayaClient` (this module's default) and the fallback below present the
same interface - `score(input_state, question) -> LayaAnswer(answer,
confidence, reasoning)` - so `linking.py`/`retrieval.py` don't care which
one they're holding.

**Not yet exercised in this build environment**: `Agent(...)` downloads its
checkpoint from Hugging Face (`convaiinnovations/laya`) on first use, and
this environment's network policy blocks `huggingface.co`. The
`laya`-package-based path below is written against the installed package's
real API (verified: `Agent.predict`/`system_one`, the `noul` question type
for a calibrated P(true) on a yes/no question - see
`laya/agent.py::_decode_answers`), but hasn't run end to end here. Once
network access to Hugging Face (or a locally cached checkpoint / a mounted
model path passed as `model_id_or_path`) is available, no code change
should be needed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_LAYA_MODEL = os.environ.get("KNOWLEDGE_FABRIC_LAYA_MODEL", "convaiinnovations/laya")
DEFAULT_HAIKU_MODEL = os.environ.get("KNOWLEDGE_FABRIC_LAYA_HAIKU_MODEL", "claude-haiku-4-5")

_HAIKU_SCHEMA = {
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
    """The real Laya model (`laya.Agent`), via a single `"noul"` (binary,
    calibrated-P(true)) typed question per call.

    Lazy-loads the checkpoint on first `.score()` call, not at construction,
    so building a `LayaClient()` never touches the network by itself - only
    using it does.
    """

    def __init__(
        self,
        model_id_or_path: str = DEFAULT_LAYA_MODEL,
        *,
        device: str | None = None,
        agent=None,
    ) -> None:
        self.model_id_or_path = model_id_or_path
        self.device = device
        self._agent = agent

    def _get_agent(self):
        if self._agent is None:
            from laya import Agent

            self._agent = Agent(self.model_id_or_path, device=self.device)
        return self._agent

    def score(self, input_state: str, question: str) -> LayaAnswer:
        """Answer a single typed yes/no `question` about `input_state`.

        Maps to Laya's `"noul"` question type: the model returns a
        calibrated P(true) in one forward pass, no free-form generation -
        so `reasoning` here is a compact summary of the model's own output
        (P(true) and its action-head probability), not a chain of thought.
        """
        agent = self._get_agent()
        result = agent.predict(
            input_state,
            questions={"decision": {"type": "noul", "instructions": question}},
        )
        ans = result["answers"]["decision"]
        p_true = float(ans["noul"])
        action_prob = ans.get("action", {}).get("act_probability")
        reasoning = f"P(true)={p_true:.2f}"
        if action_prob is not None:
            reasoning += f", action_probability={action_prob:.2f}"
        return LayaAnswer(answer=p_true >= 0.5, confidence=p_true, reasoning=reasoning)


class HaikuLayaClient:
    """Same `score()` interface as `LayaClient`, backed by a structured-output
    call to Claude Haiku 4.5 instead of the real Laya model.

    Use this where the real Laya checkpoint isn't reachable (e.g. this
    build environment's network policy blocks `huggingface.co`) or where a
    hosted-API dependency is preferred over a local model. It is *not* the
    default - `LayaClient` (the real package) is - because it has
    different latency, cost, and calibration characteristics; swap between
    them by passing whichever one to `linking.confirm_candidates`/
    `retrieval.retrieve`'s `laya=` argument.
    """

    def __init__(self, *, model: str = DEFAULT_HAIKU_MODEL, client=None) -> None:
        self.model = model
        self._client = client

    def _anthropic_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def score(self, input_state: str, question: str) -> LayaAnswer:
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
            output_config={"format": {"type": "json_schema", "schema": _HAIKU_SCHEMA}},
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
