CREATE TABLE IF NOT EXISTS code_files (
    repo_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL,
    language TEXT NOT NULL,
    line_count INTEGER NOT NULL,
    is_test INTEGER NOT NULL,
    PRIMARY KEY (repo_id, file_path)
);

CREATE TABLE IF NOT EXISTS code_nodes (
    repo_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT NOT NULL,
    decorators TEXT NOT NULL,
    PRIMARY KEY (repo_id, node_id),
    FOREIGN KEY (repo_id, file_path)
        REFERENCES code_files (repo_id, file_path)
);

CREATE TABLE IF NOT EXISTS code_edges (
    repo_id TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    PRIMARY KEY (repo_id, source_node_id, target_node_id, edge_type)
);

CREATE TABLE IF NOT EXISTS review_tasks (
    repo_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (repo_id, task_id)
);

CREATE TABLE IF NOT EXISTS context_usage (
    repo_id TEXT NOT NULL,
    usage_id TEXT NOT NULL,
    task_id TEXT,
    tool_name TEXT NOT NULL,
    node_id TEXT,
    file_path TEXT,
    agent TEXT,
    review_dimension TEXT,
    target_type TEXT,
    target_name TEXT,
    start_line INTEGER,
    end_line INTEGER,
    lines_returned INTEGER,
    used_at TEXT NOT NULL,
    PRIMARY KEY (repo_id, usage_id)
);
