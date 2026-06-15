"""下游评审 agent 的运行配置。

这个模块只从环境变量读取配置，不在代码里保存任何 API key。这样做有两个好处：
1. agent 代码可以提交到仓库，不会泄露密钥。
2. 不同机器、不同模型供应商只需要改环境变量，不需要改代码。
"""

from dataclasses import dataclass
import os


class LLMConfigError(RuntimeError):
    """模型 API 配置缺失或不完整时抛出的异常。"""


@dataclass(frozen=True)
class ContextApiConfig:
    """上下文服务的 HTTP 配置。"""

    base_url: str
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class LLMApiConfig:
    """模型服务配置。

    provider 用于决定请求格式。DeepSeek 这里按 OpenAI compatible chat
    completions 接口调用，也就是复用 /chat/completions 的请求结构。
    """

    provider: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class DownstreamAgentConfig:
    """下游 agent 的总配置入口。"""

    agent_name: str
    context_api: ContextApiConfig
    llm_api: LLMApiConfig

    @classmethod
    def from_env(cls) -> "DownstreamAgentConfig":
        """从系统环境变量读取上下文服务和模型服务配置。"""

        provider = _read_provider()
        return cls(
            agent_name=os.getenv("REVIEW_AGENT_NAME", "function-logic-agent"),
            context_api=ContextApiConfig(
                base_url=os.getenv("CONTEXT_API_BASE_URL", "http://127.0.0.1:8000"),
                timeout_seconds=float(os.getenv("CONTEXT_API_TIMEOUT", "30")),
            ),
            llm_api=_read_llm_config(provider),
        )


def _read_provider() -> str:
    """推断模型供应商。

    优先使用 REVIEW_AGENT_PROVIDER；如果没有显式指定，就根据已经存在的
    API key 环境变量推断。这里把 DeepSeek 放在 OpenAI 前面，是因为你给
    的 key 是 DeepSeek key，且本 agent 默认面向 DeepSeek v4-flash。
    """

    provider = os.getenv("REVIEW_AGENT_PROVIDER")
    if provider:
        return provider.lower()
    if os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise LLMConfigError(
        "缺少模型 API 配置：请设置 REVIEW_AGENT_PROVIDER，并提供对应 API key。"
    )


def _read_llm_config(provider: str) -> LLMApiConfig:
    """读取不同模型供应商对应的环境变量。"""

    default_model: str | None = None
    if provider == "deepseek":
        api_key_name = "DEEPSEEK_API_KEY"
        model_name = "DEEPSEEK_MODEL"
        default_base_url = "https://api.deepseek.com/v1"
        # DeepSeek API 不接受简写 v4-flash，必须使用完整模型名。
        default_model = "deepseek-v4-flash"
    elif provider == "openai":
        api_key_name = "OPENAI_API_KEY"
        model_name = "OPENAI_MODEL"
        default_base_url = "https://api.openai.com/v1"
    elif provider == "anthropic":
        api_key_name = "ANTHROPIC_API_KEY"
        model_name = "ANTHROPIC_MODEL"
        default_base_url = "https://api.anthropic.com/v1"
    else:
        raise LLMConfigError(f"不支持的 REVIEW_AGENT_PROVIDER: {provider}")

    api_key = os.getenv(api_key_name)
    model = os.getenv("REVIEW_AGENT_MODEL") or os.getenv(model_name) or default_model
    if not api_key:
        raise LLMConfigError(f"缺少 {api_key_name}")
    if not model:
        raise LLMConfigError(f"缺少 REVIEW_AGENT_MODEL 或 {model_name}")

    base_url = (
        os.getenv("REVIEW_AGENT_BASE_URL")
        or os.getenv(f"{provider.upper()}_BASE_URL")
        or default_base_url
    )
    return LLMApiConfig(
        provider=provider,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        timeout_seconds=float(os.getenv("REVIEW_AGENT_TIMEOUT", "60")),
    )
