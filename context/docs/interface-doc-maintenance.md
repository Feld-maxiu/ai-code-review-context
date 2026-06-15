# 接口文档联动维护清单

修改上下文 API、下游 agent 调用流程、参数含义或枚举值时，必须同步检查以下文档和产物：

| 文件 | 作用 | 何时必须同步 |
| --- | --- | --- |
| `context/docs/上下文工具接口文档（主要看这个）.docx` | 给下游工程师看的主接口文档 | 必须优先同步 |
| `context/docs/context-api-reference.md` | Markdown API reference | 新增/修改接口、参数、返回字段、枚举 |
| `context/docs/downstream-agent-integration.md` | 下游 agent 接入流程 | 调用顺序、任务领取方式、反馈方式变化 |
| `context/docs/README-review-agent-context-tools.md` | 上下文工具详细接入文档 | 工具调用流程、usage、任务包字段变化 |
| `context/docs/interface-test-cases.md` | 接口测试用例说明 | 新增接口、状态码、关键断言变化 |
| `context/docs/test-case-parameter-guide.md` | 参数填写指南 | `repo_id`、`task_id`、`review_dimension` 等参数来源变化 |
| `context/docs/api/README.md` | Postman / OpenAPI 使用说明 | Postman 变量、标准调用顺序变化 |
| `context/docs/api/openapi.json` | 静态 OpenAPI 产物 | FastAPI 路由、参数类型、枚举变化 |
| `context/docs/api/postman_collection.json` | Postman collection | 新增/删除接口、调用顺序变化 |
| `README.md` / `context/README.md` | 项目入口和模块入口说明 | 快速开始流程变化 |
| `agent/docs/new-agent-integration.md` | 新 agent 接入指南 | agent 领取任务或上下文调用方式变化 |

当前标准领取任务流程：

```text
POST /context/index
GET  /context/tasks?repo_id={repo_id}&review_dimension={review_dimension}
GET  /context/task-package/{task_id}?repo_id={repo_id}
GET  /context/tasks/{task_id}/graph-slice
POST /context/related-context
GET  /context/file-snippet 或 /context/node-detail
GET  /context/callees 或 /context/callers
POST /context/task-feedback
```

固定 `review_dimension` 枚举：

```text
security
function_logic
coding_style
requirement_consistency
```

`repo_id` 和 `task_id` 规则：

- `repo_id` 由 `POST /context/index` 调用方传入，后续所有接口必须使用同一个值。
- `task_id` 来自 `GET /context/tasks` 返回的 `tasks[].task_id`。
- 上下文工具调用和 `task-feedback` 应继续携带当前 `task_id`。

## Current Interface Documentation Sync Rule

Whenever API routes, parameters, response fields, enum values, or downstream calling flow change, update all interface documentation artifacts, including:

- `docs/上下文工具接入文档_新版.docx`
- `context/docs/context-api-reference.md`
- `context/docs/downstream-agent-integration.md`
- `context/docs/README-review-agent-context-tools.md`
- `context/docs/api/README.md`
- `context/docs/api/openapi.json`
- `context/docs/api/postman_collection.json`
- related interface test/parameter docs when examples or expected status codes change

The Word document above is the current primary handoff document for downstream engineers and must not be skipped.
