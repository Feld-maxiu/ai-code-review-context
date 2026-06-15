import inspect
import json
import shutil
from pathlib import Path

import pytest

import repo_context.task.review_task_generator as task_generator_module
from repo_context.index.index_builder import build_index
from repo_context.service.context_service import ContextService
from repo_context.task.review_task_generator import ReviewTaskGenerator


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


def test_sample_repo_generates_repo_summary(task_generator: ReviewTaskGenerator) -> None:
    """sample_repo 能生成 repo_summary。"""
    plan = task_generator.generate()
    summary = plan.repo_summary.to_dict()

    assert summary["repo_id"] == "sample-repo"
    assert summary["framework"] == "fastapi"
    assert summary["python_files"] > 0
    assert summary["entrypoints"]
    assert "tests/test_auth.py" in summary["test_files"]
    assert "app/config.py" in summary["config_files"]
    assert "app" in summary["main_packages"]
    _assert_json_serializable(summary)


def test_post_login_generates_entrypoint_review(
    task_generator: ReviewTaskGenerator,
) -> None:
    """POST /login 应生成入口点评审任务。"""
    tasks = task_generator.generate().review_tasks
    task = next(item for item in tasks if item.target == "POST /login")

    assert task.task_type == "entrypoint_review"
    assert task.task_id == "task_route_post_login"
    assert task.seed_node_id
    assert "身份认证" in task.review_focus
    assert "app/api/auth.py" in task.related_files


def test_config_py_generates_config_review(task_generator: ReviewTaskGenerator) -> None:
    """config.py 应生成配置评审任务。"""
    tasks = task_generator.generate().review_tasks
    task = next(item for item in tasks if item.target == "app/config.py")

    assert task.task_type == "config_review"
    assert task.priority == "medium"
    assert "敏感信息泄露" in task.review_focus
    assert task.related_files == ["app/config.py"]


def test_services_directory_generates_module_review(
    task_generator: ReviewTaskGenerator,
) -> None:
    """services 目录应生成模块评审任务。"""
    tasks = task_generator.generate().review_tasks
    task = next(item for item in tasks if item.target == "app/services")

    assert task.task_type == "module_review"
    assert task.seed_node_id
    assert "app/services/user_service.py" in task.related_files
    assert "异常处理" in task.review_focus


def test_task_card_fields_are_complete(task_generator: ReviewTaskGenerator) -> None:
    """每张 task_card 都应包含阶段 6 要求字段。"""
    required_fields = {
        "task_id",
        "repo_id",
        "task_type",
        "target",
        "seed_node_id",
        "priority",
        "review_focus",
        "related_files",
        "status",
    }

    for task in task_generator.generate().review_tasks:
        assert required_fields.issubset(task.to_dict())
        assert task.status == "pending"


def test_get_related_context_by_task_id(task_generator: ReviewTaskGenerator) -> None:
    """get_related_context(task_id) 应返回推荐节点和文件。"""
    task = next(
        item
        for item in task_generator.generate().review_tasks
        if item.task_id == "task_route_post_login"
    )

    context = task_generator.get_related_context(task.task_id)

    assert context["task_id"] == task.task_id
    assert context["seed_node_id"] == task.seed_node_id
    assert context["recommended_nodes"]
    assert "app/api/auth.py" in context["related_files"]
    assert "source" not in json.dumps(context, ensure_ascii=False)
    assert context["reason"]
    _assert_json_serializable(context)


def test_generates_coverage_report(task_generator: ReviewTaskGenerator) -> None:
    """任务计划应包含 coverage_report。"""
    report = task_generator.generate().coverage_report

    assert report["repo_id"] == "sample-repo"
    assert report["total_python_files"] > 0
    assert report["total_entrypoints"] >= 1
    assert report["total_config_files"] >= 1
    assert 0 <= report["coverage_ratio"] <= 1
    _assert_json_serializable(report)


def test_coverage_report_lists_covered_and_uncovered_python_files(
    tmp_path: Path,
) -> None:
    """coverage_report 应列出已覆盖和未覆盖 Python 源码文件。"""
    generator = _build_generator_with_extra_files(tmp_path)
    report = generator.generate().coverage_report

    assert "app/api/auth.py" in report["covered_python_files"]
    assert "app/misc/standalone.py" in report["uncovered_python_files"]
    assert isinstance(report["covered_python_files"], list)
    assert isinstance(report["uncovered_python_files"], list)


def test_uncovered_python_file_generates_file_review(tmp_path: Path) -> None:
    """未被基础任务覆盖的普通 Python 文件应生成 file_review。"""
    generator = _build_generator_with_extra_files(tmp_path)
    tasks = generator.generate().review_tasks
    task = next(item for item in tasks if item.target == "app/misc/standalone.py")

    assert task.task_id == "task_file_app_misc_standalone_py"
    assert task.task_type == "file_review"
    assert task.priority == "low"
    assert task.related_files == ["app/misc/standalone.py"]


def test_test_file_does_not_generate_fallback_file_review(tmp_path: Path) -> None:
    """测试文件只进入 test_files 统计，不生成 fallback file_review。"""
    generator = _build_generator_with_extra_files(tmp_path)
    tasks = generator.generate().review_tasks

    assert not any(item.target == "tests/test_extra.py" for item in tasks)
    assert "tests/test_extra.py" in generator.generate().repo_summary.test_files


def test_file_review_task_card_fields_are_complete(tmp_path: Path) -> None:
    """file_review 也必须包含完整 task_card 字段。"""
    generator = _build_generator_with_extra_files(tmp_path)
    task = next(
        item
        for item in generator.generate().review_tasks
        if item.task_type == "file_review"
    )
    required_fields = {
        "task_id",
        "repo_id",
        "task_type",
        "target",
        "seed_node_id",
        "priority",
        "review_focus",
        "related_files",
        "status",
    }

    assert required_fields.issubset(task.to_dict())
    assert task.review_focus == ["基础代码质量", "异常处理", "输入输出边界", "可维护性"]
    assert task.status == "pending"


def test_get_related_context_supports_file_review(tmp_path: Path) -> None:
    """get_related_context(task_id) 应支持 file_review 且不返回源码正文。"""
    generator = _build_generator_with_extra_files(tmp_path)
    task = next(
        item
        for item in generator.generate().review_tasks
        if item.task_type == "file_review"
    )

    context = generator.get_related_context(task.task_id)

    assert context["task_id"] == task.task_id
    assert context["seed_node_id"] == task.seed_node_id
    assert "app/misc/standalone.py" in context["related_files"]
    assert context["reason"]
    assert "source" not in json.dumps(context, ensure_ascii=False)
    _assert_json_serializable(context)


def test_repeated_generation_does_not_duplicate_tasks(
    tmp_path: Path,
) -> None:
    """重复运行任务生成不会产生重复 repo_id + task_type + target。"""
    generator = _build_generator_with_extra_files(tmp_path)
    tasks = [*generator.generate().review_tasks, *generator.generate().review_tasks]
    unique_keys = {(item.repo_id, item.task_type, item.target) for item in tasks}

    assert len(generator.generate().review_tasks) == len(
        {
            (item.repo_id, item.task_type, item.target)
            for item in generator.generate().review_tasks
        }
    )
    assert len(unique_keys) == len(generator.generate().review_tasks)


def test_repeated_generation_keeps_task_ids_stable(tmp_path: Path) -> None:
    """重复运行任务生成时 task_id 顺序和内容保持稳定。"""
    generator = _build_generator_with_extra_files(tmp_path)

    first = [item.task_id for item in generator.generate().review_tasks]
    second = [item.task_id for item in generator.generate().review_tasks]

    assert first == second
    assert "task_route_post_login" in first
    assert "task_file_app_misc_standalone_py" in first


def test_review_task_generation_does_not_call_llm() -> None:
    """任务生成源码不应引用 LLM 或 OpenAI 调用。"""
    source = inspect.getsource(task_generator_module).lower()

    assert "openai" not in source
    assert "llm" not in source
    assert "chatcompletion" not in source


def _assert_json_serializable(value: object) -> None:
    json.dumps(value, ensure_ascii=False)


def _build_task_generator(tmp_path: Path) -> ReviewTaskGenerator:
    db_path = tmp_path / "context.db"
    build_index("sample-repo", SAMPLE_REPO, db_path)
    return ReviewTaskGenerator(
        ContextService(
            repo_id="sample-repo",
            repo_root=SAMPLE_REPO,
            db_path=db_path,
        )
    )


def _build_generator_with_extra_files(tmp_path: Path) -> ReviewTaskGenerator:
    repo_copy = tmp_path / "sample_repo_extra"
    shutil.copytree(SAMPLE_REPO, repo_copy)
    (repo_copy / "app" / "misc").mkdir()
    (repo_copy / "app" / "misc" / "standalone.py").write_text(
        "def standalone(value: str) -> str:\n"
        "    return value.strip()\n",
        encoding="utf-8",
    )
    (repo_copy / "tests" / "test_extra.py").write_text(
        "def test_extra():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "extra_context.db"
    build_index("sample-repo-extra", repo_copy, db_path)
    return ReviewTaskGenerator(
        ContextService(
            repo_id="sample-repo-extra",
            repo_root=repo_copy,
            db_path=db_path,
        )
    )


@pytest.fixture()
def task_generator(tmp_path: Path) -> ReviewTaskGenerator:
    return _build_task_generator(tmp_path)
