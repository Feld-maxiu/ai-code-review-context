import shutil
import sqlite3
from pathlib import Path

from repo_context.index.index_builder import build_index
from repo_context.store.models import CodeFile, CodeNode
from repo_context.store.sqlite_store import SQLiteStore


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_sqlite_store_initializes_schema(tmp_path: Path) -> None:
    """SQLite 初始化后应创建阶段 3 要求的核心表。"""
    db_path = tmp_path / "context.db"
    SQLiteStore(db_path).init_db()

    with sqlite3.connect(db_path) as conn:
        table_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert {
        "code_files",
        "code_nodes",
        "code_edges",
        "review_tasks",
        "context_usage",
    }.issubset(table_names)


def test_insert_and_query_code_file(tmp_path: Path) -> None:
    """能写入并按 repo_id/file_path 查询 CodeFile。"""
    store = SQLiteStore(tmp_path / "context.db")
    store.init_db()
    store.insert_code_file(
        CodeFile(
            repo_id="repo-a",
            file_path="app/api/auth.py",
            file_type="source",
            language="python",
            line_count=12,
            is_test=False,
        )
    )

    result = store.get_code_file("repo-a", "app/api/auth.py")

    assert result is not None
    assert result.file_path == "app/api/auth.py"
    assert result.is_test is False


def test_insert_and_query_code_node(tmp_path: Path) -> None:
    """能写入并按 repo_id/node_id 查询 CodeNode。"""
    store = SQLiteStore(tmp_path / "context.db")
    store.init_db()
    store.insert_code_file(
        CodeFile("repo-a", "app/api/auth.py", "source", "python", 12, False)
    )
    node = CodeNode(
        repo_id="repo-a",
        node_id="app/api/auth.py:app.api.auth.login:10",
        type="function",
        name="login",
        qualified_name="app.api.auth.login",
        file_path="app/api/auth.py",
        start_line=10,
        end_line=13,
        signature="login(username: str, password: str)",
        decorators=['router.post("/login")'],
    )
    store.insert_code_node(node)

    result = store.get_code_node("repo-a", node.node_id)

    assert result == node


def test_repo_id_data_is_isolated(tmp_path: Path) -> None:
    """相同 file_path 在不同 repo_id 下应互不污染。"""
    store = SQLiteStore(tmp_path / "context.db")
    store.init_db()
    store.insert_code_file(
        CodeFile("repo-a", "app/config.py", "config", "python", 2, False)
    )
    store.insert_code_file(
        CodeFile("repo-b", "app/config.py", "config", "python", 7, False)
    )

    repo_a_files = store.list_code_files("repo-a")
    repo_b_files = store.list_code_files("repo-b")

    assert len(repo_a_files) == 1
    assert len(repo_b_files) == 1
    assert repo_a_files[0].line_count == 2
    assert repo_b_files[0].line_count == 7


def test_build_index_sample_repo_persists_expected_files_and_nodes(tmp_path: Path) -> None:
    """build_index(sample_repo) 后应能查到关键文件和节点。"""
    db_path = tmp_path / "context.db"

    result = build_index("sample-repo", SAMPLE_REPO, db_path)
    store = SQLiteStore(db_path)
    nodes = store.list_code_nodes("sample-repo")

    auth_file = store.get_code_file("sample-repo", "app/api/auth.py")
    login_node = next(node for node in nodes if node.qualified_name == "app.api.auth.login")
    authenticate_node = next(
        node
        for node in nodes
        if node.qualified_name == "app.services.user_service.UserService.authenticate"
    )

    assert db_path.exists()
    assert result.file_count > 0
    assert result.node_count > 0
    assert auth_file is not None
    assert login_node.type == "function"
    assert authenticate_node.type == "method"


def test_build_index_persists_contains_edges(tmp_path: Path) -> None:
    """阶段 3 写入 contains 结构边；调用边留到阶段 4。"""
    db_path = tmp_path / "context.db"

    result = build_index("sample-repo", SAMPLE_REPO, db_path)
    store = SQLiteStore(db_path)
    nodes = {node.qualified_name: node for node in store.list_code_nodes("sample-repo")}
    edges = store.list_code_edges("sample-repo")

    auth_module = nodes["app.api.auth"]
    login_node = nodes["app.api.auth.login"]
    user_service_class = nodes["app.services.user_service.UserService"]
    authenticate_method = nodes["app.services.user_service.UserService.authenticate"]

    assert result.edge_count > 0
    assert any(
        edge.source_node_id == auth_module.node_id
        and edge.target_node_id == login_node.node_id
        and edge.edge_type == "contains"
        for edge in edges
    )
    assert any(
        edge.source_node_id == user_service_class.node_id
        and edge.target_node_id == authenticate_method.node_id
        and edge.edge_type == "contains"
        for edge in edges
    )


def test_build_index_keeps_valid_files_when_one_file_has_syntax_error(
    tmp_path: Path,
) -> None:
    """存在语法错误文件时，build_index 不应影响其他正常文件入库。"""
    repo_copy = tmp_path / "sample_repo_with_bad_file"
    shutil.copytree(SAMPLE_REPO, repo_copy)
    (repo_copy / "app" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    db_path = tmp_path / "context.db"

    result = build_index("bad-sample-repo", repo_copy, db_path)
    store = SQLiteStore(db_path)
    nodes = store.list_code_nodes("bad-sample-repo")

    assert result.file_count > 0
    assert store.get_code_file("bad-sample-repo", "app/broken.py") is not None
    assert any(node.qualified_name == "app.api.auth.login" for node in nodes)
    assert any(
        node.qualified_name == "app.services.user_service.UserService.authenticate"
        for node in nodes
    )
