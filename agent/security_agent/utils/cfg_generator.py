import json
import subprocess
import tempfile
import os
from typing import Dict, List, Optional
from ..models import CFGNode, ControlFlowGraph, CodeDiff


class CFGGenerator:
    def __init__(self, clang_path: str = "clang"):
        self.clang_path = clang_path

    def generate_from_source(
        self,
        source_code: str,
        file_name: str = "temp.c",
        functions: Optional[List[str]] = None
    ) -> List[ControlFlowGraph]:
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = os.path.join(tmpdir, file_name)
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(source_code)

            ast_dump = self._run_clang_ast_dump(source_path)
            if not ast_dump:
                return []

            cfgs = self._parse_ast_to_cfg(ast_dump, file_name)
            if functions:
                cfgs = [c for c in cfgs if c.function_name in functions]
            return cfgs

    def generate_from_diff(
        self,
        diffs: List[CodeDiff],
        repository_path: str = "."
    ) -> List[ControlFlowGraph]:
        cfgs = []
        for diff in diffs:
            file_path = os.path.join(repository_path, diff.file_path)
            if not os.path.exists(file_path):
                continue
            ast_dump = self._run_clang_ast_dump(file_path)
            if ast_dump:
                changed_functions = self._extract_changed_functions(diff)
                file_cfgs = self._parse_ast_to_cfg(ast_dump, diff.file_path)
                if changed_functions:
                    file_cfgs = [c for c in file_cfgs if c.function_name in changed_functions]
                cfgs.extend(file_cfgs)
        return cfgs

    def _run_clang_ast_dump(self, source_path: str) -> Optional[str]:
        try:
            result = subprocess.run(
                [self.clang_path, "-Xclang", "-ast-dump", "-fsyntax-only", source_path],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return result.stderr + result.stdout
            return result.stdout or result.stderr
        except subprocess.TimeoutExpired:
            return None
        except FileNotFoundError:
            return self._fallback_cfg_from_source(source_path)

    def _fallback_cfg_from_source(self, source_path: str) -> Optional[str]:
        try:
            with open(source_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    def _parse_ast_to_cfg(self, ast_dump: str, file_path: str) -> List[ControlFlowGraph]:
        cfgs = []
        current_function = None
        current_nodes = []
        node_counter = 0
        in_function = False
        bracket_depth = 0

        lines = ast_dump.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()

            func_match_start = stripped
            if ("FunctionDecl" in stripped or "CXXMethodDecl" in stripped) and "line:" in stripped:
                if current_function and current_nodes:
                    cfgs.append(self._build_cfg_from_nodes(
                        current_function, current_nodes, file_path
                    ))
                import re
                name_match = re.search(r'FunctionDecl\s+\w+\s+([\w:~]+)', stripped)
                if not name_match:
                    name_match = re.search(r'CXXMethodDecl\s+\w+\s+([\w:~]+)', stripped)
                if not name_match:
                    name_match = re.search(r'name="([^"]+)"', stripped)
                func_name = name_match.group(1) if name_match else f"function_{len(cfgs)}"
                func_name = func_name.split("::")[-1]
                current_function = func_name
                current_nodes = []
                node_counter = 0
                in_function = True
                bracket_depth = 0
                continue

            if in_function and ("CompoundStmt" in stripped):
                bracket_depth = 1
                entry_node = CFGNode(
                    id=node_counter,
                    label="entry",
                    code=f"function {current_function} entry",
                    line_start=i,
                    line_end=i,
                    is_entry=True
                )
                current_nodes.append(entry_node)
                node_counter += 1
                continue

            if in_function and bracket_depth > 0:
                if "{" in stripped:
                    bracket_depth += stripped.count("{")
                if "}" in stripped:
                    bracket_depth -= stripped.count("}")
                if bracket_depth == 0:
                    in_function = False
                    if current_function and current_nodes:
                        cfgs.append(self._build_cfg_from_nodes(
                            current_function, current_nodes, file_path
                        ))
                    current_function = None
                    current_nodes = []
                    continue

                is_conditional = any(kw in stripped for kw in
                    ["IfStmt", "ForStmt", "WhileStmt", "SwitchStmt",
                     "ConditionalOperator", "BinaryOperator"])

                line_num = i
                import re
                line_match = re.search(r'line:(\d+)', stripped)
                if line_match:
                    line_num = int(line_match.group(1))

                node = CFGNode(
                    id=node_counter,
                    label=f"stmt_{node_counter}",
                    code=stripped[:200],
                    line_start=line_num,
                    line_end=line_num,
                    is_conditional=is_conditional
                )
                current_nodes.append(node)
                node_counter += 1

        if current_function and current_nodes:
            cfgs.append(self._build_cfg_from_nodes(
                current_function, current_nodes, file_path
            ))
        return cfgs

    def _build_cfg_from_nodes(
        self,
        func_name: str,
        nodes: List[CFGNode],
        file_path: str
    ) -> ControlFlowGraph:
        edges = []
        entries = [n for n in nodes if n.is_entry]

        for i in range(len(nodes)):
            if i < len(nodes) - 1:
                nodes[i].successors.append(nodes[i + 1].id)
                nodes[i + 1].predecessors.append(nodes[i].id)
                edges.append((nodes[i].id, nodes[i + 1].id))

        return ControlFlowGraph(
            function_name=func_name,
            file_path=file_path,
            nodes=nodes,
            edges=edges
        )

    def _extract_changed_functions(self, diff: CodeDiff) -> List[str]:
        functions = set()
        import re
        for addition in diff.additions:
            line = addition.get("content", "")
            match = re.search(
                r'(?:void|int|char|float|double|long|short|bool|auto|size_t|uint\w*|'
                r'struct\s+\w+|class\s+\w+)\s+(\w+)\s*\([^)]*\)\s*\{',
                line
            )
            if match:
                functions.add(match.group(1))
        return list(functions)

    def to_json(self, cfgs: List[ControlFlowGraph]) -> str:
        return json.dumps([cfg.to_dict() for cfg in cfgs], indent=2, ensure_ascii=False)