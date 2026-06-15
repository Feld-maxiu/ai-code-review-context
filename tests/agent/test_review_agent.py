import json

import httpx
import pytest

from review_agent.config import DownstreamAgentConfig, LLMConfigError
from review_agent.clients import ContextApiClient, LLMReviewClient, ReviewResult
from review_agent.config import LLMApiConfig
from review_agent.core import BasicReviewAgent, ContextToolbelt, FunctionLogicAgent


def test_context_api_client_calls_standard_flow() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/context/task-package/task_1":
            return httpx.Response(200, json={"task_id": "task_1"})
        if request.url.path == "/context/tasks":
            return httpx.Response(200, json={"tasks": [{"task_id": "task_1"}]})
        if request.url.path == "/context/tasks/task_1/graph-slice":
            return httpx.Response(200, json={"nodes": []})
        if request.url.path == "/context/related-context":
            return httpx.Response(200, json={"snippets": []})
        if request.url.path == "/context/task-feedback":
            return httpx.Response(200, json={"accepted": True})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.Client(
        base_url="http://context.test",
        transport=httpx.MockTransport(handler),
    )
    client = ContextApiClient("http://context.test", client=http_client)

    assert client.get_task_package(repo_id="repo-1", task_id="task_1")["task_id"] == "task_1"
    assert client.list_tasks(repo_id="repo-1", review_dimension="security")["tasks"][0]["task_id"] == "task_1"
    assert client.get_task_graph_slice(repo_id="repo-1", task_id="task_1")["nodes"] == []
    assert client.get_related_context(
        repo_id="repo-1",
        task_id="task_1",
        target_file="app/api/auth.py",
        review_dimension="security",
    )["snippets"] == []
    assert client.submit_task_feedback(
        repo_id="repo-1",
        task_id="task_1",
        agent="basic-agent",
        status="completed",
        context_sufficient=True,
        feedback_type="task_status",
    )["accepted"] is True

    assert [request.url.path for request in requests] == [
        "/context/task-package/task_1",
        "/context/tasks",
        "/context/tasks/task_1/graph-slice",
        "/context/related-context",
        "/context/task-feedback",
    ]


def test_llm_review_client_parses_openai_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/chat/completions"
        assert body["model"] == "test-model"
        content = json.dumps(
            {
                "status": "completed",
                "context_sufficient": True,
                "message": "done",
                "requested_context": [],
                "downstream_result_ref": "result-1",
            }
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
        )

    llm = LLMReviewClient(
        LLMApiConfig(
            provider="openai",
            base_url="http://llm.test",
            api_key="test-key",
            model="test-model",
        ),
        client=httpx.Client(base_url="http://llm.test", transport=httpx.MockTransport(handler)),
    )

    result = llm.review_task(
        task_package={"task_id": "task_1"},
        graph_slice={"nodes": []},
        related_context={"snippets": []},
    )

    assert result.status == "completed"
    assert result.context_sufficient is True
    assert result.downstream_result_ref == "result-1"


def test_basic_review_agent_runs_task_and_submits_feedback() -> None:
    context_client = _FakeContextClient()
    agent = BasicReviewAgent(
        agent_name="basic-agent",
        context_client=context_client,
        llm_client=_FakeLLMClient(),
    )

    result = agent.run_task(repo_id="repo-1", task_id="task_1")

    assert result["feedback"]["accepted"] is True
    assert result["review_result"]["status"] == "completed"
    assert context_client.feedback_payload["agent"] == "basic-agent"
    assert context_client.feedback_payload["feedback_type"] == "task_status"
    assert context_client.calls == [
        "get_task_package",
        "get_task_graph_slice",
        "get_related_context",
        "submit_task_feedback",
    ]


def test_function_logic_agent_lists_and_runs_task_with_context_tools() -> None:
    context_client = _FakeContextClient()
    agent = FunctionLogicAgent(
        agent_name="function-logic-agent",
        context_client=context_client,
        llm_client=_FakeLLMClient(),
    )

    tasks = agent.list_tasks(repo_id="repo-1")
    result = agent.run_next_task(repo_id="repo-1")

    assert tasks[0]["task_id"] == "task_1"
    assert result is not None
    assert result["review_dimension"] == "function_logic"
    assert result["feedback"]["accepted"] is True
    assert {call["name"] for call in result["tool_calls"]} == {
        "get_file_snippet",
        "get_node_detail",
        "get_callees",
        "get_callers",
    }
    assert context_client.calls == [
        "list_tasks",
        "list_tasks",
        "get_task_package",
        "get_task_graph_slice",
        "get_related_context",
        "get_file_snippet",
        "get_node_detail",
        "get_callees",
        "get_callers",
        "submit_task_feedback",
    ]


def test_context_toolbelt_exposes_all_context_interfaces() -> None:
    toolbelt = ContextToolbelt(_FakeContextClient())

    assert toolbelt.available_tools == [
        "build_index",
        "get_callees",
        "get_callers",
        "get_file_snippet",
        "get_node_detail",
        "get_related_context",
        "get_task_graph_slice",
        "get_task_package",
        "list_tasks",
        "submit_task_feedback",
    ]


def test_config_from_env_requires_llm_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "REVIEW_AGENT_PROVIDER",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
    ]:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(LLMConfigError):
        DownstreamAgentConfig.from_env()


def test_config_from_env_uses_deepseek_v4_flash_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVIEW_AGENT_NAME", raising=False)
    monkeypatch.delenv("REVIEW_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("REVIEW_AGENT_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    config = DownstreamAgentConfig.from_env()

    assert config.agent_name == "function-logic-agent"
    assert config.llm_api.provider == "deepseek"
    assert config.llm_api.model == "deepseek-v4-flash"


def test_review_agent_keeps_runtime_layers_separated() -> None:
    from pathlib import Path

    import review_agent

    root = Path(review_agent.__path__[0])
    assert root.parent.name == "agent"
    package_dirs = {path.name for path in root.iterdir() if path.is_dir()}
    assert {"clients", "config", "core"}.issubset(package_dirs)
    assert "frontend" not in package_dirs


class _FakeContextClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.feedback_payload: dict[str, object] = {}

    def build_index(self, *, repo_id: str, repo_path: str, db_path: str | None = None) -> dict[str, object]:
        self.calls.append("build_index")
        return {"repo_id": repo_id, "review_tasks": [{"task_id": "task_1"}]}

    def list_tasks(self, *, repo_id: str, review_dimension: str) -> dict[str, object]:
        self.calls.append("list_tasks")
        return {
            "repo_id": repo_id,
            "review_dimension": review_dimension,
            "tasks": [{"task_id": "task_1", "status": "pending"}],
        }

    def get_task_package(self, *, repo_id: str, task_id: str) -> dict[str, object]:
        self.calls.append("get_task_package")
        return {
            "task_id": task_id,
            "review_dimension": "function_logic",
            "target": {"file_path": "app/services/user_service.py", "symbols": ["create_user"]},
            "tags": ["service"],
        }

    def get_task_graph_slice(self, *, repo_id: str, task_id: str, depth: int) -> dict[str, object]:
        self.calls.append("get_task_graph_slice")
        return {"nodes": [{"name": "login"}], "depth": depth}

    def get_related_context(
        self,
        *,
        repo_id: str,
        task_id: str,
        target_file: str | None,
        review_dimension: str | None,
        tags: list[str],
        max_depth: int,
        max_files: int,
    ) -> dict[str, object]:
        self.calls.append("get_related_context")
        return {"target_file": target_file, "snippets": []}

    def get_file_snippet(
        self,
        *,
        repo_id: str,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        task_id: str | None = None,
        review_dimension: str | None = None,
    ) -> dict[str, object]:
        self.calls.append("get_file_snippet")
        return {"file_path": file_path, "content": "def create_user(): ..."}

    def get_node_detail(
        self,
        *,
        repo_id: str,
        node_id: str | None = None,
        symbol_name: str | None = None,
        task_id: str | None = None,
        review_dimension: str | None = None,
    ) -> dict[str, object]:
        self.calls.append("get_node_detail")
        return {"symbol_name": symbol_name, "code": "def create_user(): ..."}

    def get_callees(
        self,
        *,
        repo_id: str,
        node_id: str | None = None,
        symbol_name: str | None = None,
        depth: int = 1,
        task_id: str | None = None,
        review_dimension: str | None = None,
    ) -> list[dict[str, object]]:
        self.calls.append("get_callees")
        return [{"source": symbol_name, "target": "repo.save"}]

    def get_callers(
        self,
        *,
        repo_id: str,
        node_id: str | None = None,
        symbol_name: str | None = None,
        depth: int = 1,
        task_id: str | None = None,
        review_dimension: str | None = None,
    ) -> list[dict[str, object]]:
        self.calls.append("get_callers")
        return [{"source": "api.create_user", "target": symbol_name}]

    def submit_task_feedback(self, **payload: object) -> dict[str, object]:
        self.calls.append("submit_task_feedback")
        self.feedback_payload = dict(payload)
        return {"accepted": True, **payload}


class _FakeLLMClient:
    def review_task(
        self,
        *,
        task_package: dict[str, object],
        graph_slice: dict[str, object],
        related_context: dict[str, object],
        tool_results: list[dict[str, object]] | None = None,
    ) -> ReviewResult:
        assert tool_results is None or tool_results
        return ReviewResult(
            status="completed",
            context_sufficient=True,
            message="done",
            downstream_result_ref="result-1",
        )
