"""GraphRAG architecture per the Knowledge Fabric build spec.

Document graph + code graph + a thin cross-reference linking layer,
anchored on compliance/policy-code drift detection.

    from knowledge_fabric.graphrag.pipeline import index, link, query

See the repo README (GraphRAG section) for the phase-by-phase design and
what's real vs. stubbed in this environment.
"""
