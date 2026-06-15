from dataclasses import replace
from typing import Any

from repo_context.service.context_pack_builder import ContextPackBuilder
from repo_context.service.context_service import ContextService
from repo_context.task.review_task_generator import (
    DEFAULT_FILE_REVIEW_FOCUS,
    ReviewTaskGenerator,
    TaskCard,
)


class CoverageService:
    def __init__(
        self,
        context_service: ContextService,
        task_generator: ReviewTaskGenerator,
    ) -> None:
        self.context_service = context_service
        self.task_generator = task_generator
        self.context_pack_builder = ContextPackBuilder(context_service)

    def get_coverage_report(self) -> dict[str, Any]:
        """基于 context_usage 记录生成实际覆盖率报告。"""
        repo_id = self.context_service.repo_id
        files = self.context_service.store.list_code_files(repo_id)
        nodes = self.context_service.store.list_code_nodes(repo_id)
        usage_records = self.context_service.store.list_context_usage(repo_id)
        plan = self.task_generator.generate()

        node_by_id = {node.node_id: node for node in nodes}
        python_files = sorted(
            item.file_path
            for item in files
            if item.language == "python" and not item.is_test
        )
        used_node_ids = sorted(
            {
                item.node_id
                for item in usage_records
                if item.node_id and item.node_id in node_by_id
            }
        )
        used_files = {
            item.file_path
            for item in usage_records
            if item.file_path
        } | {
            node_by_id[node_id].file_path for node_id in used_node_ids
        }
        covered_files = sorted(file_path for file_path in used_files if file_path in python_files)
        route_nodes = [node for node in nodes if node.type == "route"]
        covered_entrypoints = sorted(
            node.name for node in route_nodes if node.node_id in used_node_ids
        )
        completed_task_ids = sorted(
            {
                item.task_id
                for item in usage_records
                if item.task_id
            }
        )
        total_tasks = len(plan.review_tasks)

        return {
            "repo_id": repo_id,
            "file_coverage": _ratio(len(covered_files), len(python_files)),
            "node_coverage": _ratio(len(used_node_ids), len(nodes)),
            "task_completion_rate": _ratio(len(completed_task_ids), total_tasks),
            "total_python_files": len(python_files),
            "covered_files": covered_files,
            "uncovered_files": sorted(set(python_files) - set(covered_files)),
            "total_nodes": len(nodes),
            "covered_nodes": used_node_ids,
            "uncovered_nodes": sorted(set(node_by_id) - set(used_node_ids)),
            "total_tasks": total_tasks,
            "completed_task_ids": completed_task_ids,
            "uncompleted_task_ids": sorted(
                {task.task_id for task in plan.review_tasks} - set(completed_task_ids)
            ),
            "total_entrypoints": len(route_nodes),
            "covered_entrypoints": covered_entrypoints,
            "uncovered_entrypoints": sorted(
                {node.name for node in route_nodes} - set(covered_entrypoints)
            ),
            "usage_records": [
                {
                    "task_id": item.task_id,
                    "agent": item.agent,
                    "review_dimension": item.review_dimension,
                    "tool_name": item.tool_name,
                    "target_type": item.target_type,
                    "target_name": item.target_name,
                    "node_id": item.node_id,
                    "file_path": item.file_path,
                    "start_line": item.start_line,
                    "end_line": item.end_line,
                    "lines_returned": item.lines_returned,
                    "created_at": item.used_at,
                }
                for item in usage_records
            ],
        }

    def generate_uncovered_file_reviews(self) -> list[TaskCard]:
        """为实际未被工具访问的源码文件生成带完整上下文包的补充任务。"""
        report = self.get_coverage_report()
        nodes = self.context_service.store.list_code_nodes(self.context_service.repo_id)
        module_by_file = {node.file_path: node for node in nodes if node.type == "module"}
        tasks: list[TaskCard] = []

        for file_path in report["uncovered_files"]:
            seed = module_by_file.get(file_path)
            task = TaskCard(
                task_id=f"task_uncovered_file_{_slug(file_path)}",
                repo_id=self.context_service.repo_id,
                task_type="uncovered_file_review",
                target=file_path,
                seed_node_id=seed.node_id if seed else _slug(file_path),
                priority="medium",
                review_dimension="coding_style",
                tags=["uncovered_file"],
                review_focus=list(DEFAULT_FILE_REVIEW_FOCUS),
                related_files=[file_path],
                recommended_nodes=[seed.node_id] if seed else [],
                reason="阶段 7 覆盖率追踪发现该源码文件尚未被上下文工具访问。",
            )
            target_detail = {
                "type": "file",
                "file_path": file_path,
                "symbols": [seed.name] if seed else [],
            }
            package = self.context_pack_builder.build_task_package(
                {
                    **task.to_dict(),
                    "target": target_detail,
                    "review_dimension": task.review_dimension,
                    "tags": task.tags,
                    "focus_points": task.review_focus,
                }
            )
            tasks.append(
                replace(
                    task,
                    target_detail=target_detail,
                    focus_points=task.review_focus,
                    initial_context=package["initial_context"],
                    available_tools=package["available_tools"],
                    context_policy=package["context_policy"],
                )
            )

        return tasks


def _ratio(covered: int, total: int) -> float:
    return covered / total if total else 1.0


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value]
    return "_".join(part for part in "".join(chars).strip("_").split("_") if part)
