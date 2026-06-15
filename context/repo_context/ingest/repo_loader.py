from pathlib import Path


def load_repo_path(repo_path: str | Path) -> Path:
    """加载本地仓库目录；路径不存在或不是目录时给出明确错误。"""
    path = Path(repo_path).expanduser().resolve()

    if not path.exists():
        raise FileNotFoundError(f"Repository path does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Repository path is not a directory: {path}")

    return path
