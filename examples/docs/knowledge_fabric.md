# About Knowledge Fabric

Knowledge Fabric is a small retrieval-augmented generation starter kit. It
ingests documents from a folder, splits them into overlapping chunks, and
stores those chunks in memory. The store ranks chunks against a question
using a lightweight TF-IDF word-overlap scorer, so no embedding model or
vector database is required to get started.

When you ask a question, the top-ranked chunks are handed to Claude, which
answers using only that retrieved context and cites which chunks it used.

## Why "fabric"?

The project treats retrieval as one small, swappable layer in a larger
weave: ingestion, chunking, storage, retrieval, and generation are each
independent modules that only agree on simple data shapes (`Document`,
`Chunk`). Any layer can be replaced - a real embedding model, a vector
database, a smarter chunker - without touching the others.
