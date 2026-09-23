"""In-memory chunk store with a lightweight keyword-overlap retriever.

No ML dependencies are required to get results. The scoring here is a
simple TF-style word-overlap ranker, good enough to prototype with and to
sanity-check the rest of the pipeline. `KnowledgeStore` exposes just
`add()` and `search()`, so a real embedding-based store (numpy vectors,
`sentence-transformers`, a hosted embeddings API, a vector DB) can be
dropped in behind the same interface without touching `qa.py` or `cli.py`.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from knowledge_fabric.chunk import Chunk

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


class KnowledgeStore:
    """Holds chunks in memory and ranks them against a query.

    Uses TF-IDF-weighted cosine similarity over a bag-of-words
    representation - no external dependencies, and reasonable results for
    small-to-medium document sets.
    """

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._term_freqs: list[Counter] = []
        self._doc_freq: Counter = Counter()

    def add(self, chunks: list[Chunk]) -> None:
        """Index a batch of chunks."""
        for chunk in chunks:
            terms = _tokenize(chunk.text)
            tf = Counter(terms)
            self._chunks.append(chunk)
            self._term_freqs.append(tf)
            for term in tf:
                self._doc_freq[term] += 1

    def __len__(self) -> int:
        return len(self._chunks)

    def _idf(self, term: str) -> float:
        n = len(self._chunks)
        df = self._doc_freq.get(term, 0)
        # Smoothed IDF; stays positive even for terms seen in every chunk.
        return math.log((n + 1) / (df + 1)) + 1

    def _score(self, query_terms: list[str], tf: Counter) -> float:
        if not tf:
            return 0.0
        dot = 0.0
        query_counts = Counter(query_terms)
        for term, qcount in query_counts.items():
            if term not in tf:
                continue
            idf = self._idf(term)
            dot += qcount * idf * tf[term] * idf
        if dot == 0.0:
            return 0.0
        query_norm = math.sqrt(
            sum((qcount * self._idf(term)) ** 2 for term, qcount in query_counts.items())
        )
        doc_norm = math.sqrt(sum((count * self._idf(term)) ** 2 for term, count in tf.items()))
        if query_norm == 0.0 or doc_norm == 0.0:
            return 0.0
        return dot / (query_norm * doc_norm)

    def search(self, query: str, *, k: int = 5) -> list[Chunk]:
        """Return the `k` chunks most relevant to `query`, best first."""
        query_terms = _tokenize(query)
        if not query_terms or not self._chunks:
            return []

        scored = [
            (self._score(query_terms, tf), chunk)
            for tf, chunk in zip(self._term_freqs, self._chunks)
        ]
        scored = [pair for pair in scored if pair[0] > 0]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [chunk for _score, chunk in scored[:k]]
