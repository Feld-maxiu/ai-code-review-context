from pathlib import Path
from typing import Any

from repo_context.service.context_service import ContextService


AVAILABLE_CONTEXT_TOOLS = [
    "get_file_snippet",
    "get_node_detail",
    "get_task_graph_slice",
    "get_callers",
    "get_callees",
    "trace_call_chain",
    "search_symbol",
    "get_related_context",
]

DEFAULT_CONTEXT_POLICY = {
    "max_depth": 2,
    "max_snippet_lines": 120,
    "max_files": 5,
    "allow_expand": True,
    "allow_task_graph_slice": True,
    "allow_full_graph": False,
    "prefer_graph_slice_first": True,
    "max_graph_depth": 2,
}

SECURITY_KEYWORDS = {
    "api",
    "auth",
    "authenticate",
    "authorization",
    "config",
    "login",
    "password",
    "permission",
    "secret",
    "token",
    "upload",
    "request",
}

FUNCTION_LOGIC_KEYWORDS = {"create", "update", "delete", "save", "load", "find", "get", "set"}
STYLE_KEYWORDS = {"long", "large", "duplicate", "class", "function"}
REQUIREMENT_KEYWORDS = {"readme", "docs", "openapi", "schema", "response", "error"}


class ContextPackBuilder:
    def __init__(self, context_service: ContextService) -> None:
        self.context_service = context_service

    def build_task_package(self, task: dict[str, Any]) -> dict[str, Any]:
        package = {
            **task,
            "initial_context": self.build_initial_context(task),
            "available_tools": self.build_available_tools(task),
            "context_policy": self.build_context_policy(task),
        }
        return package

    def build_initial_context(self, task: dict[str, Any]) -> dict[str, Any]:
        """基于任务构建小体量初始上下文，后续不足再由 agent 调工具扩展。"""
        target = task.get("target", {})
        target_file = target.get("file_path") if isinstance(target, dict) else task.get("target_file")
        symbols = target.get("symbols", []) if isinstance(target, dict) else []
        dimension = task.get("review_dimension")
        policy = self.build_context_policy(task)
        return {
            "type": "task_entry",
            "target": target,
            "file_path": target_file,
            "symbols": symbols,
            "review_dimension": dimension,
            "focus_points": list(task.get("focus_points") or task.get("review_focus") or []),
            "reason": task.get("reason"),
            "priority": task.get("priority"),
            "suggested_next_tool": "get_task_graph_slice",
            "suggested_next_params": {
                "task_id": task.get("task_id"),
                "depth": policy["max_graph_depth"],
            },
            "context_scope": {
                "task_local": True,
                "allow_full_graph": policy["allow_full_graph"],
                "prefer_graph_slice_first": policy["prefer_graph_slice_first"],
            },
            "tool_guidance": [
                "Call get_task_graph_slice first for the task-local structure.",
                "Use get_node_detail or get_file_snippet for precise source code.",
                "Use get_related_context when more surrounding context is needed.",
                "Use get_callers/get_callees for focused follow-up traversal.",
            ],
        }

    def build_task_local_graph(self, task: dict[str, Any], depth: int = 1) -> dict[str, Any]:
        target = task.get("target", {})
        target_file = target.get("file_path") if isinstance(target, dict) else task.get("target_file")
        symbols = target.get("symbols", []) if isinstance(target, dict) else []
        centers = self._center_nodes(target_file, symbols)
        return self.context_service.build_task_local_graph_slice(
            [node["node_id"] for node in centers],
            depth=depth,
        )

    def build_available_tools(self, task: dict[str, Any]) -> list[str]:
        return list(AVAILABLE_CONTEXT_TOOLS)

    def build_context_policy(self, task: dict[str, Any]) -> dict[str, Any]:
        policy = dict(DEFAULT_CONTEXT_POLICY)
        dimension = task.get("review_dimension")
        if dimension == "coding_style":
            policy["max_depth"] = 1
            policy["max_snippet_lines"] = 160
            policy["max_graph_depth"] = 1
        elif dimension == "security":
            policy["max_depth"] = 2
            policy["max_files"] = 6
            policy["max_graph_depth"] = 2
        elif dimension == "function_logic":
            policy["max_depth"] = 2
            policy["max_graph_depth"] = 2
        return policy

    def _center_nodes(
        self,
        target_file: str | None,
        symbols: list[str],
    ) -> list[dict[str, Any]]:
        centers: list[dict[str, Any]] = []
        for symbol in symbols:
            detail = self.context_service.get_node_detail(symbol_name=symbol, record_usage=False)
            if detail is not None:
                centers.append(detail)
        if centers:
            return centers

        if not target_file:
            return []

        nodes = [
            node
            for node in self.context_service.store.list_code_nodes(self.context_service.repo_id)
            if node.file_path == Path(target_file).as_posix()
            and node.type in {"function", "method", "route"}
        ]
        return [
            {
                "node_id": node.node_id,
                "qualified_name": node.qualified_name,
                "file_path": node.file_path,
            }
            for node in nodes[:3]
        ]

    def _dimension_related_symbols(
        self,
        task: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        dimension = task.get("review_dimension")
        tags = {str(tag).lower() for tag in task.get("tags", [])}
        if dimension == "security":
            keywords = SECURITY_KEYWORDS | tags
        elif dimension == "function_logic":
            keywords = FUNCTION_LOGIC_KEYWORDS | tags
        elif dimension == "coding_style":
            keywords = STYLE_KEYWORDS | tags
        elif dimension == "requirement_consistency":
            keywords = REQUIREMENT_KEYWORDS | tags
        else:
            keywords = tags

        results: list[dict[str, Any]] = []
        for keyword in sorted(keyword for keyword in keywords if keyword):
            results.extend(
                self.context_service.search_symbol(keyword, limit=limit, record_usage=False)
            )
            if len(results) >= limit:
                break
        return _dedupe_by(results, "node_id")[:limit]

    def _requirement_refs(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        if task.get("review_dimension") != "requirement_consistency":
            return []

        refs = []
        for code_file in self.context_service.store.list_code_files(self.context_service.repo_id):
            name = Path(code_file.file_path).name.lower()
            if name.startswith("readme") or code_file.file_path.startswith("docs/"):
                refs.append(self._safe_snippet(code_file.file_path, 1, 80))
            if len(refs) >= 3:
                break
        return [item for item in refs if item]

    def _safe_snippet(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
    ) -> dict[str, Any] | None:
        try:
            return self.context_service.get_file_snippet(
                file_path,
                start_line,
                end_line,
                record_usage=False,
            )
        except (FileNotFoundError, ValueError):
            return None


def _dedupe_by(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        value = item.get(key)
        if value in seen:
            continue
        seen.add(value)
        deduped.append(item)
    return deduped
