import json
from pathlib import Path
from typing import Any

import pytest

from repo_context.index.index_builder import build_index
from repo_context.service.context_service import ContextService
from repo_context.tools import (
    explore_related_symbols,
    get_callees,
    get_callers,
    get_file_snippet,
    get_node_detail,
    search_symbol,
    trace_call_chain,
)


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_search_symbol_returns_login_function(context_service: ContextService) -> None:
    """搜索 login 应返回 login 函数节点。"""
    results = search_symbol(context_service, "login")

    assert any(
        item["type"] == "function"
        and item["qualified_name"] == "app.api.auth.login"
        for item in results
    )
    _assert_json_serializable(results)


def test_get_node_detail_returns_source(context_service: ContextService) -> None:
    """get_node_detail(login) 应能返回源码。"""
    login = _find_symbol(context_service, "app.api.auth.login")

    detail = get_node_detail(context_service, login["node_id"], include_source=True)

    assert detail is not None
    assert detail["qualified_name"] == "app.api.auth.login"
    assert "def login" in detail["source"]
    assert "authenticate(" in detail["source"]
    _assert_json_serializable(detail)


def test_get_file_snippet_returns_requested_lines(
    context_service: ContextService,
) -> None:
    """get_file_snippet 应能读取指定行源码。"""
    login = _find_symbol(context_service, "app.api.auth.login")

    snippet = get_file_snippet(
        context_service,
        login["file_path"],
        login["start_line"],
        login["start_line"],
    )

    assert "def login" in snippet["source"]
    _assert_json_serializable(snippet)


def test_get_file_snippet_rejects_outside_repo(
    context_service: ContextService,
) -> None:
    """源码片段读取必须阻止仓库外路径。"""
    with pytest.raises(ValueError, match="escapes repository"):
        get_file_snippet(context_service, "../AGENTS.md", 1, 1)


def test_get_callees_login_returns_authenticate(
    context_service: ContextService,
) -> None:
    """get_callees(login) 应返回 authenticate。"""
    login = _find_symbol(context_service, "app.api.auth.login")

    callees = get_callees(context_service, login["node_id"])

    assert any(
        item["qualified_name"] == "app.services.user_service.authenticate"
        for item in callees
    )
    _assert_json_serializable(callees)


def test_get_callers_authenticate_returns_login(
    context_service: ContextService,
) -> None:
    """get_callers(authenticate) 应返回 login。"""
    authenticate = _find_symbol(
        context_service,
        "app.services.user_service.authenticate",
    )

    callers = get_callers(context_service, authenticate["node_id"])

    assert any(item["qualified_name"] == "app.api.auth.login" for item in callers)
    _assert_json_serializable(callers)


def test_trace_call_chain_login_to_find_by_username(
    context_service: ContextService,
) -> None:
    """trace_call_chain 应找到 login 到 find_by_username 的调用路径。"""
    login = _find_symbol(context_service, "app.api.auth.login")
    find_by_username = _find_symbol(
        context_service,
        "app.repositories.user_repo.find_by_username",
    )

    result = trace_call_chain(
        context_service,
        login["node_id"],
        find_by_username["node_id"],
        max_depth=4,
    )

    path_names = [item["qualified_name"] for item in result["path"]]
    assert result["found"] is True
    assert path_names[0] == "app.api.auth.login"
    assert path_names[-1] == "app.repositories.user_repo.find_by_username"
    _assert_json_serializable(result)


def test_explore_related_symbols_is_json_serializable(
    context_service: ContextService,
) -> None:
    """explore_related_symbols 返回相关节点和边，且结构可序列化。"""
    login = _find_symbol(context_service, "app.api.auth.login")

    result = explore_related_symbols(context_service, login["node_id"])

    assert result["center"]["qualified_name"] == "app.api.auth.login"
    assert result["nodes"]
    assert result["edges"]
    _assert_json_serializable(result)


def _find_symbol(service: ContextService, qualified_name: str) -> dict[str, Any]:
    name = qualified_name.rsplit(".", 1)[-1]
    matches = [
        item
        for item in service.search_symbol(name, limit=50)
        if item["qualified_name"] == qualified_name
    ]
    assert matches
    return matches[0]


def _assert_json_serializable(value: Any) -> None:
    json.dumps(value, ensure_ascii=False)


@pytest.fixture()
def context_service(tmp_path: Path) -> ContextService:
    db_path = tmp_path / "context.db"
    build_index("sample-repo", SAMPLE_REPO, db_path)
    return ContextService(
        repo_id="sample-repo",
        repo_root=SAMPLE_REPO,
        db_path=db_path,
    )
