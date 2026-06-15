from pathlib import Path

from repo_context.parser.ast_parser import parse_python_file


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_parse_auth_file_finds_login_function() -> None:
    """能从 auth.py 中识别 login 函数节点。"""
    result = parse_python_file(SAMPLE_REPO / "app" / "api" / "auth.py", SAMPLE_REPO)

    login_node = next(node for node in result.nodes if node.name == "login")

    assert login_node.type == "function"
    assert login_node.qualified_name == "app.api.auth.login"
    assert login_node.file_path == "app/api/auth.py"
    assert login_node.start_line > 0
    assert login_node.end_line >= login_node.start_line
    assert login_node.signature == "login(username: str, password: str)"


def test_parse_service_file_finds_user_service_class_and_method() -> None:
    """能识别 UserService 类和 authenticate 方法。"""
    result = parse_python_file(
        SAMPLE_REPO / "app" / "services" / "user_service.py",
        SAMPLE_REPO,
    )
    nodes = {node.qualified_name: node for node in result.nodes}

    class_node = nodes["app.services.user_service.UserService"]
    method_node = nodes["app.services.user_service.UserService.authenticate"]

    assert class_node.type == "class"
    assert class_node.name == "UserService"
    assert method_node.type == "method"
    assert method_node.signature == "authenticate(self, username: str, password: str)"
    assert method_node.start_line > class_node.start_line
    assert method_node.end_line <= class_node.end_line


def test_parse_file_extracts_imports() -> None:
    """支持 from x.y import z 形式的 import 抽取。"""
    result = parse_python_file(SAMPLE_REPO / "app" / "api" / "auth.py", SAMPLE_REPO)
    imports = {(item.import_type, item.module, item.name) for item in result.imports}

    assert ("from", "fastapi", "APIRouter") in imports
    assert ("from", "app.services.user_service", "authenticate_user") in imports


def test_parse_file_extracts_plain_imports(tmp_path: Path) -> None:
    """支持 import os 和 import x.y 形式。"""
    source_file = tmp_path / "imports.py"
    source_file.write_text(
        "import os\nimport package.module as mod\n",
        encoding="utf-8",
    )

    result = parse_python_file(source_file, tmp_path)
    imports = {(item.import_type, item.module, item.name, item.alias) for item in result.imports}

    assert ("import", "os", "os", None) in imports
    assert ("import", "package.module", "package.module", "mod") in imports


def test_parse_file_extracts_decorators() -> None:
    """支持 FastAPI 风格 decorator 表达式抽取。"""
    result = parse_python_file(SAMPLE_REPO / "app" / "api" / "auth.py", SAMPLE_REPO)
    login_node = next(node for node in result.nodes if node.name == "login")

    assert 'router.post("/login")' in login_node.decorators


def test_parse_file_extracts_app_get_and_simple_decorator(tmp_path: Path) -> None:
    """支持 app.get 表达式和普通 decorator 名称。"""
    source_file = tmp_path / "decorators.py"
    source_file.write_text(
        "@app.get(\"/health\")\n"
        "@some_decorator\n"
        "def health():\n"
        "    return {'status': 'ok'}\n",
        encoding="utf-8",
    )

    result = parse_python_file(source_file, tmp_path)
    health_node = next(node for node in result.nodes if node.name == "health")

    assert 'app.get("/health")' in health_node.decorators
    assert "some_decorator" in health_node.decorators


def test_parse_async_function_and_method_metadata(tmp_path: Path) -> None:
    """async def 函数和方法应保留正确签名、类型、名称和行号。"""
    source_file = tmp_path / "async_sample.py"
    source_file.write_text(
        "async def fetch_user(user_id: int) -> dict:\n"
        "    return {'id': user_id}\n"
        "\n"
        "class AsyncService:\n"
        "    async def authenticate(self, token: str) -> bool:\n"
        "        return token == 'ok'\n",
        encoding="utf-8",
    )

    result = parse_python_file(source_file, tmp_path)
    nodes = {node.qualified_name: node for node in result.nodes}

    function_node = nodes["async_sample.fetch_user"]
    method_node = nodes["async_sample.AsyncService.authenticate"]

    assert function_node.type == "function"
    assert function_node.name == "fetch_user"
    assert function_node.signature == "fetch_user(user_id: int)"
    assert function_node.start_line == 1
    assert function_node.end_line == 2

    assert method_node.type == "method"
    assert method_node.name == "authenticate"
    assert method_node.signature == "authenticate(self, token: str)"
    assert method_node.start_line == 5
    assert method_node.end_line == 6


def test_syntax_error_is_returned_without_crashing(tmp_path: Path) -> None:
    """语法错误文件应返回错误信息，而不是让整个解析流程崩溃。"""
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def broken(:\n", encoding="utf-8")

    result = parse_python_file(bad_file, tmp_path)

    assert result.nodes == []
    assert result.imports == []
    assert len(result.errors) == 1
    assert result.errors[0].message
