from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_context.store.models import CodeEdge, CodeNode, ContextUsage
from repo_context.store.sqlite_store import SQLiteStore


CALL_EDGE = "calls"

GRAPH_RISK_KEYWORDS = {
    "auth",
    "token",
    "password",
    "permission",
    "jwt",
    "sql",
    "query",
    "execute",
    "file",
    "path",
    "upload",
    "subprocess",
    "eval",
    "exec",
}


class ContextService:
    def __init__(self, repo_id: str, repo_root: str | Path, db_path: str | Path) -> None:
        self.repo_id = repo_id
        self.repo_root = Path(repo_root).resolve()
        self.store = SQLiteStore(db_path)

    def search_symbol(
        self,
        keyword: str | None = None,
        limit: int = 20,
        query: str | None = None,
        task_id: str | None = None,
        review_dimension: str | None = None,
        record_usage: bool = True,
    ) -> list[dict[str, Any]]:
        """按名称、限定名和类型搜索符号，返回 JSON serializable 结构。"""
        search_text = (query if query is not None else keyword) or ""
        normalized = search_text.lower()
        matches = [
            node
            for node in self.store.list_code_nodes(self.repo_id)
            if normalized in node.name.lower()
            or normalized in node.qualified_name.lower()
            or normalized in node.type.lower()
        ]
        results = [self._node_summary(node) for node in matches[:limit]]
        if record_usage:
            self._record_usage(
                tool_name="search_symbol",
                task_id=task_id,
                review_dimension=review_dimension,
                target_type="symbol",
                target_name=search_text,
                lines_returned=len(results),
            )
        return results

    def get_node_detail(
        self,
        node_id: str | None = None,
        include_source: bool = False,
        task_id: str | None = None,
        symbol_name: str | None = None,
        review_dimension: str | None = None,
        record_usage: bool = True,
    ) -> dict[str, Any] | None:
        node = self._resolve_node(symbol_name or node_id or "")
        if node is None:
            return None

        if record_usage:
            self._record_usage(
                tool_name="get_node_detail",
                task_id=task_id,
                review_dimension=review_dimension,
                node_id=node.node_id,
                file_path=node.file_path,
                target_type="symbol",
                target_name=node.qualified_name,
                start_line=node.start_line,
                end_line=node.end_line,
                lines_returned=max(node.end_line - node.start_line + 1, 0),
            )

        detail = self._node_detail(node)
        source = self.get_file_snippet(
            node.file_path,
            node.start_line,
            node.end_line,
            task_id=task_id,
            review_dimension=review_dimension,
            record_usage=False,
        )
        detail["code"] = source["content"]
        # 兼容旧工具调用方。
        detail["source"] = source["content"] if include_source else source["content"]
        detail["callers"] = self.get_callers(
            node.node_id,
            depth=1,
            limit=20,
            task_id=task_id,
            review_dimension=review_dimension,
            record_usage=False,
        )
        detail["callees"] = self.get_callees(
            node.node_id,
            depth=1,
            limit=20,
            task_id=task_id,
            review_dimension=review_dimension,
            record_usage=False,
        )
        return detail

    def get_file_snippet(
        self,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        task_id: str | None = None,
        review_dimension: str | None = None,
        record_usage: bool = True,
    ) -> dict[str, Any]:
        """读取仓库内源码片段，明确阻止仓库外路径访问。"""
        source_path = self._resolve_repo_file(file_path)
        lines = source_path.read_text(encoding="utf-8").splitlines()
        normalized_file_path = self._normalize_file_path(file_path)
        start = 1 if start_line is None else start_line
        end = len(lines) if end_line is None else end_line
        if start < 1 or end < start:
            raise ValueError("Invalid line range")

        selected = lines[start - 1 : end]
        actual_end = min(end, len(lines))
        content = "\n".join(selected)
        if record_usage:
            self._record_usage(
                tool_name="get_file_snippet",
                task_id=task_id,
                review_dimension=review_dimension,
                file_path=normalized_file_path,
                target_type="file",
                target_name=normalized_file_path,
                start_line=start,
                end_line=actual_end,
                lines_returned=len(selected),
            )
        return {
            "file_path": normalized_file_path,
            "start_line": start,
            "end_line": actual_end,
            "content": content,
            # 兼容旧测试和旧工具。
            "source": content,
        }

    def list_repo_files(
        self,
        include_tests: bool = True,
        file_type: str | None = None,
    ) -> dict[str, Any]:
        """返回索引中已扫描文件清单，供下游 agent 做全量扫描调度。"""
        files = self.store.list_code_files(self.repo_id)
        if not include_tests:
            files = [item for item in files if not item.is_test]
        if file_type is not None:
            files = [item for item in files if item.file_type == file_type]

        return {
            "repo_id": self.repo_id,
            "total": len(files),
            "files": [
                {
                    "file_path": item.file_path,
                    "file_type": item.file_type,
                    "language": item.language,
                    "line_count": item.line_count,
                    "is_test": item.is_test,
                }
                for item in files
            ],
        }

    def get_file_content(
        self,
        file_path: str,
        task_id: str | None = None,
        review_dimension: str | None = None,
        record_usage: bool = True,
    ) -> dict[str, Any]:
        """读取仓库内完整文件内容，复用路径边界校验避免访问仓库外文件。"""
        source_path = self._resolve_repo_file(file_path)
        content = source_path.read_text(encoding="utf-8")
        line_count = len(content.splitlines())
        normalized_file_path = self._normalize_file_path(file_path)
        if record_usage:
            self._record_usage(
                tool_name="get_file_content",
                task_id=task_id,
                review_dimension=review_dimension,
                file_path=normalized_file_path,
                target_type="file",
                target_name=normalized_file_path,
                start_line=1,
                end_line=line_count,
                lines_returned=line_count,
            )
        return {
            "file_path": normalized_file_path,
            "line_count": line_count,
            "content": content,
            # 兼容下游已有 source 字段读取习惯。
            "source": content,
        }

    def get_callees(
        self,
        node_id: str | None = None,
        depth: int = 1,
        limit: int = 20,
        task_id: str | None = None,
        symbol_name: str | None = None,
        review_dimension: str | None = None,
        record_usage: bool = True,
    ) -> list[dict[str, Any]]:
        node = self._resolve_node(symbol_name or node_id or "")
        if node is None:
            return []
        results = self._walk_calls(node.node_id, direction="out", depth=depth, limit=limit)
        if record_usage:
            self._record_usage(
                tool_name="get_callees",
                task_id=task_id,
                review_dimension=review_dimension,
                node_id=node.node_id,
                file_path=node.file_path,
                target_type="graph",
                target_name=node.qualified_name,
                lines_returned=len(results),
            )
        return results

    def get_callers(
        self,
        node_id: str | None = None,
        depth: int = 1,
        limit: int = 20,
        task_id: str | None = None,
        symbol_name: str | None = None,
        review_dimension: str | None = None,
        record_usage: bool = True,
    ) -> list[dict[str, Any]]:
        node = self._resolve_node(symbol_name or node_id or "")
        if node is None:
            return []
        results = self._walk_calls(node.node_id, direction="in", depth=depth, limit=limit)
        if record_usage:
            self._record_usage(
                tool_name="get_callers",
                task_id=task_id,
                review_dimension=review_dimension,
                node_id=node.node_id,
                file_path=node.file_path,
                target_type="graph",
                target_name=node.qualified_name,
                lines_returned=len(results),
            )
        return results

    def trace_call_chain(
        self,
        source_node_id: str | None = None,
        target_node_id: str | None = None,
        max_depth: int = 5,
        task_id: str | None = None,
        source: str | None = None,
        target: str | None = None,
        review_dimension: str | None = None,
    ) -> dict[str, Any]:
        """查找两个节点之间的 calls 路径；未命中时返回空路径。"""
        source_node = self._resolve_node(source or source_node_id or "")
        target_node = self._resolve_node(target or target_node_id or "") if target or target_node_id else None
        if source_node is None:
            return {"found": False, "path": []}

        self._record_usage(
            tool_name="trace_call_chain",
            task_id=task_id,
            review_dimension=review_dimension,
            node_id=source_node.node_id,
            file_path=source_node.file_path,
            target_type="graph",
            target_name=source_node.qualified_name,
        )

        if target_node is None:
            return self.build_task_local_graph_slice([source_node.node_id], depth=max_depth)

        edges = [edge for edge in self.store.list_code_edges(self.repo_id) if edge.edge_type == CALL_EDGE]
        outgoing = self._edge_map(edges, direction="out")
        node_by_id = self._nodes_by_id()
        queue: deque[tuple[str, list[str]]] = deque([(source_node.node_id, [source_node.node_id])])
        visited = {source_node.node_id}

        while queue:
            current_id, path = queue.popleft()
            if len(path) - 1 >= max_depth:
                continue

            for next_id in outgoing.get(current_id, []):
                if next_id in visited:
                    continue
                next_path = [*path, next_id]
                if next_id == target_node.node_id:
                    return {
                        "found": True,
                        "path": [
                            self._node_summary(node_by_id[node_id])
                            for node_id in next_path
                            if node_id in node_by_id
                        ],
                    }
                visited.add(next_id)
                queue.append((next_id, next_path))

        return {"found": False, "path": []}

    def explore_related_symbols(
        self,
        node_id: str,
        include_source: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        """返回某节点的一跳相关节点和边。"""
        node_by_id = self._nodes_by_id()
        center = self._resolve_node(node_id)
        if center is None:
            return {"center": None, "nodes": [], "edges": []}

        related_edges = [
            edge
            for edge in self.store.list_code_edges(self.repo_id)
            if edge.source_node_id == center.node_id or edge.target_node_id == center.node_id
        ][:limit]
        related_ids = {
            edge.source_node_id for edge in related_edges if edge.source_node_id in node_by_id
        } | {
            edge.target_node_id for edge in related_edges if edge.target_node_id in node_by_id
        }

        return {
            "center": self._node_detail(center, include_source=include_source),
            "nodes": [
                self._node_detail(node_by_id[item], include_source=include_source)
                for item in sorted(related_ids)
            ],
            "edges": [self._edge_to_dict(edge, node_by_id) for edge in related_edges],
        }

    def get_related_context(
        self,
        task_or_node_id: dict[str, Any] | str | None = None,
        include_source: bool = False,
        node_id: str | None = None,
        task_id: str | None = None,
        target_file: str | None = None,
        review_dimension: str | None = None,
        tags: list[str] | None = None,
        max_depth: int = 2,
        max_files: int = 5,
    ) -> dict[str, Any]:
        """下游 agent 的主要扩展入口，按任务返回有限上下文包。"""
        if node_id is not None and task_or_node_id is None and target_file is None:
            return self.explore_related_symbols(node_id, include_source=include_source)
        if isinstance(task_or_node_id, str) and node_id is None and target_file is None:
            return self.explore_related_symbols(task_or_node_id, include_source=include_source)

        task = task_or_node_id if isinstance(task_or_node_id, dict) else {}
        resolved_task_id = task_id or task.get("task_id")
        dimension = review_dimension or task.get("review_dimension")
        task_tags = tags or list(task.get("tags", []))
        target = task.get("target", {})
        resolved_file = target_file or task.get("target_file")
        if isinstance(target, dict):
            resolved_file = resolved_file or target.get("file_path")
            target_symbols = list(target.get("symbols", []))
        else:
            target_symbols = []

        if not resolved_file and isinstance(target, str) and target.endswith(".py"):
            resolved_file = target

        center_nodes = self._resolve_center_nodes(resolved_file, target_symbols)
        graph = self.build_task_local_graph_slice(
            [node.node_id for node in center_nodes],
            depth=max_depth,
        )
        related_files = self._related_files_for_nodes(center_nodes, graph, max_files=max_files)
        snippets = [
            self.get_file_snippet(file_path, 1, self._safe_snippet_end(file_path), record_usage=False)
            for file_path in related_files
        ]

        self._record_usage(
            tool_name="get_related_context",
            task_id=resolved_task_id,
            review_dimension=dimension,
            file_path=resolved_file,
            target_type="batch_context",
            target_name=resolved_file or ",".join(target_symbols) or None,
            lines_returned=sum(item["end_line"] - item["start_line"] + 1 for item in snippets),
        )
        return {
            "task_id": resolved_task_id,
            "target_file": resolved_file,
            "review_dimension": dimension,
            "tags": task_tags,
            "related_files": related_files,
            "related_symbols": [node["id"] for node in graph["nodes"]],
            "snippets": snippets,
            "call_graph_slice": graph,
        }

    def build_task_local_graph_slice(
        self,
        center_identifiers: list[str],
        depth: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """只暴露任务局部调用图，不返回完整仓库 graph。"""
        centers = [self._resolve_node(item) for item in center_identifiers]
        center_nodes = [node for node in centers if node is not None]
        if not center_nodes:
            return {
                "graph_scope": "local",
                "center": None,
                "depth": depth,
                "nodes": [],
                "edges": [],
            }

        all_edges = [edge for edge in self.store.list_code_edges(self.repo_id) if edge.edge_type == CALL_EDGE]
        node_by_id = self._nodes_by_id()
        adjacency = self._undirected_edge_map(all_edges)
        included_ids = {node.node_id for node in center_nodes}
        queue: deque[tuple[str, int]] = deque((node.node_id, 0) for node in center_nodes)

        while queue and len(included_ids) < limit:
            current_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for next_id in adjacency.get(current_id, []):
                if next_id not in node_by_id or next_id in included_ids:
                    continue
                included_ids.add(next_id)
                queue.append((next_id, current_depth + 1))
                if len(included_ids) >= limit:
                    break

        included_edges = [
            edge
            for edge in all_edges
            if edge.source_node_id in included_ids and edge.target_node_id in included_ids
        ]
        return {
            "graph_scope": "local",
            "center": center_nodes[0].qualified_name,
            "depth": depth,
            "nodes": [self._graph_node(node_by_id[node_id]) for node_id in sorted(included_ids)],
            "edges": [self._edge_to_dict(edge, node_by_id) for edge in included_edges],
        }

    def get_task_graph_slice(
        self,
        task_id: str,
        depth: int = 2,
        record_usage: bool = True,
    ) -> dict[str, Any]:
        """返回任务范围内的局部调用图，并把范围外相邻节点标记为边界节点。"""
        task = self._resolve_task(task_id)
        if task is None:
            result = self._empty_task_graph_slice(task_id=task_id, depth=depth)
            if record_usage:
                self._record_graph_slice_usage(result)
            return result

        requested_depth = max(depth, 0)
        policy = getattr(task, "context_policy", {}) or {}
        max_graph_depth = policy.get("max_graph_depth", requested_depth)
        effective_depth = min(requested_depth, max_graph_depth) if max_graph_depth is not None else requested_depth
        centers = self._task_center_nodes(task)
        if not centers:
            result = {
                **self._empty_task_graph_slice(task_id=task_id, depth=effective_depth),
                "target": self._task_target_to_dict(task),
            }
            if record_usage:
                self._record_graph_slice_usage(result)
            return result

        all_edges = [
            edge for edge in self.store.list_code_edges(self.repo_id) if edge.edge_type == CALL_EDGE
        ]
        node_by_id = self._nodes_by_id()
        adjacency = self._undirected_edge_map(all_edges)
        outgoing = self._edge_map(all_edges, direction="out")
        incoming = self._edge_map(all_edges, direction="in")
        allowed_node_ids = self._task_allowed_node_ids(task)
        included_ids = {node.node_id for node in centers}
        center_ids = {node.node_id for node in centers}
        boundary: dict[str, dict[str, Any]] = {}
        queue: deque[tuple[str, int]] = deque((node.node_id, 0) for node in centers)
        visited = set(included_ids)
        node_depths = {node.node_id: 0 for node in centers}

        while queue:
            current_id, current_depth = queue.popleft()
            for next_id in adjacency.get(current_id, []):
                next_node = node_by_id.get(next_id)
                if next_node is None:
                    continue
                reason = None
                if allowed_node_ids and next_id not in allowed_node_ids:
                    reason = "outside task scope"
                elif current_depth >= effective_depth:
                    reason = "beyond requested depth"

                if reason:
                    if next_id not in included_ids:
                        boundary[next_id] = self._task_graph_node_payload(
                            next_node,
                            center_ids=center_ids,
                            outgoing=outgoing,
                            incoming=incoming,
                            depth=current_depth + 1,
                            boundary_reason=reason,
                        )
                    continue

                if next_id in visited:
                    continue
                visited.add(next_id)
                included_ids.add(next_id)
                node_depths[next_id] = current_depth + 1
                queue.append((next_id, current_depth + 1))

        included_edges = [
            edge
            for edge in all_edges
            if edge.source_node_id in included_ids and edge.target_node_id in included_ids
        ]
        result = {
            "task_id": task_id,
            "target": self._task_target_to_dict(task),
            "depth": effective_depth,
            "requested_depth": requested_depth,
            "nodes": self._sort_graph_nodes(
                [
                    self._task_graph_node_payload(
                        node_by_id[node_id],
                        center_ids=center_ids,
                        outgoing=outgoing,
                        incoming=incoming,
                        depth=node_depths.get(node_id, effective_depth),
                    )
                    for node_id in included_ids
                    if node_id in node_by_id
                ]
            ),
            "edges": [self._edge_to_dict(edge, node_by_id) for edge in included_edges],
            "boundary_nodes": self._sort_graph_nodes(list(boundary.values())),
            "truncated": bool(boundary) or effective_depth < requested_depth,
            "graph_scope": "task-local",
        }
        if record_usage:
            self._record_graph_slice_usage(result)
        return result

    def _walk_calls(
        self,
        node_id: str,
        direction: str,
        depth: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        if depth < 1 or limit < 1:
            return []

        call_edges = [
            edge for edge in self.store.list_code_edges(self.repo_id) if edge.edge_type == CALL_EDGE
        ]
        edge_map = self._edge_map(call_edges, direction=direction)
        node_by_id = self._nodes_by_id()
        results: list[dict[str, Any]] = []
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        visited = {node_id}

        while queue and len(results) < limit:
            current_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue

            for next_id in edge_map.get(current_id, []):
                if next_id in visited:
                    continue
                visited.add(next_id)
                if next_id in node_by_id:
                    results.append(self._node_summary(node_by_id[next_id]))
                if len(results) >= limit:
                    break
                queue.append((next_id, current_depth + 1))

        return results

    def _resolve_repo_file(self, file_path: str) -> Path:
        normalized_file_path = self._normalize_file_path(file_path)
        source_path = (self.repo_root / normalized_file_path).resolve()
        if not source_path.is_relative_to(self.repo_root):
            raise ValueError(f"File path escapes repository: {file_path}")
        if not source_path.is_file():
            raise FileNotFoundError(f"File not found in repository: {file_path}")
        return source_path

    @staticmethod
    def _normalize_file_path(file_path: str) -> str:
        return Path(file_path).as_posix()

    def _safe_snippet_end(self, file_path: str, max_lines: int = 80) -> int:
        code_file = self.store.get_code_file(self.repo_id, self._normalize_file_path(file_path))
        if code_file is None:
            return max_lines
        return min(code_file.line_count, max_lines)

    def _resolve_node(self, identifier: str) -> CodeNode | None:
        if not identifier:
            return None
        nodes = self.store.list_code_nodes(self.repo_id)
        for node in nodes:
            if node.node_id == identifier:
                return node
        for node in nodes:
            if node.qualified_name == identifier:
                return node
        for node in nodes:
            if node.name == identifier:
                return node
        normalized = identifier.lower()
        return next(
            (
                node
                for node in nodes
                if normalized in node.qualified_name.lower()
                or normalized in node.name.lower()
            ),
            None,
        )

    def _resolve_center_nodes(
        self,
        file_path: str | None,
        symbols: list[str],
    ) -> list[CodeNode]:
        nodes = self.store.list_code_nodes(self.repo_id)
        centers: list[CodeNode] = []
        for symbol in symbols:
            node = self._resolve_node(symbol)
            if node is not None:
                centers.append(node)
        if centers:
            return centers
        if not file_path:
            return []
        normalized = self._normalize_file_path(file_path)
        candidates = [
            node
            for node in nodes
            if node.file_path == normalized and node.type in {"function", "method", "route"}
        ]
        if candidates:
            return candidates[:3]
        return [node for node in nodes if node.file_path == normalized][:1]

    def _resolve_task(self, task_id: str) -> Any | None:
        from repo_context.task.review_task_generator import ReviewTaskGenerator

        plan = ReviewTaskGenerator(self).generate()
        return next((task for task in plan.review_tasks if task.task_id == task_id), None)

    def _task_center_nodes(self, task: Any) -> list[CodeNode]:
        centers: list[CodeNode] = []
        seen: set[str] = set()
        for node_id in [getattr(task, "seed_node_id", "")]:
            node = self.store.get_code_node(self.repo_id, node_id)
            if node is not None and node.node_id not in seen:
                centers.append(node)
                seen.add(node.node_id)

        target_detail = getattr(task, "target_detail", {}) or {}
        for symbol in target_detail.get("symbols", []) or []:
            node = self._resolve_node(symbol)
            if node is not None and node.node_id not in seen:
                centers.append(node)
                seen.add(node.node_id)
        return centers[:8]

    def _task_allowed_node_ids(self, task: Any) -> set[str]:
        centers = self._task_center_nodes(task)
        policy = getattr(task, "context_policy", {}) or {}
        max_graph_depth = policy.get("max_graph_depth", 2)
        if centers:
            local_graph = self.build_task_local_graph_slice(
                [node.node_id for node in centers],
                depth=max_graph_depth,
            )
            return {
                node["node_id"]
                for node in local_graph.get("nodes", [])
                if isinstance(node, dict) and isinstance(node.get("node_id"), str)
            }

        related_files = set(getattr(task, "related_files", []) or [])
        target_detail = getattr(task, "target_detail", {}) or {}
        target_file = target_detail.get("file_path")
        if target_file:
            related_files.add(target_file)
        return {
            node.node_id
            for node in self.store.list_code_nodes(self.repo_id)
            if related_files and node.file_path in related_files
        }

    @staticmethod
    def _task_target_to_dict(task: Any) -> dict[str, Any]:
        target_detail = getattr(task, "target_detail", {}) or {}
        return {
            "task_type": getattr(task, "task_type", None),
            "review_dimension": getattr(task, "review_dimension", None),
            "target": target_detail,
            "priority": getattr(task, "priority", None),
            "tags": list(getattr(task, "tags", []) or []),
        }

    @staticmethod
    def _boundary_node(node: CodeNode, reason: str) -> dict[str, Any]:
        return {
            "id": node.qualified_name,
            "node_id": node.node_id,
            "type": node.type,
            "name": node.name,
            "file_path": node.file_path,
            "start_line": node.start_line,
            "end_line": node.end_line,
            "reason": reason,
        }

    def _task_graph_node_payload(
        self,
        node: CodeNode,
        center_ids: set[str],
        outgoing: dict[str, list[str]],
        incoming: dict[str, list[str]],
        depth: int,
        boundary_reason: str | None = None,
    ) -> dict[str, Any]:
        relation = self._relation_to_target(
            node.node_id,
            center_ids=center_ids,
            outgoing=outgoing,
            incoming=incoming,
            depth=depth,
            is_boundary=boundary_reason is not None,
        )
        risk_score = self._node_risk_score(node)
        priority = self._node_priority(relation, risk_score, is_boundary=boundary_reason is not None)
        reason = self._node_reading_reason(
            node,
            relation=relation,
            risk_score=risk_score,
            boundary_reason=boundary_reason,
        )
        return {
            **self._graph_node(node),
            "is_target": node.node_id in center_ids,
            "relation_to_target": relation,
            "priority": priority,
            "risk_score": risk_score,
            "reason": reason,
            **({"boundary_reason": boundary_reason} if boundary_reason else {}),
        }

    @staticmethod
    def _relation_to_target(
        node_id: str,
        center_ids: set[str],
        outgoing: dict[str, list[str]],
        incoming: dict[str, list[str]],
        depth: int,
        is_boundary: bool,
    ) -> str:
        if node_id in center_ids:
            return "target"
        if any(node_id in outgoing.get(center_id, []) for center_id in center_ids):
            return "direct_callee"
        if any(node_id in incoming.get(center_id, []) for center_id in center_ids):
            return "direct_caller"
        if is_boundary:
            return "boundary"
        if depth <= 1:
            return "adjacent"
        return "indirect"

    @staticmethod
    def _node_priority(relation: str, risk_score: int, is_boundary: bool) -> int:
        base_priority = {
            "target": 100,
            "direct_callee": 85,
            "direct_caller": 80,
            "adjacent": 70,
            "indirect": 55,
            "boundary": 35,
        }.get(relation, 50)
        if relation == "target":
            return base_priority
        risk_boost = min(risk_score // 10, 10)
        if is_boundary:
            risk_boost = min(risk_boost, 5)
        return min(base_priority + risk_boost, 99)

    @staticmethod
    def _node_risk_score(node: CodeNode) -> int:
        searchable = " ".join(
            [
                node.name,
                node.qualified_name,
                node.file_path,
                node.signature,
                " ".join(node.decorators),
            ]
        ).lower()
        matches = {keyword for keyword in GRAPH_RISK_KEYWORDS if keyword in searchable}
        return min(len(matches) * 20, 100)

    @staticmethod
    def _node_reading_reason(
        node: CodeNode,
        relation: str,
        risk_score: int,
        boundary_reason: str | None = None,
    ) -> str:
        relation_reasons = {
            "target": "Target node for this review task.",
            "direct_callee": "Direct callee of the target; inspect how target delegates work.",
            "direct_caller": "Direct caller of the target; inspect entry and parameter flow.",
            "adjacent": "Adjacent task-local node; useful for immediate context.",
            "indirect": "Indirect task-local node; inspect after direct neighbors if needed.",
            "boundary": "Boundary node is not expanded in this task-local graph.",
        }
        reason = relation_reasons.get(relation, "Task-local graph node.")
        if risk_score:
            reason = f"{reason} Risk keywords matched in {node.name} or path."
        if boundary_reason:
            reason = f"{boundary_reason}; {reason}"
        return reason

    @staticmethod
    def _sort_graph_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            nodes,
            key=lambda node: (
                -int(node.get("priority", 0)),
                -int(node.get("risk_score", 0)),
                str(node.get("name", "")),
            ),
        )

    @staticmethod
    def _empty_task_graph_slice(task_id: str, depth: int) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "target": None,
            "depth": depth,
            "requested_depth": depth,
            "nodes": [],
            "edges": [],
            "boundary_nodes": [],
            "truncated": False,
            "graph_scope": "task-local",
        }

    def _record_graph_slice_usage(self, graph_slice: dict[str, Any]) -> None:
        target = graph_slice.get("target") or {}
        target_detail = target.get("target") if isinstance(target, dict) else {}
        target_file = target_detail.get("file_path") if isinstance(target_detail, dict) else None
        self._record_usage(
            tool_name="get_task_graph_slice",
            task_id=graph_slice.get("task_id"),
            review_dimension=target.get("review_dimension") if isinstance(target, dict) else None,
            file_path=target_file,
            target_type="graph_slice",
            target_name=target_file,
            lines_returned=len(graph_slice.get("nodes", [])) + len(graph_slice.get("edges", [])),
        )

    def _related_files_for_nodes(
        self,
        centers: list[CodeNode],
        graph: dict[str, Any],
        max_files: int,
    ) -> list[str]:
        files: list[str] = []
        for node in centers:
            if node.file_path not in files:
                files.append(node.file_path)
        for node in graph["nodes"]:
            file_path = node.get("file_path")
            if isinstance(file_path, str) and file_path not in files:
                files.append(file_path)
            if len(files) >= max_files:
                break
        return files[:max_files]

    @staticmethod
    def _edge_map(edges: list[CodeEdge], direction: str) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for edge in edges:
            source = edge.source_node_id if direction == "out" else edge.target_node_id
            target = edge.target_node_id if direction == "out" else edge.source_node_id
            mapping.setdefault(source, []).append(target)
        return mapping

    @staticmethod
    def _undirected_edge_map(edges: list[CodeEdge]) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for edge in edges:
            mapping.setdefault(edge.source_node_id, []).append(edge.target_node_id)
            mapping.setdefault(edge.target_node_id, []).append(edge.source_node_id)
        return mapping

    def _nodes_by_id(self) -> dict[str, CodeNode]:
        return {node.node_id: node for node in self.store.list_code_nodes(self.repo_id)}

    def _node_detail(
        self,
        node: CodeNode,
        include_source: bool = False,
    ) -> dict[str, Any]:
        detail = {
            **self._node_summary(node),
            "signature": node.signature,
            "decorators": list(node.decorators),
        }
        if include_source:
            source = self.get_file_snippet(
                node.file_path,
                node.start_line,
                node.end_line,
                record_usage=False,
            )
            detail["code"] = source["content"]
            detail["source"] = source["content"]
        return detail

    @staticmethod
    def _node_summary(node: CodeNode) -> dict[str, Any]:
        return {
            "node_id": node.node_id,
            "id": node.qualified_name,
            "type": node.type,
            "name": node.name,
            "qualified_name": node.qualified_name,
            "file_path": node.file_path,
            "start_line": node.start_line,
            "end_line": node.end_line,
        }

    @staticmethod
    def _graph_node(node: CodeNode) -> dict[str, Any]:
        return {
            "id": node.qualified_name,
            "node_id": node.node_id,
            "type": node.type,
            "name": node.name,
            "file_path": node.file_path,
            "start_line": node.start_line,
            "end_line": node.end_line,
        }

    @staticmethod
    def _edge_to_dict(edge: CodeEdge, node_by_id: dict[str, CodeNode] | None = None) -> dict[str, Any]:
        source = node_by_id.get(edge.source_node_id) if node_by_id else None
        target = node_by_id.get(edge.target_node_id) if node_by_id else None
        return {
            "repo_id": edge.repo_id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "from": source.qualified_name if source else edge.source_node_id,
            "to": target.qualified_name if target else edge.target_node_id,
            "type": edge.edge_type,
            "edge_type": edge.edge_type,
            "file_path": source.file_path if source else None,
        }

    def _record_usage(
        self,
        tool_name: str,
        task_id: str | None = None,
        node_id: str | None = None,
        file_path: str | None = None,
        review_dimension: str | None = None,
        target_type: str | None = None,
        target_name: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        lines_returned: int | None = None,
        agent: str | None = None,
    ) -> None:
        """记录访问痕迹；记录失败不能影响主查询。"""
        try:
            existing_count = len(self.store.list_context_usage(self.repo_id))
            used_at = datetime.now(timezone.utc).isoformat()
            self.store.insert_context_usage(
                ContextUsage(
                    repo_id=self.repo_id,
                    usage_id=f"usage_{existing_count + 1:06d}",
                    task_id=task_id,
                    tool_name=tool_name,
                    node_id=node_id,
                    file_path=file_path,
                    used_at=used_at,
                    agent=agent or review_dimension,
                    review_dimension=review_dimension,
                    target_type=target_type,
                    target_name=target_name,
                    start_line=start_line,
                    end_line=end_line,
                    lines_returned=lines_returned,
                )
            )
        except Exception:
            return
