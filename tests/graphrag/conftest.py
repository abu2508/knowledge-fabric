"""Every test under `tests/graphrag/` that touches `GraphStore` talks to a
real Neo4j database - there is no in-memory substitute in this build (see
`knowledge_fabric/graphrag/graphstore.py`).

Set `NEO4J_URI` and `NEO4J_PASSWORD` (a free AuraDB instance from
neo4j.com/cloud/aura works fine) to run these for real. Without it, this
whole directory is skipped with the reason below rather than silently
faked or failing with a confusing connection error.
"""

from __future__ import annotations

import os
import uuid

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip only the tests that actually touch a `GraphStore` (i.e. request
    the `store` fixture, directly or via another fixture that depends on
    it). Pure-function tests in this directory - chunking, table detection,
    noun-phrase extraction, Tree-sitter parsing - need no database and
    always run."""
    if os.environ.get("NEO4J_URI"):
        return
    skip = pytest.mark.skip(
        reason=(
            "NEO4J_URI is not set - this test runs against a real Neo4j "
            "database. Create a free AuraDB instance (neo4j.com/cloud/aura), "
            "export NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD, then re-run."
        )
    )
    for item in items:
        if "store" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)


@pytest.fixture
def ns():
    """A short unique id prefix so a test's nodes don't collide with another
    run's data on the same shared database."""
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def store(ns):
    """A real `GraphStore` against the configured Neo4j database.

    Neo4j (AuraDB Free or otherwise) is shared and persistent - there's no
    cheap per-test "fresh database". Tests namespace every node id they
    create under `ns` (all fixtures in this file and every graphrag test
    use `ns` as their id prefix), and this fixture drops everything under
    that prefix on teardown so repeated runs don't accumulate data.
    """
    from knowledge_fabric.graphrag.graphstore import GraphStore

    gs = GraphStore()
    yield gs
    gs.drop_by_id_token(ns)
    gs.close()
