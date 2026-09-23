from knowledge_fabric.graphrag.graphstore import make_link
from knowledge_fabric.graphrag.retrieval import (
    HOP_RELEVANCE_FLOOR,
    MAX_HOPS,
    dynamic_hop_traversal,
    route_query,
)
from tests.graphrag.fakes import FakeLaya


def _seed_chain(store, ns, length: int) -> list[str]:
    """A straight-line chain of `length` doc_chunk nodes, each `mentions`-linked
    to the next, so traversal has somewhere to walk hop by hop."""
    ids = [f"doc:{ns}:chunk{i}" for i in range(length)]
    for i, node_id in enumerate(ids):
        store.add_node(node_id, "doc_chunk", partition="doc", text=f"chunk {i} about retention", source="x")
    for a, b in zip(ids, ids[1:]):
        store.add_edge(a, b, "mentions")
    return ids


def test_route_query_prefers_doc_only_for_low_confidence():
    laya = FakeLaya(rules=[(["needs code"], 0.1)])
    assert route_query("a query that needs code", laya) == "doc"


def test_route_query_defaults_to_both_on_scoring_failure():
    class Explodes:
        def score(self, *a, **k):
            raise RuntimeError("boom")

    assert route_query("anything", Explodes()) == "both"


def test_dynamic_hop_traversal_stops_below_confidence_floor(store, ns):
    ids = _seed_chain(store, ns, length=4)
    # allow the first hop, reject the second - traversal should stop there
    # rather than reaching chunk3.
    laya = FakeLaya(
        rules=[
            (["chunk 1"], 0.9),
            (["chunk 2"], 0.2),  # below HOP_RELEVANCE_FLOOR
        ],
        default=0.9,
    )

    shortlist, path = dynamic_hop_traversal("query", [ids[0]], store, laya=laya, max_hops=MAX_HOPS)

    assert ids[0] in shortlist
    assert ids[1] in shortlist  # first hop confidence 0.9, above floor
    assert ids[2] not in shortlist  # second hop confidence 0.2, below floor
    assert ids[3] not in shortlist  # never reached - traversal already stopped
    assert any(step.confidence < HOP_RELEVANCE_FLOOR for step in path)


def test_dynamic_hop_traversal_respects_hard_hop_cap(store, ns):
    ids = _seed_chain(store, ns, length=MAX_HOPS + 3)
    laya = FakeLaya(rules=[], default=0.99)  # always relevant - only the cap should stop it

    shortlist, _path = dynamic_hop_traversal("query", [ids[0]], store, laya=laya, max_hops=MAX_HOPS)

    # seed + at most MAX_HOPS additional nodes along a single chain
    assert len(shortlist) <= MAX_HOPS + 1
    assert ids[-1] not in shortlist  # the chain is longer than the cap allows


def test_dynamic_hop_traversal_follows_cross_reference_links(store, ns):
    doc_id = f"doc_entity:{ns}:retention"
    code_id = f"code:{ns}:RetentionService"
    store.add_node(doc_id, "doc_entity", partition="doc", name="retention")
    store.add_node(code_id, "class", partition="code", name="RetentionService", file_path=f"{ns}/f.rb")
    store.add_link(make_link(doc_id, code_id, confidence=0.9, reason="test"))

    laya = FakeLaya(rules=[], default=0.9)
    shortlist, path = dynamic_hop_traversal("query", [doc_id], store, laya=laya, max_hops=2)

    assert code_id in shortlist
    assert any(step.edge_type == "links_to" for step in path)
