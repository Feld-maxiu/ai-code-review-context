"""上下文工具封装层。

ContextApiClient 是 HTTP client，方法名直接对应 REST 接口。
ContextToolbelt 再往上包装一层，把这些 REST 接口变成 agent 可调用的
“工具”。这样核心 agent 不需要关心接口路径、HTTP method 和参数位置，
只需要按工具名调用即可。
"""

from dataclasses import dataclass, field
from typing import Any, Callable

from review_agent.clients import ContextApiClient


@dataclass(frozen=True)
class ContextToolCall:
    """一次上下文工具调用记录。

    name 是工具名，arguments 是传给工具的参数。result 用于保存工具返回值，
    error 用于保存失败原因。agent 最终可以把这些记录一起交给模型，便于模型
    理解已经读取过哪些上下文。
    """

    name: str
    arguments: dict[str, Any]
    result: Any | None = None
    error: str | None = None


@dataclass(frozen=True)
class TaskRuntime:
    """一个评审任务在 agent 内部的运行上下文。"""

    repo_id: str
    task_id: str
    review_dimension: str
    target_file: str | None = None
    symbols: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class ContextToolbelt:
    """把上下文服务接口统一封装成工具。

    当前覆盖的上下文接口：
    - POST /context/index
    - GET /context/tasks
    - GET /context/task-package/{task_id}
    - GET /context/tasks/{task_id}/graph-slice
    - POST /context/related-context
    - GET /context/file-snippet
    - GET /context/node-detail
    - GET /context/callees
    - GET /context/callers
    - POST /context/task-feedback
    """

    def __init__(self, context_client: ContextApiClient) -> None:
        self.context_client = context_client
        self._tools: dict[str, Callable[..., Any]] = {
            "build_index": self.build_index,
            "list_tasks": self.list_tasks,
            "get_task_package": self.get_task_package,
            "get_task_graph_slice": self.get_task_graph_slice,
            "get_related_context": self.get_related_context,
            "get_file_snippet": self.get_file_snippet,
            "get_node_detail": self.get_node_detail,
            "get_callees": self.get_callees,
            "get_callers": self.get_callers,
            "submit_task_feedback": self.submit_task_feedback,
        }

    @property
    def available_tools(self) -> list[str]:
        """返回当前 agent 可调用的工具名。"""

        return sorted(self._tools)

    def call_tool(self, name: str, **arguments: Any) -> ContextToolCall:
        """按工具名调用上下文接口。

        这里把成功和失败都包装成 ContextToolCall，方便上层 agent 保留工具轨迹。
        对于自动评审流程，工具失败通常意味着上下文不足，而不是整个进程必须崩掉。
        """

        tool = self._tools.get(name)
        if tool is None:
            return ContextToolCall(
                name=name,
                arguments=arguments,
                error=f"unknown context tool: {name}",
            )
        try:
            return ContextToolCall(
                name=name,
                arguments=arguments,
                result=tool(**arguments),
            )
        except Exception as exc:  # noqa: BLE001 - 工具层需要把任意接口错误转成轨迹
            return ContextToolCall(name=name, arguments=arguments, error=str(exc))

    def build_index(self, *, repo_id: str, repo_path: str, db_path: str | None = None) -> dict[str, Any]:
        return self.context_client.build_index(repo_id=repo_id, repo_path=repo_path, db_path=db_path)

    def list_tasks(self, *, repo_id: str, review_dimension: str) -> dict[str, Any]:
        return self.context_client.list_tasks(repo_id=repo_id, review_dimension=review_dimension)

    def get_task_package(self, *, repo_id: str, task_id: str) -> dict[str, Any]:
        return self.context_client.get_task_package(repo_id=repo_id, task_id=task_id)

    def get_task_graph_slice(self, *, repo_id: str, task_id: str, depth: int = 2) -> dict[str, Any]:
        return self.context_client.get_task_graph_slice(repo_id=repo_id, task_id=task_id, depth=depth)

    def get_related_context(
        self,
        *,
        repo_id: str,
        task_id: str,
        target_file: str | None,
        review_dimension: str | None,
        tags: list[str] | None = None,
        max_depth: int = 1,
        max_files: int = 3,
    ) -> dict[str, Any]:
        return self.context_client.get_related_context(
            repo_id=repo_id,
            task_id=task_id,
            target_file=target_file,
            review_dimension=review_dimension,
            tags=tags or [],
            max_depth=max_depth,
            max_files=max_files,
        )

    def get_file_snippet(
        self,
        *,
        repo_id: str,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        task_id: str | None = None,
        review_dimension: str | None = None,
    ) -> dict[str, Any]:
        return self.context_client.get_file_snippet(
            repo_id=repo_id,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            task_id=task_id,
            review_dimension=review_dimension,
        )

    def get_node_detail(
        self,
        *,
        repo_id: str,
        node_id: str | None = None,
        symbol_name: str | None = None,
        task_id: str | None = None,
        review_dimension: str | None = None,
    ) -> dict[str, Any]:
        return self.context_client.get_node_detail(
            repo_id=repo_id,
            node_id=node_id,
            symbol_name=symbol_name,
            task_id=task_id,
            review_dimension=review_dimension,
        )

    def get_callees(
        self,
        *,
        repo_id: str,
        node_id: str | None = None,
        symbol_name: str | None = None,
        depth: int = 1,
        task_id: str | None = None,
        review_dimension: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.context_client.get_callees(
            repo_id=repo_id,
            node_id=node_id,
            symbol_name=symbol_name,
            depth=depth,
            task_id=task_id,
            review_dimension=review_dimension,
        )

    def get_callers(
        self,
        *,
        repo_id: str,
        node_id: str | None = None,
        symbol_name: str | None = None,
        depth: int = 1,
        task_id: str | None = None,
        review_dimension: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.context_client.get_callers(
            repo_id=repo_id,
            node_id=node_id,
            symbol_name=symbol_name,
            depth=depth,
            task_id=task_id,
            review_dimension=review_dimension,
        )

    def submit_task_feedback(
        self,
        *,
        repo_id: str,
        task_id: str,
        agent: str,
        status: str,
        context_sufficient: bool,
        feedback_type: str,
        message: str | None = None,
        need_more_context: bool = False,
        requested_context: list[dict[str, Any]] | None = None,
        downstream_result_ref: str | None = None,
    ) -> dict[str, Any]:
        return self.context_client.submit_task_feedback(
            repo_id=repo_id,
            task_id=task_id,
            agent=agent,
            status=status,
            context_sufficient=context_sufficient,
            feedback_type=feedback_type,
            message=message,
            need_more_context=need_more_context,
            requested_context=requested_context or [],
            downstream_result_ref=downstream_result_ref,
        )
