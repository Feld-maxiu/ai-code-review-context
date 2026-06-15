# 测试用例参数填写指南

本文档说明本项目测试和接口联调时每个常用参数应该填什么。优先使用下面这组固定示例值，可以直接跑通仓库自带的 `tests/fixtures/sample_repo`。

## 一组可直接使用的默认值

| 参数 | 推荐测试值 | 说明 |
| --- | --- | --- |
| `repo_id` | `sample-repo` | 本次索引会话的唯一标识。测试里可以换成任意字符串，但同一轮接口调用要保持一致。 |
| `repo_path` | `tests/fixtures/sample_repo` | 仓库根目录路径。这里指向项目自带的示例 Python 仓库。 |
| `db_path` | `.demo_data/sample-repo.db` | SQLite 索引文件路径。pytest 中通常用 `tmp_path / "xxx.db"`，避免污染本地文件。 |
| `task_id` | `task_route_post_login` | 示例仓库里稳定生成的登录接口评审任务 ID。 |
| `review_dimension` | `security` | 评审维度。登录接口任务默认用安全维度。 |
| `target_file` / `file_path` | `app/api/auth.py` | 示例任务的目标文件，路径必须是相对仓库根目录的路径。 |
| `symbol_name` | `login` | 示例任务的目标函数短名。 |
| `node_id` | 先从 `/context/node-detail` 或 `search_symbol()` 返回值里取 | 节点唯一 ID 不建议手写，因为它由解析结果生成。 |
| `depth` | `1` 或 `2` | 调用关系展开深度。单元测试通常用 `1`，任务局部图通常用 `2`。 |
| `start_line` | `1` | 源码片段起始行，行号从 1 开始。 |
| `end_line` | `80` | 源码片段结束行，必须大于等于 `start_line`。 |
| `tags` | `["api_entry", "auth"]` | 示例登录任务的标签。 |
| `agent` | `security-review-agent` | 下游评审 Agent 名称，测试时任意可读字符串即可。 |

## 参数怎么理解

### `repo_id`

`repo_id` 是一次仓库索引的统一标识，在调用 `POST /context/index` 时由调用方传入。

构建索引后，后续所有接口都应使用同一个 `repo_id`，包括任务查询、任务包获取、上下文工具调用和任务反馈。

`repo_id` 用于关联仓库摘要、任务列表、任务包、usage 记录和 task feedback。

示例：

```json
{
  "repo_id": "sample-repo"
}
```

pytest 里常用不同 `repo_id` 避免测试之间互相影响，例如 `api-context-file`、`api-context-node`。

### `repo_path`

`repo_path` 是服务端本机可以访问的 Python 仓库目录，不是浏览器本地路径，也不是 zip 文件路径。

示例：

```json
{
  "repo_path": "tests/fixtures/sample_repo"
}
```

如果要测试自己的仓库，可以换成绝对路径：

```json
{
  "repo_path": "D:/demo_repos/my_python_repo"
}
```

### `db_path`

`db_path` 是 SQLite 索引文件保存位置。手工联调可以用 `.demo_data/sample-repo.db`；pytest 应使用临时目录，避免多个测试共享同一个数据库。

pytest 示例：

```python
db_path = tmp_path / "sample-repo.db"
```

接口示例：

```json
{
  "db_path": ".demo_data/sample-repo.db"
}
```

### `task_id`

`task_id` 是 context 模块在生成 review task 时产生的任务标识。

下游 agent 可以在获取任务列表时获取 ID，也就是读取 `/context/tasks` 返回结果里的 `tasks[].task_id`。

下游 agent 拿到 `task_id` 后，再调用：

```http
GET /context/task-package/{task_id}?repo_id={repo_id}
```

获取完整任务包。

后续上下文工具调用和 `task-feedback` 也应继续携带该 `task_id`。

示例仓库里常用：

```text
task_route_post_login
```

不要自己猜任务 ID。测试新仓库时，先索引，再从返回的任务列表里取。

### `file_path` 和 `target_file`

这两个参数都必须填“相对仓库根目录”的路径。

正确：

```text
app/api/auth.py
```

错误：

```text
tests/fixtures/sample_repo/app/api/auth.py
../../secret.txt
```

`../../secret.txt` 这类路径穿越会被拒绝，这是测试里必须覆盖的安全边界。

### `symbol_name` 和 `node_id`

`symbol_name` 是函数、类、方法的短名，例如 `login`、`authenticate`。

`node_id` 是解析入库后的节点唯一 ID，通常不要手写。推荐流程：

1. 用 `symbol_name=login` 调 `/context/node-detail`。
2. 从返回 JSON 里读取 `node_id`。
3. 再把这个 `node_id` 传给 `/context/callees` 或 `/context/callers`。

### `start_line` 和 `end_line`

行号从 1 开始，闭区间返回。`start_line=1&end_line=5` 表示返回第 1 到第 5 行。

如果只想看函数附近，先调用 `/context/node-detail`，再用返回的 `start_line` 和 `end_line`。

### `depth`

`depth` 控制调用图扩展层数。

| 值 | 适合场景 |
| --- | --- |
| `1` | 只看直接调用者或直接被调函数，测试最稳定。 |
| `2` | 看任务附近的局部调用链，适合 `/context/tasks/{task_id}/graph-slice`。 |
| `3+` | 第一版不建议默认使用，返回内容可能变多，也可能被 `context_policy.max_graph_depth` 限制。 |

## 接口测试请求模板

### 1. 建立索引

所有上下文查询前都必须先建索引。

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

### 2. 按评审维度查询任务

```http
GET /context/tasks?repo_id=sample-repo&review_dimension=security
```

参数来源：

| 参数 | 从哪里来 |
| --- | --- |
| `repo_id` | 建索引时传入的 `repo_id` |
| `review_dimension` | 固定枚举之一，例如 `security`、`function_logic`、`coding_style`、`requirement_consistency` |

返回里的 `tasks[*].task_id` 用于后续获取任务包。

### 3. 获取任务包

```http
GET /context/task-package/task_route_post_login?repo_id=sample-repo
```

参数来源：

| 参数 | 从哪里来 |
| --- | --- |
| `task_id` | `/context/tasks` 返回的 `tasks[*].task_id` |
| `repo_id` | 建索引时传入的 `repo_id` |

### 4. 获取任务局部调用图

```http
GET /context/tasks/task_route_post_login/graph-slice?repo_id=sample-repo&depth=2
```

建议先用 `depth=2`。如果返回太多，改成 `depth=1`。

### 4. 获取相关上下文

```http
POST /context/related-context
Content-Type: application/json
```

```json
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

### 5. 获取源码片段

```http
GET /context/file-snippet?repo_id=sample-repo&file_path=app/api/auth.py&start_line=1&end_line=80&task_id=task_route_post_login&review_dimension=security
```

`task_id` 和 `review_dimension` 虽然不是所有场景都强制必填，但建议测试时都带上，这样会写入 `context_usage`，覆盖率测试才能验证工具调用记录。

### 6. 获取符号详情

```http
GET /context/node-detail?repo_id=sample-repo&symbol_name=login&task_id=task_route_post_login&review_dimension=security
```

如果已经知道 `node_id`，也可以传：

```http
GET /context/node-detail?repo_id=sample-repo&node_id=app.api.auth.login&task_id=task_route_post_login&review_dimension=security
```

实际测试中更推荐用返回值里的 `node_id`，不要假设它永远等于 qualified name。

### 7. 获取下游被调函数

```http
GET /context/callees?repo_id=sample-repo&symbol_name=login&depth=1&task_id=task_route_post_login&review_dimension=security
```

也可以先查 `node-detail`，再用返回的 `node_id`：

```http
GET /context/callees?repo_id=sample-repo&node_id=<node_id>&depth=1&task_id=task_route_post_login&review_dimension=security
```

### 8. 获取上游调用者

```http
GET /context/callers?repo_id=sample-repo&symbol_name=authenticate&depth=1&task_id=task_route_post_login&review_dimension=security
```

### 9. 提交任务反馈

完成任务：

```http
POST /context/task-feedback
Content-Type: application/json
```

```json
{
  "repo_id": "sample-repo",
  "task_id": "task_route_post_login",
  "agent": "security-review-agent",
  "status": "completed",
  "context_sufficient": true,
  "feedback_type": "task_status",
  "message": "任务已完成，上下文足够支持本轮评审。",
  "need_more_context": false,
  "requested_context": [],
  "downstream_result_ref": "review-result-0001"
}
```

上下文不足：

```json
{
  "repo_id": "sample-repo",
  "task_id": "task_route_post_login",
  "agent": "security-review-agent",
  "status": "blocked",
  "context_sufficient": false,
  "feedback_type": "context_request",
  "message": "当前任务缺少认证函数的上游调用入口，需要补充调用者信息。",
  "need_more_context": true,
  "requested_context": [
    {
      "type": "callers",
      "symbol_name": "authenticate",
      "depth": 2,
      "reason": "确认认证函数是否被多个入口复用"
    }
  ],
  "downstream_result_ref": null
}
```

## pytest 用例参数速查

| 测试文件 | 主要验证 | 关键参数 |
| --- | --- | --- |
| `tests/test_api_app.py` | FastAPI 上下文接口 | `repo_id`、`repo_path`、`db_path`、`task_id`、`review_dimension` |
| `tests/test_context_tools.py` | Python 工具函数 | `node_id`、`symbol_name`、`file_path`、`start_line`、`end_line` |
| `tests/test_agent_context_tools.py` | ContextService 和 usage 记录 | `task_id`、`review_dimension`、`target.file_path` |
| `tests/test_task_graph_slice.py` | task-local graph slice | `task_id`、`depth` |
| `tests/test_coverage_acceptance.py` | 覆盖率报告 | 先调用上下文工具，再检查 `usage_records` |
| `tests/test_file_scanner.py` | 文件扫描过滤 | fixture 目录、跳过目录名、文件后缀 |
| `tests/test_ast_parser.py` | AST 符号解析 | fixture 源码内容、函数名、类名、decorator |

## 最小 pytest 写法

```python
from pathlib import Path

from fastapi.testclient import TestClient

from repo_context.api.app import app


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_can_read_login_snippet(tmp_path: Path) -> None:
    client = TestClient(app)
    repo_id = "sample-repo-test"

    index_response = client.post(
        "/context/index",
        json={
            "repo_id": repo_id,
            "repo_path": str(SAMPLE_REPO),
            "db_path": str(tmp_path / "sample-repo-test.db"),
        },
    )
    assert index_response.status_code == 200

    snippet_response = client.get(
        "/context/file-snippet",
        params={
            "repo_id": repo_id,
            "file_path": "app/api/auth.py",
            "start_line": 1,
            "end_line": 5,
            "task_id": "task_route_post_login",
            "review_dimension": "security",
        },
    )

    assert snippet_response.status_code == 200
    assert snippet_response.json()["content"]
```

## 常见填错原因

| 现象 | 常见原因 | 修正方式 |
| --- | --- | --- |
| `repo_id has not been indexed in this process` | 没有先调用 `/context/index`，或后续 `repo_id` 不一致 | 先建索引，并保持同一个 `repo_id` |
| `repo_path is not a directory` | `repo_path` 不存在，或填成了文件路径 | 填服务端本机存在的仓库目录 |
| `escapes repository` | `file_path` 使用了绝对路径或 `../` | 改成仓库内相对路径 |
| `node not found` | `symbol_name` 不存在，或 `node_id` 手写错误 | 先用 `search_symbol()` 或 `/context/node-detail?symbol_name=...` 查 |
| 覆盖率没有变化 | 调工具时没带 `task_id` / `review_dimension`，或没有调用会记录 usage 的工具 | 查询源码、节点、调用图时带上这两个参数 |
