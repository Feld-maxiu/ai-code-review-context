# API 文档与 Postman 使用说明

本目录保存从真实 FastAPI 应用导出的接口文档底稿和 Postman 请求集合。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `openapi.json` | FastAPI `app.openapi()` 导出的 OpenAPI 规范，可用于 Swagger、Postman、Apifox 等工具 |
| `postman_collection.json` | 下游 Agent 接入流程的 Postman Collection，已按调用顺序分组 |
| `../test-case-parameter-guide.md` | 测试用例和接口联调参数填写指南，说明 `repo_id`、`task_id`、`file_path`、`symbol_name` 等值应该从哪里来 |
| `../interface-test-cases.md` | 每个接口的多组请求参数、预期状态码和关键返回数据 |

## 启动后端

在仓库根目录运行：

```bash
$env:PYTHONPATH = "context"
python -m uvicorn repo_context.api.app:app --reload --port 8000
```

启动后可访问：

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/openapi.json
```

## 导入 Postman

1. 打开 Postman。
2. 点击 `Import`。
3. 选择 `context/docs/api/postman_collection.json`，或直接导入 `http://127.0.0.1:8000/openapi.json`。
4. 确认 collection 变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8000` | 后端服务地址 |
| `repo_id` | `sample-repo` | 仓库索引 ID |
| `repo_path` | `tests/fixtures/sample_repo` | 服务端本机可访问的仓库路径 |
| `task_id` | `task_route_post_login` | 示例任务 ID |
| `review_dimension` | `security` | 评审维度，固定枚举：`security`、`function_logic`、`coding_style`、`requirement_consistency` |
| `symbol_name` | `login` | 示例符号名 |

## 标准调用顺序

第一步 `POST /context/index` 必须成功执行。它会构建索引并生成 `review_tasks`；服务刚启动时还没有任务包。下游 agent 应先按自己的 `review_dimension` 调用 `/context/tasks` 查询任务，再使用返回的 `tasks[].task_id` 领取任务包。

```text
POST /context/index
GET  /context/tasks?repo_id={repo_id}&review_dimension={review_dimension}
GET  /context/task-package/{task_id}
GET  /context/tasks/{task_id}/graph-slice
POST /context/related-context
GET  /context/repo-files
GET  /context/file-snippet
GET  /context/file-content
GET  /context/node-detail
GET  /context/callees
GET  /context/callers
POST /context/task-feedback
```

下游 Agent 应优先使用 `task-package` 和 `graph-slice` 判断阅读路径，再按需调用源码和符号工具。任务完成、阻塞或上下文不足时，通过 `task-feedback` 反馈状态和上下文需求。

构建索引示例：

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

按评审维度查询任务并领取任务包示例：

```powershell
$tasks = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/context/tasks?repo_id=sample-repo&review_dimension=security" `
  -Method Get

$taskId = $tasks.tasks[0].task_id

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/context/task-package/$taskId?repo_id=sample-repo" `
  -Method Get
```

## 不知道参数怎么填时

先看 `context/docs/test-case-parameter-guide.md`。最小可跑通组合如下：

| 参数 | 示例值 |
| --- | --- |
| `repo_id` | `sample-repo` |
| `repo_path` | `tests/fixtures/sample_repo` |
| `db_path` | `.demo_data/sample-repo.db` |
| `task_id` | `task_route_post_login` |
| `file_path` / `target_file` | `app/api/auth.py` |
| `symbol_name` | `login` |
| `review_dimension` | `security`，固定枚举之一 |
| `depth` | `1` 或 `2` |
