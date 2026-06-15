from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from repo_context.index.index_builder import build_index
from repo_context.service.context_service import ContextService
from repo_context.service.coverage_service import CoverageService
from repo_context.task.review_task_generator import ReviewTaskGenerator


app = FastAPI(title="AI Code Review Context")

_DEMO_SESSIONS: dict[str, dict[str, Any]] = {}
_TASK_FEEDBACKS: dict[str, dict[str, Any]] = {}


class ReviewDimension(str, Enum):
    security = "security"
    function_logic = "function_logic"
    coding_style = "coding_style"
    requirement_consistency = "requirement_consistency"


@app.get("/health")
def health() -> dict[str, str]:
    """最小健康检查接口，用于验证阶段 0 API 骨架可启动。"""
    return {"status": "ok"}


class DemoIndexRequest(BaseModel):
    repo_id: str
    repo_path: str
    db_path: str | None = None


class RelatedContextRequest(BaseModel):
    repo_id: str | None = None
    task_id: str | None = None
    target_file: str | None = None
    review_dimension: ReviewDimension | None = None
    tags: list[str] = []
    max_depth: int = 2
    max_files: int = 5


class ContextSessionRequest(BaseModel):
    repo_id: str
    repo_path: str
    db_path: str | None = None


class TaskFeedbackRequest(BaseModel):
    repo_id: str
    task_id: str
    agent: str
    status: str
    context_sufficient: bool
    feedback_type: str
    message: str | None = None
    need_more_context: bool = False
    requested_context: list[dict[str, Any]] = Field(default_factory=list)
    downstream_result_ref: str | None = None
    created_at: str | None = None


@app.get("/", response_class=HTMLResponse)
def demo_page() -> str:
    return _DEBUG_CONSOLE_HTML


@app.get("/demo", response_class=HTMLResponse)
def demo_debug_console() -> str:
    return _DEBUG_CONSOLE_HTML


@app.get("/demo/{repo_id}/tasks/{task_id}", response_class=HTMLResponse)
def demo_task_debug_console(repo_id: str, task_id: str) -> str:
    return _DEBUG_CONSOLE_HTML


@app.post("/demo/index")
def demo_index(request: DemoIndexRequest) -> dict[str, Any]:
    repo_path = Path(request.repo_path).expanduser().resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"repo_path is not a directory: {repo_path}")

    db_path = (
        Path(request.db_path).expanduser().resolve()
        if request.db_path
        else Path.cwd() / ".demo_data" / f"{request.repo_id}.db"
    )
    result = build_index(request.repo_id, repo_path, db_path)
    service = _create_services(request.repo_id, repo_path, db_path)
    plan = service["task_generator"].generate()
    coverage = service["coverage_service"].get_coverage_report()
    _DEMO_SESSIONS[request.repo_id] = {
        "repo_root": str(repo_path),
        "db_path": str(db_path),
    }

    return {
        "index_result": result.__dict__,
        "repo_summary": plan.repo_summary.to_dict(),
        "review_tasks": [task.to_dict() for task in plan.review_tasks],
        "task_coverage_report": plan.coverage_report,
        "usage_coverage_report": coverage,
    }


@app.post("/context/index")
def context_index(request: ContextSessionRequest) -> dict[str, Any]:
    return demo_index(
        DemoIndexRequest(
            repo_id=request.repo_id,
            repo_path=request.repo_path,
            db_path=request.db_path,
        )
    )


@app.get("/context/file-snippet")
def context_file_snippet(
    repo_id: str,
    file_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    task_id: str | None = None,
    review_dimension: ReviewDimension | None = None,
) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    try:
        return service["context_service"].get_file_snippet(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            task_id=task_id,
            review_dimension=_dimension_value(review_dimension),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/context/repo-files")
def context_repo_files(
    repo_id: str,
    include_tests: bool = True,
    file_type: str | None = None,
) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    return service["context_service"].list_repo_files(
        include_tests=include_tests,
        file_type=file_type,
    )


@app.get("/context/file-content")
def context_file_content(
    repo_id: str,
    file_path: str,
    task_id: str | None = None,
    review_dimension: ReviewDimension | None = None,
) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    try:
        return service["context_service"].get_file_content(
            file_path=file_path,
            task_id=task_id,
            review_dimension=_dimension_value(review_dimension),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/context/node-detail")
def context_node_detail(
    repo_id: str,
    node_id: str | None = None,
    symbol_name: str | None = None,
    task_id: str | None = None,
    review_dimension: ReviewDimension | None = None,
) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    detail = service["context_service"].get_node_detail(
        node_id=node_id,
        symbol_name=symbol_name,
        include_source=True,
        task_id=task_id,
        review_dimension=_dimension_value(review_dimension),
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="node not found")
    return detail


@app.get("/context/callees")
def context_callees(
    repo_id: str,
    node_id: str | None = None,
    symbol_name: str | None = None,
    depth: int = 1,
    task_id: str | None = None,
    review_dimension: ReviewDimension | None = None,
) -> list[dict[str, Any]]:
    service = _load_demo_services(repo_id)
    return service["context_service"].get_callees(
        node_id=node_id,
        symbol_name=symbol_name,
        depth=depth,
        task_id=task_id,
        review_dimension=_dimension_value(review_dimension),
    )


@app.get("/context/callers")
def context_callers(
    repo_id: str,
    node_id: str | None = None,
    symbol_name: str | None = None,
    depth: int = 1,
    task_id: str | None = None,
    review_dimension: ReviewDimension | None = None,
) -> list[dict[str, Any]]:
    service = _load_demo_services(repo_id)
    return service["context_service"].get_callers(
        node_id=node_id,
        symbol_name=symbol_name,
        depth=depth,
        task_id=task_id,
        review_dimension=_dimension_value(review_dimension),
    )


@app.post("/context/related-context")
def context_related_context(request: RelatedContextRequest) -> dict[str, Any]:
    if not request.repo_id:
        raise HTTPException(status_code=400, detail="repo_id is required")
    service = _load_demo_services(request.repo_id)
    task = request.model_dump(exclude={"repo_id"})
    task["review_dimension"] = _dimension_value(request.review_dimension)
    if request.target_file:
        task["target"] = {
            "type": "file",
            "file_path": request.target_file,
            "symbols": [],
        }
    try:
        return service["context_service"].get_related_context(
            task,
            task_id=request.task_id,
            target_file=request.target_file,
            review_dimension=_dimension_value(request.review_dimension),
            tags=request.tags,
            max_depth=request.max_depth,
            max_files=request.max_files,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/context/task-package/{task_id}")
def context_task_package(repo_id: str, task_id: str) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    package = service["task_generator"].get_task_package(task_id)
    if package is None:
        raise HTTPException(status_code=404, detail="task not found")
    return package


@app.get("/context/tasks")
def context_tasks(repo_id: str, review_dimension: ReviewDimension) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    plan = service["task_generator"].generate()
    tasks = [
        task.to_dict()
        for task in plan.review_tasks
        if task.review_dimension == review_dimension.value
    ]
    return {
        "repo_id": repo_id,
        "review_dimension": review_dimension.value,
        "tasks": tasks,
    }


def _dimension_value(review_dimension: ReviewDimension | None) -> str | None:
    return review_dimension.value if review_dimension is not None else None


@app.get("/context/tasks/{task_id}/graph-slice")
def context_task_graph_slice(
    task_id: str,
    repo_id: str,
    depth: int = 2,
) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    return service["context_service"].get_task_graph_slice(task_id=task_id, depth=depth)


@app.post("/context/task-feedback")
def context_task_feedback(request: TaskFeedbackRequest) -> dict[str, Any]:
    """接收下游 agent 的任务状态和上下文需求反馈，不保存最终漏洞结论。"""
    _load_demo_services(request.repo_id)
    feedback_id = f"feedback_{len(_TASK_FEEDBACKS) + 1:06d}"
    created_at = request.created_at or datetime.now(timezone.utc).isoformat()
    record = {
        **request.model_dump(),
        "feedback_id": feedback_id,
        "created_at": created_at,
    }
    _TASK_FEEDBACKS[feedback_id] = record
    next_action = "provide_more_context" if request.need_more_context else "continue_downstream"
    return {
        "accepted": True,
        "feedback_id": feedback_id,
        "repo_id": request.repo_id,
        "task_id": request.task_id,
        "status": request.status,
        "context_sufficient": request.context_sufficient,
        "next_action": next_action,
        "message": "feedback received",
    }


@app.get("/demo/{repo_id}/summary")
def demo_summary(repo_id: str) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    plan = service["task_generator"].generate()
    return {
        "repo_summary": plan.repo_summary.to_dict(),
        "task_coverage_report": plan.coverage_report,
    }


@app.get("/demo/{repo_id}/tasks")
def demo_tasks(repo_id: str) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    plan = service["task_generator"].generate()
    return {"review_tasks": [task.to_dict() for task in plan.review_tasks]}


@app.get("/demo/{repo_id}/tasks/{task_id}/context")
def demo_task_context(repo_id: str, task_id: str) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    return service["task_generator"].get_related_context(task_id)


@app.get("/demo/{repo_id}/tasks/{task_id}/package")
def demo_task_package(repo_id: str, task_id: str) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    package = service["task_generator"].get_task_package(task_id)
    if package is None:
        raise HTTPException(status_code=404, detail="task not found")
    return package


@app.get("/demo/{repo_id}/nodes/{node_id:path}")
def demo_node_detail(repo_id: str, node_id: str, task_id: str | None = None) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    detail = service["context_service"].get_node_detail(
        node_id,
        include_source=True,
        task_id=task_id,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="node not found")
    return detail


@app.get("/demo/{repo_id}/nodes/{node_id:path}/callees")
def demo_callees(
    repo_id: str,
    node_id: str,
    depth: int = 1,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    service = _load_demo_services(repo_id)
    return service["context_service"].get_callees(node_id=node_id, depth=depth, task_id=task_id)


@app.get("/demo/{repo_id}/nodes/{node_id:path}/callers")
def demo_callers(
    repo_id: str,
    node_id: str,
    depth: int = 1,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    service = _load_demo_services(repo_id)
    return service["context_service"].get_callers(node_id=node_id, depth=depth, task_id=task_id)


@app.get("/demo/{repo_id}/files/snippet")
def demo_file_snippet(
    repo_id: str,
    file_path: str,
    start_line: int,
    end_line: int,
    task_id: str | None = None,
) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    return service["context_service"].get_file_snippet(
        file_path,
        start_line,
        end_line,
        task_id=task_id,
    )


@app.post("/demo/{repo_id}/context/related")
def demo_related_context(repo_id: str, request: RelatedContextRequest) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    task = request.model_dump()
    if request.target_file:
        task["target"] = {
            "type": "file",
            "file_path": request.target_file,
            "symbols": [],
        }
    return service["context_service"].get_related_context(
        task,
        task_id=request.task_id,
        target_file=request.target_file,
        review_dimension=request.review_dimension,
        tags=request.tags,
        max_depth=request.max_depth,
        max_files=request.max_files,
    )


@app.get("/demo/{repo_id}/coverage")
def demo_coverage(repo_id: str) -> dict[str, Any]:
    service = _load_demo_services(repo_id)
    return {
        "usage_coverage_report": service["coverage_service"].get_coverage_report(),
        "uncovered_file_reviews": [
            task.to_dict()
            for task in service["coverage_service"].generate_uncovered_file_reviews()
        ],
    }


def _load_demo_services(repo_id: str) -> dict[str, Any]:
    session = _DEMO_SESSIONS.get(repo_id)
    if session is None:
        raise HTTPException(status_code=404, detail="repo_id has not been indexed in this process")
    return _create_services(repo_id, Path(session["repo_root"]), Path(session["db_path"]))


def _create_services(repo_id: str, repo_root: Path, db_path: Path) -> dict[str, Any]:
    context_service = ContextService(repo_id=repo_id, repo_root=repo_root, db_path=db_path)
    task_generator = ReviewTaskGenerator(context_service)
    coverage_service = CoverageService(context_service, task_generator)
    return {
        "context_service": context_service,
        "task_generator": task_generator,
        "coverage_service": coverage_service,
    }


_DEMO_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Code Review Context Demo</title>
  <style>
    :root { color-scheme: light; font-family: "Segoe UI", Arial, sans-serif; }
    body { margin: 0; background: #f6f7f9; color: #172033; }
    header { padding: 20px 28px; background: #ffffff; border-bottom: 1px solid #d8dde6; }
    h1 { margin: 0 0 6px; font-size: 24px; }
    h2 { margin: 0 0 12px; font-size: 18px; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    p { line-height: 1.55; margin: 8px 0; }
    main { display: grid; grid-template-columns: 340px 1fr 380px; gap: 16px; padding: 16px; }
    section { background: #ffffff; border: 1px solid #d8dde6; border-radius: 8px; padding: 16px; min-width: 0; }
    label { display: block; font-size: 13px; font-weight: 600; margin: 10px 0 5px; }
    input, button { box-sizing: border-box; width: 100%; padding: 9px 10px; border-radius: 6px; border: 1px solid #bcc4d0; font: inherit; }
    button { margin-top: 12px; border: 0; background: #2457c5; color: #fff; font-weight: 700; cursor: pointer; }
    button.secondary { background: #eef2f8; color: #172033; border: 1px solid #bcc4d0; }
    button.inline { width: auto; margin: 4px 6px 4px 0; padding: 6px 9px; font-size: 12px; }
    pre { overflow: auto; max-height: 360px; padding: 12px; background: #101828; color: #e7edf7; border-radius: 6px; font-size: 12px; }
    .cards { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 10px; margin-bottom: 14px; }
    .card { border: 1px solid #d8dde6; border-radius: 8px; padding: 12px; background: #fff; }
    .metric { font-size: 24px; font-weight: 800; margin: 4px 0; }
    .task { border: 1px solid #d8dde6; border-radius: 6px; padding: 10px; margin-bottom: 8px; cursor: pointer; background: #fff; }
    .task:hover { background: #f3f6fb; }
    .task.active { border-color: #2457c5; background: #eef4ff; }
    .pill { display: inline-block; padding: 2px 7px; border-radius: 999px; background: #edf2ff; color: #244aa5; font-size: 12px; margin-right: 4px; }
    .priority-high { background: #fff0f0; color: #b42318; }
    .priority-medium { background: #fff7e6; color: #9a5b00; }
    .priority-low { background: #eff8f0; color: #1f7a3a; }
    .muted { color: #627089; font-size: 13px; }
    .grid { display: grid; gap: 12px; }
    .empty { padding: 24px; color: #627089; text-align: center; border: 1px dashed #bcc4d0; border-radius: 8px; }
    .list { margin: 8px 0 0; padding-left: 18px; }
    details { border: 1px solid #d8dde6; border-radius: 8px; padding: 10px; background: #fff; }
    summary { cursor: pointer; font-weight: 700; }
    @media (max-width: 1100px) { main { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>仓库上下文处理 Demo</h1>
    <p class="muted">输入本地代码目录，生成给下游 Agent 使用的代码上下文索引、评审任务、推荐上下文和覆盖率数据。</p>
  </header>
  <main>
    <section>
      <h2>分析项目</h2>
      <p class="muted">当前 demo 不上传代码，只读取后端机器可访问的本地目录。</p>
      <label>项目名称</label>
      <input id="projectName" value="sample repo" />
      <label>代码目录</label>
      <input id="repoPath" value="tests/fixtures/sample_repo" />
      <button onclick="buildIndex()">开始分析</button>
      <button class="secondary" onclick="loadCoverage()">刷新实际覆盖率</button>
      <p id="status" class="muted">等待开始。</p>
      <h2 style="margin-top: 20px;">任务列表</h2>
      <div id="tasks"></div>
    </section>

    <section>
      <h2>分析结果</h2>
      <div class="grid">
        <div id="summaryCards" class="cards"></div>
        <div id="coverageCards" class="cards"></div>
        <div id="taskDetail" class="empty">点击“开始分析”后，这里会展示仓库摘要、任务卡片和推荐上下文。</div>
        <details>
          <summary>查看原始 JSON</summary>
          <pre id="output">{ "status": "等待构建索引" }</pre>
        </details>
      </div>
    </section>

    <section>
      <h2>给下游 Agent 的说明</h2>
      <p><strong>输入：</strong>本地 Python 仓库目录。</p>
      <p><strong>输出：</strong>仓库摘要、任务卡、推荐节点/文件、源码片段、覆盖率报告。</p>
      <p><strong>建议接入顺序：</strong></p>
      <ol class="list">
        <li>读取任务列表。</li>
        <li>按 task_id 获取推荐上下文。</li>
        <li>按需读取节点详情或源码片段。</li>
        <li>用 coverage_report 判断还有哪些代码没覆盖。</li>
      </ol>
      <pre>GET /demo/{repo_id}/tasks
GET /demo/{repo_id}/tasks/{task_id}/context
GET /demo/{repo_id}/nodes/{node_id}
GET /demo/{repo_id}/files/snippet
GET /demo/{repo_id}/coverage</pre>
      <p class="muted">注意：任务覆盖率表示“任务计划覆盖了什么”；实际覆盖率表示“Agent 工具真的访问过什么”。刚构建完时实际覆盖率可能为 0，点击节点或源码片段后会更新。</p>
    </section>
  </main>

  <script>
    let currentRepoId = "";
    let currentTasks = [];
    let lastPayload = null;
    let activeTaskId = null;

    function show(data) {
      document.getElementById("output").textContent = JSON.stringify(data, null, 2);
    }

    function setStatus(text) {
      document.getElementById("status").textContent = text;
    }

    async function requestJson(url, options) {
      const response = await fetch(url, options);
      const text = await response.text();
      if (!response.ok) {
        throw new Error(text);
      }
      return JSON.parse(text);
    }

    function slug(value) {
      return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "demo-repo";
    }

    async function buildIndex() {
      try {
        setStatus("正在读取目录、扫描文件、解析代码并构建索引...");
        currentRepoId = slug(document.getElementById("projectName").value);
        const payload = {
          repo_id: currentRepoId,
          repo_path: document.getElementById("repoPath").value.trim(),
          db_path: `.demo_data/${currentRepoId}.db`
        };
        const data = await requestJson("/demo/index", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        lastPayload = data;
        currentTasks = data.review_tasks || [];
        activeTaskId = null;
        renderTasks();
        renderSummary(data.repo_summary);
        renderCoverage(data.task_coverage_report, data.usage_coverage_report);
        renderOverview(data);
        show(data);
        setStatus("分析完成。点击左侧任务查看推荐上下文。");
      } catch (error) {
        setStatus("分析失败。");
        show({ error: String(error) });
      }
    }

    function renderTasks() {
      const box = document.getElementById("tasks");
      box.innerHTML = "";
      if (!currentTasks.length) {
        box.innerHTML = `<div class="empty">暂无任务。</div>`;
        return;
      }
      currentTasks.forEach(task => {
        const item = document.createElement("div");
        item.className = `task ${task.task_id === activeTaskId ? "active" : ""}`;
        item.onclick = () => loadTaskContext(task.task_id);
        item.innerHTML = `
          <strong>${humanTaskType(task.task_type)}：${escapeHtml(task.target)}</strong><br>
          <span class="pill priority-${task.priority}">${task.priority}</span>
          <span class="muted">${task.task_id}</span>
        `;
        box.appendChild(item);
      });
    }

    function renderSummary(summary) {
      document.getElementById("summaryCards").innerHTML = `
        ${metricCard("框架", summary.framework || "unknown", "识别到的 Web 框架")}
        ${metricCard("Python 文件", summary.python_files, "参与分析的 Python 文件数")}
        ${metricCard("API 入口", summary.entrypoints.length, "路由入口数量")}
        ${metricCard("配置文件", summary.config_files.length, "需要关注的配置文件")}
      `;
    }

    function renderCoverage(taskCoverage, usageCoverage) {
      const taskRatio = percent(taskCoverage.coverage_ratio);
      const fileRatio = percent(usageCoverage.file_coverage);
      const nodeRatio = percent(usageCoverage.node_coverage);
      const taskDone = percent(usageCoverage.task_completion_rate);
      document.getElementById("coverageCards").innerHTML = `
        ${metricCard("任务计划覆盖", taskRatio, `未覆盖文件：${taskCoverage.uncovered_python_files.length}`)}
        ${metricCard("实际文件覆盖", fileRatio, `已访问文件：${usageCoverage.covered_files.length}`)}
        ${metricCard("实际节点覆盖", nodeRatio, `已访问节点：${usageCoverage.covered_nodes.length}`)}
        ${metricCard("任务完成率", taskDone, `已触达任务：${usageCoverage.completed_task_ids.length}`)}
      `;
    }

    function renderOverview(data) {
      const summary = data.repo_summary;
      document.getElementById("taskDetail").innerHTML = `
        <div class="card">
          <h3>这个仓库分析出了什么？</h3>
          <p>系统已生成 <strong>${data.review_tasks.length}</strong> 张评审任务卡。下游 Agent 可以按任务逐个获取推荐上下文。</p>
          <p><strong>入口点：</strong>${summary.entrypoints.map(item => escapeHtml(item.method_path)).join("，") || "未发现"}</p>
          <p><strong>测试文件：</strong>${summary.test_files.length} 个；<strong>配置文件：</strong>${summary.config_files.join("，") || "无"}</p>
        </div>
      `;
    }

    async function loadTaskContext(taskId) {
      try {
        activeTaskId = taskId;
        renderTasks();
        const data = await requestJson(`/demo/${currentRepoId}/tasks/${encodeURIComponent(taskId)}/context`);
        const task = currentTasks.find(item => item.task_id === taskId);
        renderTaskDetail(task, data);
        show(data);
      } catch (error) {
        show({ error: String(error) });
      }
    }

    function renderTaskDetail(task, context) {
      if (!task) return;
      const nodes = context.recommended_nodes || [];
      const files = context.related_files || [];
      document.getElementById("taskDetail").innerHTML = `
        <div class="card">
          <h3>${humanTaskType(task.task_type)}：${escapeHtml(task.target)}</h3>
          <p><span class="pill priority-${task.priority}">${task.priority}</span><span class="muted">${task.task_id}</span></p>
          <p><strong>为什么看它：</strong>${escapeHtml(context.reason || task.reason || "该任务由规则生成。")}</p>
          <p><strong>关注点：</strong></p>
          <ul class="list">${task.review_focus.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
          <p><strong>推荐节点：</strong></p>
          <div>${nodes.length ? nodes.map(node => `<button class="inline secondary" onclick="loadNode('${encodeURIComponent(node.node_id)}', '${task.task_id}')">${escapeHtml(node.qualified_name)}</button>`).join("") : "<span class='muted'>无推荐节点</span>"}</div>
          <p><strong>相关文件：</strong></p>
          <div>${files.map(file => `<button class="inline secondary" onclick="loadSnippet('${encodeURIComponent(file)}', '${task.task_id}')">${escapeHtml(file)}</button>`).join("")}</div>
        </div>
      `;
    }

    async function loadNode(encodedNodeId, taskId) {
      try {
        const data = await requestJson(`/demo/${currentRepoId}/nodes/${encodedNodeId}?task_id=${encodeURIComponent(taskId)}`);
        show(data);
        await loadCoverage();
      } catch (error) {
        show({ error: String(error) });
      }
    }

    async function loadSnippet(encodedFile, taskId) {
      try {
        const data = await requestJson(`/demo/${currentRepoId}/files/snippet?file_path=${encodedFile}&start_line=1&end_line=40&task_id=${encodeURIComponent(taskId)}`);
        show(data);
        await loadCoverage();
      } catch (error) {
        show({ error: String(error) });
      }
    }

    async function loadCoverage() {
      try {
        const repoId = currentRepoId || slug(document.getElementById("projectName").value);
        const data = await requestJson(`/demo/${repoId}/coverage`);
        if (lastPayload) {
          renderCoverage(lastPayload.task_coverage_report, data.usage_coverage_report);
        }
        show(data);
      } catch (error) {
        show({ error: String(error) });
      }
    }

    function metricCard(title, value, hint) {
      return `<div class="card"><div class="muted">${title}</div><div class="metric">${value}</div><div class="muted">${escapeHtml(String(hint || ""))}</div></div>`;
    }

    function percent(value) {
      return `${Math.round((value || 0) * 100)}%`;
    }

    function humanTaskType(type) {
      const names = {
        entrypoint_review: "入口评审",
        config_review: "配置评审",
        module_review: "模块评审",
        file_review: "文件评审",
        uncovered_file_review: "未覆盖文件补审"
      };
      return names[type] || type;
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }
  </script>
</body>
</html>
"""

_DEBUG_CONSOLE_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Agent Context Debug Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #182230;
      --muted: #667085;
      --border: #d7dee8;
      --soft: #eef3f7;
      --brand: #2563eb;
      --ok: #167647;
      --warn: #b54708;
      --danger: #b42318;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    header {
      height: 72px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 12px 18px;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 5;
    }
    h1 { margin: 0; font-size: 20px; line-height: 1.2; }
    h2 { margin: 0; font-size: 15px; }
    h3 { margin: 0 0 8px; font-size: 14px; }
    label { display: block; margin: 10px 0 5px; color: var(--muted); font-size: 12px; font-weight: 700; }
    input, select, textarea {
      width: 100%;
      min-height: 34px;
      border: 1px solid #c7d0dd;
      border-radius: 6px;
      padding: 7px 9px;
      background: #fff;
      color: var(--text);
      font: inherit;
      font-size: 13px;
    }
    textarea { min-height: 98px; resize: vertical; font-family: Consolas, "Courier New", monospace; }
    button {
      border: 0;
      border-radius: 6px;
      padding: 8px 10px;
      background: var(--brand);
      color: #fff;
      font: inherit;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      white-space: nowrap;
    }
    button.secondary { background: var(--soft); color: var(--text); border: 1px solid #c7d0dd; }
    button.ghost { background: transparent; color: var(--brand); border: 1px solid var(--border); }
    button.small { padding: 6px 8px; font-size: 12px; }
    .status {
      max-width: 560px;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--soft);
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }
    main {
      display: grid;
      grid-template-columns: 330px minmax(520px, 1fr) 420px;
      gap: 12px;
      padding: 12px;
      height: calc(100vh - 72px);
    }
    .panel {
      min-height: 0;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .panel-head {
      min-height: 46px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      background: #fbfcfe;
    }
    .panel-body { padding: 12px; overflow: auto; min-height: 0; }
    .row { display: flex; gap: 8px; align-items: center; }
    .row > * { flex: 1; }
    .stack { display: grid; gap: 10px; }
    .muted { color: var(--muted); }
    .small-text { font-size: 12px; line-height: 1.45; }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 20px;
      padding: 2px 7px;
      border-radius: 999px;
      background: var(--soft);
      color: #344054;
      font-size: 12px;
      font-weight: 700;
    }
    .pill.high { color: var(--danger); background: #fff1f0; }
    .pill.medium { color: var(--warn); background: #fff7e6; }
    .pill.low { color: var(--ok); background: #ecfdf3; }
    .task-card {
      border: 1px solid var(--border);
      border-radius: 7px;
      padding: 10px;
      cursor: pointer;
      background: #fff;
      display: grid;
      gap: 6px;
    }
    .task-card:hover, .task-card.active { border-color: var(--brand); background: #f5f8ff; }
    .task-title { font-weight: 800; line-height: 1.3; word-break: break-word; }
    .task-meta { display: flex; flex-wrap: wrap; gap: 5px; }
    .kv {
      display: grid;
      grid-template-columns: 130px minmax(0, 1fr);
      gap: 7px 10px;
      font-size: 13px;
    }
    .kv div:nth-child(odd) { color: var(--muted); font-weight: 700; }
    .box {
      border: 1px solid var(--border);
      border-radius: 7px;
      background: #fff;
      padding: 10px;
      min-width: 0;
    }
    .grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .tabs { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
    .tab { background: transparent; border: 1px solid var(--border); color: var(--muted); }
    .tab.active { background: var(--brand); color: #fff; border-color: var(--brand); }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { border-bottom: 1px solid var(--border); padding: 7px 6px; text-align: left; vertical-align: top; }
    th { color: var(--muted); background: #fbfcfe; font-weight: 800; position: sticky; top: 0; }
    tr.clickable { cursor: pointer; }
    tr.clickable:hover { background: #f5f8ff; }
    pre {
      margin: 0;
      padding: 10px;
      overflow: auto;
      max-height: 420px;
      border-radius: 7px;
      background: #101828;
      color: #e6edf7;
      font-size: 12px;
      line-height: 1.5;
    }
    .code { white-space: pre; font-family: Consolas, "Courier New", monospace; }
    .empty {
      padding: 22px;
      border: 1px dashed #b7c1cf;
      border-radius: 7px;
      color: var(--muted);
      text-align: center;
      background: #fbfcfe;
    }
    .log-item {
      border-bottom: 1px solid var(--border);
      padding: 8px 0;
      font-size: 12px;
      display: grid;
      gap: 4px;
    }
    .ok { color: var(--ok); font-weight: 800; }
    .bad { color: var(--danger); font-weight: 800; }
    .nowrap { white-space: nowrap; }
    .wrap { word-break: break-word; }
    @media (max-width: 1180px) {
      header { height: auto; align-items: flex-start; flex-direction: column; }
      main { grid-template-columns: 1fr; height: auto; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Agent Context Debug Console</h1>
      <div class="muted small-text">调试展示层：只调用接口、展示响应、记录日志；priority、risk_score、relation_to_target、reason 均以后端返回为准。</div>
    </div>
    <div id="status" class="status">等待构建索引或输入已有 repo_id。</div>
  </header>

  <main>
    <aside class="panel">
      <div class="panel-head"><h2>Repository & Tasks</h2></div>
      <div class="panel-body stack">
        <div class="box">
          <label for="repoId">repo_id</label>
          <input id="repoId" value="sample-repo" />
          <label for="repoPath">repo_path</label>
          <input id="repoPath" value="tests/fixtures/sample_repo" />
          <label for="dbPath">db_path</label>
          <input id="dbPath" value=".demo_data/sample-repo.db" />
          <div class="row" style="margin-top:10px">
            <button onclick="buildIndex()">POST /context/index</button>
            <button class="secondary" onclick="loadTasks()">Load Tasks</button>
          </div>
        </div>

        <div class="box">
          <div class="row">
            <div>
              <label for="dimensionFilter">review_dimension</label>
              <select id="dimensionFilter" onchange="loadTasks()">
                <option value="function_logic">function_logic</option>
                <option value="security">security</option>
                <option value="coding_style">coding_style</option>
                <option value="requirement_consistency">requirement_consistency</option>
              </select>
            </div>
            <div>
              <label for="taskTypeFilter">task_type</label>
              <select id="taskTypeFilter" onchange="renderTasks()">
                <option value="">all</option>
                <option value="entrypoint_review">entrypoint_review</option>
                <option value="config_review">config_review</option>
                <option value="module_review">module_review</option>
                <option value="file_review">file_review</option>
              </select>
            </div>
          </div>
        </div>

        <div id="repoSummary" class="box small-text muted">暂无仓库摘要。</div>
        <div class="row"><span id="taskCount" class="pill">0 tasks</span><button class="ghost small" onclick="refreshUsage()">Refresh Usage</button></div>
        <div id="taskList" class="stack"><div class="empty">暂无任务。</div></div>
      </div>
    </aside>

    <section class="panel">
      <div class="panel-head">
        <h2 id="taskTitle">Task Debug Area</h2>
        <div class="row" style="flex:0 0 auto">
          <button class="ghost small" onclick="refreshSelectedTask()">Reload Task</button>
          <button class="secondary small" onclick="showRaw(state.selectedPackage || {})">Raw Task</button>
        </div>
      </div>
      <div class="tabs">
        <button id="tabPackage" class="tab active" onclick="switchCenterTab('package')">Package</button>
        <button id="tabGraph" class="tab" onclick="switchCenterTab('graph')">Graph Slice</button>
        <button id="tabNode" class="tab" onclick="switchCenterTab('node')">Node Detail</button>
        <button id="tabSnippet" class="tab" onclick="switchCenterTab('snippet')">File Snippet</button>
        <button id="tabTool" class="tab" onclick="switchCenterTab('tool')">Tool Debugger</button>
      </div>
      <div id="centerPane" class="panel-body stack"></div>
    </section>

    <aside class="panel">
      <div class="panel-head"><h2>Raw JSON / Logs / Usage</h2></div>
      <div class="tabs">
        <button id="tabRightRaw" class="tab active" onclick="switchRightTab('raw')">Raw JSON</button>
        <button id="tabRightLogs" class="tab" onclick="switchRightTab('logs')">API Logs</button>
        <button id="tabRightUsage" class="tab" onclick="switchRightTab('usage')">Usage</button>
      </div>
      <div id="rightPane" class="panel-body"></div>
    </aside>
  </main>

  <script>
    const state = {
      repoId: "",
      tasks: [],
      selectedTaskId: "",
      selectedPackage: null,
      graphSlice: null,
      nodeDetail: null,
      fileSnippet: null,
      relatedContext: null,
      usage: null,
      raw: {},
      logs: []
    };

    const apiClient = {
      buildIndex(body) {
        return requestJson("POST", "/context/index", { body });
      },
      listTasks(repoId, reviewDimension) {
        return requestJson("GET", `/context/tasks?repo_id=${enc(repoId)}&review_dimension=${enc(reviewDimension)}`);
      },
      getTaskPackage(taskId) {
        return requestJson("GET", `/context/task-package/${enc(taskId)}?repo_id=${enc(state.repoId)}`);
      },
      getTaskGraphSlice(taskId, depth = 2) {
        return requestJson("GET", `/context/tasks/${enc(taskId)}/graph-slice?repo_id=${enc(state.repoId)}&depth=${enc(depth)}`);
      },
      getNodeDetail({ node_id, symbol_name, task_id, review_dimension }) {
        const qs = query({ repo_id: state.repoId, node_id, symbol_name, task_id, review_dimension });
        return requestJson("GET", `/context/node-detail?${qs}`);
      },
      getFileSnippet({ file_path, start_line, end_line, task_id, review_dimension }) {
        const qs = query({ repo_id: state.repoId, file_path, start_line, end_line, task_id, review_dimension });
        return requestJson("GET", `/context/file-snippet?${qs}`);
      },
      getCallers({ node_id, symbol_name, depth, task_id, review_dimension }) {
        const qs = query({ repo_id: state.repoId, node_id, symbol_name, depth, task_id, review_dimension });
        return requestJson("GET", `/context/callers?${qs}`);
      },
      getCallees({ node_id, symbol_name, depth, task_id, review_dimension }) {
        const qs = query({ repo_id: state.repoId, node_id, symbol_name, depth, task_id, review_dimension });
        return requestJson("GET", `/context/callees?${qs}`);
      },
      getRelatedContext(body) {
        return requestJson("POST", "/context/related-context", { body: { repo_id: state.repoId, ...body } });
      },
      getUsage(repoId = state.repoId) {
        return requestJson("GET", `/demo/${enc(repoId)}/coverage`);
      }
    };

    function enc(value) {
      return encodeURIComponent(value == null ? "" : String(value));
    }

    function query(params) {
      return Object.entries(params)
        .filter(([, value]) => value !== undefined && value !== null && value !== "")
        .map(([key, value]) => `${enc(key)}=${enc(value)}`)
        .join("&");
    }

    async function requestJson(method, url, options = {}) {
      const started = performance.now();
      const init = { method, headers: {} };
      if (options.body !== undefined) {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(options.body);
      }
      let status = 0;
      let rawText = "";
      try {
        const response = await fetch(url, init);
        status = response.status;
        rawText = await response.text();
        const elapsedMs = Math.round(performance.now() - started);
        let payload = parseMaybeJson(rawText);
        addLog({ method, url, params: options.body || {}, status, elapsedMs, ok: response.ok, response: payload });
        if (!response.ok) {
          showRaw({ error: payload, method, url, status });
          throw new Error(typeof payload === "string" ? payload : JSON.stringify(payload));
        }
        showRaw(payload);
        return payload;
      } catch (error) {
        if (!status) {
          addLog({ method, url, params: options.body || {}, status: "-", elapsedMs: Math.round(performance.now() - started), ok: false, response: String(error) });
          showRaw({ error: String(error), method, url });
        }
        throw error;
      }
    }

    function parseMaybeJson(text) {
      try { return JSON.parse(text); } catch { return text; }
    }

    function addLog(log) {
      state.logs.unshift({ at: new Date().toLocaleTimeString(), ...log });
      state.logs = state.logs.slice(0, 80);
      if (activeRightTab() === "logs") renderRightPane();
    }

    async function buildIndex() {
      const repoId = valueOf("repoId") || "sample-repo";
      state.repoId = repoId;
      setStatus("Building index...");
      const body = {
        repo_id: repoId,
        repo_path: valueOf("repoPath") || "tests/fixtures/sample_repo",
        db_path: valueOf("dbPath") || `.demo_data/${repoId}.db`
      };
      try {
        const data = await apiClient.buildIndex(body);
        state.tasks = Array.isArray(data.review_tasks) ? data.review_tasks : [];
        renderRepoSummary(data.repo_summary || {});
        renderTasks();
        renderTaskEmpty("索引已构建。请选择左侧任务查看 task package 和 graph slice。");
        state.usage = data.usage_coverage_report || null;
        renderRightPane();
        setStatus(`Index ready: ${state.tasks.length} tasks.`);
      } catch (error) {
        setStatus(`Index failed: ${String(error)}`);
      }
    }

    async function loadTasks() {
      state.repoId = valueOf("repoId") || state.repoId || "sample-repo";
      const dimension = valueOf("dimensionFilter");
      setStatus(`Loading tasks for ${dimension}...`);
      try {
        const data = await apiClient.listTasks(state.repoId, dimension);
        state.tasks = Array.isArray(data.tasks) ? data.tasks : [];
        renderTasks();
        setStatus(`Loaded ${state.tasks.length} tasks.`);
      } catch (error) {
        setStatus(`Load tasks failed: ${String(error)}`);
      }
    }

    async function selectTask(taskId) {
      state.selectedTaskId = taskId;
      renderTasks();
      setStatus(`Loading task ${taskId}...`);
      try {
        const [pkg, graph] = await Promise.all([
          apiClient.getTaskPackage(taskId),
          apiClient.getTaskGraphSlice(taskId, 2)
        ]);
        state.selectedPackage = pkg;
        state.graphSlice = graph;
        state.nodeDetail = null;
        state.fileSnippet = null;
        renderPackage();
        await refreshUsage(false);
        setStatus(`Task loaded: ${taskId}`);
      } catch (error) {
        renderTaskEmpty(`任务加载失败：${String(error)}`);
        setStatus(`Task load failed: ${String(error)}`);
      }
    }

    function refreshSelectedTask() {
      if (state.selectedTaskId) selectTask(state.selectedTaskId);
    }

    async function refreshUsage(showStatus = true) {
      state.repoId = valueOf("repoId") || state.repoId;
      if (!state.repoId) return;
      try {
        const data = await apiClient.getUsage(state.repoId);
        state.usage = data;
        if (showStatus) setStatus("Usage refreshed.");
        if (activeRightTab() === "usage") renderRightPane();
      } catch (error) {
        if (showStatus) setStatus(`Usage refresh failed: ${String(error)}`);
      }
    }

    async function loadNodeDetail(node) {
      const args = {
        node_id: node.id || node.node_id || "",
        symbol_name: node.name || "",
        task_id: state.selectedTaskId,
        review_dimension: selectedDimension()
      };
      try {
        state.nodeDetail = await apiClient.getNodeDetail(args);
        switchCenterTab("node");
        await refreshUsage(false);
      } catch (error) {
        setStatus(`Node detail failed: ${String(error)}`);
      }
    }

    async function quickCall(tool) {
      const node = state.nodeDetail || {};
      const pkg = state.selectedPackage || {};
      const target = pkg.target || {};
      const symbol = node.name || node.symbol_name || (Array.isArray(target.symbols) ? target.symbols[0] : "");
      const common = {
        node_id: node.node_id || node.id || "",
        symbol_name: symbol || "",
        task_id: state.selectedTaskId,
        review_dimension: selectedDimension(),
        depth: 1
      };
      try {
        let data;
        if (tool === "callers") data = await apiClient.getCallers(common);
        if (tool === "callees") data = await apiClient.getCallees(common);
        if (tool === "related") data = await apiClient.getRelatedContext({
          task_id: state.selectedTaskId,
          target_file: target.file_path || "",
          review_dimension: selectedDimension(),
          tags: pkg.tags || [],
          max_depth: 1,
          max_files: 3
        });
        if (tool === "snippet") data = await apiClient.getFileSnippet({
          file_path: node.file_path || target.file_path || "",
          start_line: node.start_line || 1,
          end_line: node.end_line || 80,
          task_id: state.selectedTaskId,
          review_dimension: selectedDimension()
        });
        if (tool === "snippet") state.fileSnippet = data;
        switchCenterTab(tool === "snippet" ? "snippet" : "node");
        showRaw(data || {});
        await refreshUsage(false);
      } catch (error) {
        setStatus(`${tool} failed: ${String(error)}`);
      }
    }

    async function runToolDebugger() {
      const tool = valueOf("toolName");
      let args = {};
      try {
        args = JSON.parse(valueOf("toolArgs") || "{}");
      } catch (error) {
        showRaw({ error: "Tool args must be JSON", detail: String(error) });
        return;
      }
      try {
        let data;
        if (tool === "get_task_package") data = await apiClient.getTaskPackage(args.task_id || state.selectedTaskId);
        if (tool === "get_task_graph_slice") data = await apiClient.getTaskGraphSlice(args.task_id || state.selectedTaskId, args.depth || 2);
        if (tool === "get_node_detail") data = await apiClient.getNodeDetail({ task_id: state.selectedTaskId, review_dimension: selectedDimension(), ...args });
        if (tool === "get_file_snippet") data = await apiClient.getFileSnippet({ task_id: state.selectedTaskId, review_dimension: selectedDimension(), ...args });
        if (tool === "get_callers") data = await apiClient.getCallers({ task_id: state.selectedTaskId, review_dimension: selectedDimension(), ...args });
        if (tool === "get_callees") data = await apiClient.getCallees({ task_id: state.selectedTaskId, review_dimension: selectedDimension(), ...args });
        if (tool === "get_related_context") data = await apiClient.getRelatedContext({ task_id: state.selectedTaskId, review_dimension: selectedDimension(), ...args });
        if (tool === "get_usage") data = await apiClient.getUsage(args.repo_id || state.repoId);
        showRaw(data || {});
      } catch (error) {
        setStatus(`Tool debugger failed: ${String(error)}`);
      }
    }

    function renderTasks() {
      const list = document.getElementById("taskList");
      const typeFilter = valueOf("taskTypeFilter");
      const tasks = state.tasks.filter(task => !typeFilter || task.task_type === typeFilter);
      document.getElementById("taskCount").textContent = `${tasks.length} tasks`;
      if (!tasks.length) {
        list.innerHTML = `<div class="empty">没有匹配任务。</div>`;
        return;
      }
      list.innerHTML = tasks.map(task => `
        <div class="task-card ${task.task_id === state.selectedTaskId ? "active" : ""}" onclick="selectTask('${escapeAttr(task.task_id)}')">
          <div class="task-title">${html(task.task_type)} · ${html(readTargetLabel(task.target || task.legacy_target))}</div>
          <div class="task-meta">
            <span class="pill ${html(task.priority)}">${html(task.priority || "-")}</span>
            <span class="pill">${html(task.review_dimension || "-")}</span>
          </div>
          <div class="muted small-text wrap">${html(task.task_id || "-")}</div>
        </div>
      `).join("");
    }

    function renderRepoSummary(summary) {
      document.getElementById("repoSummary").innerHTML = `
        <div class="kv">
          <div>repo_id</div><div>${html(summary.repo_id || state.repoId || "-")}</div>
          <div>framework</div><div>${html(summary.framework || "-")}</div>
          <div>python_files</div><div>${html(summary.python_files ?? "-")}</div>
          <div>entrypoints</div><div>${html((summary.entrypoints || []).length)}</div>
        </div>`;
    }

    function renderPackage() {
      switchCenterTab("package", false);
      const pkg = state.selectedPackage || {};
      const target = pkg.target || {};
      document.getElementById("taskTitle").textContent = `Task: ${pkg.task_id || state.selectedTaskId || "-"}`;
      document.getElementById("centerPane").innerHTML = `
        <div class="grid-2">
          <div class="box">
            <h3>Task Package</h3>
            <div class="kv">
              <div>task_id</div><div>${html(pkg.task_id || "-")}</div>
              <div>task_type</div><div>${html(pkg.task_type || "-")}</div>
              <div>dimension</div><div>${html(pkg.review_dimension || "-")}</div>
              <div>priority</div><div>${html(pkg.priority || "-")}</div>
              <div>target file</div><div>${html(target.file_path || "-")}</div>
              <div>symbols</div><div>${html((target.symbols || []).join(", ") || "-")}</div>
            </div>
          </div>
          <div class="box">
            <h3>Context Policy / Tools</h3>
            <div class="small-text muted">前端只展示后端返回策略，不自行判断范围。</div>
            <pre>${jsonText({ context_policy: pkg.context_policy || {}, available_tools: pkg.available_tools || [] })}</pre>
          </div>
        </div>
        <div class="box">
          <h3>initial_context</h3>
          <pre>${jsonText(pkg.initial_context || {})}</pre>
        </div>
        <div class="box">
          <h3>Graph Slice Preview</h3>
          ${renderGraphTables(state.graphSlice || {})}
        </div>`;
    }

    function renderGraph() {
      document.getElementById("centerPane").innerHTML = `
        <div class="box">
          <div class="row" style="margin-bottom:8px">
            <h3>task-local graph slice</h3>
            <button class="secondary small" onclick="reloadGraphDepth()">Reload depth</button>
            <input id="graphDepth" value="2" style="max-width:80px" />
          </div>
          ${renderGraphTables(state.graphSlice || {})}
        </div>`;
    }

    async function reloadGraphDepth() {
      if (!state.selectedTaskId) return;
      state.graphSlice = await apiClient.getTaskGraphSlice(state.selectedTaskId, Number(valueOf("graphDepth") || 2));
      renderGraph();
    }

    function renderGraphTables(graph) {
      const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
      const edges = Array.isArray(graph.edges) ? graph.edges : [];
      const boundary = Array.isArray(graph.boundary_nodes) ? graph.boundary_nodes : [];
      return `
        <h3>nodes</h3>
        ${table(["id","name","type","file_path","relation_to_target","priority","risk_score","reason"], nodes, row => rowClick(row), true)}
        <h3 style="margin-top:12px">edges</h3>
        ${table(["source","target","type"], edges)}
        <h3 style="margin-top:12px">boundary_nodes</h3>
        ${table(["id","name","file_path","reason","risk_score"], boundary, row => rowClick(row), true)}
      `;
    }

    function renderNode() {
      const node = state.nodeDetail || {};
      document.getElementById("centerPane").innerHTML = `
        <div class="box">
          <h3>Node Detail</h3>
          <div class="kv">
            <div>node_id</div><div>${html(node.node_id || node.id || "-")}</div>
            <div>name</div><div>${html(node.name || node.symbol_name || "-")}</div>
            <div>type</div><div>${html(node.type || node.node_type || "-")}</div>
            <div>file_path</div><div>${html(node.file_path || "-")}</div>
            <div>line</div><div>${html([node.start_line, node.end_line].filter(v => v != null).join("-") || "-")}</div>
          </div>
          <div class="row" style="margin-top:10px">
            <button class="secondary small" onclick="quickCall('callers')">get_callers</button>
            <button class="secondary small" onclick="quickCall('callees')">get_callees</button>
            <button class="secondary small" onclick="quickCall('related')">get_related_context</button>
            <button class="secondary small" onclick="quickCall('snippet')">get_file_snippet</button>
          </div>
        </div>
        <div class="box"><h3>Source</h3><pre>${html(node.source || node.code || "-")}</pre></div>
        <div class="box"><h3>Raw Node JSON</h3><pre>${jsonText(node)}</pre></div>`;
    }

    function renderSnippet() {
      const snippet = state.fileSnippet || {};
      document.getElementById("centerPane").innerHTML = `
        <div class="box">
          <h3>File Snippet</h3>
          <div class="kv">
            <div>file_path</div><div>${html(snippet.file_path || "-")}</div>
            <div>lines</div><div>${html([snippet.start_line, snippet.end_line].filter(v => v != null).join("-") || "-")}</div>
          </div>
        </div>
        <div class="box"><pre>${html(snippet.content || snippet.source || "-")}</pre></div>`;
    }

    function renderToolDebugger() {
      const defaultArgs = {
        task_id: state.selectedTaskId || "",
        node_id: "",
        symbol_name: "",
        file_path: ((state.selectedPackage || {}).target || {}).file_path || "",
        start_line: 1,
        end_line: 80,
        depth: 1
      };
      document.getElementById("centerPane").innerHTML = `
        <div class="box">
          <h3>Tool Debugger</h3>
          <label for="toolName">tool</label>
          <select id="toolName">
            <option value="get_task_package">get_task_package</option>
            <option value="get_task_graph_slice">get_task_graph_slice</option>
            <option value="get_node_detail">get_node_detail</option>
            <option value="get_file_snippet">get_file_snippet</option>
            <option value="get_callers">get_callers</option>
            <option value="get_callees">get_callees</option>
            <option value="get_related_context">get_related_context</option>
            <option value="get_usage">get_usage</option>
          </select>
          <label for="toolArgs">arguments JSON</label>
          <textarea id="toolArgs">${jsonText(defaultArgs)}</textarea>
          <button onclick="runToolDebugger()">Run</button>
        </div>`;
    }

    function renderRightPane() {
      const tab = activeRightTab();
      if (tab === "raw") document.getElementById("rightPane").innerHTML = `<pre>${jsonText(state.raw)}</pre>`;
      if (tab === "logs") document.getElementById("rightPane").innerHTML = state.logs.length
        ? state.logs.map(log => `<div class="log-item">
            <div><span class="${log.ok ? "ok" : "bad"}">${log.ok ? "OK" : "ERR"}</span> <strong>${html(log.method)}</strong> ${html(log.url)}</div>
            <div class="muted">${html(log.status)} · ${html(log.elapsedMs)}ms · ${html(log.at)}</div>
            <pre>${jsonText({ params: log.params, response: log.response })}</pre>
          </div>`).join("")
        : `<div class="empty">暂无 API 调用日志。</div>`;
      if (tab === "usage") document.getElementById("rightPane").innerHTML = `<pre>${jsonText(state.usage || {})}</pre>`;
    }

    function renderTaskEmpty(message) {
      document.getElementById("centerPane").innerHTML = `<div class="empty">${html(message)}</div>`;
    }

    function switchCenterTab(tab, render = true) {
      for (const name of ["package","graph","node","snippet","tool"]) {
        document.getElementById(`tab${cap(name)}`).classList.toggle("active", name === tab);
      }
      if (!render) return;
      if (tab === "package") renderPackage();
      if (tab === "graph") renderGraph();
      if (tab === "node") renderNode();
      if (tab === "snippet") renderSnippet();
      if (tab === "tool") renderToolDebugger();
    }

    function switchRightTab(tab) {
      for (const name of ["raw","logs","usage"]) {
        document.getElementById(`tabRight${cap(name)}`).classList.toggle("active", name === tab);
      }
      renderRightPane();
    }

    function activeRightTab() {
      if (document.getElementById("tabRightLogs").classList.contains("active")) return "logs";
      if (document.getElementById("tabRightUsage").classList.contains("active")) return "usage";
      return "raw";
    }

    function showRaw(data) {
      state.raw = data;
      if (activeRightTab() === "raw") renderRightPane();
    }

    function table(columns, rows, onClick, clickable = false) {
      if (!rows.length) return `<div class="empty">-</div>`;
      return `<table><thead><tr>${columns.map(col => `<th>${html(col)}</th>`).join("")}</tr></thead><tbody>` +
        rows.map((row, index) => `<tr class="${clickable ? "clickable" : ""}" ${clickable ? `onclick="${onClick(row, index)}"` : ""}>` +
          columns.map(col => `<td>${html(read(row, col))}</td>`).join("") +
        `</tr>`).join("") + `</tbody></table>`;
    }

    function rowClick(row) {
      const encoded = encodeURIComponent(JSON.stringify(row));
      return `loadNodeDetail(JSON.parse(decodeURIComponent('${encoded}')))`;
    }

    function read(row, key) {
      const value = row && row[key];
      if (Array.isArray(value)) return value.join(", ");
      if (value && typeof value === "object") return JSON.stringify(value);
      return value ?? "-";
    }

    function readTargetLabel(target) {
      if (!target) return "-";
      if (typeof target === "string") return target;
      return target.file_path || (target.symbols || []).join(", ") || target.type || "-";
    }

    function selectedDimension() {
      return (state.selectedPackage || {}).review_dimension || valueOf("dimensionFilter") || "function_logic";
    }

    function valueOf(id) {
      const el = document.getElementById(id);
      return el ? el.value.trim() : "";
    }

    function html(value) {
      return String(value ?? "-")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function escapeAttr(value) {
      return html(value).replaceAll("`", "&#096;");
    }

    function jsonText(value) {
      return html(JSON.stringify(value ?? {}, null, 2));
    }

    function cap(value) {
      return value.charAt(0).toUpperCase() + value.slice(1);
    }

    function setStatus(text) {
      document.getElementById("status").textContent = text;
    }

    function initFromPath() {
      const match = location.pathname.match(/^[/]demo[/]([^/]+)[/]tasks[/]([^/]+)$/);
      if (match) {
        document.getElementById("repoId").value = decodeURIComponent(match[1]);
        state.repoId = decodeURIComponent(match[1]);
        state.selectedTaskId = decodeURIComponent(match[2]);
        setStatus(`Task page loaded. Click Reload Task after this repo_id has been indexed in this process.`);
      }
      renderTaskEmpty("构建索引或加载任务后，调试链路会显示在这里。");
      renderRightPane();
    }

    initFromPath();
  </script>
</body>
</html>
"""

_DEMO_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Code Review Context</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #172033;
      --muted: #5f6f86;
      --border: #dbe2ec;
      --soft: #eef3f8;
      --brand: #1f5fbf;
      --brand-2: #0f766e;
      --danger: #b42318;
      --warn: #9a5b00;
      --ok: #1f7a3a;
      --shadow: 0 10px 28px rgba(25, 39, 70, 0.08);
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
    }

    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    header {
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      padding: 20px 28px;
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .topbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
    .brand h1 { margin: 0; font-size: 22px; line-height: 1.2; }
    .brand p { margin: 6px 0 0; color: var(--muted); font-size: 13px; }
    .status {
      min-width: 260px;
      max-width: 520px;
      padding: 10px 12px;
      border-radius: 8px;
      background: var(--soft);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    main {
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      gap: 16px;
      padding: 16px;
      max-width: 1500px;
      margin: 0 auto;
    }

    section, aside {
      min-width: 0;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .panel-header {
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .panel-header h2 { margin: 0; font-size: 16px; }
    .panel-body { padding: 16px; }

    label { display: block; margin: 12px 0 6px; font-size: 13px; font-weight: 700; }
    input {
      width: 100%;
      height: 38px;
      border: 1px solid #c6ceda;
      border-radius: 7px;
      padding: 8px 10px;
      font: inherit;
      color: var(--text);
      background: #fff;
    }
    button {
      border: 0;
      border-radius: 7px;
      padding: 9px 12px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      background: var(--brand);
      color: #fff;
    }
    button.secondary { background: #eef3f8; color: var(--text); border: 1px solid #c6ceda; }
    button.ghost { background: transparent; color: var(--brand); border: 1px solid var(--border); }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .button-row { display: flex; gap: 8px; margin-top: 14px; }
    .button-row button { flex: 1; }

    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(130px, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }
    .metric {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 13px;
    }
    .metric .label { color: var(--muted); font-size: 12px; font-weight: 700; }
    .metric .value { margin-top: 5px; font-size: 24px; font-weight: 800; }
    .metric .hint { margin-top: 3px; color: var(--muted); font-size: 12px; }

    .task-list { display: grid; gap: 10px; max-height: calc(100vh - 310px); overflow: auto; padding-right: 2px; }
    .task-card {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
      cursor: pointer;
      transition: border-color 120ms ease, background 120ms ease, transform 120ms ease;
    }
    .task-card:hover { border-color: #9eb4d5; background: #f9fbfe; transform: translateY(-1px); }
    .task-card.active { border-color: var(--brand); background: #eef5ff; }
    .task-title { font-weight: 800; line-height: 1.35; margin-bottom: 8px; }
    .task-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
    .task-target { color: var(--muted); font-size: 12px; word-break: break-all; line-height: 1.45; }

    .pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      min-height: 22px;
      padding: 3px 8px;
      border-radius: 999px;
      background: var(--soft);
      color: #334155;
      font-size: 12px;
      font-weight: 700;
    }
    .pill.high { background: #fff0f0; color: var(--danger); }
    .pill.medium { background: #fff7e6; color: var(--warn); }
    .pill.low { background: #eff8f0; color: var(--ok); }
    .pill.security { background: #edf2ff; color: #244aa5; }
    .pill.logic { background: #ecfdf5; color: var(--brand-2); }

    .workspace {
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) minmax(360px, 0.65fr);
      gap: 16px;
    }
    .detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .info-box {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
      min-width: 0;
    }
    .info-box h3 { margin: 0 0 8px; font-size: 14px; }
    .info-box p, .info-box li { color: var(--muted); font-size: 13px; line-height: 1.55; }
    .info-box p { margin: 6px 0; }
    ul { margin: 8px 0 0; padding-left: 18px; }
    .empty {
      padding: 28px;
      border: 1px dashed #b8c2d1;
      border-radius: 8px;
      color: var(--muted);
      text-align: center;
      background: #fbfcff;
    }

    .tabs { display: flex; gap: 6px; border-bottom: 1px solid var(--border); padding: 0 12px; }
    .tab {
      border: 0;
      border-radius: 0;
      background: transparent;
      color: var(--muted);
      padding: 11px 8px;
      border-bottom: 2px solid transparent;
    }
    .tab.active { color: var(--brand); border-bottom-color: var(--brand); }
    .tab-content { padding: 14px; }

    pre {
      margin: 0;
      padding: 12px;
      overflow: auto;
      max-height: 430px;
      border-radius: 8px;
      background: #111827;
      color: #e5edf7;
      font-size: 12px;
      line-height: 1.5;
    }

    .context-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
    .context-actions button { padding: 7px 9px; font-size: 12px; }
    .split-line { height: 1px; background: var(--border); margin: 12px 0; }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    .mono { font-family: Consolas, "Courier New", monospace; }

    @media (max-width: 1180px) {
      main, .workspace { grid-template-columns: 1fr; }
      .task-list { max-height: none; }
    }
    @media (max-width: 760px) {
      header { padding: 16px; }
      .topbar { align-items: flex-start; flex-direction: column; }
      .status { min-width: 0; width: 100%; }
      main { padding: 10px; }
      .metrics, .detail-grid { grid-template-columns: 1fr; }
      .button-row { flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div class="brand">
        <h1>AI 仓库级代码评审上下文台</h1>
        <p>为下游 review agent 构建任务包、局部调用图、按需源码片段和覆盖率追踪。</p>
      </div>
      <div id="status" class="status">等待索引仓库。输入本地仓库路径后开始分析。</div>
    </div>
  </header>

  <main>
    <aside class="panel">
      <div class="panel-header">
        <h2>分析入口</h2>
      </div>
      <div class="panel-body">
        <label for="projectName">项目标识</label>
        <input id="projectName" value="sample-repo" />

        <label for="repoPath">仓库路径</label>
        <input id="repoPath" value="tests/fixtures/sample_repo" />

        <div class="button-row">
          <button onclick="buildIndex()">构建索引</button>
          <button class="secondary" onclick="refreshCoverage()">刷新覆盖率</button>
        </div>

        <div class="split-line"></div>
        <div class="small muted">任务卡片按优先级和目标展示。点击任务后右侧会显示上下文包、局部调用图和可继续查询的工具入口。</div>
      </div>

      <div class="panel-header">
        <h2>评审任务</h2>
        <span id="taskCount" class="pill">0 个</span>
      </div>
      <div class="panel-body">
        <div id="tasks" class="task-list">
          <div class="empty">尚未生成任务。</div>
        </div>
      </div>
    </aside>

    <section>
      <div id="metrics" class="metrics"></div>
      <div class="workspace">
        <div class="panel">
          <div class="panel-header">
            <h2 id="detailTitle">任务详情</h2>
            <button class="ghost" onclick="showRaw(lastSelectedPackage || lastPayload || {})">查看 JSON</button>
          </div>
          <div id="detail" class="panel-body">
            <div class="empty">先点击“构建索引”，再选择左侧任务。这里会展示任务为什么存在、看哪些文件和符号、初始上下文有多大。</div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-header">
            <h2>上下文与覆盖率</h2>
          </div>
          <div class="tabs">
            <button id="tabContext" class="tab active" onclick="switchTab('context')">上下文</button>
            <button id="tabCoverage" class="tab" onclick="switchTab('coverage')">覆盖率</button>
            <button id="tabRaw" class="tab" onclick="switchTab('raw')">原始数据</button>
          </div>
          <div id="contextPane" class="tab-content"></div>
          <div id="coveragePane" class="tab-content" style="display:none"></div>
          <div id="rawPane" class="tab-content" style="display:none"><pre id="rawOutput">{}</pre></div>
        </div>
      </div>
    </section>
  </main>

  <script>
    let currentRepoId = "";
    let currentTasks = [];
    let activeTaskId = "";
    let lastPayload = null;
    let lastSelectedPackage = null;
    let lastCoverage = null;

    function setStatus(text) {
      document.getElementById("status").textContent = text;
    }

    function slug(value) {
      return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "demo-repo";
    }

    async function requestJson(url, options) {
      const response = await fetch(url, options);
      const text = await response.text();
      if (!response.ok) throw new Error(text || response.statusText);
      return JSON.parse(text);
    }

    async function buildIndex() {
      try {
        setStatus("正在扫描文件、解析 AST、构建 SQLite 索引和任务包...");
        currentRepoId = slug(document.getElementById("projectName").value);
        const payload = {
          repo_id: currentRepoId,
          repo_path: document.getElementById("repoPath").value.trim(),
          db_path: `.demo_data/${currentRepoId}.db`
        };
        const data = await requestJson("/context/index", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        lastPayload = data;
        lastCoverage = data.usage_coverage_report;
        currentTasks = sortTasks(data.review_tasks || []);
        activeTaskId = "";
        lastSelectedPackage = null;
        renderMetrics(data.repo_summary, data.task_coverage_report, data.usage_coverage_report);
        renderTasks();
        renderOverview(data);
        renderCoverage(data.usage_coverage_report);
        showRaw(data);
        setStatus(`分析完成：${currentTasks.length} 个任务，${data.repo_summary.python_files} 个 Python 文件。`);
      } catch (error) {
        setStatus("分析失败，请检查路径或后端日志。");
        showRaw({ error: String(error) });
      }
    }

    function sortTasks(tasks) {
      const rank = { high: 0, medium: 1, low: 2 };
      return [...tasks].sort((a, b) => {
        const pa = rank[a.priority] ?? 9;
        const pb = rank[b.priority] ?? 9;
        return pa - pb || String(a.task_type).localeCompare(String(b.task_type)) || String(a.task_id).localeCompare(String(b.task_id));
      });
    }

    function renderMetrics(summary = {}, taskCoverage = {}, usageCoverage = {}) {
      const metrics = [
        ["框架", summary.framework || "unknown", "基于路由装饰器识别"],
        ["Python 文件", summary.python_files ?? 0, `${summary.test_files?.length || 0} 个测试文件`],
        ["任务覆盖", percent(taskCoverage.coverage_ratio), `${taskCoverage.uncovered_python_files?.length || 0} 个文件未被任务覆盖`],
        ["实际读取", percent(usageCoverage.file_coverage), `${usageCoverage.covered_files?.length || 0} 个文件已被工具读取`]
      ];
      document.getElementById("metrics").innerHTML = metrics.map(([label, value, hint]) => `
        <div class="metric">
          <div class="label">${escapeHtml(label)}</div>
          <div class="value">${escapeHtml(String(value))}</div>
          <div class="hint">${escapeHtml(String(hint))}</div>
        </div>
      `).join("");
    }

    function renderTasks() {
      const box = document.getElementById("tasks");
      document.getElementById("taskCount").textContent = `${currentTasks.length} 个`;
      if (!currentTasks.length) {
        box.innerHTML = `<div class="empty">尚未生成任务。</div>`;
        return;
      }
      box.innerHTML = currentTasks.map(task => {
        const target = normalizeTarget(task);
        const entry = task.initial_context || {};
        return `
          <article class="task-card ${task.task_id === activeTaskId ? "active" : ""}" onclick="selectTask('${escapeAttr(task.task_id)}')">
            <div class="task-title">${escapeHtml(humanTaskType(task.task_type))}</div>
            <div class="task-meta">
              <span class="pill ${task.priority}">${escapeHtml(humanPriority(task.priority))}</span>
              <span class="pill ${dimensionClass(task.review_dimension)}">${escapeHtml(humanDimension(task.review_dimension))}</span>
              <span class="pill">${escapeHtml(entry.suggested_next_tool || "context tools")}</span>
            </div>
            <div class="task-target">
              <div><strong>目标文件：</strong>${escapeHtml(target.file || "未指定")}</div>
              <div><strong>目标符号：</strong>${escapeHtml(target.symbols || "自动选择")}</div>
            </div>
          </article>
        `;
      }).join("");
    }

    function renderOverview(data) {
      document.getElementById("detailTitle").textContent = "仓库概览";
      const summary = data.repo_summary || {};
      document.getElementById("detail").innerHTML = `
        <div class="detail-grid">
          <div class="info-box">
            <h3>入口与配置</h3>
            <p>识别到 ${summary.entrypoints?.length || 0} 个 API 入口，${summary.config_files?.length || 0} 个配置文件。</p>
            <p class="mono small">${escapeHtml((summary.config_files || []).join(", ") || "无配置文件")}</p>
          </div>
          <div class="info-box">
            <h3>建议工作流</h3>
            <p>从 high priority 任务开始。先看任务包，再按需展开源码片段和调用关系。</p>
          </div>
        </div>
      `;
      document.getElementById("contextPane").innerHTML = `
        <div class="info-box">
          <h3>下一步</h3>
          <p>点击左侧任意任务卡片，查看目标、关注点、初始上下文和 task-local graph slice。</p>
        </div>
      `;
    }

    async function selectTask(taskId) {
      try {
        activeTaskId = taskId;
        renderTasks();
        setStatus("正在读取任务包...");
        const task = currentTasks.find(item => item.task_id === taskId);
        const pkg = await requestJson(`/context/task-package/${encodeURIComponent(taskId)}?repo_id=${encodeURIComponent(currentRepoId)}`);
        lastSelectedPackage = pkg;
        renderTaskDetail(task || pkg, pkg);
        await renderContextPane(pkg);
        setStatus(`已打开任务：${taskId}`);
      } catch (error) {
        setStatus("读取任务包失败。");
        showRaw({ error: String(error) });
      }
    }

    function renderTaskDetail(task, pkg) {
      const target = normalizeTarget(pkg || task);
      document.getElementById("detailTitle").textContent = humanTaskType(pkg.task_type);
      document.getElementById("detail").innerHTML = `
        <div class="detail-grid">
          <div class="info-box">
            <h3>这张卡片要看什么</h3>
            <p><strong>目标文件：</strong><span class="mono">${escapeHtml(target.file || "未指定")}</span></p>
            <p><strong>目标符号：</strong>${escapeHtml(target.symbols || "自动选择")}</p>
            <p><strong>原因：</strong>${escapeHtml(pkg.reason || "由规则生成的仓库评审任务。")}</p>
            <div class="task-meta">
              <span class="pill ${pkg.priority}">${escapeHtml(humanPriority(pkg.priority))}</span>
              <span class="pill ${dimensionClass(pkg.review_dimension)}">${escapeHtml(humanDimension(pkg.review_dimension))}</span>
              ${(pkg.tags || []).map(tag => `<span class="pill">${escapeHtml(tag)}</span>`).join("")}
            </div>
          </div>
          <div class="info-box">
            <h3>上下文范围</h3>
            <p>初始上下文类型：${escapeHtml(pkg.initial_context?.type || "task_entry")}</p>
            <p>建议下一步：${escapeHtml(pkg.initial_context?.suggested_next_tool || "get_task_graph_slice")}</p>
            <p>局部图深度上限：depth=${pkg.context_policy?.max_graph_depth ?? "-"}</p>
            <p>策略：最多 ${pkg.context_policy?.max_files ?? "-"} 个文件，片段最多 ${pkg.context_policy?.max_snippet_lines ?? "-"} 行。</p>
          </div>
        </div>
        <div class="info-box" style="margin-top:12px">
          <h3>关注点</h3>
          <ul>${(pkg.focus_points || pkg.review_focus || []).map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
          <div class="context-actions">
            <button onclick="loadRelatedContext()">扩展相关上下文</button>
            <button class="secondary" onclick="loadPrimarySnippet()">查看目标文件片段</button>
            <button class="secondary" onclick="loadPrimaryNode()">查看目标符号</button>
          </div>
        </div>
      `;
    }

    async function renderContextPane(pkg) {
      const depth = pkg.context_policy?.max_graph_depth ?? 2;
      const graph = await requestJson(`/context/tasks/${encodeURIComponent(pkg.task_id)}/graph-slice?repo_id=${encodeURIComponent(currentRepoId)}&depth=${encodeURIComponent(depth)}`);
      const rankedNodes = (graph.nodes || []).slice(0, 6);
      document.getElementById("contextPane").innerHTML = `
        <div class="info-box">
          <h3>Task-local graph slice</h3>
          <p>只展示当前任务附近的调用关系，不返回完整仓库图。</p>
          <p>${graph.nodes?.length || 0} 个节点，${graph.edges?.length || 0} 条边，边界节点 ${graph.boundary_nodes?.length || 0} 个。</p>
          <div class="context-actions">
            ${rankedNodes.map(node => `<span class="pill">${escapeHtml(node.name)} · P${node.priority ?? "-"} · R${node.risk_score ?? "-"}</span>`).join("")}
          </div>
          <pre>${escapeHtml(JSON.stringify(graph, null, 2))}</pre>
        </div>
        <div class="info-box" style="margin-top:12px">
          <h3>Lightweight initial_context</h3>
          <p>任务包只保留入口、关注点和工具引导，源码和调用图按需读取。</p>
          <pre>${escapeHtml(JSON.stringify(pkg.initial_context || {}, null, 2))}</pre>
        </div>
      `;
      showRaw({ task_package: pkg, graph_slice: graph });
      await refreshCoverage();
    }

    async function loadRelatedContext() {
      if (!lastSelectedPackage) return;
      const target = normalizeTarget(lastSelectedPackage);
      const data = await requestJson("/context/related-context", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_id: currentRepoId,
          task_id: lastSelectedPackage.task_id,
          target_file: target.file,
          review_dimension: lastSelectedPackage.review_dimension,
          tags: lastSelectedPackage.tags || [],
          max_depth: 1,
          max_files: 3
        })
      });
      document.getElementById("contextPane").innerHTML = `
        <div class="info-box">
          <h3>扩展上下文结果</h3>
          <p>文件 ${data.related_files?.length || 0} 个，符号 ${data.related_symbols?.length || 0} 个，片段 ${data.snippets?.length || 0} 个。</p>
          <pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>
        </div>
      `;
      showRaw(data);
      await refreshCoverage();
    }

    async function loadPrimarySnippet() {
      if (!lastSelectedPackage) return;
      const target = normalizeTarget(lastSelectedPackage);
      if (!target.file) return;
      const data = await requestJson(`/context/file-snippet?repo_id=${encodeURIComponent(currentRepoId)}&file_path=${encodeURIComponent(target.file)}&start_line=1&end_line=80&task_id=${encodeURIComponent(lastSelectedPackage.task_id)}&review_dimension=${encodeURIComponent(lastSelectedPackage.review_dimension || "")}`);
      document.getElementById("contextPane").innerHTML = `<div class="info-box"><h3>目标文件片段</h3><pre>${escapeHtml(data.content || "")}</pre></div>`;
      showRaw(data);
      await refreshCoverage();
    }

    async function loadPrimaryNode() {
      if (!lastSelectedPackage) return;
      const target = normalizeTarget(lastSelectedPackage);
      const symbol = target.symbolList[0];
      if (!symbol) return;
      const data = await requestJson(`/context/node-detail?repo_id=${encodeURIComponent(currentRepoId)}&symbol_name=${encodeURIComponent(symbol)}&task_id=${encodeURIComponent(lastSelectedPackage.task_id)}&review_dimension=${encodeURIComponent(lastSelectedPackage.review_dimension || "")}`);
      document.getElementById("contextPane").innerHTML = `<div class="info-box"><h3>目标符号详情</h3><pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre></div>`;
      showRaw(data);
      await refreshCoverage();
    }

    async function refreshCoverage() {
      if (!currentRepoId) return;
      const data = await requestJson(`/demo/${encodeURIComponent(currentRepoId)}/coverage`);
      lastCoverage = data.usage_coverage_report;
      if (lastPayload) renderMetrics(lastPayload.repo_summary, lastPayload.task_coverage_report, lastCoverage);
      renderCoverage(lastCoverage);
      return data;
    }

    function renderCoverage(report = {}) {
      document.getElementById("coveragePane").innerHTML = `
        <div class="info-box">
          <h3>实际读取覆盖率</h3>
          <p>文件覆盖：${percent(report.file_coverage)}，节点覆盖：${percent(report.node_coverage)}，任务触达：${percent(report.task_completion_rate)}</p>
          <p>已读文件：${escapeHtml((report.covered_files || []).join(", ") || "暂无")}</p>
          <p>未读文件：${escapeHtml((report.uncovered_files || []).slice(0, 8).join(", ") || "暂无")}</p>
        </div>
        <div class="info-box" style="margin-top:12px">
          <h3>Context usage</h3>
          <pre>${escapeHtml(JSON.stringify(report.usage_records || [], null, 2))}</pre>
        </div>
      `;
    }

    function switchTab(name) {
      for (const key of ["context", "coverage", "raw"]) {
        document.getElementById(`${key}Pane`).style.display = key === name ? "block" : "none";
        document.getElementById(`tab${capitalize(key)}`).classList.toggle("active", key === name);
      }
    }

    function showRaw(data) {
      document.getElementById("rawOutput").textContent = JSON.stringify(data || {}, null, 2);
    }

    function normalizeTarget(task) {
      const target = task?.target || {};
      const file = typeof target === "object" ? target.file_path : (String(target).endsWith(".py") ? target : "");
      const symbolList = typeof target === "object" && Array.isArray(target.symbols) ? target.symbols : [];
      return {
        file,
        symbolList,
        symbols: symbolList.length ? symbolList.join(", ") : ""
      };
    }

    function humanTaskType(type) {
      return {
        entrypoint_review: "入口安全评审",
        config_review: "配置风险评审",
        module_review: "模块逻辑评审",
        file_review: "文件补充评审",
        uncovered_file_review: "未覆盖文件补充"
      }[type] || type || "评审任务";
    }

    function humanPriority(value) {
      return { high: "高优先级", medium: "中优先级", low: "低优先级" }[value] || value || "未分级";
    }

    function humanDimension(value) {
      return {
        security: "安全",
        function_logic: "功能逻辑",
        coding_style: "代码质量",
        requirement_consistency: "需求一致性"
      }[value] || value || "通用";
    }

    function dimensionClass(value) {
      return value === "security" ? "security" : "logic";
    }

    function percent(value) {
      return `${Math.round((value || 0) * 100)}%`;
    }

    function capitalize(value) {
      return value.charAt(0).toUpperCase() + value.slice(1);
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function escapeAttr(value) {
      return escapeHtml(value).replaceAll("`", "&#096;");
    }
  </script>
</body>
</html>
"""
