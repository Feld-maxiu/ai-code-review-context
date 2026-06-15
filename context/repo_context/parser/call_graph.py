import ast
from dataclasses import dataclass, field
from pathlib import Path


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


@dataclass(frozen=True)
class RawCall:
    source_qualified_name: str
    call_name: str
    line: int


@dataclass(frozen=True)
class RouteInfo:
    method: str
    path: str
    handler_qualified_name: str
    handler_name: str
    file_path: str
    start_line: int
    end_line: int
    decorators: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def qualified_name(self) -> str:
        return f"route:{self.method} {self.path}"


@dataclass(frozen=True)
class RelationAnalysis:
    calls: list[RawCall]
    routes: list[RouteInfo]


def analyze_file_relations(
    file_path: str | Path,
    repo_root: str | Path | None = None,
) -> RelationAnalysis:
    """抽取基础调用表达式和 Web 路由；不做评审任务和覆盖率逻辑。"""
    path = Path(file_path)
    display_path = _display_path(path, repo_root)
    source = path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(source, filename=display_path)
    except SyntaxError:
        # 阶段 4 延续阶段 2 的容错：坏文件不影响其他文件构图。
        return RelationAnalysis(calls=[], routes=[])

    visitor = _RelationVisitor(display_path, _module_name(display_path), source)
    visitor.visit(tree)
    return RelationAnalysis(calls=visitor.calls, routes=visitor.routes)


class _RelationVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, module_name: str, source: str) -> None:
        self.file_path = file_path
        self.module_name = module_name
        self.source = source
        self.calls: list[RawCall] = []
        self.routes: list[RouteInfo] = []
        self._class_stack: list[str] = []
        self._function_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._function_stack:
            call_name = _call_name(node.func)
            if call_name:
                self.calls.append(
                    RawCall(
                        source_qualified_name=self._function_stack[-1],
                        call_name=call_name,
                        line=node.lineno,
                    )
                )
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified_name = self._qualified_name(node.name)
        self.routes.extend(self._routes_for_function(node, qualified_name))
        self._function_stack.append(qualified_name)
        self.generic_visit(node)
        self._function_stack.pop()

    def _routes_for_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        qualified_name: str,
    ) -> list[RouteInfo]:
        routes: list[RouteInfo] = []
        decorators = _decorators(node, self.source)

        for decorator in node.decorator_list:
            route = _route_from_decorator(decorator)
            if route is None:
                continue

            method, path = route
            routes.append(
                RouteInfo(
                    method=method,
                    path=path,
                    handler_qualified_name=qualified_name,
                    handler_name=node.name,
                    file_path=self.file_path,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    decorators=decorators,
                )
            )

        return routes

    def _qualified_name(self, name: str) -> str:
        parts = [self.module_name, *self._class_stack, name]
        return ".".join(part for part in parts if part)


def _route_from_decorator(decorator: ast.expr) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call):
        return None

    if isinstance(decorator.func, ast.Attribute):
        attr = decorator.func.attr.lower()
        if attr in HTTP_METHODS and decorator.args:
            path = _literal_string(decorator.args[0])
            return (attr.upper(), path) if path else None

        if attr == "route" and decorator.args:
            path = _literal_string(decorator.args[0])
            methods = _methods_from_keywords(decorator.keywords)
            if path and methods:
                return methods[0], path

    return None


def _methods_from_keywords(keywords: list[ast.keyword]) -> list[str]:
    for keyword in keywords:
        if keyword.arg != "methods":
            continue
        value = keyword.value
        if isinstance(value, ast.List):
            return [
                item.value.upper()
                for item in value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
    return ["GET"]


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return None


def _decorators(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    source: str,
) -> list[str]:
    return [
        ast.get_source_segment(source, item) or ast.unparse(item)
        for item in node.decorator_list
    ]


def _display_path(path: Path, repo_root: str | Path | None) -> str:
    if repo_root is None:
        return path.as_posix()
    return path.resolve().relative_to(Path(repo_root).resolve()).as_posix()


def _module_name(file_path: str) -> str:
    return ".".join(Path(file_path).with_suffix("").parts)
