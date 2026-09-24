from pathlib import Path

from knowledge_fabric.graphrag.codegraph import build_code_graph, extract_file, incremental_index

FIXTURE = (Path(__file__).parent.parent / "fixtures" / "code" / "retention_service.rb").read_bytes()


def test_extract_file_finds_classes_and_methods():
    file_unit, members = extract_file("retention_service.rb", FIXTURE)
    class_names = {m.name for m in members if m.kind == "class"}
    method_names = {m.name for m in members if m.kind == "function"}

    assert {"RetentionService", "RefundService"} <= class_names
    assert "delete_expired_user_records" in method_names
    assert "process_refund" in method_names


def test_extract_file_captures_requires_as_imports():
    file_unit, _members = extract_file("retention_service.rb", FIXTURE)
    assert "record" in file_unit.imports


def test_extract_file_captures_calls():
    _file_unit, members = extract_file("retention_service.rb", FIXTURE)
    refund_service = next(m for m in members if m.name == "process_refund")
    assert "notify_customer" in refund_service.calls


def test_build_code_graph_populates_store_with_contains_and_calls_edges(store, ns):
    file_path = f"retention_service-{ns}.rb"
    build_code_graph({file_path: FIXTURE}, store)

    # `store` is a real, shared Neo4j database - scope every query to this
    # test's namespace so it isn't looking at other runs' leftover data.
    files = [n for n in store.nodes(node_type="file", partition="code") if ns in n]
    classes = [n for n in store.nodes(node_type="class", partition="code") if ns in n]
    functions = [n for n in store.nodes(node_type="function", partition="code") if ns in n]
    assert len(files) == 1
    assert len(classes) == 2
    assert len(functions) >= 3

    file_id = files[0]
    contained = store.neighbors(file_id, edge_type="contains")
    assert set(contained) == set(classes)

    # process_refund -> notify_customer should resolve to a real function
    # node (unambiguous name within this small corpus), not an external stub.
    process_refund_id = next(f for f in functions if f.endswith("#process_refund"))
    called = store.neighbors(process_refund_id, edge_type="calls")
    assert any(c.endswith("#notify_customer") for c in called)


def test_incremental_index_skips_unchanged_files(tmp_path, store, ns):
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    file_name = f"a-{ns}.rb"
    (code_dir / file_name).write_bytes(FIXTURE)
    cache_path = tmp_path / "cache.json"

    first = incremental_index(code_dir, store, cache_path=cache_path)
    assert file_name in first

    second = incremental_index(code_dir, store, cache_path=cache_path)
    assert second == {}  # nothing changed - nothing reparsed

    (code_dir / file_name).write_text("class Changed\nend\n")
    third = incremental_index(code_dir, store, cache_path=cache_path)
    assert file_name in third
