# AI Code Review Workspace

这是一个仓库级 AI 代码评审实验工程，当前拆成两个并行模块：

```text
context/
└── repo_context/      # 上下文处理模块：索引、AST、任务包、上下文 API

agent/
└── review_agent/      # 下游 agent：调用上下文 API 和模型 API
```

## 模块边界

- `context/` 负责把 Python 仓库整理成可查询、可追踪、可限制的上下文服务。
- `agent/` 负责按任务调用 `context` 暴露的 API，并把任务状态反馈回去。
- 两个模块通过 HTTP API 和环境配置解耦；`agent` 不直接调用 `repo_context` 内部实现。
- 前端后续应单独放在 `frontend/`，不要混入 `context/` 或 `agent/`。

## 快速开始

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

从仓库根目录启动上下文服务：

```powershell
$env:PYTHONPATH = "context"
python -m uvicorn repo_context.api.app:app --host 127.0.0.1 --port 8000 --reload
```

服务启动后还没有任务包。必须先构建索引，系统才会扫描仓库、写入 SQLite，并生成 `review_tasks`：

```powershell
$body = @{
  repo_id = "sample-repo"
  repo_path = "tests/fixtures/sample_repo"
  db_path = ".demo_data/sample-repo.db"
} | ConvertTo-Json

$response = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/context/index" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body

$response.review_tasks | Select-Object task_id, task_type, priority
```

这里的 `repo_id = "sample-repo"` 可以换成你自己的仓库分析 ID，例如 `"payment-service"`；`repo_path = "tests/fixtures/sample_repo"` 可以换成服务端本机可访问的真实 Python 仓库路径，例如 `"D:\demo_repos\my_python_repo"`。

构建索引后，后续任务查询、任务包获取、上下文工具调用和任务反馈都应继续使用同一个 `repo_id`。

构建索引成功后，下游 agent 先按自己的评审维度查询任务。`review_dimension` 必须使用固定枚举：`security`、`function_logic`、`coding_style`、`requirement_consistency`。

```powershell
$tasks = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/context/tasks?repo_id=sample-repo&review_dimension=security" `
  -Method Get

$taskId = $tasks.tasks[0].task_id

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/context/task-package/$taskId?repo_id=sample-repo" `
  -Method Get
```

后续上下文工具调用和 `POST /context/task-feedback` 也应继续携带这个 `task_id`。

下游 agent （我自己写的demo） 需要模型环境变量：

```powershell
$env:REVIEW_AGENT_PROVIDER = "openai"
$env:OPENAI_API_KEY = "<your-api-key>"
$env:OPENAI_MODEL = "<model-name>"
```

## 文档位置

- 上下文模块说明：[context/README.md](context/README.md)
- 上下文 API 文档：[context/docs/context-api-reference.md](context/docs/context-api-reference.md)
- 下游接入说明：[context/docs/downstream-agent-integration.md](context/docs/downstream-agent-integration.md)
- agent 模块说明：[agent/README.md](agent/README.md)
- 新 agent 接入指南：[agent/docs/new-agent-integration.md](agent/docs/new-agent-integration.md)
## Agent Context Debug Console

上下文服务内置一个轻量调试台，用于手动检查“任务包 → initial_context → task-local graph slice → 节点详情 → 源码片段 → usage 记录”的完整链路。它只是展示和调试层，不在前端计算 priority、risk_score、relation_to_target、reason，也不判断 task-local 边界。

启动服务后打开：

```text
http://127.0.0.1:8000/demo
```

常用调试流程：

1. 在左侧填写 `repo_id`、`repo_path`、`db_path`，点击 `POST /context/index` 构建索引。
2. 使用 `review_dimension` 和 `task_type` 过滤任务列表。
3. 点击任务后，页面会自动调用 `GET /context/task-package/{task_id}` 和 `GET /context/tasks/{task_id}/graph-slice?depth=2`。
4. 在 graph slice 的 nodes 或 boundary_nodes 表格里点击节点，会调用 `GET /context/node-detail`。
5. 在节点详情里可以继续手动调用 `get_callers`、`get_callees`、`get_related_context`、`get_file_snippet`。
6. 右侧 `Raw JSON`、`API Logs`、`Usage` 分别用于检查原始响应、接口状态码/耗时、context usage 覆盖记录。

也可以直接打开某个任务调试页：

```text
http://127.0.0.1:8000/demo/{repo_id}/tasks/{task_id}
```
