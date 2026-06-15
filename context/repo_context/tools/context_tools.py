from typing import Any

from repo_context.service.context_service import ContextService


def get_related_context(
    service: ContextService,
    task_or_node_id: dict[str, Any] | str | None = None,
    include_source: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    return service.get_related_context(
        task_or_node_id=task_or_node_id,
        include_source=include_source,
        **kwargs,
    )
