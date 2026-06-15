import json
from pathlib import Path

import pytest

from repo_context.index.index_builder import build_index
from repo_context.service.context_service import ContextService
from repo_context.service.coverage_service import CoverageService
from repo_context.task.review_task_generator import ReviewTaskGenerator


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_get_node_detail_records_node_id(stage7_services: tuple[ContextService, CoverageService]) -> None:
    """调用 get_node_detail 应记录 node_id。"""
    context_service, _ = stage7_services
    login = _find_symbol(context_service, "app.api.auth.login")

    context_service.get_node_detail(login["node_id"], task_id="task_route_post_login")
    usage = context_service.store.list_context_usage(context_service.repo_id)

    assert any(item.node_id == login["node_id"] for item in usage)
    assert any(item.tool_name == "get_node_detail" for item in usage)


def test_get_file_snippet_records_file_path(
    stage7_services: tuple[ContextService, CoverageService],
) -> None:
    """调用 get_file_snippet 应记录 file_path。"""
    context_service, _ = stage7_services

    context_service.get_file_snippet(
        "app/api/auth.py",
        1,
        3,
        task_id="task_route_post_login",
    )
    usage = context_service.store.list_context_usage(context_service.repo_id)

    assert any(item.file_path == "app/api/auth.py" for item in usage)
    assert any(item.tool_name == "get_file_snippet" for item in usage)


def test_can_calculate_file_coverage(
    stage7_services: tuple[ContextService, CoverageService],
) -> None:
    """覆盖率报告应能统计文件覆盖率。"""
    context_service, coverage_service = stage7_services
    context_service.get_file_snippet("app/api/auth.py", 1, 3)

    report = coverage_service.get_coverage_report()

    assert report["file_coverage"] > 0
    assert "app/api/auth.py" in report["covered_files"]
    _assert_json_serializable(report)


def test_can_calculate_node_coverage(
    stage7_services: tuple[ContextService, CoverageService],
) -> None:
    """覆盖率报告应能统计节点覆盖率。"""
    context_service, coverage_service = stage7_services
    login = _find_symbol(context_service, "app.api.auth.login")
    context_service.get_node_detail(login["node_id"])

    report = coverage_service.get_coverage_report()

    assert report["node_coverage"] > 0
    assert login["node_id"] in report["covered_nodes"]


def test_can_list_uncovered_files(
    stage7_services: tuple[ContextService, CoverageService],
) -> None:
    """覆盖率报告应列出未覆盖文件。"""
    context_service, coverage_service = stage7_services
    context_service.get_file_snippet("app/api/auth.py", 1, 3)

    report = coverage_service.get_coverage_report()

    assert report["uncovered_files"]
    assert "app/services/user_service.py" in report["uncovered_files"]


def test_uncovered_files_generate_uncovered_file_review(
    stage7_services: tuple[ContextService, CoverageService],
) -> None:
    """未覆盖源码文件应生成 uncovered_file_review。"""
    context_service, coverage_service = stage7_services
    context_service.get_file_snippet("app/api/auth.py", 1, 3)

    tasks = coverage_service.generate_uncovered_file_reviews()

    assert any(
        item.task_type == "uncovered_file_review"
        and item.target == "app/services/user_service.py"
        for item in tasks
    )


def test_stage7_end_to_end_flow(tmp_path: Path) -> None:
    """端到端流程：构建索引、生成任务、查询工具、记录覆盖率、输出报告。"""
    db_path = tmp_path / "context.db"
    build_index("sample-repo", SAMPLE_REPO, db_path)
    context_service = ContextService("sample-repo", SAMPLE_REPO, db_path)
    task_generator = ReviewTaskGenerator(context_service)
    coverage_service = CoverageService(context_service, task_generator)
    plan = task_generator.generate()
    route_task = next(item for item in plan.review_tasks if item.task_id == "task_route_post_login")

    context_service.get_node_detail(route_task.seed_node_id, task_id=route_task.task_id)
    context_service.get_file_snippet(
        route_task.related_files[0],
        1,
        3,
        task_id=route_task.task_id,
    )
    report = coverage_service.get_coverage_report()
    uncovered_tasks = coverage_service.generate_uncovered_file_reviews()

    assert context_service.store.list_context_usage("sample-repo")
    assert report["repo_id"] == "sample-repo"
    assert report["task_completion_rate"] > 0
    assert uncovered_tasks
    _assert_json_serializable(report)
    _assert_json_serializable([item.to_dict() for item in uncovered_tasks])


def _find_symbol(context_service: ContextService, qualified_name: str) -> dict[str, object]:
    name = qualified_name.rsplit(".", 1)[-1]
    matches = [
        item
        for item in context_service.search_symbol(name, limit=50)
        if item["qualified_name"] == qualified_name
    ]
    assert matches
    return matches[0]


def _assert_json_serializable(value: object) -> None:
    json.dumps(value, ensure_ascii=False)


@pytest.fixture()
def stage7_services(tmp_path: Path) -> tuple[ContextService, CoverageService]:
    db_path = tmp_path / "context.db"
    build_index("sample-repo", SAMPLE_REPO, db_path)
    context_service = ContextService("sample-repo", SAMPLE_REPO, db_path)
    task_generator = ReviewTaskGenerator(context_service)
    return context_service, CoverageService(context_service, task_generator)
