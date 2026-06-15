import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from repo_context.api.app import app
from repo_context.index.index_builder import build_index
from repo_context.service.context_service import ContextService
from repo_context.service.coverage_service import CoverageService
from repo_context.task.review_task_generator import ReviewTaskGenerator


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_get_task_graph_slice_returns_local_graph(
    context_service: ContextService,
) -> None:
    graph = context_service.get_task_graph_slice("task_route_post_login", depth=2)

    assert graph["task_id"] == "task_route_post_login"
    assert graph["graph_scope"] == "task-local"
    assert graph["target"]["target"]["file_path"] == "app/api/auth.py"
    assert graph["depth"] == 2
    assert graph["nodes"]
    assert graph["edges"]
    assert any(item["is_target"] for item in graph["nodes"])
    assert all(
        {"relation_to_target", "priority", "risk_score", "reason"}.issubset(item)
        for item in graph["nodes"]
    )
    _assert_json_serializable(graph)


def test_get_task_graph_slice_guides_target_and_risky_nodes(
    context_service: ContextService,
) -> None:
    graph = context_service.get_task_graph_slice("task_route_post_login", depth=2)
    target_nodes = [node for node in graph["nodes"] if node["is_target"]]
    risky_auth_nodes = [
        node
        for node in graph["nodes"]
        if "auth" in f"{node['name']} {node['file_path']}".lower()
    ]

    assert target_nodes
    assert all(node["priority"] == 100 for node in target_nodes)
    assert all(node["relation_to_target"] == "target" for node in target_nodes)
    assert risky_auth_nodes
    assert any(node["risk_score"] > 0 for node in risky_auth_nodes)


def test_get_task_graph_slice_sorts_nodes_by_guidance(
    context_service: ContextService,
) -> None:
    graph = context_service.get_task_graph_slice("task_route_post_login", depth=2)
    sort_keys = [
        (-node["priority"], -node["risk_score"], node["name"])
        for node in graph["nodes"]
    ]

    assert sort_keys == sorted(sort_keys)
    assert graph["nodes"][0]["priority"] == 100


def test_get_task_graph_slice_respects_depth_limit(
    context_service: ContextService,
) -> None:
    depth_1 = context_service.get_task_graph_slice("task_route_post_login", depth=1)
    depth_2 = context_service.get_task_graph_slice("task_route_post_login", depth=2)

    assert depth_1["depth"] == 1
    assert len(depth_1["nodes"]) <= len(depth_2["nodes"])
    assert len(depth_1["edges"]) <= len(depth_2["edges"])
    assert depth_1["boundary_nodes"]
    assert depth_1["truncated"] is True


def test_get_task_graph_slice_limits_to_task_files(
    context_service: ContextService,
    task_generator: ReviewTaskGenerator,
) -> None:
    task = next(
        item for item in task_generator.generate().review_tasks if item.task_id == "task_route_post_login"
    )
    allowed_node_ids = {
        node["node_id"]
        for node in context_service.build_task_local_graph_slice(
            [node.node_id for node in context_service._task_center_nodes(task)],
            depth=task.context_policy["max_graph_depth"],
        )["nodes"]
    }

    graph = context_service.get_task_graph_slice(task.task_id, depth=2)

    assert graph["nodes"]
    assert {node["node_id"] for node in graph["nodes"]}.issubset(allowed_node_ids)


def test_get_task_graph_slice_marks_boundary_nodes(context_service: ContextService) -> None:
    graph = context_service.get_task_graph_slice("task_route_post_login", depth=1)

    assert graph["boundary_nodes"]
    assert all("reason" in item for item in graph["boundary_nodes"])
    assert all(
        {"relation_to_target", "priority", "risk_score", "reason"}.issubset(item)
        for item in graph["boundary_nodes"]
    )
    assert all(item["relation_to_target"] == "boundary" for item in graph["boundary_nodes"])
    assert not {
        boundary["node_id"] for boundary in graph["boundary_nodes"]
    } & {
        node["node_id"] for node in graph["nodes"]
    }


def test_get_task_graph_slice_records_usage(
    services: tuple[ContextService, CoverageService],
) -> None:
    context_service, coverage_service = services

    context_service.get_task_graph_slice("task_route_post_login", depth=1)
    report = coverage_service.get_coverage_report()

    assert any(
        item["tool_name"] == "get_task_graph_slice"
        and item["target_type"] == "graph_slice"
        and item["task_id"] == "task_route_post_login"
        for item in report["usage_records"]
    )


def test_task_package_exposes_graph_slice_tool_and_policy(
    task_generator: ReviewTaskGenerator,
) -> None:
    task = next(
        item for item in task_generator.generate().review_tasks if item.task_id == "task_route_post_login"
    )
    package = task.to_dict()

    assert "get_task_graph_slice" in package["available_tools"]
    assert package["initial_context"]["type"] == "task_entry"
    assert package["initial_context"]["suggested_next_tool"] == "get_task_graph_slice"
    assert "call_graph_slice" not in package["initial_context"]
    assert package["context_policy"]["allow_task_graph_slice"] is True
    assert package["context_policy"]["allow_full_graph"] is False
    assert package["context_policy"]["prefer_graph_slice_first"] is True
    assert package["context_policy"]["max_graph_depth"] == 2


def test_task_graph_slice_api_returns_usage_and_local_graph(tmp_path: Path) -> None:
    client = TestClient(app)
    repo_id = "graph-slice-api"
    index_response = client.post(
        "/context/index",
        json={
            "repo_id": repo_id,
            "repo_path": str(SAMPLE_REPO),
            "db_path": str(tmp_path / "context.db"),
        },
    )
    response = client.get(
        "/context/tasks/task_route_post_login/graph-slice",
        params={"repo_id": repo_id, "depth": 1},
    )
    coverage = client.get(f"/demo/{repo_id}/coverage").json()["usage_coverage_report"]

    assert index_response.status_code == 200
    assert response.status_code == 200
    graph = response.json()
    assert graph["graph_scope"] == "task-local"
    assert graph["nodes"]
    assert graph["boundary_nodes"]
    assert any(item["target_type"] == "graph_slice" for item in coverage["usage_records"])


def test_existing_context_tools_stay_compatible(context_service: ContextService) -> None:
    snippet = context_service.get_file_snippet("app/api/auth.py", 1, 5)
    detail = context_service.get_node_detail(symbol_name="login")
    callees = context_service.get_callees(symbol_name="login", depth=1)
    callers = context_service.get_callers(symbol_name="authenticate", depth=1)
    related = context_service.get_related_context(
        {
            "task_id": "task_route_post_login",
            "target": {"type": "file", "file_path": "app/api/auth.py", "symbols": ["login"]},
            "review_dimension": "security",
        }
    )

    assert snippet["content"]
    assert detail is not None and detail["name"] == "login"
    assert callees
    assert callers
    assert related["snippets"]


def _assert_json_serializable(value: object) -> None:
    json.dumps(value, ensure_ascii=False)


@pytest.fixture()
def context_service(tmp_path: Path) -> ContextService:
    db_path = tmp_path / "context.db"
    build_index("sample-repo", SAMPLE_REPO, db_path)
    return ContextService("sample-repo", SAMPLE_REPO, db_path)


@pytest.fixture()
def task_generator(context_service: ContextService) -> ReviewTaskGenerator:
    return ReviewTaskGenerator(context_service)


@pytest.fixture()
def services(context_service: ContextService) -> tuple[ContextService, CoverageService]:
    task_generator = ReviewTaskGenerator(context_service)
    return context_service, CoverageService(context_service, task_generator)
