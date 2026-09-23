"""Load documents from disk.

Keeps ingestion deliberately dumb: read text-like files from a directory
into `Document` objects. Swap this out (or add a loader) for PDFs, HTML,
databases, etc. as needed - `chunk.py` and everything downstream only
depend on the `Document` shape.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EXTENSIONS = (".txt", ".md")


@dataclass
class Document:
    """A single loaded document."""

    doc_id: str
    text: str
    source: str
    metadata: dict = field(default_factory=dict)


def load_documents(
    path: str | os.PathLike,
    *,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
) -> list[Document]:
    """Load every matching file under `path` into a list of `Document`s.

    `path` may be a single file or a directory (searched recursively).
    """
    root = Path(path)
    if root.is_file():
        files = [root]
    else:
        files = sorted(
            p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in extensions
        )

    documents: list[Document] = []
    for file_path in files:
        text = file_path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        documents.append(
            Document(
                doc_id=str(file_path.relative_to(root) if root.is_dir() else file_path.name),
                text=text,
                source=str(file_path),
            )
        )
    return documents
