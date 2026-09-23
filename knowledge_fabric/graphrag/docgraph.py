"""Phase 1, document path: chunk prose, pull out tables intact, extract
noun phrases, and build a document graph where co-occurring phrases in the
same chunk become edges.

Chunking: recursive character splitting (paragraph -> sentence -> word
boundaries), the default per the spec. Tables are detected and extracted
*before* the splitter runs so a row is never split across chunks; each
table becomes its own chunk (short caption + the table as markdown).

Noun-phrase extraction: the spec calls for "spaCy-style" grammar-based
phrase-span pulling - mechanical, not LLM summarization. This module
implements that mechanically with a POS-free heuristic (stopword-filtered
runs of word tokens) rather than a spaCy pipeline, because this
environment's network policy blocks downloading a spaCy language model
(spaCy itself is installable from PyPI, but `en_core_web_sm` is not). The
extraction interface (`extract_noun_phrases`) is the seam: swap in a real
spaCy noun_chunks pass here without touching chunking, tables, or the
graph-building code below it.

**Checkpoint the spec calls for and this build has not yet done**: manually
eyeball the noun phrases this extractor pulls from real OpenProject policy
sentences before trusting the linking layer built on top of it. Run
`review_noun_phrases()` against real ingested policy text once it's
available (see the repo README for why it isn't yet) and read the output
before relying on Phase 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from knowledge_fabric.graphrag.graphstore import GraphStore
from knowledge_fabric.ingest import Document

# Generic/stopword terms stripped before treating a run of words as a noun
# phrase candidate. Deliberately the same spirit of list as the linking
# layer's stopword list (knowledge_fabric.graphrag.linking.STOPWORDS) but
# kept separate: this one is about what makes a *phrase*, that one is about
# what makes two phrases *the same concept*.
_STOPWORDS = {
    "a", "an", "the", "this", "that", "these", "those", "is", "are", "was",
    "were", "be", "been", "being", "of", "in", "on", "at", "to", "for",
    "with", "as", "by", "and", "or", "but", "if", "then", "than", "so",
    "not", "no", "it", "its", "we", "you", "they", "he", "she", "shall",
    "will", "must", "may", "can", "should", "would", "has", "have", "had",
}

_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")

# A run of 2-4 word tokens, none of them stopwords, is treated as a
# candidate noun phrase. Short enough to stay precise, long enough to catch
# multi-word policy terms like "data retention period".
_MIN_PHRASE_WORDS = 2
_MAX_PHRASE_WORDS = 4


@dataclass
class TableBlock:
    """A table extracted intact from prose, before chunking splits anything."""

    markdown: str
    caption: str
    start: int
    end: int


@dataclass
class DocChunk:
    """One retrievable unit from the document path: prose text or a table."""

    chunk_id: str
    text: str
    doc_id: str
    source: str
    is_table: bool = False
    entities: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Table detection
# ---------------------------------------------------------------------------


def detect_tables(text: str, *, caption_fn=None) -> tuple[list[TableBlock], str]:
    """Pull markdown-style tables out of `text` before it goes to the splitter.

    Returns `(tables, remaining_text)` where `remaining_text` has each table
    block removed (replaced with nothing, so surrounding prose still joins
    cleanly). `caption_fn(markdown) -> str` generates the short caption for
    each table; defaults to a mechanical one-liner (first row's cells) so
    this stays usable with no LLM call. Pass an LLM-backed `caption_fn` (see
    `knowledge_fabric.graphrag.laya` for the client pattern) to match the
    spec's "short LLM-generated caption" exactly.
    """
    if caption_fn is None:
        caption_fn = _mechanical_caption

    lines = text.split("\n")
    tables: list[TableBlock] = []
    kept_lines: list[str] = []
    i = 0
    offset = 0
    while i < len(lines):
        line = lines[i]
        if _TABLE_ROW_RE.match(line):
            block_lines = [line]
            j = i + 1
            while j < len(lines) and (_TABLE_ROW_RE.match(lines[j]) or _is_separator_row(lines[j])):
                block_lines.append(lines[j])
                j += 1
            markdown = "\n".join(block_lines)
            tables.append(
                TableBlock(
                    markdown=markdown,
                    caption=caption_fn(markdown),
                    start=offset,
                    end=offset + len(markdown),
                )
            )
            i = j
        else:
            kept_lines.append(line)
            offset += len(line) + 1
            i += 1
    return tables, "\n".join(kept_lines)


def _is_separator_row(line: str) -> bool:
    return bool(re.match(r"^\s*\|?[\s:|-]+\|?\s*$", line)) and "-" in line


def _mechanical_caption(markdown: str) -> str:
    first_line = markdown.strip().split("\n", 1)[0]
    cells = [c.strip() for c in first_line.strip("|").split("|") if c.strip()]
    return f"Table: {', '.join(cells)}" if cells else "Table"


# ---------------------------------------------------------------------------
# Recursive character splitting
# ---------------------------------------------------------------------------


def recursive_split(
    text: str,
    *,
    chunk_size: int = 1000,
    separators: tuple[str, ...] = ("\n\n", ". ", " "),
) -> list[str]:
    """Split `text` at the first separator that yields small-enough pieces,
    recursing into any still-oversized piece with the next separator.

    This is the paragraph -> sentence -> word boundary strategy from the
    spec: try paragraph breaks first, fall back to sentence breaks, then to
    whitespace, only splitting as finely as each piece actually needs.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size or not separators:
        return [text]

    sep, rest = separators[0], separators[1:]
    parts = [p for p in text.split(sep) if p.strip()]
    if len(parts) <= 1:
        # This separator didn't split anything - try the next one directly.
        return recursive_split(text, chunk_size=chunk_size, separators=rest)

    chunks: list[str] = []
    buffer = ""
    for part in parts:
        candidate = f"{buffer}{sep}{part}" if buffer else part
        if len(candidate) <= chunk_size:
            buffer = candidate
        else:
            if buffer:
                chunks.append(buffer)
            if len(part) <= chunk_size:
                buffer = part
            else:
                chunks.extend(recursive_split(part, chunk_size=chunk_size, separators=rest))
                buffer = ""
    if buffer:
        chunks.append(buffer)
    return chunks


# ---------------------------------------------------------------------------
# Noun-phrase extraction (see module docstring for the spaCy caveat)
# ---------------------------------------------------------------------------


def extract_noun_phrases(text: str) -> list[str]:
    """Pull candidate noun phrases out of `text` via stopword-filtered word runs.

    Mechanical span extraction, not summarization: every returned phrase is
    a contiguous substring of the input. See the module docstring for how
    this stands in for a spaCy noun_chunks pass.
    """
    tokens = _WORD_RE.findall(text)
    phrases: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if _MIN_PHRASE_WORDS <= len(run) <= _MAX_PHRASE_WORDS:
            phrases.append(" ".join(run))
        elif len(run) > _MAX_PHRASE_WORDS:
            # Slide a window rather than dropping a long run entirely.
            for start in range(0, len(run) - _MIN_PHRASE_WORDS + 1):
                window = run[start : start + _MAX_PHRASE_WORDS]
                if len(window) >= _MIN_PHRASE_WORDS:
                    phrases.append(" ".join(window))

    for tok in tokens:
        if tok.lower() in _STOPWORDS:
            flush()
            run = []
        else:
            run.append(tok)
    flush()

    # De-duplicate case-insensitively, preserving first-seen casing/order.
    seen: dict[str, str] = {}
    for phrase in phrases:
        key = phrase.lower()
        seen.setdefault(key, phrase)
    return list(seen.values())


def review_noun_phrases(text: str) -> None:
    """Print extracted phrases for the manual-eyeball checkpoint the spec calls for.

    Not used by the pipeline itself - a standalone tool to run against real
    policy text before trusting Phase 2 on top of it.
    """
    for phrase in extract_noun_phrases(text):
        print(phrase)


# ---------------------------------------------------------------------------
# Graph building
# ---------------------------------------------------------------------------


def build_document_graph(
    documents: list[Document],
    store: GraphStore,
    *,
    chunk_size: int = 1000,
    caption_fn=None,
) -> list[DocChunk]:
    """Chunk `documents`, extract entities, and populate `store`'s doc partition.

    Node types: `doc_chunk`, `doc_entity`. Edge type: `co_occurs`, between
    every pair of entities extracted from the same chunk (undirected in
    spirit; stored as edges in both directions so traversal doesn't care
    which side it entered from).
    """
    all_chunks: list[DocChunk] = []

    for doc in documents:
        tables, remaining_text = detect_tables(doc.text, caption_fn=caption_fn)
        pieces: list[tuple[str, bool]] = [(t.markdown, True) for t in tables]
        for i, table in enumerate(tables):
            pieces[i] = (f"{table.caption}\n\n{table.markdown}", True)
        for piece in recursive_split(remaining_text, chunk_size=chunk_size):
            pieces.append((piece, False))

        for i, (text, is_table) in enumerate(pieces):
            chunk_id = f"doc:{doc.doc_id}#{i}"
            entities = extract_noun_phrases(text)
            chunk = DocChunk(
                chunk_id=chunk_id,
                text=text,
                doc_id=doc.doc_id,
                source=doc.source,
                is_table=is_table,
                entities=entities,
            )
            all_chunks.append(chunk)

            store.add_node(
                chunk_id,
                "doc_chunk",
                partition="doc",
                text=text,
                doc_id=doc.doc_id,
                source=doc.source,
                is_table=is_table,
            )

            entity_ids = []
            for entity in entities:
                entity_id = f"doc_entity:{entity.lower()}"
                if not store.has_node(entity_id):
                    store.add_node(entity_id, "doc_entity", partition="doc", name=entity)
                store.add_edge(chunk_id, entity_id, "mentions")
                entity_ids.append(entity_id)

            for a in range(len(entity_ids)):
                for b in range(a + 1, len(entity_ids)):
                    store.add_edge(entity_ids[a], entity_ids[b], "co_occurs", chunk_id=chunk_id)
                    store.add_edge(entity_ids[b], entity_ids[a], "co_occurs", chunk_id=chunk_id)

    return all_chunks
