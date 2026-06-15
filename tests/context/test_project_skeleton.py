from pathlib import Path

import repo_context


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_repo_context_package_can_be_imported() -> None:
    """确认阶段 0 已经形成可导入的 Python 包。"""
    assert repo_context.__all__


def test_expected_package_directories_exist() -> None:
    """确认后续阶段需要的包目录已经就位。"""
    expected_dirs = [
        "api",
        "index",
        "ingest",
        "parser",
        "service",
        "store",
        "task",
        "tools",
    ]

    for dirname in expected_dirs:
        package_dir = ROOT / "context" / "repo_context" / dirname
        assert package_dir.is_dir()
        assert (package_dir / "__init__.py").is_file()


def test_sample_repo_fixture_has_required_files() -> None:
    """确认用于后续测试的最小示例仓库结构完整。"""
    required_files = [
        "app/api/auth.py",
        "app/services/user_service.py",
        "app/repositories/user_repo.py",
        "app/config.py",
        "tests/test_auth.py",
    ]

    for relative_path in required_files:
        assert (SAMPLE_REPO / relative_path).is_file()


def test_sample_repo_contains_login_route() -> None:
    """确认示例仓库中包含 FastAPI 风格的 login 路由。"""
    auth_source = (SAMPLE_REPO / "app" / "api" / "auth.py").read_text(
        encoding="utf-8"
    )

    assert '@router.post("/login")' in auth_source
    assert "def login" in auth_source
