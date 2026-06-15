"""功能逻辑评审 agent。

这个 agent 面向固定 review_dimension = "function_logic"。它的目标不是直接
判断安全漏洞，也不是生成最终报告，而是围绕一个功能逻辑任务：
1. 从上下文服务领取任务；
2. 读取任务包和 task-local graph slice；
3. 调用所有必要的上下文工具补齐代码片段、符号详情和调用关系；
4. 把结构化上下文交给模型；
5. 通过 task-feedback 回传本轮处理状态。
"""

from dataclasses import asdict
from typing import Any

from review_agent.clients import ContextApiClient, LLMReviewClient, ReviewResult
from review_agent.config import DownstreamAgentConfig
from review_agent.core.context_toolbelt import ContextToolCall, ContextToolbelt, TaskRuntime


class FunctionLogicAgent:
    """功能逻辑维度的下游评审 agent。"""

    review_dimension = "function_logic"

    def __init__(
        self,
        *,
        agent_name: str,
        context_client: ContextApiClient,
        llm_client: LLMReviewClient,
        toolbelt: ContextToolbelt | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.context_client = context_client
        self.llm_client = llm_client
        self.toolbelt = toolbelt or ContextToolbelt(context_client)

    @classmethod
    def from_env(cls) -> "FunctionLogicAgent":
        """从环境变量创建 agent。

        需要的关键环境变量：
        - CONTEXT_API_BASE_URL：上下文服务地址，默认 http://127.0.0.1:8000
        - DEEPSEEK_API_KEY：DeepSeek key
        - REVIEW_AGENT_PROVIDER：可选；不填时如果存在 DEEPSEEK_API_KEY 会自动使用 deepseek
        - REVIEW_AGENT_MODEL：可选；DeepSeek 默认 v4-flash
        """

        config = DownstreamAgentConfig.from_env()
        return cls(
            agent_name=config.agent_name,
            context_client=ContextApiClient(
                config.context_api.base_url,
                timeout=config.context_api.timeout_seconds,
            ),
            llm_client=LLMReviewClient(config.llm_api),
        )

    def list_tasks(self, *, repo_id: str) -> list[dict[str, Any]]:
        """领取功能逻辑维度的任务列表。"""

        response = self.toolbelt.list_tasks(
            repo_id=repo_id,
            review_dimension=self.review_dimension,
        )
        tasks = response.get("tasks") or []
        return [task for task in tasks if isinstance(task, dict)]

    def run_next_task(self, *, repo_id: str) -> dict[str, Any] | None:
        """领取并执行一个待处理任务。

        当前策略很保守：按接口返回顺序选择第一个 pending 任务。如果没有 pending，
        就选择第一个任务；如果该维度没有任务，则返回 None。
        """

        tasks = self.list_tasks(repo_id=repo_id)
        if not tasks:
            return None
        task = next((item for item in tasks if item.get("status") == "pending"), tasks[0])
        return self.run_task(repo_id=repo_id, task_id=str(task["task_id"]))

    def run_task(
        self,
        *,
        repo_id: str,
        task_id: str,
        graph_depth: int = 2,
        related_max_depth: int = 1,
        related_max_files: int = 3,
        snippet_line_window: int = 80,
    ) -> dict[str, Any]:
        """执行一个功能逻辑评审任务。

        这里会显式调用上下文工具，而不是让模型自由决定是否调用外部接口。
        这样工具调用顺序可测试、可追踪，也符合当前 context 模块的边界设计。
        """

        task_package = self.toolbelt.get_task_package(repo_id=repo_id, task_id=task_id)
        runtime = _build_runtime(repo_id=repo_id, task_id=task_id, task_package=task_package)

        graph_slice = self.toolbelt.get_task_graph_slice(
            repo_id=repo_id,
            task_id=task_id,
            depth=graph_depth,
        )
        related_context = self.toolbelt.get_related_context(
            repo_id=repo_id,
            task_id=task_id,
            target_file=runtime.target_file,
            review_dimension=runtime.review_dimension,
            tags=runtime.tags,
            max_depth=related_max_depth,
            max_files=related_max_files,
        )
        tool_calls = self._collect_context(runtime, snippet_line_window=snippet_line_window)

        result = self.llm_client.review_task(
            task_package=task_package,
            graph_slice=graph_slice,
            related_context=related_context,
            tool_results=[asdict(call) for call in tool_calls],
        )
        feedback = self._submit_feedback(repo_id=repo_id, task_id=task_id, result=result)
        return {
            "repo_id": repo_id,
            "task_id": task_id,
            "review_dimension": runtime.review_dimension,
            "agent": self.agent_name,
            "available_tools": self.toolbelt.available_tools,
            "task_package": task_package,
            "graph_slice": graph_slice,
            "related_context": related_context,
            "tool_calls": [asdict(call) for call in tool_calls],
            "review_result": {
                "status": result.status,
                "context_sufficient": result.context_sufficient,
                "message": result.message,
                "requested_context": result.requested_context,
                "downstream_result_ref": result.downstream_result_ref,
            },
            "feedback": feedback,
        }

    def _collect_context(
        self,
        runtime: TaskRuntime,
        *,
        snippet_line_window: int,
    ) -> list[ContextToolCall]:
        """根据任务目标自动补充上下文。

        功能逻辑评审通常需要看三类信息：
        - 目标文件的局部源码片段；
        - 目标函数/类的定义细节；
        - 目标符号的上游调用者和下游被调用者。
        """

        calls: list[ContextToolCall] = []
        if runtime.target_file:
            calls.append(
                self.toolbelt.call_tool(
                    "get_file_snippet",
                    repo_id=runtime.repo_id,
                    file_path=runtime.target_file,
                    start_line=1,
                    end_line=snippet_line_window,
                    task_id=runtime.task_id,
                    review_dimension=runtime.review_dimension,
                )
            )

        for symbol_name in runtime.symbols:
            common_args = {
                "repo_id": runtime.repo_id,
                "symbol_name": symbol_name,
                "task_id": runtime.task_id,
                "review_dimension": runtime.review_dimension,
            }
            calls.append(self.toolbelt.call_tool("get_node_detail", **common_args))
            calls.append(self.toolbelt.call_tool("get_callees", depth=1, **common_args))
            calls.append(self.toolbelt.call_tool("get_callers", depth=1, **common_args))
        return calls

    def _submit_feedback(
        self,
        *,
        repo_id: str,
        task_id: str,
        result: ReviewResult,
    ) -> dict[str, Any]:
        """把模型处理结果反馈给上下文服务。"""

        need_more_context = not result.context_sufficient or result.status == "blocked"
        feedback_type = "context_request" if need_more_context else "task_status"
        return self.toolbelt.submit_task_feedback(
            repo_id=repo_id,
            task_id=task_id,
            agent=self.agent_name,
            status=result.status,
            context_sufficient=result.context_sufficient,
            feedback_type=feedback_type,
            message=result.message,
            need_more_context=need_more_context,
            requested_context=result.requested_context,
            downstream_result_ref=result.downstream_result_ref,
        )


def _build_runtime(
    *,
    repo_id: str,
    task_id: str,
    task_package: dict[str, Any],
) -> TaskRuntime:
    """从任务包里提取工具调用所需的运行参数。"""

    target = task_package.get("target") or {}
    if not isinstance(target, dict):
        target = {}
    symbols = target.get("symbols") or task_package.get("recommended_nodes") or []
    if not isinstance(symbols, list):
        symbols = []
    tags = task_package.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    return TaskRuntime(
        repo_id=repo_id,
        task_id=task_id,
        review_dimension=str(task_package.get("review_dimension") or FunctionLogicAgent.review_dimension),
        target_file=target.get("file_path") or None,
        symbols=[str(symbol) for symbol in symbols if symbol],
        tags=[str(tag) for tag in tags if tag],
    )
