from pathlib import Path

from fastapi.testclient import TestClient

from repo_context.api.app import app


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_demo_page_loads() -> None:
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Agent Context Debug Console" in response.text
    assert "Tool Debugger" in response.text
    assert "apiClient" in response.text


def test_demo_console_route_loads() -> None:
    client = TestClient(app)

    response = client.get("/demo")

    assert response.status_code == 200
    assert "Agent Context Debug Console" in response.text
    assert "API Logs" in response.text


def test_demo_task_detail_page_loads() -> None:
    client = TestClient(app)

    response = client.get("/demo/sample-repo/tasks/task_route_post_login")

    assert response.status_code == 200
    assert "Agent Context Debug Console" in response.text
    assert "Reload Task" in response.text


def test_demo_index_returns_outputs(tmp_path: Path) -> None:
    client = TestClient(app)

    response = client.post(
        "/demo/index",
        json={
            "repo_id": "frontend-sample",
            "repo_path": str(SAMPLE_REPO),
            "db_path": str(tmp_path / "frontend-sample.db"),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["repo_summary"]["repo_id"] == "frontend-sample"
    assert data["review_tasks"]
    assert data["task_coverage_report"]
    assert data["usage_coverage_report"]


def test_demo_task_context_and_coverage(tmp_path: Path) -> None:
    client = TestClient(app)
    client.post(
        "/demo/index",
        json={
            "repo_id": "frontend-context",
            "repo_path": str(SAMPLE_REPO),
            "db_path": str(tmp_path / "frontend-context.db"),
        },
    )

    context_response = client.get(
        "/demo/frontend-context/tasks/task_route_post_login/context"
    )
    coverage_response = client.get("/demo/frontend-context/coverage")

    assert context_response.status_code == 200
    assert context_response.json()["task_id"] == "task_route_post_login"
    assert coverage_response.status_code == 200
    assert "usage_coverage_report" in coverage_response.json()
