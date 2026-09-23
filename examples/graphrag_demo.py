"""End-to-end GraphRAG demo: index -> link -> query, against the small
fixture policy doc + Ruby service in tests/fixtures/.

This is a demo of the *pipeline*, not the OpenProject benchmark the build
spec targets - see the repo README's GraphRAG section for why real
OpenProject data isn't pulled here. The fixture is deliberately built to
contain one real drift case (a 90-day policy vs. a 30-day hardcoded
constant) so a successful run demonstrates the thing the spec is actually
for.

Requires:
  - A reachable Neptune cluster (NEPTUNE_ENDPOINT / AWS_REGION / AWS
    credentials - see infra/README.md)
  - ANTHROPIC_API_KEY, for Laya (Claude Haiku 4.5) and the final answer
    (Claude Opus 5)

Usage:
    python examples/graphrag_demo.py
"""

from __future__ import annotations

from pathlib import Path

from knowledge_fabric.graphrag.graphstore import GraphStore
from knowledge_fabric.graphrag.pipeline import index, link, query

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"


def main() -> None:
    store = GraphStore()
    try:
        print("Indexing (Phase 1)...")
        index(FIXTURES / "docs", FIXTURES / "code", store=store)
        print(f"  {store.stats()}")

        print("Linking (Phase 2)...")
        decisions = link(store)
        for d in decisions:
            print(f"  {d.outcome:<18} {d.candidate.doc_entity_name!r} <-> "
                  f"{d.candidate.code_entity_name!r} ({d.confidence:.2f})")

        print("\nQuerying (Phase 3)...")
        question = "Does the code enforce the retention period the policy claims?"
        print(f"  Q: {question}")
        print(f"  A: {query(question, store)}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
