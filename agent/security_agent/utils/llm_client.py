import json
import time
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: Dict[str, int]
    raw_response: Any = None


class LLMClient:
    def __init__(
        self,
        api_key: str = "",
        api_base: str = "https://api.openai.com/v1",
        model: str = "gpt-4",
        temperature: float = 0.1,
        max_tokens: int = 4096,
        max_retries: int = 3,
        retry_delay: float = 2.0
    ):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.api_base
                )
            except ImportError:
                raise ImportError(
                    "需要安装 openai 库: pip install openai"
                )

    def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None
    ) -> LLMResponse:
        self._ensure_client()

        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        last_error = None
        for attempt in range(self.max_retries):
            try:
                kwargs = {
                    "model": self.model,
                    "messages": full_messages,
                    "temperature": temperature or self.temperature,
                    "max_tokens": max_tokens or self.max_tokens,
                }
                if response_format:
                    kwargs["response_format"] = response_format

                response = self._client.chat.completions.create(**kwargs)

                choice = response.choices[0]
                return LLMResponse(
                    content=choice.message.content or "",
                    model=response.model,
                    usage={
                        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                        "total_tokens": response.usage.total_tokens if response.usage else 0,
                    },
                    raw_response=response
                )
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))
                continue

        raise RuntimeError(f"LLM调用失败，已重试{self.max_retries}次: {last_error}")

    def chat_with_json_output(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0
    ) -> Dict[str, Any]:
        response = self.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            temperature=temperature,
            response_format={"type": "json_object"}
        )
        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            import re
            match = re.search(r'\{[\s\S]*\}', response.content)
            if match:
                return json.loads(match.group(0))
            raise ValueError(f"无法解析LLM返回的JSON: {response.content[:500]}")

    def semantic_search(
        self,
        query: str,
        candidates: List[str],
        top_k: int = 5
    ) -> List[int]:
        if not candidates:
            return []

        prompt = f"""你是一个代码安全专家。请评估以下候选规则与查询的相关性。

查询: {query}

候选规则:
"""
        for i, candidate in enumerate(candidates):
            prompt += f"[{i}] {candidate[:300]}\n"

        prompt += f"\n请返回与查询最相关的 {top_k} 个候选规则的索引列表，以JSON数组格式返回，如 [0, 3, 5]"

        result = self.chat_with_json_output(prompt)
        if isinstance(result, list):
            return [int(i) for i in result if isinstance(i, (int, float)) and int(i) < len(candidates)]
        if isinstance(result, dict):
            for v in result.values():
                if isinstance(v, list):
                    return [int(i) for i in v if isinstance(i, (int, float)) and int(i) < len(candidates)]
        return list(range(min(top_k, len(candidates))))

    def generate_code(
        self,
        specification: str,
        language: str = "python"
    ) -> str:
        system_prompt = f"你是一个{language}编程专家。请根据需求编写干净、可用的{language}代码。只返回代码，不要解释。"
        response = self.chat(
            messages=[{"role": "user", "content": specification}],
            system_prompt=system_prompt,
            temperature=0.0
        )
        content = response.content
        code_pattern = rf'```{language}\s*\n(.*?)```'
        import re
        match = re.search(code_pattern, content, re.DOTALL)
        if match:
            return match.group(1).strip()
        match = re.search(r'```\s*\n(.*?)```', content, re.DOTALL)
        if match:
            return match.group(1).strip()
        return content.strip()

    def analyze_code_semantics(
        self,
        code: str,
        question: str
    ) -> str:
        system_prompt = """你是一位资深代码安全审计专家。请基于提供的代码片段回答安全问题。
分析要点:
1. 数据流追踪
2. 控制流分析
3. 潜在的安全漏洞
4. 漏洞的可利用性评估"""
        response = self.chat(
            messages=[{"role": "user", "content": f"代码:\n```\n{code}\n```\n\n问题: {question}"}],
            system_prompt=system_prompt,
            temperature=0.1
        )
        return response.content