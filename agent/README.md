# Review Agent

`agent/review_agent` 是下游 review agent 的基础模块。它不直接解析仓库，也不直接读取 `repo_context` 内部对象，而是通过上下文服务 API 工作。

## 目录结构

```text
agent/
├── docs/          # 新 agent 接入、扩展和协作说明
└── review_agent/
    ├── clients/   # 外部 API client：context API、LLM API
    ├── config/    # 环境变量配置
    └── core/      # agent 任务编排
```

## 调用流程

1. 调用 `/context/task-package/{task_id}` 获取任务包。
2. 调用 `/context/tasks/{task_id}/graph-slice` 获取 task-local graph slice。
3. 调用 `/context/related-context` 按需扩展上下文。
4. 调用模型 API 生成本轮任务状态。
5. 调用 `/context/task-feedback` 回传状态和上下文需求。

## 环境变量

```powershell
$env:CONTEXT_API_BASE_URL = "http://127.0.0.1:8000"
$env:REVIEW_AGENT_PROVIDER = "openai"
$env:OPENAI_API_KEY = "<your-api-key>"
$env:OPENAI_MODEL = "<model-name>"
```

也可以用 `REVIEW_AGENT_MODEL` 覆盖 provider 默认模型变量。

## 设计约束

- 不把前端代码放进 `agent/`。
- 不直接 import `repo_context` 内部模块。
- 不请求完整仓库源码。
- 不提交最终漏洞报告，只提交任务状态和上下文需求。

## 新 agent 接入

新增 agent 的目录、配置和测试约定见 [docs/new-agent-integration.md](docs/new-agent-integration.md)。

