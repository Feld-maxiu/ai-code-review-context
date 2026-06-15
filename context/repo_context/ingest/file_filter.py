from pathlib import Path


SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "site-packages",
    "node_modules",
}


def should_skip_dir(path: str | Path) -> bool:
    """判断目录是否应跳过，避免扫描依赖、缓存和构建产物。"""
    return Path(path).name in SKIP_DIR_NAMES


def is_python_file(path: str | Path) -> bool:
    """阶段 1 只识别 Python 源文件，其他类型留到后续阶段再扩展。"""
    return Path(path).suffix == ".py"
