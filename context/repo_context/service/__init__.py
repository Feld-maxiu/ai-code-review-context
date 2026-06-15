"""上下文服务层。"""

from repo_context.service.context_service import ContextService

__all__ = ["ContextService", "CoverageService"]


def __getattr__(name: str):
    if name == "CoverageService":
        from repo_context.service.coverage_service import CoverageService

        return CoverageService
    raise AttributeError(name)
