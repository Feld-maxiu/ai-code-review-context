from typing import Any

from repo_context.service.context_service import ContextService


def get_file_snippet(
    service: ContextService,
    file_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    task_id: str | None = None,
    review_dimension: str | None = None,
) -> dict[str, Any]:
    return service.get_file_snippet(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        task_id=task_id,
        review_dimension=review_dimension,
    )
