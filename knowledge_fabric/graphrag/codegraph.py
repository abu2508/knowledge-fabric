"""Phase 1, code path: parse source with Tree-sitter and build a code graph.

Tree-sitter gets syntax structure for free (no model call). Only meaningful
extracted nodes go into the graph - functions, classes, imports, call
relationships - never the raw parse tree. Three-level granularity: file /
class / function.

Language: Ruby. The benchmark target (OpenProject) is a Ruby on Rails
codebase, so this module is written against `tree_sitter_ruby`. The parsing
entry point (`parse_source`) is the seam for adding another language later:
swap the `tree_sitter.Language` object and the node-type table
(`_CLASS_TYPES` / `_METHOD_TYPES` / `_CALL_TYPES` / `_IMPORT_CALLS`) for the
new grammar; `extract_code_graph` and everything above it is
language-agnostic.

Reparsing is incremental and diff-based: `incremental_index` hashes each
file's content and only reparses files whose hash changed since the last
run, using a small on-disk cache (mirrors the spec's "same pattern as IRR
v2" - reparse-on-change, not reparse-everything-every-time).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter
import tree_sitter_ruby

from knowledge_fabric.graphrag.graphstore import GraphStore

_LANGUAGE = tree_sitter.Language(tree_sitter_ruby.language())

# Ruby-grammar node types that count as a class-level unit.
_CLASS_TYPES = {"class", "module"}
# Ruby-grammar node type for a method definition.
_METHOD_TYPES = {"method", "singleton_method"}
# Ruby-grammar node type for any call expression (`foo.bar(...)`, `bar(...)`).
_CALL_TYPES = {"call", "method_call"}
# Identifier names that make a call an import rather than a regular call.
_IMPORT_CALLS = {"require", "require_relative", "load", "include", "extend"}


@dataclass
class CodeUnit:
    """A single extracted node: a file, class/module, or function/method."""

    node_id: str
    kind: str  # "file" | "class" | "function"
    name: str
    file_path: str
    start_line: int
    end_line: int
    calls: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


def _parser() -> tree_sitter.Parser:
    return tree_sitter.Parser(_LANGUAGE)


def parse_source(source: bytes) -> tree_sitter.Tree:
    """Parse Ruby source bytes into a Tree-sitter syntax tree."""
    return _parser().parse(source)


def _node_text(node: tree_sitter.Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_name(node: tree_sitter.Node, source: bytes) -> str:
    for child in node.children:
        if child.type in ("constant", "identifier", "scope_resolution"):
            return _node_text(child, source)
    return "<anonymous>"


def _find_call_target(node: tree_sitter.Node, source: bytes) -> str | None:
    """Best-effort callee name for a `call`/`method_call` node."""
    # tree-sitter-ruby's `call` node has a trailing `identifier` (the method
    # name) after the receiver and `.`; a bare `method_call`/`identifier`
    # call has just the identifier itself as a child.
    name = None
    for child in node.children:
        if child.type == "identifier":
            name = _node_text(child, source)
    return name


def extract_file(file_path: str, source: bytes) -> tuple[CodeUnit, list[CodeUnit]]:
    """Extract the file-level unit plus every class/function unit inside it.

    Returns `(file_unit, member_units)`. `file_unit.calls`/`.imports` hold
    top-level (outside any class/function) calls and imports; each member
    unit holds its own.
    """
    tree = parse_source(source)
    root = tree.root_node

    file_id = f"code:{file_path}"
    file_unit = CodeUnit(
        node_id=file_id,
        kind="file",
        name=Path(file_path).name,
        file_path=file_path,
        start_line=root.start_point[0] + 1,
        end_line=root.end_point[0] + 1,
    )

    members: list[CodeUnit] = []

    def walk(node: tree_sitter.Node, enclosing: CodeUnit) -> None:
        for child in node.children:
            if child.type in _CLASS_TYPES:
                name = _find_name(child, source)
                unit = CodeUnit(
                    node_id=f"{file_id}::{name}",
                    kind="class",
                    name=name,
                    file_path=file_path,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                )
                members.append(unit)
                walk(child, unit)
            elif child.type in _METHOD_TYPES:
                name = _find_name(child, source)
                unit = CodeUnit(
                    node_id=f"{enclosing.node_id}#{name}"
                    if enclosing is not file_unit
                    else f"{file_id}::{name}",
                    kind="function",
                    name=name,
                    file_path=file_path,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                )
                members.append(unit)
                walk(child, unit)
            elif child.type in _CALL_TYPES:
                target = _find_call_target(child, source)
                if target:
                    if target in _IMPORT_CALLS:
                        arg = _first_string_arg(child, source)
                        enclosing.imports.append(arg or target)
                    else:
                        enclosing.calls.append(target)
                walk(child, enclosing)
            else:
                walk(child, enclosing)

    walk(root, file_unit)
    return file_unit, members


def _first_string_arg(call_node: tree_sitter.Node, source: bytes) -> str | None:
    for child in call_node.children:
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type == "string":
                    return _node_text(arg, source).strip("'\"")
    return None


def build_code_graph(files: dict[str, bytes], store: GraphStore) -> list[CodeUnit]:
    """Extract every `files` entry (path -> source bytes) into `store`'s code partition.

    Edges: `contains` (file -> class -> function), `calls` (function/class/
    file -> the function it calls, resolved within this corpus when the
    name matches exactly one indexed function; otherwise a lightweight
    external node is created so the call is still visible), `imports`
    (file/class/function -> the required path or module name).
    """
    all_units: list[CodeUnit] = []
    by_name: dict[str, list[str]] = {}

    for file_path, source in files.items():
        file_unit, members = extract_file(file_path, source)
        all_units.append(file_unit)
        all_units.extend(members)

        store.add_node(
            file_unit.node_id,
            "file",
            partition="code",
            name=file_unit.name,
            file_path=file_path,
            start_line=file_unit.start_line,
            end_line=file_unit.end_line,
        )
        for unit in members:
            store.add_node(
                unit.node_id,
                unit.kind,
                partition="code",
                name=unit.name,
                file_path=file_path,
                start_line=unit.start_line,
                end_line=unit.end_line,
            )
            by_name.setdefault(unit.name, []).append(unit.node_id)

        # `contains`: file -> each class, and each class -> its own methods,
        # inferred from node_id nesting (`file::Class` contains
        # `file::Class#method`; anything else nests directly under the file).
        for unit in members:
            if unit.kind == "class":
                store.add_edge(file_unit.node_id, unit.node_id, "contains")
            elif "#" in unit.node_id:
                parent_id = unit.node_id.rsplit("#", 1)[0]
                store.add_edge(parent_id, unit.node_id, "contains")
            else:
                store.add_edge(file_unit.node_id, unit.node_id, "contains")

    # Second pass for `calls`/`imports`, once every unit is known by name -
    # a call can target a function defined later in the same corpus.
    for unit in all_units:
        for target_name in unit.imports:
            import_id = f"code_external:{target_name}"
            if not store.has_node(import_id):
                store.add_node(import_id, "external_module", partition="code", name=target_name)
            store.add_edge(unit.node_id, import_id, "imports")
        for called_name in unit.calls:
            candidates = by_name.get(called_name, [])
            if len(candidates) == 1:
                store.add_edge(unit.node_id, candidates[0], "calls")
            else:
                # Ambiguous or external callee - keep it visible as a
                # lightweight node rather than dropping the edge.
                external_id = f"code_call:{called_name}"
                if not store.has_node(external_id):
                    store.add_node(external_id, "external_call", partition="code", name=called_name)
                store.add_edge(unit.node_id, external_id, "calls")

    return all_units


# ---------------------------------------------------------------------------
# Incremental, diff-based reparsing
# ---------------------------------------------------------------------------


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def incremental_index(
    root: str | Path,
    store: GraphStore,
    *,
    cache_path: str | Path,
    extensions: tuple[str, ...] = (".rb",),
) -> dict[str, list[CodeUnit] | None]:
    """Reparse only files under `root` whose content changed since the last run.

    `cache_path` holds a JSON map of file path -> content hash from the
    previous run. Files with an unchanged hash are skipped entirely (their
    existing graph nodes/edges are left as-is); changed or new files are
    (re-)extracted and added to `store`. Returns `{changed_file: units}` for
    the files actually reparsed this run (an empty dict if nothing changed).
    """
    root = Path(root)
    cache_path = Path(cache_path)
    previous: dict[str, str] = {}
    if cache_path.exists():
        previous = json.loads(cache_path.read_text(encoding="utf-8"))

    current: dict[str, str] = {}
    changed_files: dict[str, bytes] = {}
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file() or file_path.suffix not in extensions:
            continue
        data = file_path.read_bytes()
        rel = str(file_path.relative_to(root))
        digest = _hash_bytes(data)
        current[rel] = digest
        if previous.get(rel) != digest:
            changed_files[rel] = data

    result: dict[str, list[CodeUnit] | None] = {}
    if changed_files:
        # build_code_graph expects the file_path key used in node IDs to be
        # stable across runs, so keep it relative to `root`.
        units_by_file: dict[str, list[CodeUnit]] = {}
        all_units = build_code_graph(changed_files, store)
        for unit in all_units:
            units_by_file.setdefault(unit.file_path, []).append(unit)
        for rel in changed_files:
            result[rel] = units_by_file.get(rel, [])

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
    return result
