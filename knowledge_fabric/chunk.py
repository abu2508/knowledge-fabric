"""Split documents into overlapping text chunks.

Chunking is word-based with a configurable size and overlap. It's simple on
purpose - if a document's structure matters (headings, code blocks, tables),
add a smarter splitter here while keeping the same `Chunk` output shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from knowledge_fabric.ingest import Document

DEFAULT_CHUNK_SIZE = 200  # words per chunk
DEFAULT_OVERLAP = 40  # words shared between consecutive chunks


@dataclass
class Chunk:
    """A chunk of text from a single source document."""

    chunk_id: str
    text: str
    doc_id: str
    source: str
    metadata: dict = field(default_factory=dict)


def chunk_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """Split `text` into overlapping word-based chunks."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    words = text.split()
    if not words:
        return []

    step = chunk_size - overlap
    chunks = []
    for start in range(0, len(words), step):
        piece = words[start : start + chunk_size]
        if not piece:
            break
        chunks.append(" ".join(piece))
        if start + chunk_size >= len(words):
            break
    return chunks


def chunk_documents(
    documents: list[Document],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Chunk every document in `documents` into a flat list of `Chunk`s."""
    chunks: list[Chunk] = []
    for doc in documents:
        pieces = chunk_text(doc.text, chunk_size=chunk_size, overlap=overlap)
        for i, piece in enumerate(pieces):
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}#{i}",
                    text=piece,
                    doc_id=doc.doc_id,
                    source=doc.source,
                )
            )
    return chunks
