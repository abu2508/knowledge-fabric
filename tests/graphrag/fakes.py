"""Test doubles for Laya and the Anthropic client, so linking/retrieval
tests exercise real threshold and traversal logic without a network call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from knowledge_fabric.graphrag.laya import LayaAnswer


class FakeLaya:
    """Scores by checking whether any of a rule's substrings appear in the
    input state (case-insensitive), first match wins; falls back to a
    default confidence."""

    def __init__(self, rules: list[tuple[list[str], float]], *, default: float = 0.5) -> None:
        self.rules = rules
        self.default = default
        self.calls: list[str] = []

    def score(self, input_state: str, question: str) -> LayaAnswer:
        self.calls.append(input_state)
        lowered = input_state.lower()
        for substrings, confidence in self.rules:
            if all(s.lower() in lowered for s in substrings):
                return LayaAnswer(answer=confidence >= 0.5, confidence=confidence, reasoning="fake rule match")
        return LayaAnswer(answer=self.default >= 0.5, confidence=self.default, reasoning="fake default")


@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeResponse:
    content: list[_FakeTextBlock]
    stop_reason: str = "end_turn"


class FakeAnthropicClient:
    """Stands in for `anthropic.Anthropic()`. `messages.create(...)` returns
    a canned structured-output payload from `responses`, consumed in order."""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self._responses.pop(0) if self._responses else {}
        return _FakeResponse(content=[_FakeTextBlock(text=json.dumps(payload))])
