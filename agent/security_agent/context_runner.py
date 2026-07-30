import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .main import PipelineTimeoutError
from .models import (
    CFGNode,
    CodeDiff,
    ControlFlowGraph,
    PreprocessedContext,
)


class ContextAgentRunner:
    def __init__(
        self,
        context_client: Any,
        orchestrator: Any,
        agent_name: str = "security-agent",
    ):
        self.context_client = context_client
        self.orchestrator = orchestrator
        self.agent_name = agent_name

    def run_tasks_for_repo(
        self,
        repo_id: str,
        review_dimension: str = "security",
        agent_name: str = "security-agent",
        output_dir: str = "results",
        security_config: Optional[Dict[str, Any]] = None,
        target_binary: Optional[str] = None,
        context_depth: int = 2,
        max_context_files: int = 3,
    ) -> Dict[str, Any]:
        """按评审维度拉取全部任务并逐个运行安全评审。

        单个任务失败不会中断整体流程，失败信息会被记录到返回结果中。
        """

        os.makedirs(output_dir, exist_ok=True)
        tasks_response = self.context_client.list_tasks(
            repo_id=repo_id, review_dimension=review_dimension
        )
        tasks = tasks_response.get("tasks", []) if isinstance(tasks_response, dict) else []

        completed: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []

        for task in tasks:
            task_id = task.get("task_id") if isinstance(task, dict) else task
            if not task_id:
                continue
            output_file = os.path.join(output_dir, f"{task_id}.json")
            try:
                result = self.run_task(
                    repo_id=repo_id,
                    task_id=task_id,
                    review_dimension=review_dimension,
                    agent_name=agent_name,
                    security_config=security_config,
                    output_file=output_file,
                    target_binary=target_binary,
                    context_depth=context_depth,
                    max_context_files=max_context_files,
                )
                # PipelineResult 是 dataclass，不可直接 json.dump，转为 summary dict
                result_dict = result.summary if hasattr(result, "summary") else vars(result)
                completed.append({
                    "task_id": task_id,
                    "output_file": output_file,
                    "result": result_dict,
                })
            except Exception as exc:
                failed.append({"task_id": task_id, "error": str(exc)})

        return {
            "repo_id": repo_id,
            "review_dimension": review_dimension,
            "total_tasks": len(tasks),
            "completed": completed,
            "failed": failed,
        }

    def run_task(
        self,
        repo_id: str,
        task_id: str,
        review_dimension: str = "security",
        agent_name: str = "security-agent",
        security_config: Optional[Dict[str, Any]] = None,
        output_file: str = "result.json",
        target_binary: Optional[str] = None,
        context_depth: int = 2,
        max_context_files: int = 3,
    ) -> Any:
        self.agent_name = agent_name
        security_config = security_config or {}
        try:
            task_package = self.context_client.get_task_package(task_id, repo_id)
            target = self._extract_target(task_package)
            target_file = target.get("file_path") or target.get("target_file")
            if not target_file:
                self._post_feedback(
                    repo_id=repo_id,
                    task_id=task_id,
                    status="skipped",
                    context_sufficient=False,
                    feedback_type="blocked_reason",
                    message="任务包缺少 target.file_path，无法启动安全审计。",
                    need_more_context=True,
                    requested_context=[{"type": "task-package", "field": "target.file_path"}],
                    downstream_result_ref=None,
                )
                raise ValueError("task package missing target.file_path")

            graph_slice = self.context_client.get_task_graph_slice(
                task_id, repo_id, depth=context_depth
            )
            node_details = self._fetch_node_details(
                repo_id=repo_id,
                task_id=task_id,
                review_dimension=review_dimension,
                graph_slice=graph_slice,
                target=target,
            )
            related_context = self.context_client.get_related_context(
                repo_id=repo_id,
                task_id=task_id,
                target_file=target_file,
                review_dimension=review_dimension,
                tags=self._extract_tags(task_package),
                max_depth=max(context_depth - 1, 1),
                max_files=max_context_files,
            )

            context = build_preprocessed_context_from_context_tools(
                task_package=task_package,
                graph_slice=graph_slice,
                related_context=related_context,
                node_details=node_details,
            )
            result = self.orchestrator.run_with_preprocessed_context(
                context,
                target_binary=target_binary,
            )
            self.orchestrator.export_results(
                result,
                output_file,
                metadata={
                    "task_id": task_id,
                    "repo_id": repo_id,
                    "review_dimension": review_dimension,
                    "dimension": review_dimension,
                    "agent_name": agent_name,
                    "security_config": security_config,
                    "status": "completed",
                    "scan_id": task_package.get("scan_id", ""),
                    "snapshot_id": task_package.get("snapshot_id", ""),
                    "message": None,
                    "context": context,
                },
            )
            self._post_feedback(
                repo_id=repo_id,
                task_id=task_id,
                status="completed",
                context_sufficient=True,
                feedback_type="task_status",
                message="网络安全 agent 已完成当前任务。",
                need_more_context=False,
                requested_context=[],
                downstream_result_ref=output_file,
            )
            return result
        except PipelineTimeoutError as exc:
            # 流水线整体超时：明确标记为 agent 自身超时，不需要更多上下文
            self._post_feedback(
                repo_id=repo_id,
                task_id=task_id,
                status="failed",
                context_sufficient=True,
                feedback_type="agent_timeout",
                message=str(exc),
                need_more_context=False,
                requested_context=[],
                downstream_result_ref=None,
            )
            raise
        except Exception as exc:
            if not self._last_call_was_feedback():
                self._post_feedback(
                    repo_id=repo_id,
                    task_id=task_id,
                    status="blocked",
                    context_sufficient=False,
                    feedback_type="context_request",
                    message=f"网络安全 agent 联调阻塞: {exc}",
                    need_more_context=True,
                    requested_context=[{
                        "type": "related-context",
                        "task_id": task_id,
                        "review_dimension": review_dimension,
                    }],
                    downstream_result_ref=None,
                )
            raise

    def _post_feedback(self, **kwargs):
        self.context_client.post_task_feedback(
            agent=self.agent_name,
            **kwargs,
        )

    def _last_call_was_feedback(self) -> bool:
        calls = getattr(self.context_client, "calls", [])
        return bool(calls and calls[-1][0] == "task-feedback")

    def _extract_target(self, task_package: Dict[str, Any]) -> Dict[str, Any]:
        target = task_package.get("target") or {}
        return target if isinstance(target, dict) else {}

    def _extract_tags(self, task_package: Dict[str, Any]) -> List[str]:
        tags = task_package.get("tags", [])
        if isinstance(tags, list):
            return [str(tag) for tag in tags]
        focus_points = task_package.get("focus_points", [])
        if isinstance(focus_points, list):
            return [str(point) for point in focus_points]
        return []

    def _fetch_node_details(
        self,
        repo_id: str,
        task_id: str,
        review_dimension: str,
        graph_slice: Dict[str, Any],
        target: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        symbols = self._select_symbols_for_detail(graph_slice, target)
        details = []
        for symbol_name in symbols:
            detail = self.context_client.get_node_detail(
                repo_id=repo_id,
                symbol_name=symbol_name,
                task_id=task_id,
                review_dimension=review_dimension,
            )
            if isinstance(detail, dict):
                details.append(detail)
        return details

    def _select_symbols_for_detail(
        self,
        graph_slice: Dict[str, Any],
        target: Dict[str, Any],
    ) -> List[str]:
        nodes = graph_slice.get("nodes", []) if isinstance(graph_slice, dict) else []
        candidates = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            symbol_name = node.get("qualified_name") or node.get("name")
            if not symbol_name:
                continue
            risk_score = node.get("risk_score", 0.0)
            try:
                risk_score = float(risk_score)
            except (TypeError, ValueError):
                risk_score = 0.0
            candidates.append((risk_score, str(symbol_name)))

        candidates.sort(key=lambda item: item[0], reverse=True)
        selected = [name for score, name in candidates if score >= 0.5][:3]
        if selected:
            return selected

        target_symbol = target.get("symbol_name") or target.get("qualified_name")
        return [str(target_symbol)] if target_symbol else []


def build_preprocessed_context_from_context_tools(
    task_package: Dict[str, Any],
    graph_slice: Dict[str, Any],
    related_context: Dict[str, Any],
    node_details: Optional[List[Dict[str, Any]]] = None,
) -> PreprocessedContext:
    target = _merge_target(task_package, graph_slice)
    target_file = target.get("file_path") or target.get("target_file") or "unknown"
    snippets = list(_iter_snippets(task_package.get("initial_context", {})))
    snippets.extend(_node_details_to_snippets(node_details or [], target_file))
    snippets.extend(_iter_snippets(related_context))

    repository_files = _build_repository_files(snippets, target_file)
    raw_diff = _extract_raw_diff(task_package, repository_files, target_file)
    additions = _build_additions(repository_files.get(target_file, raw_diff))
    diffs = [CodeDiff(
        file_path=target_file,
        additions=additions,
        deletions=[],
        raw_diff=raw_diff,
    )]

    cfgs = _build_cfgs(
        target=target,
        graph_slice=graph_slice,
        related_context=related_context,
        default_file=target_file,
    )
    return PreprocessedContext(
        diffs=diffs,
        cfgs=cfgs,
        repository_files=repository_files,
    )


def _merge_target(
    task_package: Dict[str, Any],
    graph_slice: Dict[str, Any],
) -> Dict[str, Any]:
    target = {}
    for source in (task_package.get("target"), graph_slice.get("target")):
        if isinstance(source, dict):
            target.update(source)
    return target


def _iter_snippets(container: Any) -> Iterable[Dict[str, Any]]:
    if not isinstance(container, dict):
        return []
    candidates = []
    for key in ("snippets", "source_snippets", "code_snippets"):
        value = container.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
    return candidates


def _node_details_to_snippets(
    node_details: List[Dict[str, Any]],
    target_file: str,
) -> List[Dict[str, Any]]:
    snippets = []
    for detail in node_details:
        snippets.append({
            "file_path": detail.get("file_path") or target_file,
            "start_line": detail.get("start_line") or 1,
            "end_line": detail.get("end_line") or detail.get("start_line") or 1,
            "content": detail.get("code") or "",
        })
    return snippets


def _build_repository_files(
    snippets: List[Dict[str, Any]],
    target_file: str,
) -> Dict[str, str]:
    files: Dict[str, List[Tuple[int, str]]] = {}
    for snippet in snippets:
        file_path = snippet.get("file_path") or snippet.get("path") or target_file
        content = snippet.get("content") or snippet.get("code") or ""
        start_line = int(snippet.get("start_line") or 1)
        files.setdefault(file_path, []).append((start_line, content))

    if not files:
        files[target_file] = [(1, "")]

    repository_files = {}
    for file_path, parts in files.items():
        ordered = [content for _, content in sorted(parts, key=lambda item: item[0])]
        repository_files[file_path] = "\n".join(part.rstrip("\n") for part in ordered)
    return repository_files


def _extract_raw_diff(
    task_package: Dict[str, Any],
    repository_files: Dict[str, str],
    target_file: str,
) -> str:
    initial_context = task_package.get("initial_context", {})
    if isinstance(initial_context, dict):
        for key in ("diff", "raw_diff", "code_diff"):
            value = initial_context.get(key)
            if isinstance(value, str) and value.strip():
                return value
    content = repository_files.get(target_file, "")
    return "\n".join(f"+ {line}" for line in content.splitlines())


def _build_additions(content: str) -> List[Dict[str, Any]]:
    return [
        {"line": line_no, "content": line}
        for line_no, line in enumerate(content.splitlines(), start=1)
    ]


def _build_cfgs(
    target: Dict[str, Any],
    graph_slice: Dict[str, Any],
    related_context: Dict[str, Any],
    default_file: str,
) -> List[ControlFlowGraph]:
    nodes_data = []
    edges_data = []
    for source in (
        graph_slice,
        related_context.get("call_graph_slice", {}) if isinstance(related_context, dict) else {},
    ):
        if isinstance(source, dict):
            nodes_data.extend(n for n in source.get("nodes", []) if isinstance(n, dict))
            edges_data.extend(e for e in source.get("edges", []) if isinstance(e, dict))
    if not nodes_data:
        return []

    id_map = {}
    nodes = []
    for idx, item in enumerate(nodes_data):
        source_id = item.get("node_id") or item.get("id") or item.get("qualified_name") or idx
        id_map[str(source_id)] = idx
        nodes.append(CFGNode(
            id=idx,
            label=str(item.get("label") or item.get("qualified_name") or item.get("name") or source_id),
            code=str(item.get("code") or ""),
            line_start=int(item.get("start_line") or item.get("line_start") or 0),
            line_end=int(item.get("end_line") or item.get("line_end") or item.get("start_line") or 0),
            is_entry=idx == 0,
        ))

    edges = []
    for edge in edges_data:
        source = edge.get("source") or edge.get("from") or edge.get("caller")
        target_id = edge.get("target") or edge.get("to") or edge.get("callee")
        if str(source) in id_map and str(target_id) in id_map:
            src_idx = id_map[str(source)]
            dst_idx = id_map[str(target_id)]
            nodes[src_idx].successors.append(dst_idx)
            nodes[dst_idx].predecessors.append(src_idx)
            edges.append((src_idx, dst_idx))

    return [ControlFlowGraph(
        function_name=str(target.get("symbol_name") or target.get("qualified_name") or "task_graph_slice"),
        file_path=str(target.get("file_path") or default_file),
        nodes=nodes,
        edges=edges,
    )]
