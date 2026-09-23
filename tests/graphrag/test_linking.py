from knowledge_fabric.graphrag.linking import (
    STOPWORDS,
    Candidate,
    confirm_candidates,
    generate_candidates,
)
from tests.graphrag.fakes import FakeAnthropicClient, FakeLaya


def _seed(store, ns):
    """A small doc + code graph: one clear match, one unrelated pair."""
    store.add_node(f"doc_entity:{ns}:data retention period", "doc_entity", partition="doc", name="data retention period")
    store.add_node(f"doc_entity:{ns}:refund eligibility", "doc_entity", partition="doc", name="refund eligibility")
    store.add_node(f"code:{ns}:file.rb::RetentionService", "class", partition="code", name="RetentionService", file_path=f"{ns}/file.rb")
    store.add_node(f"code:{ns}:file.rb::MailerHelper", "class", partition="code", name="MailerHelper", file_path=f"{ns}/file.rb")


def test_generate_candidates_word_overlap(store, ns):
    _seed(store, ns)
    candidates = generate_candidates(store)
    candidates = [c for c in candidates if ns in c.doc_entity_id]

    # "retention" is significant and shared; STOPWORDS shouldn't include it.
    assert "retention" not in STOPWORDS
    matched = [c for c in candidates if "RetentionService" in c.code_entity_name]
    assert matched
    assert matched[0].source == "word_overlap"


def test_generate_candidates_embedding_fallback_when_no_overlap(store, ns):
    store.add_node(f"doc_entity:{ns}:zzz unmatched phrase", "doc_entity", partition="doc", name="zzz unmatched phrase")
    store.add_node(f"code:{ns}:f.rb::SomeClass", "class", partition="code", name="SomeClass", file_path=f"{ns}/f.rb")

    candidates = generate_candidates(store)
    candidates = [c for c in candidates if ns in c.doc_entity_id and "zzz unmatched phrase" in c.doc_entity_name]
    # no shared significant words -> falls back to embedding similarity,
    # which for two totally unrelated names should score 0 and produce no
    # candidates at all (not a false-positive candidate).
    assert candidates == []


def test_confirm_candidates_auto_accepts_above_threshold(store, ns):
    _seed(store, ns)
    candidate = Candidate(
        doc_entity_id=f"doc_entity:{ns}:data retention period",
        doc_entity_name="data retention period",
        code_entity_id=f"code:{ns}:file.rb::RetentionService",
        code_entity_name="RetentionService",
        source="word_overlap",
    )
    laya = FakeLaya(rules=[(["retentionservice"], 0.95)])

    decisions = confirm_candidates([candidate], store, laya=laya)

    assert decisions[0].outcome == "auto_accept"
    links = store.links(candidate.doc_entity_id)
    assert any(link.target == candidate.code_entity_id for link in links)


def test_confirm_candidates_auto_rejects_below_threshold(store, ns):
    _seed(store, ns)
    candidate = Candidate(
        doc_entity_id=f"doc_entity:{ns}:refund eligibility",
        doc_entity_name="refund eligibility",
        code_entity_id=f"code:{ns}:file.rb::MailerHelper",
        code_entity_name="MailerHelper",
        source="embedding_fallback",
    )
    laya = FakeLaya(rules=[(["mailerhelper"], 0.1)])

    decisions = confirm_candidates([candidate], store, laya=laya)

    assert decisions[0].outcome == "auto_reject"
    assert store.links(candidate.doc_entity_id) == []


def test_confirm_candidates_escalates_ambiguous_band_to_llm(store, ns):
    _seed(store, ns)
    candidate = Candidate(
        doc_entity_id=f"doc_entity:{ns}:data retention period",
        doc_entity_name="data retention period",
        code_entity_id=f"code:{ns}:file.rb::RetentionService",
        code_entity_name="RetentionService",
        source="word_overlap",
    )
    laya = FakeLaya(rules=[(["retentionservice"], 0.65)])  # lands in 0.50-0.85 ambiguous band
    llm = FakeAnthropicClient(responses=[{"same_concept": True, "confidence": 0.9, "reasoning": "same thing"}])

    decisions = confirm_candidates([candidate], store, laya=laya, llm_client=llm)

    assert decisions[0].outcome == "escalated_accept"
    assert llm.calls, "expected the ambiguous band to escalate to the LLM"
    links = store.links(candidate.doc_entity_id)
    assert any(link.target == candidate.code_entity_id for link in links)
