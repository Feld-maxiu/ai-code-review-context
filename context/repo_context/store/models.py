from dataclasses import dataclass, field


@dataclass(frozen=True)
class CodeFile:
    repo_id: str
    file_path: str
    file_type: str
    language: str
    line_count: int
    is_test: bool


@dataclass(frozen=True)
class CodeNode:
    repo_id: str
    node_id: str
    type: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str
    decorators: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CodeEdge:
    repo_id: str
    source_node_id: str
    target_node_id: str
    edge_type: str


@dataclass(frozen=True)
class ReviewTask:
    repo_id: str
    task_id: str
    title: str
    status: str


@dataclass(frozen=True)
class ContextUsage:
    repo_id: str
    usage_id: str
    task_id: str | None
    tool_name: str
    node_id: str | None
    file_path: str | None
    used_at: str
    agent: str | None = None
    review_dimension: str | None = None
    target_type: str | None = None
    target_name: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    lines_returned: int | None = None
