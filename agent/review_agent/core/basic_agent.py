"""下游 agent 最小任务执行编排。"""

from typing import Any

from review_agent.clients import ContextApiClient, LLMReviewClient, ReviewResult
from review_agent.config import DownstreamAgentConfig


class BasicReviewAgent:
    """按标准顺序调用上下文模块，再把任务处理状态反馈回去。"""

    def __init__(
        self,
        *,
        agent_name: str,
        context_client: ContextApiClient,
        llm_client: LLMReviewClient,
    ) -> None:
        self.agent_name = agent_name
        self.context_client = context_client
        self.llm_client = llm_client

    @classmethod
    def from_env(cls) -> "BasicReviewAgent":
        config = DownstreamAgentConfig.from_env()
        return cls(
            agent_name=config.agent_name,
            context_client=ContextApiClient(
                config.context_api.base_url,
                timeout=config.context_api.timeout_seconds,
            ),
            llm_client=LLMReviewClient(config.llm_api),
        )

    def run_task(
        self,
        *,
        repo_id: str,
        task_id: str,
        graph_depth: int = 2,
        related_max_depth: int = 1,
        related_max_files: int = 3,
    ) -> dict[str, Any]:
        task_package = self.context_client.get_task_package(repo_id=repo_id, task_id=task_id)
        review_dimension = task_package.get("review_dimension")
        target = task_package.get("target") or {}
        target_file = target.get("file_path")
        tags = task_package.get("tags") or []

        graph_slice = self.context_client.get_task_graph_slice(
            repo_id=repo_id,
            task_id=task_id,
            depth=graph_depth,
        )
        related_context = self.context_client.get_related_context(
            repo_id=repo_id,
            task_id=task_id,
            target_file=target_file,
            review_dimension=review_dimension,
            tags=tags,
            max_depth=related_max_depth,
            max_files=related_max_files,
        )

        result = self.llm_client.review_task(
            task_package=task_package,
            graph_slice=graph_slice,
            related_context=related_context,
        )
        feedback = self._submit_feedback(repo_id=repo_id, task_id=task_id, result=result)
        return {
            "repo_id": repo_id,
            "task_id": task_id,
            "agent": self.agent_name,
            "task_package": task_package,
            "graph_slice": graph_slice,
            "related_context": related_context,
            "review_result": {
                "status": result.status,
                "context_sufficient": result.context_sufficient,
                "message": result.message,
                "requested_context": result.requested_context,
                "downstream_result_ref": result.downstream_result_ref,
            },
            "feedback": feedback,
        }

    def _submit_feedback(
        self,
        *,
        repo_id: str,
        task_id: str,
        result: ReviewResult,
    ) -> dict[str, Any]:
        need_more_context = not result.context_sufficient or result.status == "blocked"
        feedback_type = "context_request" if need_more_context else "task_status"
        return self.context_client.submit_task_feedback(
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
