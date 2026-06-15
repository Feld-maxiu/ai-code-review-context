# 下游工程师接入说明

本文面向接入本模块的下游 review agent / 工程服务。目标是说明：如何构建仓库上下文索引、如何领取任务包、如何按需查询上下文、如何记录上下文使用情况，以及如何回传评审过程中的状态。

接口参数和返回字段详见 `context/docs/context-api-reference.md`。Postman 导入文件见 `context/docs/api/postman_collection.json`。

## 接入流程

推荐流程：

1. 调用 `/context/index` 为目标仓库构建索引。
2. 下游 agent 根据自己的评审维度查询任务：`GET /context/tasks?repo_id=sample-repo&review_dimension=security`。
3. agent 从返回结果的 `tasks[].task_id` 中读取任务 ID。
4. agent 调用 `/context/task-package/{task_id}` 获取完整任务包。
5. agent 根据任务包中的 `initial_context`、`suggested_next_tool`、`available_tools` 调用上下文工具。
6. 每次上下文查询会自动写入 `context_usage`。
7. agent 调用 `/context/task-feedback` 反馈任务状态和上下文需求。

注意：上下文服务启动后不会自动生成任务包。必须先等待 `/context/index` 构建索引成功，再通过 `/context/tasks` 按 `review_dimension` 领取任务。

固定评审维度枚举：

| review_dimension | 说明 |
| --- | --- |
| `security` | 安全评审任务 |
| `function_logic` | 功能逻辑评审任务 |
| `coding_style` | 代码风格和可维护性任务 |
| `requirement_consistency` | 需求一致性任务，当前主要作为预留维度 |

## 1. 构建索引

请求：

```http
POST /context/index
Content-Type: application/json

{
  "repo_id": "sample-repo",
  "repo_path": "tests/fixtures/sample_repo",
  "db_path": ".demo_data/sample-repo.db"
}
```

PowerShell 示例：

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

`repo_id = "sample-repo"` 可以换成你自己的仓库分析 ID，例如 `"payment-service"`；`repo_path = "tests/fixtures/sample_repo"` 可以换成服务端本机可访问的真实 Python 仓库路径，例如 `"D:\demo_repos\my_python_repo"`。

字段说明：

- `repo_id`：本次仓库分析的唯一标识。
- `repo_path`：服务端本机可访问的 Python 仓库目录。
- `db_path`：SQLite 索引文件路径。

### repo_id 说明

`repo_id` 是一次仓库索引的统一标识，在调用 `POST /context/index` 时由调用方传入。

构建索引后，后续所有接口都应使用同一个 `repo_id`，包括任务查询、任务包获取、上下文工具调用和任务反馈。

`repo_id` 用于关联仓库摘要、任务列表、任务包、usage 记录和 task feedback。

返回重点字段：

- `repo_summary`：仓库摘要。
- `review_tasks`：下游 agent 要处理的任务列表。
- `task_coverage_report`：任务生成阶段的规划覆盖率。
- `usage_coverage_report`：实际上下文读取覆盖率。

## 2. 按评审维度查询任务

索引完成后，下游 agent 应根据自己的评审维度调用 `/context/tasks` 查询任务。构建索引成功前，任务包接口没有可领取的任务。

请求：

```http
GET /context/tasks?repo_id=sample-repo&review_dimension=security
```

返回中的 `tasks` 是该维度下的任务列表，每个任务都包含：

```json
{
  "task_id": "task_route_post_login",
  "task_type": "entrypoint_review",
  "review_dimension": "security",
  "priority": "high",
  "target": {
    "type": "file",
    "file_path": "app/api/auth.py",
    "symbols": ["POST /login", "login"]
  },
  "tags": ["api_entry", "auth"],
  "focus_points": ["输入校验", "身份认证", "异常处理"],
  "context_policy": {
    "max_depth": 2,
    "max_snippet_lines": 120,
    "max_files": 6,
    "allow_expand": true,
    "allow_task_graph_slice": true,
    "allow_full_graph": false,
    "prefer_graph_slice_first": true,
    "max_graph_depth": 2
  }
}
```

### task_id 说明

`task_id` 是 context 模块在生成 review task 时产生的任务标识。

下游 agent 可以在获取任务列表时获取 ID，也就是读取 `tasks[].task_id`。

下游 agent 拿到 `task_id` 后，再调用：

```http
GET /context/task-package/{task_id}?repo_id={repo_id}
```

获取完整任务包。

后续上下文工具调用和 `task-feedback` 也应继续携带该 `task_id`。

下游调度建议：

- 优先处理 `priority = high` 的任务。
- 同一个任务只分配给一个 review agent。
- 不要把所有任务同时强行分配给所有 agent。
- 不要绕过任务包直接读取完整仓库源码。
- `review_dimension` 必须使用固定枚举值，不要自造维度名。

## 3. 获取完整任务包

请求：

```http
GET /context/task-package/{task_id}?repo_id=sample-repo
```

示例：

```http
GET /context/task-package/task_route_post_login?repo_id=sample-repo
```

命令行示例：

```powershell
$taskId = $response.review_tasks[0].task_id

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/context/task-package/$taskId?repo_id=sample-repo" `
  -Method Get
```

返回重点字段：

- `target`：本任务目标文件和目标符号。
- `focus_points`：本任务建议关注点。
- `initial_context.type`：固定为 `task_entry`，表示这是轻量任务入口。
- `initial_context.suggested_next_tool`：通常为 `get_task_graph_slice`。
- `initial_context.file_path` / `symbols`：下游 agent 的首轮关注目标。
- `available_tools`：允许继续调用的上下文工具。
- `context_policy`：上下文扩展上限。

注意：阶段 9 后，`initial_context` 不再预塞源码片段、相关符号或调用图。下游 agent 应通过 graph slice 和上下文工具按需读取代码。

### 获取任务局部图

```http
GET /context/tasks/task_route_post_login/graph-slice?repo_id=sample-repo&depth=2
```

返回重点字段：

- `nodes`：任务范围内节点，包含 `relation_to_target`、`priority`、`risk_score`、`reason`。
- `edges`：任务范围内调用关系。
- `boundary_nodes`：超出任务范围或深度限制的相邻节点，也包含 `reason` 和 `risk_score`，但不会继续展开。
- `truncated`：是否发生截断。
- `graph_scope`：固定为 `task-local`。

下游 agent 应优先阅读 `priority` 高、`risk_score` 高、`relation_to_target` 为 `target` / `direct_callee` / `direct_caller` 的节点。约束：该接口只返回任务局部图，不返回完整仓库调用图。

## 4. 按需扩展上下文

### 读取源码片段

全量文件扫描场景（例如先拿文件列表，再逐个运行 ruff / pylint）：

```http
GET /context/repo-files?repo_id=sample-repo&include_tests=true
```

返回：

```json
{
  "repo_id": "sample-repo",
  "total": 2,
  "files": [
    {
      "file_path": "app/api/auth.py",
      "file_type": "python",
      "language": "python",
      "line_count": 13,
      "is_test": false
    }
  ]
}
```

```http
GET /context/file-snippet?repo_id=sample-repo&file_path=app/api/auth.py&start_line=1&end_line=80&task_id=task_route_post_login&review_dimension=security
```

返回：

```json
{
  "file_path": "app/api/auth.py",
  "start_line": 1,
  "end_line": 80,
  "content": "..."
}
```

约束：

- 只能读取仓库内路径。
- `../../secret.txt` 这类路径穿越会被拒绝。
- 不要一次请求过大范围源码。

需要完整文件内容时使用：

```http
GET /context/file-content?repo_id=sample-repo&file_path=app/api/auth.py&task_id=task_route_post_login&review_dimension=security
```

返回：

```json
{
  "file_path": "app/api/auth.py",
  "line_count": 13,
  "content": "...完整文件内容...",
  "source": "...完整文件内容..."
}
```

`/context/file-content` 同样只允许读取仓库内相对路径，并会记录 `get_file_content` usage，方便后续覆盖率统计。

### 获取符号详情

```http
GET /context/node-detail?repo_id=sample-repo&symbol_name=login&task_id=task_route_post_login&review_dimension=security
```

返回：

```json
{
  "node_id": "...",
  "name": "login",
  "qualified_name": "app.api.auth.login",
  "file_path": "app/api/auth.py",
  "start_line": 10,
  "end_line": 13,
  "code": "...",
  "callers": [],
  "callees": []
}
```

### 查询调用关系

```http
GET /context/callees?repo_id=sample-repo&symbol_name=login&depth=1&task_id=task_route_post_login&review_dimension=security
```

```http
GET /context/callers?repo_id=sample-repo&symbol_name=authenticate&depth=1&task_id=task_route_post_login&review_dimension=security
```

建议：

- 默认 `depth=1`。
- 需要更长链路时再提高到 `depth=2`。
- 不要要求完整仓库 graph。

### 获取相关上下文包

```http
POST /context/related-context
Content-Type: application/json

{
  "repo_id": "sample-repo",
  "task_id": "task_route_post_login",
  "target_file": "app/api/auth.py",
  "review_dimension": "security",
  "tags": ["api_entry", "auth"],
  "max_depth": 1,
  "max_files": 3
}
```

返回：

```json
{
  "task_id": "task_route_post_login",
  "target_file": "app/api/auth.py",
  "related_files": [],
  "related_symbols": [],
  "snippets": [],
  "call_graph_slice": {
    "graph_scope": "local",
    "center": "app.api.auth.login",
    "depth": 1,
    "nodes": [],
    "edges": []
  }
}
```

这是下游 agent 扩展上下文的首选入口。

## 5. Context Usage 自动记录

下游 agent 调用上下文接口时，服务会自动写入 `context_usage`。

会记录：

- `task_id`
- `review_dimension`
- `tool_name`
- `target_type`
- `target_name`
- `node_id`
- `file_path`
- `start_line`
- `end_line`
- `lines_returned`
- `created_at`

工具和 usage 类型对应关系：

| 工具 | target_type |
| --- | --- |
| `get_file_snippet` | `file` |
| `get_node_detail` | `symbol` |
| `get_callees` | `graph` |
| `get_callers` | `graph` |
| `trace_call_chain` | `graph` |
| `get_related_context` | `batch_context` |

usage 记录失败不会影响主查询返回。

## 6. 查看覆盖率

请求：

```http
GET /demo/{repo_id}/coverage
```

示例：

```http
GET /demo/sample-repo/coverage
```

返回中重点看：

- `usage_coverage_report.file_coverage`
- `usage_coverage_report.node_coverage`
- `usage_coverage_report.task_completion_rate`
- `usage_coverage_report.covered_files`
- `usage_coverage_report.uncovered_files`
- `usage_coverage_report.usage_records`
- `uncovered_file_reviews`

`uncovered_file_reviews` 可作为补充任务来源，用于覆盖尚未实际读取过的源码文件。

## 7. 下游评审反馈建议

当前模块不保存最终评审结论。如果下游系统需要回传评审结果，建议另建结果表或服务，至少保存：

```json
{
  "repo_id": "sample-repo",
  "task_id": "task_route_post_login",
  "agent": "security-review-agent",
  "status": "completed",
  "summary": "本任务已检查登录入口的输入校验、认证调用和 token 返回路径。",
  "findings": [
    {
      "severity": "medium",
      "file_path": "app/api/auth.py",
      "start_line": 10,
      "end_line": 13,
      "title": "错误信息需要避免暴露认证细节",
      "evidence_context": ["usage_000001", "usage_000002"]
    }
  ],
  "used_context": ["usage_000001", "usage_000002"],
  "created_at": "2026-06-01T00:00:00Z"
}
```

建议下游反馈包含：

- 任务状态：`pending` / `running` / `completed` / `blocked`。
- 使用过的 `context_usage` 记录。
- 结论引用的文件和行号。
- 如果上下文不足，说明还需要哪些工具或范围。

## 8. 接入注意事项

- 下游 agent 不要直接读取完整仓库源码。
- 下游 agent 不要要求完整仓库 graph。
- 优先使用任务包里的 `initial_context`。
- 上下文不足时，优先调用 `/context/related-context`。
- 所有工具返回值都是 JSON serializable。
- 路径必须是仓库内相对路径。
- 评审结论和风险判断属于下游 agent，不属于本上下文模块。
