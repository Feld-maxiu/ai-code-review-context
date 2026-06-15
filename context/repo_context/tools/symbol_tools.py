from typing import Any

from repo_context.service.context_service import ContextService


def search_symbol(
    service: ContextService,
    keyword: str,
    limit: int = 20,
    task_id: str | None = None,
    review_dimension: str | None = None,
) -> list[dict[str, Any]]:
    return service.search_symbol(
        keyword=keyword,
        limit=limit,
        task_id=task_id,
        review_dimension=review_dimension,
    )


def get_node_detail(
    service: ContextService,
    node_id: str,
    include_source: bool = False,
    task_id: str | None = None,
    review_dimension: str | None = None,
) -> dict[str, Any] | None:
    return service.get_node_detail(
        node_id=node_id,
        include_source=include_source,
        task_id=task_id,
        review_dimension=review_dimension,
    )
