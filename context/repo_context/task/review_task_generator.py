from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from repo_context.service.context_service import ContextService
from repo_context.service.context_pack_builder import ContextPackBuilder
from repo_context.store.models import CodeFile, CodeNode


IMPORTANT_DIRS = {"services", "repositories", "utils", "models", "schemas", "core"}
CONFIG_NAMES = {"config.py", "settings.py", "database.py", "security.py", ".env.example"}
AUTH_KEYWORDS = {"auth", "login", "token", "password", "authenticate"}
HIGH_PRIORITY_FILE_KEYWORDS = {"auth", "security", "database", "token", "permission"}
DEFAULT_FILE_REVIEW_FOCUS = ["基础代码质量", "异常处理", "输入输出边界", "可维护性"]


@dataclass(frozen=True)
class RepoSummary:
    repo_id: str
    framework: str
    python_files: int
    entrypoints: list[dict[str, str]]
    test_files: list[str]
    config_files: list[str]
    main_packages: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskCard:
    task_id: str
    repo_id: str
    task_type: str
    target: str
    seed_node_id: str
    priority: str
    review_focus: list[str]
    related_files: list[str]
    status: str = "pending"
    recommended_nodes: list[str] = field(default_factory=list)
    reason: str = ""
    review_dimension: str = "function_logic"
    tags: list[str] = field(default_factory=list)
    focus_points: list[str] = field(default_factory=list)
    target_detail: dict[str, Any] = field(default_factory=dict)
    initial_context: dict[str, Any] = field(default_factory=dict)
    available_tools: list[str] = field(default_factory=list)
    context_policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        legacy_target = data["target"]
        data["legacy_target"] = legacy_target
        data["target"] = self.target_detail or {
            "type": "file" if str(legacy_target).endswith(".py") else "symbol",
            "file_path": legacy_target if str(legacy_target).endswith(".py") else "",
            "symbols": [],
        }
        if not data["focus_points"]:
            data["focus_points"] = list(self.review_focus)
        return data


@dataclass(frozen=True)
class ReviewTaskPlan:
    repo_summary: RepoSummary
    review_tasks: list[TaskCard]
    coverage_report: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_summary": self.repo_summary.to_dict(),
            "review_tasks": [task.to_dict() for task in self.review_tasks],
            "coverage_report": self.coverage_report,
        }


class ReviewTaskGenerator:
    def __init__(self, context_service: ContextService) -> None:
        self.context_service = context_service
        self.context_pack_builder = ContextPackBuilder(context_service)

    def generate(self) -> ReviewTaskPlan:
        """基于索引确定性生成评审任务，不调用模型服务。"""
        files = self.context_service.store.list_code_files(self.context_service.repo_id)
        nodes = self.context_service.store.list_code_nodes(self.context_service.repo_id)
        repo_summary = self._build_repo_summary(files, nodes)
        base_tasks = [
            *self._entrypoint_tasks(nodes),
            *self._config_tasks(files, nodes),
            *self._module_tasks(files, nodes),
        ]
        deduped_base_tasks = _dedupe_tasks(base_tasks)
        coverage_report = self._build_coverage_report(
            repo_summary=repo_summary,
            files=files,
            nodes=nodes,
            tasks=deduped_base_tasks,
        )
        tasks = _dedupe_tasks(
            [
                *deduped_base_tasks,
                *self._fallback_file_tasks(
                    files=files,
                    nodes=nodes,
                    uncovered_python_files=coverage_report["uncovered_python_files"],
                ),
            ]
        )
        tasks = [self._with_context_package(task) for task in tasks]
        return ReviewTaskPlan(
            repo_summary=repo_summary,
            review_tasks=tasks,
            coverage_report=coverage_report,
        )

    def get_related_context(self, task_id: str) -> dict[str, Any]:
        """按 task_id 返回推荐上下文目标，不默认返回大段源码。"""
        plan = self.generate()
        task = next((item for item in plan.review_tasks if item.task_id == task_id), None)
        if task is None:
            return {
                "task_id": task_id,
                "seed_node_id": None,
                "recommended_nodes": [],
                "related_files": [],
                "reason": "task not found",
            }

        recommended = [
            _node_summary(node)
            for node_id in task.recommended_nodes
            for node in [self.context_service.store.get_code_node(self.context_service.repo_id, node_id)]
            if node is not None
        ]
        return {
            "task_id": task.task_id,
            "seed_node_id": task.seed_node_id,
            "recommended_nodes": [item for item in recommended if item is not None],
            "related_files": task.related_files,
            "available_tools": task.available_tools,
            "context_policy": task.context_policy,
            "reason": task.reason,
        }

    def get_task_package(self, task_id: str) -> dict[str, Any] | None:
        task = next((item for item in self.generate().review_tasks if item.task_id == task_id), None)
        return task.to_dict() if task else None

    def _build_repo_summary(
        self,
        files: list[CodeFile],
        nodes: list[CodeNode],
    ) -> RepoSummary:
        route_nodes = [node for node in nodes if node.type == "route"]
        return RepoSummary(
            repo_id=self.context_service.repo_id,
            framework=_detect_framework(route_nodes),
            python_files=sum(1 for item in files if item.language == "python"),
            entrypoints=[
                {
                    "method_path": node.name,
                    "node_id": node.node_id,
                    "file_path": node.file_path,
                }
                for node in route_nodes
            ],
            test_files=[item.file_path for item in files if item.is_test],
            config_files=[item.file_path for item in files if _is_config_file(item)],
            main_packages=sorted(
                {
                    Path(item.file_path).parts[0]
                    for item in files
                    if len(Path(item.file_path).parts) > 1
                    and not item.file_path.startswith("tests/")
                }
            ),
        )

    def _entrypoint_tasks(self, nodes: list[CodeNode]) -> list[TaskCard]:
        tasks: list[TaskCard] = []
        route_nodes = [node for node in nodes if node.type == "route"]

        for route_node in route_nodes:
            related = self.context_service.explore_related_symbols(route_node.node_id)
            recommended_nodes = [
                node["node_id"]
                for node in related["nodes"]
                if isinstance(node.get("node_id"), str)
            ]
            task_id = f"task_route_{_slug(route_node.name)}"
            tasks.append(
                TaskCard(
                    task_id=task_id,
                    repo_id=self.context_service.repo_id,
                    task_type="entrypoint_review",
                    target=route_node.name,
                    seed_node_id=route_node.node_id,
                    priority="high",
                    review_focus=_focus_for_target(route_node.name),
                    review_dimension="security"
                    if any(keyword in route_node.name.lower() for keyword in AUTH_KEYWORDS)
                    else "function_logic",
                    tags=_tags_for_route(route_node),
                    related_files=sorted(
                        {
                            route_node.file_path,
                            *[
                                node["file_path"]
                                for node in related["nodes"]
                                if isinstance(node.get("file_path"), str)
                            ],
                        }
                    ),
                    recommended_nodes=[route_node.node_id, *recommended_nodes],
                    reason="API 入口点任务，建议从路由映射的 handler 及其下游调用开始审查。",
                )
            )

        return tasks

    def _config_tasks(
        self,
        files: list[CodeFile],
        nodes: list[CodeNode],
    ) -> list[TaskCard]:
        by_file = _module_node_by_file(nodes)
        tasks: list[TaskCard] = []

        for code_file in files:
            if not _is_config_file(code_file):
                continue
            seed = by_file.get(code_file.file_path)
            tasks.append(
                TaskCard(
                    task_id=f"task_config_{_slug(code_file.file_path)}",
                    repo_id=self.context_service.repo_id,
                    task_type="config_review",
                    target=code_file.file_path,
                    seed_node_id=seed.node_id if seed else "",
                    priority="medium",
                    review_dimension="security",
                    tags=["config", "secret"],
                    review_focus=[
                        "敏感信息泄露",
                        "DEBUG 配置",
                        "数据库连接配置",
                        "Token / Secret 配置",
                        "跨域 CORS 配置",
                    ],
                    related_files=[code_file.file_path],
                    recommended_nodes=[seed.node_id] if seed else [],
                    reason="配置文件任务，建议关注敏感配置、调试开关和连接参数。",
                )
            )

        return tasks

    def _module_tasks(
        self,
        files: list[CodeFile],
        nodes: list[CodeNode],
    ) -> list[TaskCard]:
        by_file = _module_node_by_file(nodes)
        tasks: list[TaskCard] = []
        dirs = sorted(
            {
                "/".join(Path(item.file_path).parts[:2])
                for item in files
                if len(Path(item.file_path).parts) >= 2
                and Path(item.file_path).parts[1] in IMPORTANT_DIRS
            }
        )

        for dirname in dirs:
            related_files = [
                item.file_path for item in files if item.file_path.startswith(f"{dirname}/")
            ]
            seed = next(
                (by_file[file_path] for file_path in related_files if file_path in by_file),
                None,
            )
            tasks.append(
                TaskCard(
                    task_id=f"task_module_{_slug(dirname)}",
                    repo_id=self.context_service.repo_id,
                    task_type="module_review",
                    target=dirname,
                    seed_node_id=seed.node_id if seed else "",
                    priority="medium",
                    review_dimension="function_logic",
                    tags=_tags_for_path(dirname),
                    review_focus=["关键业务流程", "边界条件", "异常处理", "依赖调用关系"],
                    related_files=related_files,
                    recommended_nodes=[seed.node_id] if seed else [],
                    reason="重要模块任务，建议查看模块入口文件和相邻调用关系。",
                )
            )

        return tasks

    def _fallback_file_tasks(
        self,
        files: list[CodeFile],
        nodes: list[CodeNode],
        uncovered_python_files: list[str],
    ) -> list[TaskCard]:
        by_file = _module_node_by_file(nodes)
        tasks: list[TaskCard] = []
        for file_path in uncovered_python_files:
            code_file = next((item for item in files if item.file_path == file_path), None)
            if code_file is None or code_file.is_test:
                continue

            seed = by_file.get(file_path)
            tasks.append(
                TaskCard(
                    task_id=f"task_file_{_slug(file_path)}",
                    repo_id=self.context_service.repo_id,
                    task_type="file_review",
                    target=file_path,
                    seed_node_id=seed.node_id if seed else _slug(file_path),
                    priority=_file_review_priority(file_path),
                    review_dimension=_dimension_for_file(file_path),
                    tags=_tags_for_path(file_path),
                    review_focus=list(DEFAULT_FILE_REVIEW_FOCUS),
                    related_files=[file_path],
                    recommended_nodes=[seed.node_id] if seed else [],
                    reason="兜底文件任务，用于覆盖未被入口、配置或模块任务覆盖的 Python 源码文件。",
                )
            )
        return tasks

    def _with_context_package(self, task: TaskCard) -> TaskCard:
        target_detail = self._build_target_detail(task)
        focus_points = task.focus_points or list(task.review_focus)
        package = self.context_pack_builder.build_task_package(
            {
                **asdict(task),
                "target": target_detail,
                "review_dimension": task.review_dimension,
                "tags": task.tags,
                "focus_points": focus_points,
            }
        )
        return replace(
            task,
            target_detail=target_detail,
            focus_points=focus_points,
            initial_context=package["initial_context"],
            available_tools=package["available_tools"],
            context_policy=package["context_policy"],
        )

    def _build_target_detail(self, task: TaskCard) -> dict[str, Any]:
        file_path = task.target if task.target.endswith(".py") else ""
        symbols: list[str] = []
        if task.seed_node_id:
            seed = self.context_service.store.get_code_node(
                self.context_service.repo_id,
                task.seed_node_id,
            )
            if seed is not None:
                file_path = seed.file_path
                symbols.append(seed.name)
        for node_id in task.recommended_nodes:
            node = self.context_service.store.get_code_node(self.context_service.repo_id, node_id)
            if node is not None and node.name not in symbols:
                symbols.append(node.name)
        return {
            "type": "file" if file_path else "symbol",
            "file_path": file_path,
            "symbols": symbols[:8],
        }

    def _build_coverage_report(
        self,
        repo_summary: RepoSummary,
        files: list[CodeFile],
        nodes: list[CodeNode],
        tasks: list[TaskCard],
    ) -> dict[str, Any]:
        python_files = sorted(
            item.file_path
            for item in files
            if item.language == "python" and not item.is_test
        )
        config_files = sorted(repo_summary.config_files)
        entrypoints = sorted(item["method_path"] for item in repo_summary.entrypoints)

        covered_python_files = sorted(
            {
                file_path
                for task in tasks
                for file_path in task.related_files
                if file_path in python_files
            }
        )
        covered_entrypoints = sorted(
            task.target for task in tasks if task.task_type == "entrypoint_review"
        )
        covered_config_files = sorted(
            task.target for task in tasks if task.task_type == "config_review"
        )

        total_python_files = len(python_files)
        coverage_ratio = (
            len(covered_python_files) / total_python_files
            if total_python_files
            else 1.0
        )

        return {
            "repo_id": self.context_service.repo_id,
            "total_python_files": total_python_files,
            "covered_python_files": covered_python_files,
            "uncovered_python_files": sorted(set(python_files) - set(covered_python_files)),
            "total_entrypoints": len(entrypoints),
            "covered_entrypoints": covered_entrypoints,
            "uncovered_entrypoints": sorted(set(entrypoints) - set(covered_entrypoints)),
            "total_config_files": len(config_files),
            "covered_config_files": covered_config_files,
            "uncovered_config_files": sorted(set(config_files) - set(covered_config_files)),
            "coverage_ratio": coverage_ratio,
        }


def _detect_framework(route_nodes: list[CodeNode]) -> str:
    decorators = " ".join(" ".join(node.decorators) for node in route_nodes).lower()
    if "router." in decorators or "app.get" in decorators or "app.post" in decorators:
        return "fastapi"
    if "app.route" in decorators:
        return "flask"
    return "unknown"


def _focus_for_target(target: str) -> list[str]:
    normalized = target.lower()
    if any(keyword in normalized for keyword in AUTH_KEYWORDS):
        return [
            "输入校验",
            "身份认证",
            "密码校验",
            "Token 生成与过期",
            "认证绕过风险",
            "异常处理",
        ]
    return ["输入校验", "权限控制", "异常处理", "下游调用风险"]


def _is_config_file(code_file: CodeFile) -> bool:
    return code_file.file_type == "config" or Path(code_file.file_path).name in CONFIG_NAMES


def _module_node_by_file(nodes: list[CodeNode]) -> dict[str, CodeNode]:
    return {node.file_path: node for node in nodes if node.type == "module"}


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value]
    return "_".join(part for part in "".join(chars).strip("_").split("_") if part)


def _file_review_priority(file_path: str) -> str:
    normalized = file_path.lower()
    if any(keyword in normalized for keyword in HIGH_PRIORITY_FILE_KEYWORDS):
        return "high"
    return "low"


def _dimension_for_file(file_path: str) -> str:
    normalized = file_path.lower()
    if any(keyword in normalized for keyword in HIGH_PRIORITY_FILE_KEYWORDS | AUTH_KEYWORDS):
        return "security"
    return "coding_style"


def _tags_for_route(node: CodeNode) -> list[str]:
    tags = ["api_entry", *_tags_for_path(node.file_path), *_tags_for_path(node.name)]
    return _dedupe_strings(tags)


def _tags_for_path(value: str) -> list[str]:
    normalized = value.lower()
    tags = []
    for keyword in sorted(HIGH_PRIORITY_FILE_KEYWORDS | AUTH_KEYWORDS | IMPORTANT_DIRS):
        if keyword in normalized:
            tags.append(keyword)
    if "config" in normalized or "settings" in normalized:
        tags.append("config")
    return _dedupe_strings(tags)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _node_summary(node: CodeNode) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "type": node.type,
        "name": node.name,
        "qualified_name": node.qualified_name,
        "file_path": node.file_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
    }


def _dedupe_tasks(tasks: list[TaskCard]) -> list[TaskCard]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[TaskCard] = []
    for task in tasks:
        key = (task.repo_id, task.task_type, task.target)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(task)
    return sorted(deduped, key=lambda item: (item.task_type, item.target, item.task_id))
