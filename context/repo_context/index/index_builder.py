from dataclasses import dataclass
from pathlib import Path

from repo_context.ingest.file_scanner import scan_repo
from repo_context.parser.ast_parser import ImportInfo, ParseResult, parse_python_file
from repo_context.parser.call_graph import RelationAnalysis, RouteInfo, analyze_file_relations
from repo_context.store.models import CodeEdge, CodeFile, CodeNode
from repo_context.store.sqlite_store import SQLiteStore


EDGE_CONTAINS = "contains"
EDGE_CALLS = "calls"
EDGE_MAPS_TO = "maps_to"


@dataclass(frozen=True)
class IndexBuildResult:
    repo_id: str
    db_path: str
    file_count: int
    node_count: int
    edge_count: int


def build_index(repo_id: str, repo_path: str | Path, db_path: str | Path) -> IndexBuildResult:
    """串联扫描、AST 解析和 SQLite 写入，不生成任务或覆盖率。"""
    root = Path(repo_path).resolve()
    store = SQLiteStore(db_path)
    store.init_db()

    scanned_files = scan_repo(root)
    store.insert_code_files(
        CodeFile(
            repo_id=repo_id,
            file_path=item.file_path,
            file_type=item.file_type,
            language=item.language,
            line_count=item.line_count,
            is_test=item.is_test,
        )
        for item in scanned_files
    )

    parse_results: dict[str, ParseResult] = {}
    relation_results: dict[str, RelationAnalysis] = {}
    all_nodes: list[CodeNode] = []

    for code_file in scanned_files:
        parse_result = parse_python_file(root / code_file.file_path, root)
        relation_result = analyze_file_relations(root / code_file.file_path, root)
        parse_results[code_file.file_path] = parse_result
        relation_results[code_file.file_path] = relation_result

        # 语法错误由解析阶段记录；阶段 3 只写入成功解析出的节点。
        code_nodes = [
            CodeNode(
                repo_id=repo_id,
                node_id=node.node_id,
                type=node.type,
                name=node.name,
                qualified_name=node.qualified_name,
                file_path=node.file_path,
                start_line=node.start_line,
                end_line=node.end_line,
                signature=node.signature,
                decorators=node.decorators,
            )
            for node in parse_result.nodes
        ]
        route_nodes = [
            _route_to_code_node(repo_id, route)
            for route in relation_result.routes
        ]
        all_nodes.extend([*code_nodes, *route_nodes])

    store.insert_code_nodes(all_nodes)

    contains_edges = _build_contains_edges(repo_id, all_nodes)
    route_edges = _build_route_mapping_edges(repo_id, all_nodes, relation_results)
    call_edges = _build_call_edges(repo_id, all_nodes, parse_results, relation_results)
    all_edges = [*contains_edges, *route_edges, *call_edges]
    store.insert_code_edges(all_edges)

    return IndexBuildResult(
        repo_id=repo_id,
        db_path=str(Path(db_path)),
        file_count=len(scanned_files),
        node_count=len(all_nodes),
        edge_count=len(all_edges),
    )


def _build_contains_edges(repo_id: str, nodes: list[CodeNode]) -> list[CodeEdge]:
    """构建 module/class 到下级节点的包含关系。"""
    by_qualified_name = {node.qualified_name: node for node in nodes}
    edges: list[CodeEdge] = []

    for node in nodes:
        if node.type in {"module", "route"}:
            continue

        parent_name = node.qualified_name.rsplit(".", 1)[0]
        parent = by_qualified_name.get(parent_name)
        if parent is None:
            continue

        edges.append(
            CodeEdge(
                repo_id=repo_id,
                source_node_id=parent.node_id,
                target_node_id=node.node_id,
                    edge_type=EDGE_CONTAINS,
            )
        )

    return edges


def _route_to_code_node(repo_id: str, route: RouteInfo) -> CodeNode:
    return CodeNode(
        repo_id=repo_id,
        node_id=f"{route.file_path}:{route.qualified_name}:{route.start_line}",
        type="route",
        name=route.name,
        qualified_name=route.qualified_name,
        file_path=route.file_path,
        start_line=route.start_line,
        end_line=route.end_line,
        signature=route.name,
        decorators=route.decorators,
    )


def _build_route_mapping_edges(
    repo_id: str,
    nodes: list[CodeNode],
    relation_results: dict[str, RelationAnalysis],
) -> list[CodeEdge]:
    by_qualified_name = {node.qualified_name: node for node in nodes}
    edges: list[CodeEdge] = []

    for relation_result in relation_results.values():
        for route in relation_result.routes:
            route_node = by_qualified_name.get(route.qualified_name)
            handler_node = by_qualified_name.get(route.handler_qualified_name)
            if route_node is None or handler_node is None:
                continue
            edges.append(
                CodeEdge(
                    repo_id=repo_id,
                    source_node_id=route_node.node_id,
                    target_node_id=handler_node.node_id,
                    edge_type=EDGE_MAPS_TO,
                )
            )

    return edges


def _build_call_edges(
    repo_id: str,
    nodes: list[CodeNode],
    parse_results: dict[str, ParseResult],
    relation_results: dict[str, RelationAnalysis],
) -> list[CodeEdge]:
    by_qualified_name = {node.qualified_name: node for node in nodes}
    edges: list[CodeEdge] = []

    for file_path, relation_result in relation_results.items():
        import_map = _build_import_map(parse_results[file_path].imports)
        for call in relation_result.calls:
            source_node = by_qualified_name.get(call.source_qualified_name)
            if source_node is None:
                continue

            target_node = _resolve_call_target(
                call.call_name,
                source_node.qualified_name,
                import_map,
                by_qualified_name,
            )
            target_node_id = (
                target_node.node_id
                if target_node is not None
                else f"unresolved:{call.call_name}"
            )
            edges.append(
                CodeEdge(
                    repo_id=repo_id,
                    source_node_id=source_node.node_id,
                    target_node_id=target_node_id,
                    edge_type=EDGE_CALLS,
                )
            )

    return edges


def _build_import_map(imports: list[ImportInfo]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in imports:
        if item.import_type == "from":
            local_name = item.alias or item.name
            mapping[local_name] = f"{item.module}.{item.name}".strip(".")
        else:
            local_name = item.alias or item.name.split(".", 1)[0]
            mapping[local_name] = item.module
    return mapping


def _resolve_call_target(
    call_name: str,
    source_qualified_name: str,
    import_map: dict[str, str],
    by_qualified_name: dict[str, CodeNode],
) -> CodeNode | None:
    candidates = _call_candidates(call_name, source_qualified_name, import_map)
    for candidate in candidates:
        if candidate in by_qualified_name:
            return by_qualified_name[candidate]
    return None


def _call_candidates(
    call_name: str,
    source_qualified_name: str,
    import_map: dict[str, str],
) -> list[str]:
    candidates: list[str] = []
    parts = call_name.split(".")
    first = parts[0]

    if first in import_map:
        candidates.append(".".join([import_map[first], *parts[1:]]))

    module_name = source_qualified_name.rsplit(".", 1)[0]
    candidates.append(f"{module_name}.{call_name}")

    if "." in module_name:
        parent_module = module_name.rsplit(".", 1)[0]
        candidates.append(f"{parent_module}.{call_name}")

    candidates.append(call_name)
    return candidates
