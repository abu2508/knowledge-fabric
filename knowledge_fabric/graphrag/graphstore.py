"""Graph storage backed by Amazon Neptune (Gremlin), per the build spec's
"NetworkX for local POC, Neptune for AWS" - this build targets Neptune
directly, no local/in-memory substitute.

One `GraphStore` instance backs the whole fabric: the document graph and
the code graph are stored as two logical partitions of the same underlying
Neptune graph (each vertex carries a `partition` property, `"doc"` or
`"code"`), and Phase 2's cross-reference edges are added directly onto it
as a distinct edge label (`links_to`). This matches the spec's "three
separate stores... plus a thin linking layer" at the logical level (each
partition is queried and reasoned about independently) while keeping link
storage literally "in the existing graph store" rather than standing up a
fourth database.

Connection: Neptune's Gremlin endpoint over a SigV4-signed WebSocket
(IAM database authentication - the standard way to reach Neptune from
outside its VPC, e.g. through a bastion/proxy or VPC peering). Configure
via environment variables:

    NEPTUNE_ENDPOINT   required - the cluster's Gremlin endpoint hostname
    NEPTUNE_PORT       optional - defaults to 8182
    AWS_REGION         optional - defaults to the boto3 session's region

Credentials are resolved the normal boto3 way (env vars, shared config,
instance/task role, etc.) - this module never takes a key directly.

See `infra/` for Terraform that provisions the cluster this module expects
to find at `NEPTUNE_ENDPOINT`, and `infra/README.md` for why reaching it
from outside AWS (e.g. this repo's own test suite, run from a machine that
isn't inside the cluster's VPC) needs a bastion or proxy that Terraform
does not yet stand up.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.process.anonymous_traversal import traversal
from gremlin_python.process.graph_traversal import __
from gremlin_python.process.traversal import T, TextP

# Edge label used for confirmed cross-reference links between the document
# graph and the code graph (Phase 2 of the spec). Kept distinct from
# structural edges (contains/calls/co_occurs/mentions/imports) so traversal
# code can tell a cross-graph hop from a same-graph one.
LINK_EDGE_TYPE = "links_to"

_RESERVED_KEYS = {"type", "partition"}


@dataclass
class Link:
    """A confirmed cross-reference edge between a doc entity and a code entity."""

    source: str
    target: str
    confidence: float
    timestamp: float
    reason: str = ""


class NeptuneNotConfiguredError(RuntimeError):
    """Raised when `NEPTUNE_ENDPOINT` isn't set and no connection was passed explicitly."""


def _sigv4_headers(endpoint: str, port: int, region: str) -> dict[str, str]:
    """Sign the Gremlin WebSocket handshake per Neptune's IAM-auth requirement.

    Neptune's Gremlin endpoint, when IAM database authentication is
    enabled, expects the WebSocket upgrade request signed like any other
    `neptune-db` SigV4 request. `gremlinpython`'s `DriverRemoteConnection`
    accepts arbitrary `headers`, so this signs a representative GET request
    to `https://{endpoint}:{port}/gremlin` and forwards the resulting
    `Authorization`/`X-Amz-Date`/`X-Amz-Security-Token` headers onto the
    real `wss://` connection.
    """
    session = boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        raise NeptuneNotConfiguredError(
            "No AWS credentials found (checked env vars, shared config, and "
            "instance/task role via boto3). Neptune IAM auth needs a signed "
            "request - configure credentials the normal boto3 way."
        )

    request = AWSRequest(method="GET", url=f"https://{endpoint}:{port}/gremlin")
    request.context["timestamp"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    SigV4Auth(credentials, "neptune-db", region).add_auth(request)

    headers = {
        "Authorization": request.headers["Authorization"],
        "X-Amz-Date": request.headers["X-Amz-Date"],
        "Host": f"{endpoint}:{port}",
    }
    if "X-Amz-Security-Token" in request.headers:
        headers["X-Amz-Security-Token"] = request.headers["X-Amz-Security-Token"]
    return headers


def connect(
    *,
    endpoint: str | None = None,
    port: int | None = None,
    region: str | None = None,
) -> DriverRemoteConnection:
    """Open a SigV4-authenticated Gremlin connection to a Neptune cluster.

    Reads `NEPTUNE_ENDPOINT` / `NEPTUNE_PORT` / `AWS_REGION` when the
    matching argument isn't passed explicitly. Raises
    `NeptuneNotConfiguredError` if no endpoint is available anywhere -
    there is no local/in-memory fallback in this build.
    """
    endpoint = endpoint or os.environ.get("NEPTUNE_ENDPOINT")
    if not endpoint:
        raise NeptuneNotConfiguredError(
            "NEPTUNE_ENDPOINT is not set. This build stores graphs in Amazon "
            "Neptune only - provision a cluster (see infra/) and set "
            "NEPTUNE_ENDPOINT to its Gremlin endpoint hostname."
        )
    port = port or int(os.environ.get("NEPTUNE_PORT", "8182"))
    region = region or os.environ.get("AWS_REGION") or boto3.Session().region_name
    if not region:
        raise NeptuneNotConfiguredError(
            "No AWS region found - set AWS_REGION or configure a default region."
        )

    headers = _sigv4_headers(endpoint, port, region)
    return DriverRemoteConnection(f"wss://{endpoint}:{port}/gremlin", "g", headers=headers)


class GraphStore:
    """Thin wrapper over a Gremlin traversal source (`g`), talking to Neptune.

    Every write is an upsert (safe to call `add_node`/`add_edge` again for
    the same id - properties are overwritten, not duplicated), so re-running
    indexing is idempotent the same way NetworkX's dict-based graph was.
    """

    def __init__(self, connection: DriverRemoteConnection | None = None, *, name: str = "graph") -> None:
        self.name = name
        self._connection = connection or connect()
        self.g = traversal().withRemote(self._connection)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- nodes ----------------------------------------------------------

    def add_node(self, node_id: str, node_type: str, *, partition: str, **attrs: Any) -> None:
        """Upsert a vertex. `partition` is `"doc"` or `"code"` (see module docstring)."""
        traversal_ = (
            self.g.V(node_id)
            .fold()
            .coalesce(__.unfold(), __.addV("entity").property(T.id, node_id))
            .property("type", node_type)
            .property("partition", partition)
        )
        for key, value in attrs.items():
            if key in _RESERVED_KEYS:
                continue
            traversal_ = traversal_.property(key, value)
        traversal_.iterate()

    def has_node(self, node_id: str) -> bool:
        return self.g.V(node_id).has_next()

    def get_node(self, node_id: str) -> dict:
        raw = self.g.V(node_id).value_map(True).next()
        return _flatten_value_map(raw)

    def nodes(self, node_type: str | None = None, partition: str | None = None) -> list[str]:
        t = self.g.V()
        if node_type is not None:
            t = t.has("type", node_type)
        if partition is not None:
            t = t.has("partition", partition)
        return t.id_().to_list()

    # -- structural edges (contains, calls, co_occurs, imports, mentions) --

    def add_edge(self, source: str, target: str, edge_type: str, **attrs: Any) -> None:
        """Upsert a directed edge labeled `edge_type` from `source` to `target`."""
        traversal_ = (
            self.g.V(source)
            .as_("a")
            .V(target)
            .coalesce(
                __.in_e(edge_type).where(__.out_v().as_("a")),
                __.add_e(edge_type).from_("a"),
            )
        )
        for key, value in attrs.items():
            traversal_ = traversal_.property(key, value)
        traversal_.iterate()

    def edges(self, node_id: str, edge_type: str | None = None) -> list[tuple[str, str, dict]]:
        """Outgoing edges from `node_id`, optionally filtered by label.

        Always reads the edge's real Gremlin label into `edge_type` on the
        returned dict - it isn't just echoed back from the `edge_type`
        argument, which is `None` on the (common) unfiltered call.
        """
        t = self.g.V(node_id).out_e(edge_type) if edge_type else self.g.V(node_id).out_e()
        results = (
            t.project("target", "label", "props")
            .by(__.in_v().id_())
            .by(__.label())
            .by(__.value_map())
            .to_list()
        )
        out = []
        for row in results:
            props = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in row["props"].items()}
            props["edge_type"] = row["label"]
            out.append((node_id, row["target"], props))
        return out

    def neighbors(self, node_id: str, edge_type: str | None = None) -> list[str]:
        return [target for _src, target, _data in self.edges(node_id, edge_type=edge_type)]

    def predecessors(self, node_id: str, edge_type: str | None = None) -> list[str]:
        """Nodes with an edge (optionally of `edge_type`) pointing into `node_id`."""
        t = self.g.V(node_id).in_e(edge_type) if edge_type else self.g.V(node_id).in_e()
        return t.out_v().id_().to_list()

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
        return self.g.V().count().next()

    def stats(self) -> dict:
        return {"nodes": self.g.V().count().next(), "edges": self.g.E().count().next()}

    def drop_by_id_token(self, token: str) -> None:
        """Delete every vertex (and its incident edges) whose id contains `token`.

        Neptune is a shared, persistent cluster - there's no cheap
        per-test "fresh database". Callers that namespace the doc/file ids
        they feed into indexing (see `tests/graphrag/conftest.py`'s `ns`
        fixture) use this to clean up after themselves instead of leaving
        data behind on every run. A substring match (not a prefix match)
        because node ids are built as `<kind>:<doc_or_file_id>...`, so the
        namespace token lands in the middle of the id, not at the start.
        """
        self.g.V().has(T.id, TextP.containing(token)).drop().iterate()


def _flatten_value_map(raw: dict) -> dict:
    """`value_map(True)` returns id/label directly but every property as a
    single-item list - flatten to plain scalars, and expose id/label as
    `type`-consistent keys matching what `add_node` wrote."""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key == T.id:
            out["id"] = value
        elif key == T.label:
            continue  # vertex label is always "entity" here; `type` property carries the real type
        elif isinstance(value, list) and len(value) == 1:
            out[key] = value[0]
        else:
            out[key] = value
    return out


def make_link(source: str, target: str, confidence: float, reason: str = "") -> Link:
    """Convenience constructor that stamps the current time."""
    return Link(source=source, target=target, confidence=confidence, timestamp=time.time(), reason=reason)
