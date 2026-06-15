from pathlib import Path

from repo_context.index.index_builder import build_index
from repo_context.store.models import CodeEdge, CodeNode
from repo_context.store.sqlite_store import SQLiteStore


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_fastapi_post_login_route_is_indexed(tmp_path: Path) -> None:
    """sample_repo 中 POST /login 应被识别为 route 节点。"""
    store = _build_sample_index(tmp_path)
    nodes = _nodes_by_qualified_name(store, "sample-repo")

    route_node = nodes["route:POST /login"]

    assert route_node.type == "route"
    assert route_node.name == "POST /login"
    assert route_node.file_path == "app/api/auth.py"


def test_route_is_linked_to_login_handler(tmp_path: Path) -> None:
    """route 节点应通过 maps_to 边关联到 login 函数。"""
    store = _build_sample_index(tmp_path)
    nodes = _nodes_by_qualified_name(store, "sample-repo")
    edges = store.list_code_edges("sample-repo")

    route_node = nodes["route:POST /login"]
    login_node = nodes["app.api.auth.login"]

    assert _has_edge(edges, route_node, login_node, "maps_to")


def test_login_calls_authenticate(tmp_path: Path) -> None:
    """login 函数应能查到下游 authenticate 调用。"""
    store = _build_sample_index(tmp_path)
    nodes = _nodes_by_qualified_name(store, "sample-repo")
    edges = store.list_code_edges("sample-repo")

    login_node = nodes["app.api.auth.login"]
    authenticate_node = nodes["app.services.user_service.authenticate"]

    assert _has_edge(edges, login_node, authenticate_node, "calls")


def test_authenticate_method_calls_find_by_username(tmp_path: Path) -> None:
    """UserService.authenticate 方法应能查到 repository 查询调用。"""
    store = _build_sample_index(tmp_path)
    nodes = _nodes_by_qualified_name(store, "sample-repo")
    edges = store.list_code_edges("sample-repo")

    authenticate_node = nodes["app.services.user_service.UserService.authenticate"]
    find_node = nodes["app.repositories.user_repo.find_by_username"]

    assert _has_edge(edges, authenticate_node, find_node, "calls")


def test_unresolved_call_is_kept_without_crashing(tmp_path: Path) -> None:
    """无法解析的调用应保留 unresolved 信息，且不影响索引构建。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text(
        "def entrypoint():\n"
        "    missing_dependency()\n",
        encoding="utf-8",
    )

    build_index("unresolved-repo", repo, tmp_path / "context.db")
    store = SQLiteStore(tmp_path / "context.db")
    edges = store.list_code_edges("unresolved-repo")

    assert any(
        edge.edge_type == "calls"
        and edge.target_node_id == "unresolved:missing_dependency"
        for edge in edges
    )


def test_flask_route_is_indexed(tmp_path: Path) -> None:
    """支持 Flask @app.route(..., methods=[...]) 路由识别。"""
    repo = tmp_path / "flask_repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "\n"
        "@app.route(\"/login\", methods=[\"POST\"])\n"
        "def login():\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )

    build_index("flask-repo", repo, tmp_path / "flask.db")
    store = SQLiteStore(tmp_path / "flask.db")
    nodes = _nodes_by_qualified_name(store, "flask-repo")

    assert nodes["route:POST /login"].type == "route"


def _build_sample_index(tmp_path: Path) -> SQLiteStore:
    db_path = tmp_path / "context.db"
    build_index("sample-repo", SAMPLE_REPO, db_path)
    return SQLiteStore(db_path)


def _nodes_by_qualified_name(
    store: SQLiteStore,
    repo_id: str,
) -> dict[str, CodeNode]:
    return {node.qualified_name: node for node in store.list_code_nodes(repo_id)}


def _has_edge(
    edges: list[CodeEdge],
    source: CodeNode,
    target: CodeNode,
    edge_type: str,
) -> bool:
    return any(
        edge.source_node_id == source.node_id
        and edge.target_node_id == target.node_id
        and edge.edge_type == edge_type
        for edge in edges
    )
