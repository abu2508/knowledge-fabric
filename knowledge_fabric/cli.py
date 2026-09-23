"""Command-line glue: ingest -> chunk -> store -> retrieve -> answer.

Usage:
    python -m knowledge_fabric.cli <docs_dir_or_file> "<question>" [--k N]
"""

from __future__ import annotations

import argparse
import sys

from knowledge_fabric.chunk import chunk_documents
from knowledge_fabric.ingest import load_documents
from knowledge_fabric.store import KnowledgeStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="File or directory of documents to index")
    parser.add_argument("question", help="Question to answer")
    parser.add_argument("--k", type=int, default=4, help="Number of chunks to retrieve")
    parser.add_argument(
        "--no-answer",
        action="store_true",
        help="Only show retrieved chunks; skip the Claude API call",
    )
    args = parser.parse_args(argv)

    documents = load_documents(args.path)
    if not documents:
        print(f"No documents found under {args.path!r}", file=sys.stderr)
        return 1

    chunks = chunk_documents(documents)
    store = KnowledgeStore()
    store.add(chunks)

    top_chunks = store.search(args.question, k=args.k)
    if not top_chunks:
        print("No relevant chunks found for that question.")
        return 0

    print(f"Retrieved {len(top_chunks)} chunk(s):\n")
    for i, chunk in enumerate(top_chunks, start=1):
        print(f"[{i}] ({chunk.source}) {chunk.text[:200]}...\n")

    if args.no_answer:
        return 0

    from knowledge_fabric.qa import answer_question

    answer = answer_question(args.question, [c.text for c in top_chunks])
    print("Answer:\n")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
