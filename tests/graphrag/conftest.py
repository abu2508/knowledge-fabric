"""Every test under `tests/graphrag/` that touches `GraphStore` talks to a
real Amazon Neptune cluster - there is no in-memory substitute in this
build (see `knowledge_fabric/graphrag/graphstore.py`).

Set `NEPTUNE_ENDPOINT` (and `AWS_REGION`, and AWS credentials the normal
boto3 way) to a cluster provisioned via `infra/` to run these for real.
Without it, this whole directory is skipped with the reason below rather
than silently faked or failing with a confusing connection error.
"""

from __future__ import annotations

import os
import uuid

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip only the tests that actually touch a `GraphStore` (i.e. request
    the `store` fixture, directly or via another fixture that depends on
    it). Pure-function tests in this directory - chunking, table detection,
    noun-phrase extraction, Tree-sitter parsing - need no cluster and
    always run."""
    if os.environ.get("NEPTUNE_ENDPOINT"):
        return
    skip = pytest.mark.skip(
        reason=(
            "NEPTUNE_ENDPOINT is not set - this test runs against a real "
            "Neptune cluster. Provision one with infra/ (see infra/README.md), "
            "export NEPTUNE_ENDPOINT/AWS_REGION and AWS credentials, then "
            "re-run."
        )
    )
    for item in items:
        if "store" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)


@pytest.fixture
def ns():
    """A short unique id prefix so a test's nodes don't collide with another
    run's data on the same shared cluster."""
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def store(ns):
    """A real `GraphStore` against the configured Neptune cluster.

    Neptune is shared and persistent - there's no cheap per-test "fresh
    database". Tests namespace every node id they create under `ns` (all
    fixtures in this file and every graphrag test use `ns` as their id
    prefix), and this fixture drops everything under that prefix on
    teardown so repeated runs don't accumulate data on the cluster.
    """
    from knowledge_fabric.graphrag.graphstore import GraphStore

    gs = GraphStore()
    yield gs
    gs.drop_by_id_token(ns)
    gs.close()
