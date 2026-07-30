import json
import re
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class VulnerabilityReport:
    file_path: str
    line_start: int
    line_end: int
    cwe_id: str
    cwe_name: str
    severity: str
    description: str
    code_snippet: str
    confidence: float = 1.0
    is_false_positive: bool = False
    filter_reason: str = ""
    runtime_status: str = ""
    runtime_input: str = ""
    stack_trace: str = ""
    coverage: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeDiff:
    file_path: str
    additions: List[Dict[str, Any]]
    deletions: List[Dict[str, Any]]
    raw_diff: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "additions": self.additions,
            "deletions": self.deletions,
            "raw_diff": self.raw_diff
        }


@dataclass
class CFGNode:
    id: int
    label: str
    code: str
    line_start: int
    line_end: int
    successors: List[int] = field(default_factory=list)
    predecessors: List[int] = field(default_factory=list)
    is_entry: bool = False
    is_conditional: bool = False
    # 函数调用点信息，每项: {callee, args_text, arg_vars, line, assignment_target}
    calls: List[Dict[str, Any]] = field(default_factory=list)
    # 赋值信息，每项: {target, source_vars, source_text, line}
    assignments: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ControlFlowGraph:
    function_name: str
    file_path: str
    nodes: List[CFGNode]
    edges: List[tuple]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "function_name": self.function_name,
            "file_path": self.file_path,
            "nodes": [{"id": n.id, "label": n.label, "code": n.code,
                       "line_start": n.line_start, "line_end": n.line_end,
                       "successors": n.successors, "predecessors": n.predecessors,
                       "is_entry": n.is_entry, "is_conditional": n.is_conditional,
                       "calls": n.calls, "assignments": n.assignments}
                      for n in self.nodes],
            "edges": self.edges
        }

    def find_node_by_line(self, line: int) -> Optional[CFGNode]:
        for node in self.nodes:
            if node.line_start <= line <= node.line_end:
                return node
        return None

    def find_path_to(self, target_node_id: int) -> List[List[int]]:
        entries = [n for n in self.nodes if n.is_entry]
        paths = []
        for entry in entries:
            path = self._dfs_path(entry.id, target_node_id, set())
            if path:
                paths.append(path)
        return paths

    def _dfs_path(self, current: int, target: int, visited: set) -> Optional[List[int]]:
        if current == target:
            return [current]
        if current in visited:
            return None
        visited.add(current)
        node = next((n for n in self.nodes if n.id == current), None)
        if not node:
            return None
        for succ in node.successors:
            path = self._dfs_path(succ, target, visited.copy())
            if path:
                return [current] + path
        return None


@dataclass
class PreprocessedContext:
    diffs: List[CodeDiff]
    cfgs: List[ControlFlowGraph]
    repository_files: Dict[str, str]

    def to_json(self) -> str:
        return json.dumps({
            "diffs": [d.to_dict() for d in self.diffs],
            "cfgs": [c.to_dict() for c in self.cfgs],
            "repository_files": self.repository_files
        }, indent=2, ensure_ascii=False)


def parse_cwe_id(cwe_str: str) -> str:
    match = re.search(r'CWE-(\d+)', cwe_str, re.IGNORECASE)
    if match:
        return f"CWE-{match.group(1)}"
    return cwe_str