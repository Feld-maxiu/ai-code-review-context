# AI Code Review Context

> 当前仓库采用并行模块结构：上下文模块位于 `context/repo_context/`，下游 agent 位于 `agent/review_agent/`。从仓库根目录运行上下文服务时，请先设置 `$env:PYTHONPATH = "context"`；如果进入 `context/` 目录运行，则可以直接使用 `repo_context` 包名。

这是一个面向“仓库级 AI 代码评审”的上下文处理模块。它不直接判断漏洞，也不生成最终评审结论；它负责把一个 Python 仓库解析成可查询的上下文索引，并给下游 review agent 提供受控、可追踪、可逐步扩展的代码上下文。

## 能力边界

本模块负责：

- 读取本地 Python 仓库或安全解压 zip 包。
- 扫描并过滤有效文件。
- 使用 Python 标准库 `ast` 抽取 module、class、function、method、route、import、decorator 和基础调用关系。
- 构建 SQLite 上下文索引。
- 生成仓库摘要和规则化 review task package。
- 提供上下文查询 API 和 service 工具。
- 记录下游 agent 实际读取过的文件、符号和图关系，并生成覆盖率报告。

本模块不负责：

- 不做漏洞判断。
- 不生成最终评审报告。
- 不做多 agent 调度。
- 不把完整仓库源码一次性塞给 agent。
- 不暴露完整仓库调用图，只暴露 task-local graph slice。

## 安装依赖

```powershell
python -m pip install -r requirements.txt
```

当前根目录依赖包括：

```text
fastapi
httpx
pytest
```

`httpx` 是 FastAPI / Starlette `TestClient` 的测试依赖。不要为了当前测试随意改成 `httpx2`。

## 启动教程

最常用的本地 demo 启动方式如下。

1. 进入项目目录：

```powershell
cd D:\HSBC-AIreview\ai-code-review-context
```

2. 安装依赖：

```powershell
python -m pip install -r requirements.txt
```

3. 启动 FastAPI 服务：

```powershell
$env:PYTHONPATH = "context"
python -m uvicorn repo_context.api.app:app --host 127.0.0.1 --port 8000 --reload
```

4. 打开 demo 页面：

```text
http://127.0.0.1:8000/
```

5. 使用页面：

- `项目标识` 可以保持默认 `sample-repo`，也可以换成自己的 repo id。
- `仓库路径` 可以先用默认值 `tests/fixtures/sample_repo` 验证流程。
- 点击 `构建索引`，系统会扫描仓库、解析 AST、写入 SQLite、生成评审任务包。
- 注意：服务启动后不会自动生成任务包。必须先构建索引，返回值里的 `review_tasks` 才会包含可领取的任务。
- 点击左侧任务卡片，查看目标文件、目标符号、关注点、初始上下文和 task-local graph slice。
- 使用 `扩展相关上下文`、`查看目标文件片段`、`查看目标符号` 按需读取更多上下文。
- 切到 `覆盖率` 视图，可以看到下游工具实际读取过哪些文件、符号和图关系。

6. 查看接口文档：

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/openapi.json
```

7. 使用 Postman：

- 可直接导入 `context/docs/api/postman_collection.json`。
- 也可以在服务启动后导入 `http://127.0.0.1:8000/openapi.json`。
- 详细说明见 `context/docs/api/README.md`。

8. 停止服务：

在启动服务的终端按 `Ctrl+C`。

如果想分析自己的仓库，把页面里的 `仓库路径` 改成服务端本机可访问的 Python 仓库目录，例如：

```text
D:\demo_repos\my_python_repo
```

当前 demo 读取的是服务端本机路径，不是浏览器上传文件。

## 仓库输入方式

当前模块支持两类输入：本地仓库目录和 zip 包。无论哪种方式，最终都会得到一个服务端本机可访问的仓库目录，再调用 `build_index()` 构建 SQLite 上下文索引。

### 方式一：本地仓库目录

如果待评审仓库已经在服务端本机，例如：

```text
D:\demo_repos\my_python_repo
```

可以直接构建索引：

```python
from repo_context.index.index_builder import build_index

build_index(
    repo_id="my-repo",
    repo_path=r"D:\demo_repos\my_python_repo",
    db_path=r"D:\demo_output\context.db",
)
```

### 方式二：zip 包

如果用户提供的是 zip 包，可以先安全解压，再构建索引：

```python
from repo_context.ingest.zip_loader import extract_zip
from repo_context.index.index_builder import build_index

repo_dir = extract_zip(
    zip_path=r"D:\uploads\my_python_repo.zip",
    output_dir=r"D:\workspaces\my_python_repo",
)

build_index(
    repo_id="my-repo",
    repo_path=repo_dir,
    db_path=r"D:\demo_output\context.db",
)
```

zip 解压会检查路径穿越，例如 `../../evil.py` 会被拒绝，避免文件写出目标目录。

### 扫描规则

文件扫描阶段会跳过常见缓存、依赖和构建目录，例如：

```text
.git
.venv
venv
__pycache__
dist
build
site-packages
node_modules
```

AST 解析失败不会中断整个仓库处理。语法错误文件会保留文件记录，但不会生成符号节点，其他正常文件会继续入库。

## 快速开始

使用项目自带的 sample repo 构建索引：

```python
from pathlib import Path

from repo_context.index.index_builder import build_index
from repo_context.service.context_service import ContextService
from repo_context.service.coverage_service import CoverageService
from repo_context.task.review_task_generator import ReviewTaskGenerator

repo_id = "sample-repo"
repo_path = Path("tests/fixtures/sample_repo")
db_path = Path("demo_context.db")

build_index(repo_id=repo_id, repo_path=repo_path, db_path=db_path)

context_service = ContextService(
    repo_id=repo_id,
    repo_root=repo_path,
    db_path=db_path,
)
task_generator = ReviewTaskGenerator(context_service)
coverage_service = CoverageService(context_service, task_generator)

plan = task_generator.generate()
first_task = plan.review_tasks[0].to_dict()
coverage = coverage_service.get_coverage_report()

print(first_task)
print(coverage)
```

## Review Task Package

下游 agent 不应该一次性拿到完整仓库源码。任务生成器会为每个任务提供一个有限的 task package：

```json
{
  "task_id": "task_route_post_login",
  "task_type": "entrypoint_review",
  "review_dimension": "security",
  "target": {
    "type": "file",
    "file_path": "app/api/auth.py",
    "symbols": ["login"]
  },
  "tags": ["api_entry", "auth"],
  "focus_points": ["输入校验", "身份认证", "异常处理"],
  "initial_context": {
    "type": "task_entry",
    "file_path": "app/api/auth.py",
    "symbols": ["login"],
    "review_dimension": "security",
    "suggested_next_tool": "get_task_graph_slice",
    "suggested_next_params": {
      "task_id": "task_route_post_login",
      "depth": 2
    },
    "context_scope": {
      "task_local": true,
      "allow_full_graph": false,
      "prefer_graph_slice_first": true
    }
  },
  "available_tools": [
    "get_file_snippet",
    "get_node_detail",
    "get_task_graph_slice",
    "get_callers",
    "get_callees",
    "trace_call_chain",
    "search_symbol",
    "get_related_context"
  ],
  "context_policy": {
    "max_depth": 2,
    "max_snippet_lines": 120,
    "max_files": 5,
    "allow_expand": true,
    "allow_task_graph_slice": true,
    "allow_full_graph": false,
    "prefer_graph_slice_first": true,
    "max_graph_depth": 2
  }
}
```

重点约束：

- `initial_context` 只放任务入口、关注点和工具引导，不再预塞源码片段或调用图。
- agent 应先调用 `get_task_graph_slice` / `/context/tasks/{task_id}/graph-slice` 获取 task-local graph slice。
- `get_task_graph_slice` 只返回任务局部图，完整仓库 graph 不对下游暴露。
- graph slice 的 `nodes` 和 `boundary_nodes` 会带 `relation_to_target`、`priority`、`risk_score`、`reason`，下游 agent 可优先阅读高优先级和高风险节点。
- agent 需要更多源码上下文时，再调用 `related-context`、`file-snippet`、`node-detail`、`callers`、`callees` 逐步扩展。
- 每次上下文读取都会尽量写入 `context_usage`，用于覆盖率统计。

## ContextService 工具

常用 service 方法：

```python
matches = context_service.search_symbol("login")

graph_slice = context_service.get_task_graph_slice(
    task_id="task_route_post_login",
    depth=2,
)

node = context_service.get_node_detail(
    symbol_name="login",
    task_id="task_route_post_login",
    review_dimension="security",
)

snippet = context_service.get_file_snippet(
    file_path="app/api/auth.py",
    start_line=1,
    end_line=40,
    task_id="task_route_post_login",
    review_dimension="security",
)

callees = context_service.get_callees(
    symbol_name="login",
    depth=1,
    task_id="task_route_post_login",
    review_dimension="security",
)

related = context_service.get_related_context(
    {
        "task_id": "task_route_post_login",
        "target": {
            "type": "file",
            "file_path": "app/api/auth.py",
            "symbols": ["login"]
        },
        "review_dimension": "security",
        "tags": ["api_entry", "auth"]
    }
)
```

`get_file_snippet()` 会拒绝仓库外路径，例如 `../../secret.txt`。

## FastAPI API

启动服务：

```powershell
$env:PYTHONPATH = "context"
uvicorn repo_context.api.app:app --reload
```

服务启动只是让 API 可访问；要生成任务包，必须先调用 `/context/index` 构建索引：

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

参数说明：

- `repo_id = "sample-repo"` 是本次仓库分析的自定义 ID，可以换成自己的项目标识，例如 `"payment-service"`。
- `repo_path = "tests/fixtures/sample_repo"` 是服务端本机可访问的 Python 仓库路径，可以换成真实待评审仓库路径，例如 `"D:\demo_repos\my_python_repo"`。
- 构建索引成功后，响应里的 `review_tasks` 才会包含可领取的任务。
- 构建索引后，后续任务查询、任务包获取、上下文工具调用和任务反馈都应继续使用同一个 `repo_id`。

对应的 HTTP 请求是：

```http
POST /context/index
Content-Type: application/json

{
  "repo_id": "sample-repo",
  "repo_path": "tests/fixtures/sample_repo",
  "db_path": ".demo_data/sample-repo.db"
}
```

上下文查询接口：

```text
GET  /context/file-snippet
GET  /context/node-detail
GET  /context/callees
GET  /context/callers
POST /context/related-context
GET  /context/tasks?repo_id={repo_id}&review_dimension={review_dimension}
GET  /context/task-package/{task_id}
```

按评审维度查询任务并领取任务包示例。`review_dimension` 必须使用固定枚举：`security`、`function_logic`、`coding_style`、`requirement_consistency`。

```powershell
$tasks = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/context/tasks?repo_id=sample-repo&review_dimension=security" `
  -Method Get

$taskId = $tasks.tasks[0].task_id

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/context/task-package/$taskId?repo_id=sample-repo" `
  -Method Get
```

如果替换了 `repo_id`，任务包 URL 里的 `repo_id` 也要使用同一个值。
后续上下文工具调用和 `POST /context/task-feedback` 也应继续携带这个 `task_id`。

更多查询示例：

```http
GET /context/file-snippet?repo_id=sample-repo&file_path=app/api/auth.py&start_line=1&end_line=40
```

```http
GET /context/node-detail?repo_id=sample-repo&symbol_name=login
```

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
  "max_files": 2
}
```

Demo 页面和兼容 demo API 仍然可用：

```text
GET  /
POST /demo/index
GET  /demo/{repo_id}/tasks
GET  /demo/{repo_id}/tasks/{task_id}/context
GET  /demo/{repo_id}/coverage
```

## Context Usage 和覆盖率

`context_usage` 会记录下游 agent 实际读过什么：

- `task_id`
- `agent` / `review_dimension`
- `tool_name`
- `target_type`
- `target_name`
- `node_id`
- `file_path`
- `start_line`
- `end_line`
- `lines_returned`
- `created_at`

覆盖率报告：

```python
coverage = coverage_service.get_coverage_report()
```

可用于判断：

- 哪些文件被实际读取过。
- 哪些符号被实际查看过。
- 哪些任务已经触发过上下文访问。
- 哪些文件还需要生成 `uncovered_file_review` 补充任务。

## 测试

运行完整测试：

```powershell
python -m pytest -q
```

编译检查：

```powershell
python -m compileall -q context/repo_context
```

当前 pytest 配置会跳过 `tests/fixtures` 下的第三方示例仓库内部测试。那些文件是被本项目扫描和解析的输入样本，不属于本项目自身测试集。

## 前端接入建议

推荐前端流程：

1. 让用户选择本地仓库路径或 zip 上传后的服务端路径。
2. 调用 `/context/index` 构建索引。
3. 展示 `repo_summary` 和 `review_tasks`。
4. 点击任务卡片后展示完整 task package。
5. 默认展示 `initial_context`，不要展示完整仓库源码。
6. 当用户或 agent 需要更多上下文时，调用 `/context/file-snippet`、`/context/node-detail`、`/context/callees`、`/context/related-context`。
7. 展示 coverage report，让用户知道哪些文件和符号已被实际读取。

任务卡片建议展示：

- 任务类型和 review dimension。
- 目标文件和目标符号。
- tags 和 priority。
- focus points。
- initial context 摘要。
- graph slice 节点数和边数。
- “继续扩展上下文”的操作入口。
## Agent Context Debug Console

FastAPI 应用内置轻量调试台，入口为：

```text
GET /
GET /demo
GET /demo/{repo_id}/tasks/{task_id}
```

调试台用于开发者检查上下文接口调用链，不承载后端业务逻辑：

- 左侧填写 `repo_id`、`repo_path`、`db_path`，调用 `POST /context/index` 构建索引。
- 左侧任务列表支持按 `review_dimension` 和 `task_type` 过滤。
- 点击任务后会自动调用 `GET /context/task-package/{task_id}` 和 `GET /context/tasks/{task_id}/graph-slice?depth=2`。
- graph slice 使用表格展示 `nodes`、`edges`、`boundary_nodes`，字段缺失时显示 `-`。
- 点击节点会调用 `GET /context/node-detail`，并在节点详情附近提供 `get_callers`、`get_callees`、`get_related_context`、`get_file_snippet` 快捷按钮。
- Tool Debugger 面板支持手动输入 JSON 参数调试 task package、graph slice、node detail、file snippet、callers、callees、related context 和 usage。
- 右侧面板展示 Raw JSON、API Logs 和 Usage。API Logs 会记录 method、URL、参数、状态码、耗时和成功/失败状态。

前端只负责调用接口、展示返回结果、记录日志和辅助调试；`priority`、`risk_score`、`relation_to_target`、`reason`、task-local 范围、截断和 boundary nodes 均以后端返回为准。
