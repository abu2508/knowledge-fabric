"""Graph storage backed by Neo4j (AuraDB Free or self-hosted) via the
official driver and Cypher.

One `GraphStore` instance backs the whole fabric: the document graph and
the code graph are stored as two logical partitions of the same underlying
Neo4j graph (every node carries a `partition` property, `"doc"` or
`"code"`), and Phase 2's cross-reference edges are added directly onto it
as a distinct relationship type (`links_to`). This matches the spec's
"three separate stores... plus a thin linking layer" at the logical level
(each partition is queried and reasoned about independently) while keeping
link storage literally "in the existing graph store" rather than standing
up a fourth database.

Connection: standard Neo4j Bolt driver auth. Configure via environment
variables:

    NEO4J_URI       required - e.g. neo4j+s://xxxxxxxx.databases.neo4j.io
    NEO4J_USER      optional - defaults to "neo4j"
    NEO4J_PASSWORD  required

An AuraDB Free instance (neo4j.com/cloud/aura) has a public endpoint, so
unlike the Neptune/VPC path this module previously used, no bastion or
VPC peering is needed to reach it from outside AWS.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any

from neo4j import GraphDatabase

# Relationship type used for confirmed cross-reference links between the
# document graph and the code graph (Phase 2 of the spec). Kept distinct
# from structural edges (contains/calls/co_occurs/mentions/imports) so
# traversal code can tell a cross-graph hop from a same-graph one.
LINK_EDGE_TYPE = "links_to"

_RESERVED_KEYS = {"type", "partition"}
_NODE_LABEL = "Entity"
# Every edge_type this codebase uses is interpolated into Cypher query text
# (Cypher relationship types can't be query-parametrized) - validated
# against this pattern first as a defense-in-depth check, even though every
# caller in this package only ever passes one of a small fixed set of
# internal literals (mentions/co_occurs/contains/calls/imports/links_to).
_SAFE_EDGE_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Link:
    """A confirmed cross-reference edge between a doc entity and a code entity."""

    source: str
    target: str
    confidence: float
    timestamp: float
    reason: str = ""


class Neo4jNotConfiguredError(RuntimeError):
    """Raised when `NEO4J_URI`/`NEO4J_PASSWORD` aren't set and no driver was passed explicitly."""


def _check_edge_type(edge_type: str) -> str:
    if not _SAFE_EDGE_TYPE.match(edge_type):
        raise ValueError(f"Unsafe edge_type for Cypher interpolation: {edge_type!r}")
    return edge_type


def connect(
    *,
    uri: str | None = None,
    user: str | None = None,
    password: str | None = None,
):
    """Open a Neo4j driver from `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`
    when the matching argument isn't passed explicitly.

    Raises `Neo4jNotConfiguredError` if no URI/password is available
    anywhere - there is no local/in-memory fallback in this build.
    """
    uri = uri or os.environ.get("NEO4J_URI")
    if not uri:
        raise Neo4jNotConfiguredError(
            "NEO4J_URI is not set. This build stores graphs in Neo4j only - "
            "create a free AuraDB instance (neo4j.com/cloud/aura) and set "
            "NEO4J_URI to its connection URI."
        )
    user = user or os.environ.get("NEO4J_USER", "neo4j")
    password = password or os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise Neo4jNotConfiguredError("NEO4J_PASSWORD is not set.")

    return GraphDatabase.driver(uri, auth=(user, password))


class GraphStore:
    """Thin wrapper over a Neo4j driver, issuing Cypher.

    Every write is an upsert (`MERGE` - safe to call `add_node`/`add_edge`
    again for the same id; properties are overwritten, not duplicated), so
    re-running indexing is idempotent.
    """

    def __init__(self, driver=None, *, name: str = "graph") -> None:
        self.name = name
        self.driver = driver or connect()
        self._ensure_constraint()

    def _ensure_constraint(self) -> None:
        with self.driver.session() as session:
            session.run(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{_NODE_LABEL}) "
                "REQUIRE n.id IS UNIQUE"
            )

    def close(self) -> None:
        self.driver.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- nodes ----------------------------------------------------------

    def add_node(self, node_id: str, node_type: str, *, partition: str, **attrs: Any) -> None:
        """Upsert a node. `partition` is `"doc"` or `"code"` (see module docstring)."""
        props = {k: v for k, v in attrs.items() if k not in _RESERVED_KEYS}
        with self.driver.session() as session:
            session.run(
                f"MERGE (n:{_NODE_LABEL} {{id: $id}}) "
                "SET n.type = $type, n.partition = $partition, n += $props",
                id=node_id,
                type=node_type,
                partition=partition,
                props=props,
            )

    def has_node(self, node_id: str) -> bool:
        with self.driver.session() as session:
            result = session.run(f"MATCH (n:{_NODE_LABEL} {{id: $id}}) RETURN n LIMIT 1", id=node_id)
            return result.single() is not None

    def get_node(self, node_id: str) -> dict:
        with self.driver.session() as session:
            result = session.run(f"MATCH (n:{_NODE_LABEL} {{id: $id}}) RETURN properties(n) AS props", id=node_id)
            record = result.single()
            if record is None:
                raise KeyError(node_id)
            return dict(record["props"])

    def nodes(self, node_type: str | None = None, partition: str | None = None) -> list[str]:
        query = (
            f"MATCH (n:{_NODE_LABEL}) "
            "WHERE ($type IS NULL OR n.type = $type) "
            "AND ($partition IS NULL OR n.partition = $partition) "
            "RETURN n.id AS id"
        )
        with self.driver.session() as session:
            result = session.run(query, type=node_type, partition=partition)
            return [record["id"] for record in result]

    # -- structural edges (contains, calls, co_occurs, imports, mentions) --

    def add_edge(self, source: str, target: str, edge_type: str, **attrs: Any) -> None:
        """Upsert a directed edge typed `edge_type` from `source` to `target`."""
        edge_type = _check_edge_type(edge_type)
        with self.driver.session() as session:
            session.run(
                f"MATCH (a:{_NODE_LABEL} {{id: $source}}), (b:{_NODE_LABEL} {{id: $target}}) "
                f"MERGE (a)-[r:{edge_type}]->(b) "
                "SET r += $props",
                source=source,
                target=target,
                props=attrs,
            )

    def edges(self, node_id: str, edge_type: str | None = None) -> list[tuple[str, str, dict]]:
        """Outgoing edges from `node_id`, optionally filtered by type."""
        rel_pattern = f"[r:{_check_edge_type(edge_type)}]" if edge_type else "[r]"
        query = (
            f"MATCH (n:{_NODE_LABEL} {{id: $id}})-{rel_pattern}->(m) "
            "RETURN m.id AS target, properties(r) AS props, type(r) AS edge_type"
        )
        out = []
        with self.driver.session() as session:
            result = session.run(query, id=node_id)
            for record in result:
                props = dict(record["props"])
                props["edge_type"] = record["edge_type"]
                out.append((node_id, record["target"], props))
        return out

    def neighbors(self, node_id: str, edge_type: str | None = None) -> list[str]:
        return [target for _src, target, _data in self.edges(node_id, edge_type=edge_type)]

    def predecessors(self, node_id: str, edge_type: str | None = None) -> list[str]:
        """Nodes with an edge (optionally of `edge_type`) pointing into `node_id`."""
        rel_pattern = f"[r:{_check_edge_type(edge_type)}]" if edge_type else "[r]"
        query = f"MATCH (n:{_NODE_LABEL} {{id: $id}})<-{rel_pattern}-(m) RETURN m.id AS source"
        with self.driver.session() as session:
            result = session.run(query, id=node_id)
            return [record["source"] for record in result]

    # -- cross-reference links (Phase 2 linking layer) -------------------

    def add_link(self, link: Link) -> None:
        """Store a confirmed cross-reference edge, bidirectionally.

        Both directions are written so traversal in Phase 3 can walk from a
        code node to its linked doc entities and vice versa without a
        second index.
        """
        self.add_edge(
            link.source,
            link.target,
            LINK_EDGE_TYPE,
            confidence=link.confidence,
            timestamp=link.timestamp,
            reason=link.reason,
        )
        self.add_edge(
            link.target,
            link.source,
            LINK_EDGE_TYPE,
            confidence=link.confidence,
            timestamp=link.timestamp,
            reason=link.reason,
        )

    def links(self, node_id: str) -> list[Link]:
        """Confirmed cross-reference links out of `node_id`, either direction."""
        out = []
        for _src, target, data in self.edges(node_id, edge_type=LINK_EDGE_TYPE):
            out.append(
                Link(
                    source=node_id,
                    target=target,
                    confidence=data["confidence"],
                    timestamp=data["timestamp"],
                    reason=data.get("reason", ""),
                )
            )
        return out

    # -- misc -------------------------------------------------------------

    def __len__(self) -> int:
        with self.driver.session() as session:
            return session.run(f"MATCH (n:{_NODE_LABEL}) RETURN count(n) AS c").single()["c"]

    def stats(self) -> dict:
        with self.driver.session() as session:
            nodes = session.run(f"MATCH (n:{_NODE_LABEL}) RETURN count(n) AS c").single()["c"]
            edges = session.run(f"MATCH (:{_NODE_LABEL})-[r]->(:{_NODE_LABEL}) RETURN count(r) AS c").single()["c"]
        return {"nodes": nodes, "edges": edges}

    def drop_by_id_token(self, token: str) -> None:
        """Delete every node (and its incident edges) whose id contains `token`.

        A shared, persistent database has no cheap per-test "fresh
        database". Callers that namespace the doc/file ids they feed into
        indexing (see `tests/graphrag/conftest.py`'s `ns` fixture) use this
        to clean up after themselves instead of leaving data behind on
        every run. A substring match (not a prefix match) because node ids
        are built as `<kind>:<doc_or_file_id>...`, so the namespace token
        lands in the middle of the id, not at the start.
        """
        with self.driver.session() as session:
            session.run(f"MATCH (n:{_NODE_LABEL}) WHERE n.id CONTAINS $token DETACH DELETE n", token=token)


def make_link(source: str, target: str, confidence: float, reason: str = "") -> Link:
    """Convenience constructor that stamps the current time."""
    return Link(source=source, target=target, confidence=confidence, timestamp=time.time(), reason=reason)
