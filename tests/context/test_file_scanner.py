from pathlib import Path
from zipfile import ZipFile

import pytest

from repo_context.ingest.file_scanner import scan_repo
from repo_context.ingest.repo_loader import load_repo_path
from repo_context.ingest.zip_loader import extract_zip


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_scan_sample_repo_returns_python_files() -> None:
    """正常仓库扫描应返回结构化 Python 文件信息。"""
    results = scan_repo(SAMPLE_REPO)
    paths = {item.file_path for item in results}

    assert "app/api/auth.py" in paths
    assert "app/services/user_service.py" in paths
    assert all(item.language == "python" for item in results)
    assert all(item.line_count > 0 for item in results)


def test_scan_filters_ignored_directories(tmp_path: Path) -> None:
    """依赖、缓存、构建产物和 Git 目录不应进入扫描结果。"""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    ignored_dirs = [
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        "site-packages",
        "node_modules",
    ]
    for dirname in ignored_dirs:
        ignored_dir = tmp_path / dirname
        ignored_dir.mkdir()
        (ignored_dir / "ignored.py").write_text("print('skip')\n", encoding="utf-8")

    paths = {item.file_path for item in scan_repo(tmp_path)}

    assert paths == {"app/main.py"}


def test_config_file_is_detected_as_config() -> None:
    """config.py 应被识别为配置文件。"""
    files = {item.file_path: item for item in scan_repo(SAMPLE_REPO)}

    assert files["app/config.py"].file_type == "config"
    assert files["app/config.py"].is_test is False


def test_settings_file_is_detected_as_config(tmp_path: Path) -> None:
    """settings.py 也属于配置文件边界。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "settings.py").write_text("DEBUG = False\n", encoding="utf-8")

    files = {item.file_path: item for item in scan_repo(tmp_path)}

    assert files["app/settings.py"].file_type == "config"
    assert files["app/settings.py"].is_test is False


def test_tests_directory_is_detected_as_test() -> None:
    """tests/test_xxx.py 应被识别为测试文件。"""
    files = {item.file_path: item for item in scan_repo(SAMPLE_REPO)}

    assert files["tests/test_auth.py"].file_type == "test"
    assert files["tests/test_auth.py"].is_test is True


def test_missing_repo_path_returns_clear_error(tmp_path: Path) -> None:
    """本地仓库路径不存在时抛出明确异常。"""
    with pytest.raises(FileNotFoundError, match="Repository path does not exist"):
        load_repo_path(tmp_path / "missing")


def test_zip_path_traversal_is_rejected(tmp_path: Path) -> None:
    """zip 中的路径穿越条目必须拒绝，避免写出目标目录。"""
    zip_path = tmp_path / "unsafe.zip"
    output_dir = tmp_path / "extracted"

    with ZipFile(zip_path, "w") as archive:
        archive.writestr("../../evil.py", "print('bad')\n")

    with pytest.raises(ValueError, match="Unsafe zip entry path"):
        extract_zip(zip_path, output_dir)

    assert not (tmp_path.parent / "evil.py").exists()
