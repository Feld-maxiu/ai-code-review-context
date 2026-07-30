"""基于 Python 标准库 ast 的 CFG 生成器。

本模块替代 clang，用于解析 Python 源码并生成控制流图，
使 security agent 能够真正支持 Python 仓库的评审。
"""

import ast
import re
from typing import Any, Dict, List, Optional, Tuple

from ..models import CFGNode, CodeDiff, ControlFlowGraph


class PythonCFGGenerator:
    """基于 Python AST 生成简化版 CFG。

    目前支持的节点粒度：函数定义、条件语句(if)、循环(for/while)、
    赋值、函数调用、返回语句。后续可扩展异常处理、with 语句等。
    """

    def generate_from_source(
        self,
        source_code: str,
        file_name: str = "temp.py",
        functions: Optional[List[str]] = None,
    ) -> List[ControlFlowGraph]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return []

        cfgs: List[ControlFlowGraph] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_name = node.name
                if functions and func_name not in functions:
                    continue
                cfg = self._build_cfg(node, file_name)
                if cfg:
                    cfgs.append(cfg)
        return cfgs

    def generate_from_diff(
        self,
        diffs: List[CodeDiff],
        repository_path: str = ".",
    ) -> List[ControlFlowGraph]:
        import os

        cfgs: List[ControlFlowGraph] = []
        for diff in diffs:
            file_path = os.path.join(repository_path, diff.file_path)
            if not os.path.exists(file_path):
                continue
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    source = f.read()
            except Exception:
                continue
            changed_functions = self._extract_changed_functions(diff)
            file_cfgs = self.generate_from_source(source, diff.file_path, changed_functions)
            cfgs.extend(file_cfgs)
        return cfgs

    def _extract_changed_functions(self, diff: CodeDiff) -> Optional[List[str]]:
        functions = set()
        for addition in diff.additions:
            content = addition.get("content", "")
            stripped = content.strip()
            if stripped.startswith("def ") or stripped.startswith("async def "):
                # 提取函数名: def func_name(args):
                match = self._FUNC_DEF_RE.match(stripped)
                if match:
                    functions.add(match.group(1))
        return list(functions) if functions else None

    _FUNC_DEF_RE = re.compile(r"^(?:async\s+)?def\s+([a-zA-Z_]\w*)\s*\(")

    def _build_cfg(
        self,
        func_node: ast.AST,
        file_path: str,
    ) -> Optional[ControlFlowGraph]:
        func_name = getattr(func_node, "name", "<lambda>")
        body = getattr(func_node, "body", [])
        if not body:
            return None

        builder = _FuncCFGBuilder(func_node, file_path)
        builder.visit_body(body)
        return builder.build(func_name, file_path)


class _FuncCFGBuilder:
    """单个函数的 CFG 构建器。"""

    def __init__(self, func_node: ast.AST, file_path: str):
        self.func_node = func_node
        self.file_path = file_path
        self.nodes: List[CFGNode] = []
        self.edges: List[tuple] = []
        self._node_counter = 0
        self._last_ids: List[int] = []

    def _new_node(
        self,
        label: str,
        code: str,
        line_start: int,
        line_end: int,
        is_entry: bool = False,
        is_conditional: bool = False,
        calls: Optional[List[Dict[str, Any]]] = None,
        assignments: Optional[List[Dict[str, Any]]] = None,
    ) -> CFGNode:
        node = CFGNode(
            id=self._node_counter,
            label=label,
            code=code,
            line_start=line_start,
            line_end=line_end,
            is_entry=is_entry,
            is_conditional=is_conditional,
            calls=calls or [],
            assignments=assignments or [],
        )
        self.nodes.append(node)
        self._node_counter += 1
        return node

    def _connect(self, from_id: int, to_id: int) -> None:
        self.edges.append((from_id, to_id))
        from_node = self.nodes[from_id]
        to_node = self.nodes[to_id]
        from_node.successors.append(to_id)
        to_node.predecessors.append(from_id)

    def visit_body(self, body: List[ast.AST]) -> List[int]:
        """访问语句列表，返回这组语句的入口节点 id 列表。"""
        if not body:
            return []

        entry_ids: List[int] = []
        prev_ids: List[int] = []

        for stmt in body:
            stmt_entry, stmt_exits = self._visit_stmt(stmt)
            if not entry_ids:
                entry_ids = stmt_entry
            for prev_id in prev_ids:
                for entry_id in stmt_entry:
                    self._connect(prev_id, entry_id)
            prev_ids = stmt_exits

        return entry_ids if entry_ids else prev_ids

    def _visit_stmt(self, stmt: ast.AST) -> Tuple[List[int], List[int]]:
        """访问单条语句，返回 (入口节点 ids, 出口节点 ids)。"""
        line = getattr(stmt, "lineno", 0)
        end_line = getattr(stmt, "end_lineno", line)

        if isinstance(stmt, ast.If):
            return self._visit_if(stmt)
        if isinstance(stmt, (ast.For, ast.While)):
            return self._visit_loop(stmt)
        if isinstance(stmt, ast.Return):
            return self._visit_return(stmt)
        if isinstance(stmt, ast.FunctionDef):
            # 嵌套函数定义作为独立节点，不展开内部
            node = self._new_node(
                f"nested_func_{stmt.name}",
                f"def {stmt.name}(...)",
                line,
                end_line,
            )
            return [node.id], [node.id]

        # 普通语句（赋值、表达式、调用等）
        code = ast.unparse(stmt) if hasattr(ast, "unparse") else self._simple_code(stmt)
        calls = self._extract_calls(stmt)
        assignments = self._extract_assignments(stmt)
        node = self._new_node(
            f"stmt_{self._node_counter}",
            code,
            line,
            end_line,
            is_conditional=isinstance(stmt, ast.BoolOp),
            calls=calls,
            assignments=assignments,
        )
        return [node.id], [node.id]

    def _visit_if(self, stmt: ast.If) -> Tuple[List[int], List[int]]:
        line = stmt.lineno
        end_line = getattr(stmt, "end_lineno", line)
        test_code = ast.unparse(stmt.test) if hasattr(ast, "unparse") else "<condition>"
        cond_node = self._new_node(
            f"if_{self._node_counter}",
            f"if {test_code}",
            line,
            end_line,
            is_conditional=True,
        )

        body_entry = self.visit_body(stmt.body)
        for entry_id in body_entry:
            self._connect(cond_node.id, entry_id)
        body_exits = self._collect_last_ids()

        orelse_entry = self.visit_body(stmt.orelse)
        if orelse_entry:
            for entry_id in orelse_entry:
                self._connect(cond_node.id, entry_id)
            exits = body_exits + self._collect_last_ids()
        else:
            exits = body_exits + [cond_node.id]

        return [cond_node.id], exits

    def _visit_loop(self, stmt: ast.AST) -> Tuple[List[int], List[int]]:
        line = getattr(stmt, "lineno", 0)
        end_line = getattr(stmt, "end_lineno", line)
        if isinstance(stmt, ast.For):
            code = f"for {ast.unparse(stmt.target)} in {ast.unparse(stmt.iter)}" if hasattr(ast, "unparse") else "for ..."
        else:
            code = f"while {ast.unparse(stmt.test)}" if hasattr(ast, "unparse") else "while ..."

        cond_node = self._new_node(
            f"loop_{self._node_counter}",
            code,
            line,
            end_line,
            is_conditional=True,
        )

        body_entry = self.visit_body(stmt.body)
        for entry_id in body_entry:
            self._connect(cond_node.id, entry_id)
        body_exits = self._collect_last_ids()
        # 循环回边
        for exit_id in body_exits:
            self._connect(exit_id, cond_node.id)

        return [cond_node.id], [cond_node.id]

    def _visit_return(self, stmt: ast.Return) -> Tuple[List[int], List[int]]:
        line = getattr(stmt, "lineno", 0)
        end_line = getattr(stmt, "end_lineno", line)
        code = f"return {ast.unparse(stmt.value)}" if stmt.value and hasattr(ast, "unparse") else "return"
        node = self._new_node(
            f"return_{self._node_counter}",
            code,
            line,
            end_line,
        )
        return [node.id], []

    def _collect_last_ids(self) -> List[int]:
        """返回最近访问的语句出口节点 ids（未连接后续语句的节点）。"""
        if not self.nodes:
            return []
        # 简化：返回所有没有后继的节点
        return [n.id for n in self.nodes if not n.successors]

    def build(self, func_name: str, file_path: str) -> ControlFlowGraph:
        if not self.nodes:
            return ControlFlowGraph(
                function_name=func_name,
                file_path=file_path,
                nodes=[],
                edges=[],
            )

        # 添加 entry 节点
        first_real = self.nodes[0]
        entry_node = self._new_node(
            "entry",
            f"function {func_name} entry",
            first_real.line_start,
            first_real.line_start,
            is_entry=True,
        )
        # 将 entry 插入到最前面
        self.nodes.remove(entry_node)
        self.nodes.insert(0, entry_node)
        # entry 的 id 是最大的，需要重新编号以保持顺序
        self._renumber_nodes()

        # entry 连到原来的第一个节点
        if len(self.nodes) > 1:
            self._connect(self.nodes[0].id, self.nodes[1].id)

        return ControlFlowGraph(
            function_name=func_name,
            file_path=file_path,
            nodes=self.nodes,
            edges=self.edges,
        )

    def _renumber_nodes(self) -> None:
        old_to_new = {n.id: i for i, n in enumerate(self.nodes)}
        for i, node in enumerate(self.nodes):
            node.id = i
            node.successors = [old_to_new[s] for s in node.successors]
            node.predecessors = [old_to_new[p] for p in node.predecessors]
        self.edges = [(old_to_new[e[0]], old_to_new[e[1]]) for e in self.edges]
        self._node_counter = len(self.nodes)

    def _extract_calls(self, stmt: ast.AST) -> List[Dict[str, Any]]:
        calls: List[Dict[str, Any]] = []
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                callee = self._get_call_name(node.func)
                line = getattr(node, "lineno", 0)
                args_text = [ast.unparse(a) for a in node.args] if hasattr(ast, "unparse") else []
                arg_vars = [
                    n.id for n in ast.walk(node)
                    if isinstance(n, ast.Name)
                ]
                calls.append({
                    "callee": callee,
                    "args_text": args_text,
                    "arg_vars": arg_vars,
                    "line": line,
                    "assignment_target": None,
                })
        return calls

    def _get_call_name(self, func: ast.AST) -> str:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            parts = []
            current: ast.AST = func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return "<call>"

    def _extract_assignments(self, stmt: ast.AST) -> List[Dict[str, Any]]:
        assignments: List[Dict[str, Any]] = []
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                target_names = self._get_target_names(target)
                source_vars = [n.id for n in ast.walk(stmt.value) if isinstance(n, ast.Name)]
                for tname in target_names:
                    assignments.append({
                        "target": tname,
                        "source_vars": source_vars,
                        "source_text": ast.unparse(stmt.value) if hasattr(ast, "unparse") else "",
                        "line": getattr(stmt, "lineno", 0),
                    })
        elif isinstance(stmt, ast.AnnAssign):
            if stmt.value:
                target_names = self._get_target_names(stmt.target)
                source_vars = [n.id for n in ast.walk(stmt.value) if isinstance(n, ast.Name)]
                for tname in target_names:
                    assignments.append({
                        "target": tname,
                        "source_vars": source_vars,
                        "source_text": ast.unparse(stmt.value) if hasattr(ast, "unparse") else "",
                        "line": getattr(stmt, "lineno", 0),
                    })
        return assignments

    def _get_target_names(self, target: ast.AST) -> List[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            names: List[str] = []
            for elt in target.elts:
                names.extend(self._get_target_names(elt))
            return names
        return []

    def _simple_code(self, stmt: ast.AST) -> str:
        try:
            return ast.dump(stmt)[:200]
        except Exception:
            return "<stmt>"

