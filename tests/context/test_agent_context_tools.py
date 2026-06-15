import json
from pathlib import Path

import pytest

from repo_context.index.index_builder import build_index
from repo_context.service.context_service import ContextService
from repo_context.service.coverage_service import CoverageService
from repo_context.task.review_task_generator import ReviewTaskGenerator


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_get_file_snippet_returns_content_and_records_usage(
    services: tuple[ContextService, CoverageService],
) -> None:
    context_service, coverage_service = services

    snippet = context_service.get_file_snippet(
        "app/api/auth.py",
        1,
        4,
        task_id="task_route_post_login",
        review_dimension="security",
    )
    report = coverage_service.get_coverage_report()

    assert snippet["content"]
    assert snippet["file_path"] == "app/api/auth.py"
    assert any(
        item["tool_name"] == "get_file_snippet"
        and item["target_type"] == "file"
        and item["lines_returned"] == 4
        for item in report["usage_records"]
    )
    _assert_json_serializable(snippet)


def test_get_node_detail_returns_code_position_and_edges(context_service: ContextService) -> None:
    login = _find_symbol(context_service, "app.api.auth.login")

    detail = context_service.get_node_detail(login["node_id"], include_source=True)

    assert detail is not None
    assert detail["name"] == "login"
    assert detail["file_path"] == "app/api/auth.py"
    assert "def login" in detail["code"]
    assert detail["callees"]
    assert "callers" in detail
    _assert_json_serializable(detail)


def test_get_related_context_returns_expandable_context(
    context_service: ContextService,
) -> None:
    task = {
        "task_id": "review-auth-routes-security-001",
        "target": {
            "type": "file",
            "file_path": "app/api/auth.py",
            "symbols": ["login"],
        },
        "review_dimension": "security",
        "tags": ["api_entry", "auth"],
        "max_depth": 2,
        "max_files": 5,
    }

    related = context_service.get_related_context(task)

    assert related["target_file"] == "app/api/auth.py"
    assert related["snippets"]
    assert related["related_symbols"]
    assert related["call_graph_slice"]["graph_scope"] == "local"
    _assert_json_serializable(related)


def test_context_usage_records_graph_and_batch_calls(
    services: tuple[ContextService, CoverageService],
) -> None:
    context_service, coverage_service = services
    login = _find_symbol(context_service, "app.api.auth.login")

    context_service.get_callees(
        login["node_id"],
        task_id="task_route_post_login",
        review_dimension="security",
    )
    context_service.get_related_context(
        {
            "task_id": "task_route_post_login",
            "target": {"type": "file", "file_path": "app/api/auth.py", "symbols": ["login"]},
            "review_dimension": "security",
        }
    )
    report = coverage_service.get_coverage_report()

    assert any(item["tool_name"] == "get_callees" and item["target_type"] == "graph" for item in report["usage_records"])
    assert any(item["tool_name"] == "get_related_context" and item["target_type"] == "batch_context" for item in report["usage_records"])


def test_supplement_review_task_has_full_package(
    services: tuple[ContextService, CoverageService],
) -> None:
    context_service, coverage_service = services
    context_service.get_file_snippet("app/api/auth.py", 1, 2)

    task = next(
        item
        for item in coverage_service.generate_uncovered_file_reviews()
        if item.target == "app/services/user_service.py"
    )
    package = task.to_dict()

    assert task.task_type == "uncovered_file_review"
    assert package["initial_context"]
    assert package["available_tools"]
    assert package["context_policy"]


def test_path_traversal_is_rejected(context_service: ContextService) -> None:
    with pytest.raises(ValueError, match="escapes repository"):
        context_service.get_file_snippet("../../secret.txt", 1, 1)


def _find_symbol(service: ContextService, qualified_name: str) -> dict[str, object]:
    name = qualified_name.rsplit(".", 1)[-1]
    matches = [
        item
        for item in service.search_symbol(name, limit=50)
        if item["qualified_name"] == qualified_name
    ]
    assert matches
    return matches[0]


def _assert_json_serializable(value: object) -> None:
    json.dumps(value, ensure_ascii=False)


@pytest.fixture()
def context_service(tmp_path: Path) -> ContextService:
    db_path = tmp_path / "context.db"
    build_index("sample-repo", SAMPLE_REPO, db_path)
    return ContextService("sample-repo", SAMPLE_REPO, db_path)


@pytest.fixture()
def services(context_service: ContextService) -> tuple[ContextService, CoverageService]:
    task_generator = ReviewTaskGenerator(context_service)
    return context_service, CoverageService(context_service, task_generator)
