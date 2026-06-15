# 接口测试用例与预期返回数据

本文档用于接口联调、Postman、Apifox 和 pytest 用例设计。每个接口都给出几组可直接使用的参数，以及预期状态码和关键返回数据。

## 使用前准备

除 `/health` 和 `/` 外，其他上下文接口都依赖一次已建立的索引。建议先调用：

```http
POST /context/index
Content-Type: application/json
```

```json
{
  "repo_id": "sample-repo",
  "repo_path": "tests/fixtures/sample_repo",
  "db_path": ".demo_data/sample-repo.db"
}
```

后续所有示例默认使用：

| 参数 | 示例值 |
| --- | --- |
| `repo_id` | `sample-repo` |
| `task_id` | `task_route_post_login` |
| `file_path` / `target_file` | `app/api/auth.py` |
| `symbol_name` | `login` |
| `review_dimension` | `security` |

## 1. GET /health

### 用例 1：服务健康

请求：

```http
GET /health
```

预期：

```json
{
  "status": "ok"
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `status` | `ok` |

## 2. GET /

### 用例 1：打开 Demo 页面

请求：

```http
GET /
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| Content-Type | `text/html` |
| 页面文本 | 包含 `AI 仓库级代码评审上下文台` 和 `评审任务` |

## 3. POST /context/index

### 用例 1：索引示例仓库

请求：

```json
{
  "repo_id": "sample-repo",
  "repo_path": "tests/fixtures/sample_repo",
  "db_path": ".demo_data/sample-repo.db"
}
```

预期：

```json
{
  "repo_summary": {
    "repo_id": "sample-repo",
    "framework": "fastapi",
    "python_files": 5
  },
  "review_tasks": [
    {
      "task_id": "task_route_post_login",
      "task_type": "entrypoint_review",
      "priority": "high"
    }
  ],
  "task_coverage_report": {
    "coverage_ratio": 1.0
  },
  "usage_coverage_report": {
    "file_coverage": 0
  }
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `repo_summary.repo_id` | 等于请求里的 `repo_id` |
| `repo_summary.framework` | `fastapi` |
| `review_tasks` | 非空 |
| `review_tasks[*].task_id` | 包含 `task_route_post_login` |
| 后续任务领取 | 建议调用 `/context/tasks?repo_id=sample-repo&review_dimension=security` 获取 `tasks[].task_id` |

### 用例 2：使用临时数据库路径

请求：

```json
{
  "repo_id": "api-context-file",
  "repo_path": "tests/fixtures/sample_repo",
  "db_path": "D:/tmp/api-context-file.db"
}
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `repo_summary.repo_id` | `api-context-file` |
| 后续查询 | 必须继续使用 `repo_id=api-context-file` |

### 用例 3：仓库路径不存在

请求：

```json
{
  "repo_id": "bad-repo",
  "repo_path": "not-exists",
  "db_path": ".demo_data/bad-repo.db"
}
```

预期：

```json
{
  "detail": "repo_path is not a directory: ..."
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `400` |
| `detail` | 包含 `repo_path is not a directory` |

## 4. POST /demo/index

该接口与 `/context/index` 行为一致，主要给 Demo 页面使用。

### 用例 1：Demo 索引示例仓库

请求：

```json
{
  "repo_id": "frontend-sample",
  "repo_path": "tests/fixtures/sample_repo",
  "db_path": ".demo_data/frontend-sample.db"
}
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `repo_summary.repo_id` | `frontend-sample` |
| `review_tasks` | 非空 |
| `task_coverage_report` | 存在 |
| `usage_coverage_report` | 存在 |

### 用例 2：路径错误

请求：

```json
{
  "repo_id": "frontend-bad",
  "repo_path": "missing_repo",
  "db_path": ".demo_data/frontend-bad.db"
}
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `400` |
| `detail` | 包含 `repo_path is not a directory` |

## 5. GET /context/tasks

该接口用于下游 agent 按固定评审维度领取任务。必须先成功调用 `/context/index` 构建索引。

固定 `review_dimension` 枚举：

| review_dimension | 说明 |
| --- | --- |
| `security` | 安全评审任务 |
| `function_logic` | 功能逻辑评审任务 |
| `coding_style` | 代码风格和可维护性任务 |
| `requirement_consistency` | 需求一致性任务，当前主要作为预留维度 |

### 用例 1：查询安全评审任务

请求：

```http
GET /context/tasks?repo_id=sample-repo&review_dimension=security
```

预期：

```json
{
  "repo_id": "sample-repo",
  "review_dimension": "security",
  "tasks": [
    {
      "task_id": "task_route_post_login",
      "review_dimension": "security"
    }
  ]
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `repo_id` | `sample-repo` |
| `review_dimension` | `security` |
| `tasks` | 非空 |
| `tasks[*].review_dimension` | 全部等于 `security` |
| `tasks[*].task_id` | 包含 `task_route_post_login` |

### 用例 2：查询功能逻辑评审任务

请求：

```http
GET /context/tasks?repo_id=sample-repo&review_dimension=function_logic
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `review_dimension` | `function_logic` |
| `tasks[*].review_dimension` | 全部等于 `function_logic` |

### 用例 3：非法评审维度

请求：

```http
GET /context/tasks?repo_id=sample-repo&review_dimension=unknown
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `422` |
| `detail` | 包含枚举校验错误 |

## 6. GET /context/task-package/{task_id}

### 用例 1：获取登录接口任务包

请求：

```http
GET /context/task-package/task_route_post_login?repo_id=sample-repo
```

预期：

```json
{
  "task_id": "task_route_post_login",
  "task_type": "entrypoint_review",
  "priority": "high",
  "target": {
    "file_path": "app/api/auth.py",
    "symbols": ["POST /login", "login"]
  },
  "initial_context": {
    "type": "task_entry",
    "suggested_next_tool": "get_task_graph_slice"
  },
  "context_policy": {
    "allow_task_graph_slice": true,
    "allow_full_graph": false,
    "max_graph_depth": 2
  }
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `task_id` | `task_route_post_login` |
| `target.file_path` | `app/api/auth.py` |
| `available_tools` | 包含 `get_task_graph_slice` |
| `initial_context` | 不包含完整源码和完整调用图 |

### 用例 2：获取配置文件任务包

请求：

```http
GET /context/task-package/task_config_app_config_py?repo_id=sample-repo
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `task_type` | `config_review` |
| `target.file_path` | `app/config.py` |
| `review_dimension` | `security` |
| `focus_points` | 包含敏感配置、DEBUG、Token 等关注点 |

### 用例 3：任务不存在

请求：

```http
GET /context/task-package/not-exist?repo_id=sample-repo
```

预期：

```json
{
  "detail": "task not found"
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `404` |
| `detail` | `task not found` |

## 7. GET /context/tasks/{task_id}/graph-slice

### 用例 1：获取 depth=1 的任务局部图

请求：

```http
GET /context/tasks/task_route_post_login/graph-slice?repo_id=sample-repo&depth=1
```

预期：

```json
{
  "task_id": "task_route_post_login",
  "depth": 1,
  "requested_depth": 1,
  "graph_scope": "task-local",
  "nodes": [
    {
      "name": "login",
      "file_path": "app/api/auth.py",
      "is_target": true,
      "relation_to_target": "target",
      "priority": 100
    }
  ],
  "edges": [],
  "boundary_nodes": [],
  "truncated": true
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `graph_scope` | `task-local` |
| `nodes` | 非空 |
| `nodes[*]` | 包含 `relation_to_target`、`priority`、`risk_score`、`reason` |
| `boundary_nodes` | depth 为 1 时通常非空 |

### 用例 2：获取 depth=2 的任务局部图

请求：

```http
GET /context/tasks/task_route_post_login/graph-slice?repo_id=sample-repo&depth=2
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `depth` | `2` |
| `nodes` | 数量大于或等于 depth=1 |
| `edges` | 数量大于或等于 depth=1 |
| `nodes[0].priority` | 通常为 `100`，目标节点优先 |

### 用例 3：任务不存在

请求：

```http
GET /context/tasks/not-exist/graph-slice?repo_id=sample-repo&depth=1
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `nodes` | `[]` |
| `edges` | `[]` |
| `graph_scope` | `task-local` |

说明：当前实现对不存在的 task graph slice 返回空图，而不是 404。

## 8. POST /context/related-context

### 用例 1：扩展登录任务上下文

请求：

```json
{
  "repo_id": "sample-repo",
  "task_id": "task_route_post_login",
  "target_file": "app/api/auth.py",
  "review_dimension": "security",
  "tags": ["api_entry", "auth"],
  "max_depth": 1,
  "max_files": 2
}
```

预期：

```json
{
  "task_id": "task_route_post_login",
  "target_file": "app/api/auth.py",
  "review_dimension": "security",
  "tags": ["api_entry", "auth"],
  "related_files": [
    "app/api/auth.py",
    "app/services/user_service.py"
  ],
  "related_symbols": [
    "app.api.auth.login",
    "route:POST /login",
    "app.services.user_service.authenticate"
  ],
  "snippets": [
    {
      "file_path": "app/api/auth.py",
      "content": "..."
    }
  ],
  "call_graph_slice": {
    "graph_scope": "local"
  }
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `related_files` | 数量小于等于 `max_files` |
| `snippets` | 非空 |
| `related_symbols` | 包含 `login` 或 `authenticate` 相关符号 |
| `call_graph_slice.graph_scope` | `local` |

### 用例 2：只传最小参数

请求：

```json
{
  "repo_id": "sample-repo"
}
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `related_files` | 可能为空 |
| `snippets` | 可能为空 |
| `call_graph_slice` | 存在 |

说明：这组用于验证接口兼容性。真实评审不建议只传 `repo_id`，因为相关性会很弱。

### 用例 3：缺少 repo_id

请求：

```json
{
  "task_id": "task_route_post_login",
  "target_file": "app/api/auth.py"
}
```

预期：

```json
{
  "detail": "repo_id is required"
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `400` |
| `detail` | `repo_id is required` |

## 9. GET /context/file-snippet

### 用例 1：读取目标文件前 5 行

请求：

```http
GET /context/file-snippet?repo_id=sample-repo&file_path=app/api/auth.py&start_line=1&end_line=5&task_id=task_route_post_login&review_dimension=security
```

预期：

```json
{
  "file_path": "app/api/auth.py",
  "start_line": 1,
  "end_line": 5,
  "content": "from fastapi import APIRouter\n\nfrom app.services.user_service import authenticate, authenticate_user\n\n",
  "source": "from fastapi import APIRouter\n\nfrom app.services.user_service import authenticate, authenticate_user\n\n"
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `content` | 非空 |
| `file_path` | `app/api/auth.py` |
| `start_line` | `1` |
| `end_line` | `5` |

### 用例 2：读取 login 函数附近源码

请求：

```http
GET /context/file-snippet?repo_id=sample-repo&file_path=app/api/auth.py&start_line=8&end_line=13&task_id=task_route_post_login&review_dimension=security
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `content` | 包含 `@router.post("/login")` |
| `content` | 包含 `def login` |

### 用例 3：路径穿越被拒绝

请求：

```http
GET /context/file-snippet?repo_id=sample-repo&file_path=../../secret.txt&start_line=1&end_line=1
```

预期：

```json
{
  "detail": "File path escapes repository: ../../secret.txt"
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `400` |
| `detail` | 包含 `escapes repository` |

## 10. GET /context/node-detail

### 用例 1：用 symbol_name 查询 login

请求：

```http
GET /context/node-detail?repo_id=sample-repo&symbol_name=login&task_id=task_route_post_login&review_dimension=security
```

预期：

```json
{
  "name": "login",
  "qualified_name": "app.api.auth.login",
  "file_path": "app/api/auth.py",
  "start_line": 10,
  "end_line": 13,
  "signature": "login(username: str, password: str)",
  "decorators": ["router.post(\"/login\")"],
  "code": "def login(...)",
  "callers": [],
  "callees": [
    {
      "name": "authenticate",
      "file_path": "app/services/user_service.py"
    }
  ]
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `name` | `login` |
| `qualified_name` | `app.api.auth.login` |
| `code` | 包含 `def login` |
| `callees` | 包含 `authenticate` |

### 用例 2：用 symbol_name 查询 authenticate

请求：

```http
GET /context/node-detail?repo_id=sample-repo&symbol_name=authenticate&task_id=task_route_post_login&review_dimension=security
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `name` | `authenticate` |
| `file_path` | `app/services/user_service.py` |
| `code` | 包含 `find_by_username` 或认证逻辑 |

### 用例 3：符号不存在

请求：

```http
GET /context/node-detail?repo_id=sample-repo&symbol_name=not_exist_symbol
```

预期：

```json
{
  "detail": "node not found"
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `404` |
| `detail` | `node not found` |

## 11. GET /context/callees

### 用例 1：查询 login 的直接下游

请求：

```http
GET /context/callees?repo_id=sample-repo&symbol_name=login&depth=1&task_id=task_route_post_login&review_dimension=security
```

预期：

```json
[
  {
    "name": "authenticate",
    "qualified_name": "app.services.user_service.authenticate",
    "file_path": "app/services/user_service.py"
  }
]
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| 返回类型 | array |
| 数组元素 | 包含 `authenticate` |

### 用例 2：查询 authenticate 的下游

请求：

```http
GET /context/callees?repo_id=sample-repo&symbol_name=authenticate&depth=1&task_id=task_route_post_login&review_dimension=security
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| 返回类型 | array |
| 数组元素 | 通常包含用户查询或 token 生成相关下游，具体以 AST 解析结果为准 |

### 用例 3：符号不存在

请求：

```http
GET /context/callees?repo_id=sample-repo&symbol_name=not_exist_symbol&depth=1
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| 返回体 | `[]` |

## 12. GET /context/callers

### 用例 1：查询 authenticate 的调用者

请求：

```http
GET /context/callers?repo_id=sample-repo&symbol_name=authenticate&depth=1&task_id=task_route_post_login&review_dimension=security
```

预期：

```json
[
  {
    "name": "login",
    "qualified_name": "app.api.auth.login",
    "file_path": "app/api/auth.py"
  }
]
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| 返回类型 | array |
| 数组元素 | 包含 `login` |

### 用例 2：查询 login 的调用者

请求：

```http
GET /context/callers?repo_id=sample-repo&symbol_name=login&depth=1&task_id=task_route_post_login&review_dimension=security
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| 返回类型 | array |
| 返回体 | 可能为空，因为 `login` 是路由入口 |

### 用例 3：符号不存在

请求：

```http
GET /context/callers?repo_id=sample-repo&symbol_name=not_exist_symbol&depth=1
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| 返回体 | `[]` |

## 13. POST /context/task-feedback

### 用例 1：任务完成

请求：

```json
{
  "repo_id": "sample-repo",
  "task_id": "task_route_post_login",
  "agent": "security-review-agent",
  "status": "completed",
  "context_sufficient": true,
  "feedback_type": "task_status",
  "message": "task completed",
  "need_more_context": false,
  "requested_context": [],
  "downstream_result_ref": "review-result-0001"
}
```

预期：

```json
{
  "accepted": true,
  "feedback_id": "feedback_000001",
  "repo_id": "sample-repo",
  "task_id": "task_route_post_login",
  "status": "completed",
  "context_sufficient": true,
  "next_action": "continue_downstream",
  "message": "feedback received"
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `accepted` | `true` |
| `next_action` | `continue_downstream` |

### 用例 2：上下文不足，要求补充调用者

请求：

```json
{
  "repo_id": "sample-repo",
  "task_id": "task_route_post_login",
  "agent": "security-review-agent",
  "status": "blocked",
  "context_sufficient": false,
  "feedback_type": "context_request",
  "message": "need caller context",
  "need_more_context": true,
  "requested_context": [
    {
      "type": "callers",
      "symbol_name": "authenticate",
      "depth": 2
    }
  ],
  "downstream_result_ref": null
}
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `accepted` | `true` |
| `status` | `blocked` |
| `context_sufficient` | `false` |
| `next_action` | `provide_more_context` |

### 用例 3：必填字段缺失

请求：

```json
{
  "repo_id": "sample-repo",
  "task_id": "task_route_post_login"
}
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `422` |
| `detail` | 包含 Pydantic 缺字段校验信息 |

## 14. GET /demo/{repo_id}/summary

### 用例 1：查看仓库摘要

请求：

```http
GET /demo/sample-repo/summary
```

预期：

```json
{
  "repo_summary": {
    "repo_id": "sample-repo",
    "framework": "fastapi",
    "python_files": 5,
    "entrypoints": [
      {
        "method_path": "POST /login",
        "file_path": "app/api/auth.py"
      }
    ],
    "test_files": ["tests/test_auth.py"],
    "config_files": ["app/config.py"]
  },
  "task_coverage_report": {
    "coverage_ratio": 1.0
  }
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `repo_summary.framework` | `fastapi` |
| `repo_summary.entrypoints` | 包含 `POST /login` |

### 用例 2：repo_id 未索引

请求：

```http
GET /demo/not-indexed/summary
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `404` |
| `detail` | `repo_id has not been indexed in this process` |

## 15. GET /demo/{repo_id}/tasks

### 用例 1：获取任务列表

请求：

```http
GET /demo/sample-repo/tasks
```

预期：

```json
{
  "review_tasks": [
    {
      "task_id": "task_route_post_login",
      "task_type": "entrypoint_review",
      "priority": "high",
      "target": {
        "file_path": "app/api/auth.py"
      }
    }
  ]
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `review_tasks` | 非空 |
| `review_tasks[*].task_id` | 包含 `task_route_post_login` |

### 用例 2：repo_id 未索引

请求：

```http
GET /demo/not-indexed/tasks
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `404` |
| `detail` | `repo_id has not been indexed in this process` |

## 16. GET /demo/{repo_id}/tasks/{task_id}/context

### 用例 1：获取任务推荐上下文

请求：

```http
GET /demo/sample-repo/tasks/task_route_post_login/context
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `task_id` | `task_route_post_login` |
| `target_file` 或目标字段 | 指向 `app/api/auth.py` |
| 推荐上下文字段 | 包含相关文件、相关符号或源码片段 |

### 用例 2：任务不存在

请求：

```http
GET /demo/sample-repo/tasks/not-exist/context
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` 或空上下文结构 |
| 说明 | 当前 Demo 兼容接口偏宽松，建议核心接入优先使用 `/context/task-package/{task_id}` |

## 17. GET /demo/{repo_id}/tasks/{task_id}/package

### 用例 1：获取 Demo 任务包

请求：

```http
GET /demo/sample-repo/tasks/task_route_post_login/package
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `task_id` | `task_route_post_login` |
| `initial_context.suggested_next_tool` | `get_task_graph_slice` |
| `available_tools` | 非空 |

### 用例 2：任务不存在

请求：

```http
GET /demo/sample-repo/tasks/not-exist/package
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `404` |
| `detail` | `task not found` |

## 18. GET /demo/{repo_id}/nodes/{node_id}

### 用例 1：按 node_id 获取节点详情

请求：

```http
GET /demo/sample-repo/nodes/app/api/auth.py:app.api.auth.login:10?task_id=task_route_post_login
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `name` | `login` |
| `qualified_name` | `app.api.auth.login` |
| `source` 或 `code` | 包含 `def login` |

注意：`node_id` 建议从 `/context/node-detail?symbol_name=login` 返回值里复制，不建议手写。

### 用例 2：节点不存在

请求：

```http
GET /demo/sample-repo/nodes/not-exist
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `404` |
| `detail` | `node not found` |

## 19. GET /demo/{repo_id}/nodes/{node_id}/callees

### 用例 1：查询 Demo 节点下游

请求：

```http
GET /demo/sample-repo/nodes/app/api/auth.py:app.api.auth.login:10/callees?depth=1&task_id=task_route_post_login
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| 返回类型 | array |
| 数组元素 | 包含 `authenticate` |

### 用例 2：节点不存在

请求：

```http
GET /demo/sample-repo/nodes/not-exist/callees?depth=1
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| 返回体 | `[]` |

## 20. GET /demo/{repo_id}/nodes/{node_id}/callers

### 用例 1：查询 Demo 节点上游

请求：

```http
GET /demo/sample-repo/nodes/app/services/user_service.py:app.services.user_service.authenticate:15/callers?depth=1&task_id=task_route_post_login
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| 返回类型 | array |
| 数组元素 | 包含 `login` |

### 用例 2：节点不存在

请求：

```http
GET /demo/sample-repo/nodes/not-exist/callers?depth=1
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| 返回体 | `[]` |

## 21. GET /demo/{repo_id}/files/snippet

### 用例 1：Demo 源码片段

请求：

```http
GET /demo/sample-repo/files/snippet?file_path=app/api/auth.py&start_line=1&end_line=5&task_id=task_route_post_login
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `file_path` | `app/api/auth.py` |
| `content` | 包含 `from fastapi import APIRouter` |

### 用例 2：路径穿越

请求：

```http
GET /demo/sample-repo/files/snippet?file_path=../../secret.txt&start_line=1&end_line=1
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `500` 或错误响应 |
| 说明 | Demo 旧接口未统一捕获该异常；核心接入应使用 `/context/file-snippet`，其返回 `400` |

## 22. POST /demo/{repo_id}/context/related

### 用例 1：Demo 相关上下文

请求：

```http
POST /demo/sample-repo/context/related
Content-Type: application/json
```

```json
{
  "task_id": "task_route_post_login",
  "target_file": "app/api/auth.py",
  "review_dimension": "security",
  "tags": ["api_entry", "auth"],
  "max_depth": 1,
  "max_files": 2
}
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `target_file` | `app/api/auth.py` |
| `related_files` | 数量小于等于 `2` |
| `snippets` | 非空 |

### 用例 2：最小参数

请求：

```json
{
  "max_depth": 1,
  "max_files": 1
}
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| 返回体 | 保持 JSON serializable |

## 23. GET /demo/{repo_id}/coverage

### 用例 1：刚索引后查看覆盖率

请求：

```http
GET /demo/sample-repo/coverage
```

预期：

```json
{
  "usage_coverage_report": {
    "repo_id": "sample-repo",
    "file_coverage": 0,
    "node_coverage": 0,
    "task_completion_rate": 0,
    "usage_records": []
  },
  "uncovered_file_reviews": []
}
```

验收点：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `usage_coverage_report` | 存在 |
| `uncovered_file_reviews` | 存在 |

### 用例 2：调用上下文工具后查看覆盖率

前置调用：

```http
GET /context/file-snippet?repo_id=sample-repo&file_path=app/api/auth.py&start_line=1&end_line=5&task_id=task_route_post_login&review_dimension=security
```

再请求：

```http
GET /demo/sample-repo/coverage
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `usage_coverage_report.covered_files` | 包含 `app/api/auth.py` |
| `usage_coverage_report.usage_records[*].tool_name` | 包含 `get_file_snippet` |
| `usage_coverage_report.file_coverage` | 大于 `0` |

### 用例 3：repo_id 未索引

请求：

```http
GET /demo/not-indexed/coverage
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `404` |
| `detail` | `repo_id has not been indexed in this process` |

## 24. GET /openapi.json

### 用例 1：导出 OpenAPI

请求：

```http
GET /openapi.json
```

预期：

| 项 | 预期 |
| --- | --- |
| HTTP 状态码 | `200` |
| `openapi` | 存在 |
| `info.title` | `AI Code Review Context` |
| `paths` | 包含 `/context/index`、`/context/file-snippet`、`/context/task-feedback` |

## 建议测试顺序

按下面顺序跑最稳：

```text
GET  /health
POST /context/index
GET  /context/tasks?repo_id={repo_id}&review_dimension={review_dimension}
GET  /context/task-package/{task_id}
GET  /context/tasks/{task_id}/graph-slice
POST /context/related-context
GET  /context/file-snippet
GET  /context/node-detail
GET  /context/callees
GET  /context/callers
POST /context/task-feedback
GET  /demo/{repo_id}/coverage
GET  /openapi.json
```

## 断言建议

写自动化测试时，不建议断言完整 JSON 完全相等，因为字段顺序、覆盖率数字和 usage 记录数量会随着前置调用变化。建议断言：

| 类型 | 推荐断言 |
| --- | --- |
| 基础接口 | 状态码和关键字段 |
| 列表接口 | 列表非空，且包含目标元素 |
| 源码接口 | `content` 包含目标源码文本 |
| 图接口 | `nodes` 非空，节点包含 `relation_to_target`、`priority`、`risk_score` |
| 覆盖率接口 | 调用工具后 `usage_records` 包含对应 `tool_name` |
| 异常接口 | 状态码和 `detail` 关键错误文本 |
