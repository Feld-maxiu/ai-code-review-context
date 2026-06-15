"""模型 API 调用与评审结果解析。

这个 client 的职责很窄：把已经准备好的任务包和上下文材料发给模型，
再把模型返回的 JSON 解析成 ReviewResult。它不直接调用上下文服务，
也不直接决定还要读哪些代码；这些由 agent orchestration 层负责。
"""

from dataclasses import dataclass, field
import json
import re
from typing import Any

import httpx

from review_agent.config import LLMApiConfig


@dataclass(frozen=True)
class ReviewResult:
    """一次任务评审的标准输出。

    这些字段会被 BasicReviewAgent / FunctionLogicAgent 转成
    POST /context/task-feedback 的请求体，所以这里保持和反馈接口兼容。
    """

    status: str
    context_sufficient: bool
    message: str
    requested_context: list[dict[str, Any]] = field(default_factory=list)
    downstream_result_ref: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


class LLMReviewClient:
    """调用系统环境配置的模型 API。

    DeepSeek 的接口按 OpenAI compatible chat completions 调用，因此和
    OpenAI 分支共用同一套请求和响应解析逻辑。Anthropic 分支保留给后续
    如果要切 Claude 系列模型时使用。
    """

    def __init__(
        self,
        config: LLMApiConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "LLMReviewClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def review_task(
        self,
        *,
        task_package: dict[str, Any],
        graph_slice: dict[str, Any],
        related_context: dict[str, Any],
        tool_results: list[dict[str, Any]] | None = None,
    ) -> ReviewResult:
        """让模型基于当前上下文完成一次任务判断。

        tool_results 是 FunctionLogicAgent 额外调用 file-snippet、node-detail、
        callers、callees 等工具后的结果集合。这里把它作为上下文材料传给模型，
        不让模型自己直接访问外部接口，从而保证工具调用仍由 agent 代码控制。
        """

        prompt = _build_prompt(
            task_package=task_package,
            graph_slice=graph_slice,
            related_context=related_context,
            tool_results=tool_results or [],
        )
        if self._config.provider in {"openai", "deepseek"}:
            payload = self._call_openai_compatible(prompt)
            content = _extract_openai_content(payload)
        elif self._config.provider == "anthropic":
            payload = self._call_anthropic(prompt)
            content = _extract_anthropic_content(payload)
        else:
            raise ValueError(f"unsupported provider: {self._config.provider}")
        parsed = _parse_review_content(content)
        return ReviewResult(raw_response=payload, **parsed)

    def _call_openai_compatible(self, prompt: str) -> dict[str, Any]:
        """调用 OpenAI compatible 的 /chat/completions 接口。"""

        response = self._client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {self._config.api_key}"},
            json={
                "model": self._config.model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        return response.json()

    def _call_anthropic(self, prompt: str) -> dict[str, Any]:
        response = self._client.post(
            "/messages",
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "x-api-key": self._config.api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": self._config.model,
                "max_tokens": 1200,
                "temperature": 0,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        return response.json()


_SYSTEM_PROMPT = (
    "你是一个功能逻辑代码评审 agent。你只能基于用户提供的 task package、"
    "task-local graph slice 和上下文工具返回内容进行判断。不要假设未读取的代码，"
    "不要要求完整仓库源码。输出必须是 JSON 对象。"
)


def _build_prompt(
    *,
    task_package: dict[str, Any],
    graph_slice: dict[str, Any],
    related_context: dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> str:
    """构造模型输入。

    这里故意只传结构化上下文，不传 agent 内部对象。这样模型看到的内容
    和下游接口文档一致，也方便以后把同一套输入交给不同供应商模型。
    """

    payload = {
        "task_package": task_package,
        "task_graph_slice": graph_slice,
        "related_context": related_context,
        "additional_context_tool_results": tool_results,
        "required_output": {
            "status": "completed 或 blocked",
            "context_sufficient": "true 或 false",
            "message": "简短说明本轮处理结果、功能逻辑风险或阻塞原因",
            "requested_context": "如果 blocked，列出仍需要的上下文请求；否则为空数组",
            "downstream_result_ref": "可选，下游结果引用；没有则为 null",
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _extract_openai_content(payload: dict[str, Any]) -> str:
    return str(payload.get("choices", [{}])[0].get("message", {}).get("content", ""))


def _extract_anthropic_content(payload: dict[str, Any]) -> str:
    chunks = payload.get("content", [])
    texts = [str(item.get("text", "")) for item in chunks if isinstance(item, dict)]
    return "\n".join(text for text in texts if text)


def _parse_review_content(content: str) -> dict[str, Any]:
    """把模型输出解析成 task-feedback 可用字段。

    模型偶尔会在 JSON 前后带说明文字，所以这里先尝试直接 json.loads，
    失败后再从文本里截取第一个 JSON object。仍然失败时，把原文放进
    message，避免 agent 因模型格式问题中断整条任务流程。
    """

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        data = json.loads(match.group(0)) if match else {"message": content}

    status = str(data.get("status") or "completed")
    context_sufficient = bool(data.get("context_sufficient", status != "blocked"))
    requested_context = data.get("requested_context") or []
    if not isinstance(requested_context, list):
        requested_context = []
    return {
        "status": status,
        "context_sufficient": context_sufficient,
        "message": str(data.get("message") or "task processed"),
        "requested_context": requested_context,
        "downstream_result_ref": data.get("downstream_result_ref"),
    }
