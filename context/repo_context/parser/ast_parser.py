import ast
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CodeNode:
    node_id: str
    type: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str
    decorators: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, str | int | list[str]]:
        """转换为 JSON serializable 字典，供后续阶段复用。"""
        return asdict(self)


@dataclass(frozen=True)
class ImportInfo:
    module: str
    name: str
    alias: str | None
    import_type: str

    def to_dict(self) -> dict[str, str | None]:
        """转换 import 信息为普通字典。"""
        return asdict(self)


@dataclass(frozen=True)
class ParseError:
    message: str
    line: int | None
    offset: int | None

    def to_dict(self) -> dict[str, str | int | None]:
        """转换解析错误为普通字典，语法错误不会中断批处理。"""
        return asdict(self)


@dataclass(frozen=True)
class ParseResult:
    file_path: str
    nodes: list[CodeNode]
    imports: list[ImportInfo]
    errors: list[ParseError]

    def to_dict(self) -> dict[str, str | list[dict[str, object]]]:
        """返回 JSON serializable 结果，不包含 AST 原始对象。"""
        return {
            "file_path": self.file_path,
            "nodes": [node.to_dict() for node in self.nodes],
            "imports": [item.to_dict() for item in self.imports],
            "errors": [error.to_dict() for error in self.errors],
        }


def parse_python_file(file_path: str | Path, repo_root: str | Path | None = None) -> ParseResult:
    """解析单个 Python 文件，抽取符号、import 和 decorator。"""
    path = Path(file_path)
    display_path = _display_path(path, repo_root)
    source = path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(source, filename=display_path)
    except SyntaxError as exc:
        # 语法错误只记录到结果中，不向外抛出，避免中断整个仓库解析。
        return ParseResult(
            file_path=display_path,
            nodes=[],
            imports=[],
            errors=[
                ParseError(message=exc.msg, line=exc.lineno, offset=exc.offset),
            ],
        )

    visitor = _AstSymbolVisitor(display_path, _module_name(display_path), source)
    visitor.visit(tree)
    return ParseResult(
        file_path=display_path,
        nodes=visitor.nodes,
        imports=visitor.imports,
        errors=[],
    )


class _AstSymbolVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, module_name: str, source: str) -> None:
        self.file_path = file_path
        self.module_name = module_name
        self.source = source
        self.nodes: list[CodeNode] = []
        self.imports: list[ImportInfo] = []
        self._class_stack: list[str] = []

        line_count = len(source.splitlines()) or 1
        self.nodes.append(
            CodeNode(
                node_id=f"{file_path}:{module_name}:1",
                type="module",
                name=module_name,
                qualified_name=module_name,
                file_path=file_path,
                start_line=1,
                end_line=line_count,
                signature="",
                decorators=[],
            )
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(
                ImportInfo(
                    module=alias.name,
                    name=alias.name,
                    alias=alias.asname,
                    import_type="import",
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        for alias in node.names:
            self.imports.append(
                ImportInfo(
                    module=module,
                    name=alias.name,
                    alias=alias.asname,
                    import_type="from",
                )
            )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified_name = self._qualified_name(node.name)
        self.nodes.append(
            CodeNode(
                node_id=self._node_id(qualified_name, node),
                type="class",
                name=node.name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                signature=node.name,
                decorators=_decorators(node, self.source),
            )
        )

        self._class_stack.append(node.name)
        # 类体内继续访问方法和 import；阶段 2 不构建调用关系。
        for child in node.body:
            self.visit(child)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_function_node(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._add_function_node(node)

    def _add_function_node(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified_name = self._qualified_name(node.name)
        node_type = "method" if self._class_stack else "function"
        self.nodes.append(
            CodeNode(
                node_id=self._node_id(qualified_name, node),
                type=node_type,
                name=node.name,
                qualified_name=qualified_name,
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                signature=f"{node.name}({_format_arguments(node.args)})",
                decorators=_decorators(node, self.source),
            )
        )

    def _qualified_name(self, name: str) -> str:
        parts = [self.module_name, *self._class_stack, name]
        return ".".join(part for part in parts if part)

    def _node_id(self, qualified_name: str, node: ast.AST) -> str:
        return f"{self.file_path}:{qualified_name}:{node.lineno}"


def _display_path(path: Path, repo_root: str | Path | None) -> str:
    if repo_root is None:
        return path.as_posix()
    return path.resolve().relative_to(Path(repo_root).resolve()).as_posix()


def _module_name(file_path: str) -> str:
    return ".".join(Path(file_path).with_suffix("").parts)


def _decorators(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    source: str,
) -> list[str]:
    decorators: list[str] = []
    for item in node.decorator_list:
        # 优先保留源码写法，例如双引号；取不到源码片段时再退回 ast.unparse。
        decorators.append(ast.get_source_segment(source, item) or ast.unparse(item))
    return decorators


def _format_arguments(args: ast.arguments) -> str:
    """用标准库 ast 反序列化参数，保留注解和默认值的基本形态。"""
    parts: list[str] = []

    positional = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(positional, defaults, strict=True):
        parts.append(_format_arg(arg, default))

    if args.vararg:
        parts.append(f"*{_format_arg(args.vararg, None)}")
    elif args.kwonlyargs:
        parts.append("*")

    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        parts.append(_format_arg(arg, default))

    if args.kwarg:
        parts.append(f"**{_format_arg(args.kwarg, None)}")

    return ", ".join(parts)


def _format_arg(arg: ast.arg, default: ast.expr | None) -> str:
    text = arg.arg
    if arg.annotation is not None:
        text = f"{text}: {ast.unparse(arg.annotation)}"
    if default is not None:
        text = f"{text} = {ast.unparse(default)}"
    return text
