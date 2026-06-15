# 新 Agent 接入指南

本文说明如果要接入新的下游 agent，应该怎么新建目录、复用现有 client、添加测试，以及如何保持和上下文模块解耦。

## 目录约定

现有基础 agent 包在：

```text
agent/review_agent/
├── clients/
├── config/
└── core/
```

新增 agent 有两种推荐方式。

方式一：在 `review_agent/core/` 下新增一个编排类。适合只是换任务策略、提示词或反馈逻辑，但仍复用同一套 context API client 和 LLM client。

```text
agent/review_agent/core/
├── basic_agent.py
└── security_agent.py
```

方式二：在 `agent/` 下新增独立 agent 包。适合 agent 的职责、配置、模型调用或执行流程明显不同。

```text
agent/
├── review_agent/
└── security_review_agent/
    ├── clients/
    ├── config/
    └── core/
```

如果只是“多一个评审维度”，优先用方式一；如果是“另一类 agent 产品”，再用方式二。

## 解耦规则

- agent 通过 HTTP 调用上下文服务，不直接 import `repo_context` 内部模块。
- agent 只依赖上下文模块公开接口：`tasks`、`task-package`、`graph-slice`、`related-context`、`file-snippet`、`node-detail`、`callers`、`callees`、`task-feedback`。
- agent 不读取完整仓库源码，不请求完整仓库 graph。
- agent 不把最终漏洞报告写回上下文模块；上下文模块只接收任务状态和上下文需求反馈。
- 前端单独放 `frontend/`，不要放进 `agent/` 或 `context/`。

## 新建步骤

1. 确认上下文服务已启动：

```powershell
$env:PYTHONPATH = "context"
python -m uvicorn repo_context.api.app:app --host 127.0.0.1 --port 8000 --reload
```

2. 在 `agent/review_agent/core/` 新建 agent 编排文件，例如：

```text
agent/review_agent/core/security_agent.py
```

3. 复用已有 client：

```python
from review_agent.clients import ContextApiClient, LLMReviewClient
from review_agent.config import DownstreamAgentConfig
```

4. 按标准顺序调用上下文：

```text
GET  /context/tasks?repo_id={repo_id}&review_dimension={review_dimension}
GET  /context/task-package/{task_id}
GET  /context/tasks/{task_id}/graph-slice
POST /context/related-context
POST /context/task-feedback
```

`review_dimension` 必须使用固定枚举：`security`、`function_logic`、`coding_style`、`requirement_consistency`。

5. 在 `tests/agent/` 下新增对应测试：

```text
tests/agent/test_security_agent.py
```

6. 如果新增独立 agent 包，需要在 `pytest.ini` 的 `pythonpath` 中保持 `agent` 路径可用，不要把包路径写死到上下文模块里。

## 环境变量

基础配置：

```powershell
$env:CONTEXT_API_BASE_URL = "http://127.0.0.1:8000"
$env:REVIEW_AGENT_PROVIDER = "openai"
$env:OPENAI_API_KEY = "<your-api-key>"
$env:OPENAI_MODEL = "<model-name>"
```

可选配置：

```powershell
$env:REVIEW_AGENT_NAME = "security-review-agent"
$env:REVIEW_AGENT_MODEL = "<override-model>"
$env:REVIEW_AGENT_BASE_URL = "<openai-compatible-base-url>"
```

## 测试约定

测试按模块分组：

```text
tests/
├── agent/
├── context/
└── fixtures/
```

新增 agent 测试放 `tests/agent/`。测试应优先使用 fake HTTP client 或 `httpx.MockTransport`，不要依赖真实 API key。

运行 agent 测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\agent -q
```

运行全量测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 最小实现清单

- 新 agent 有独立类名和清晰入口。
- 新 agent 先通过 `/context/tasks` 按固定 `review_dimension` 查询任务。
- 调用上下文 API 时携带 `repo_id`、`task_id`、`review_dimension`。
- 任务结束后调用 `/context/task-feedback`。
- 缺少上下文时返回 `blocked` 和 `requested_context`。
- 有对应单元测试，且不需要真实模型 API 才能通过。
