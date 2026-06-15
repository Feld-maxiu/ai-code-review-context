from dataclasses import asdict, dataclass
from pathlib import Path

from repo_context.ingest.file_filter import is_python_file, should_skip_dir
from repo_context.ingest.repo_loader import load_repo_path


@dataclass(frozen=True)
class CodeFile:
    file_path: str
    file_type: str
    language: str
    line_count: int
    is_test: bool

    def to_dict(self) -> dict[str, str | int | bool]:
        """转换为 JSON serializable 字典，方便后续 API 或工具层复用。"""
        return asdict(self)


def scan_repo(repo_path: str | Path) -> list[CodeFile]:
    """扫描仓库中的有效 Python 文件，不解析 AST、不写入数据库。"""
    root = load_repo_path(repo_path)
    code_files: list[CodeFile] = []

    for path in sorted(root.rglob("*")):
        if any(should_skip_dir(parent) for parent in path.relative_to(root).parents):
            continue
        if path.is_dir() or not is_python_file(path):
            continue

        relative_path = path.relative_to(root).as_posix()
        is_test = _is_test_file(path.relative_to(root))
        code_files.append(
            CodeFile(
                file_path=relative_path,
                file_type=_detect_file_type(path.relative_to(root)),
                language="python",
                line_count=_count_lines(path),
                is_test=is_test,
            )
        )

    return code_files


def _detect_file_type(relative_path: Path) -> str:
    """用路径和文件名做轻量分类，阶段 1 不读取语法结构。"""
    if _is_test_file(relative_path):
        return "test"
    if relative_path.name in {"config.py", "settings.py"}:
        return "config"
    if relative_path.suffix == ".py":
        return "source"
    return "other"


def _is_test_file(relative_path: Path) -> bool:
    parts = set(relative_path.parts)
    return (
        "tests" in parts
        or relative_path.name.startswith("test_")
        or relative_path.name.endswith("_test.py")
    )


def _count_lines(path: Path) -> int:
    """按文本行统计代码行数；读文件失败应暴露给调用方定位输入问题。"""
    return len(path.read_text(encoding="utf-8").splitlines())
