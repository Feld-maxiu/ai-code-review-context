import json
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ContextServiceError(RuntimeError):
    pass


class ContextServiceClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def build_index(
        self,
        repo_id: str,
        repo_path: str,
        db_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """调用 POST /context/index 为指定仓库构建上下文索引。"""

        json_body = {
            "repo_id": repo_id,
            "repo_path": repo_path,
        }
        if db_path is not None:
            json_body["db_path"] = db_path
        return self._request("POST", "/context/index", json_body=json_body)

    def list_tasks(
        self,
        repo_id: str,
        review_dimension: str,
    ) -> Dict[str, Any]:
        """按评审维度调用 GET /context/tasks 查询可领取任务列表。"""

        return self._request(
            "GET",
            "/context/tasks",
            query={"repo_id": repo_id, "review_dimension": review_dimension},
        )

    def get_task_package(self, task_id: str, repo_id: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"/context/task-package/{task_id}",
            query={"repo_id": repo_id},
        )

    def get_task_graph_slice(
        self,
        task_id: str,
        repo_id: str,
        depth: int = 2,
    ) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"/context/tasks/{task_id}/graph-slice",
            query={"repo_id": repo_id, "depth": depth},
        )

    def get_related_context(
        self,
        repo_id: str,
        task_id: str,
        target_file: str,
        review_dimension: str,
        tags: Optional[List[str]] = None,
        max_depth: int = 1,
        max_files: int = 3,
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/context/related-context",
            json_body={
                "repo_id": repo_id,
                "task_id": task_id,
                "target_file": target_file,
                "review_dimension": review_dimension,
                "tags": tags or [],
                "max_depth": max_depth,
                "max_files": max_files,
            },
        )

    def get_file_snippet(
        self,
        repo_id: str,
        file_path: str,
        start_line: int,
        end_line: int,
        task_id: Optional[str] = None,
        review_dimension: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = {
            "repo_id": repo_id,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
        }
        self._add_usage_params(query, task_id, review_dimension)
        return self._request("GET", "/context/file-snippet", query=query)

    def get_node_detail(
        self,
        repo_id: str,
        symbol_name: str,
        task_id: Optional[str] = None,
        review_dimension: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = {"repo_id": repo_id, "symbol_name": symbol_name}
        self._add_usage_params(query, task_id, review_dimension)
        return self._request("GET", "/context/node-detail", query=query)

    def get_callees(
        self,
        repo_id: str,
        symbol_name: str,
        depth: int = 1,
        task_id: Optional[str] = None,
        review_dimension: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = {"repo_id": repo_id, "symbol_name": symbol_name, "depth": depth}
        self._add_usage_params(query, task_id, review_dimension)
        return self._request("GET", "/context/callees", query=query)

    def get_callers(
        self,
        repo_id: str,
        symbol_name: str,
        depth: int = 1,
        task_id: Optional[str] = None,
        review_dimension: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = {"repo_id": repo_id, "symbol_name": symbol_name, "depth": depth}
        self._add_usage_params(query, task_id, review_dimension)
        return self._request("GET", "/context/callers", query=query)

    def post_task_feedback(
        self,
        repo_id: str,
        task_id: str,
        agent: str,
        status: str,
        context_sufficient: bool,
        feedback_type: str,
        message: str = "",
        need_more_context: bool = False,
        requested_context: Optional[List[Dict[str, Any]]] = None,
        downstream_result_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/context/task-feedback",
            json_body={
                "repo_id": repo_id,
                "task_id": task_id,
                "agent": agent,
                "status": status,
                "context_sufficient": context_sufficient,
                "feedback_type": feedback_type,
                "message": message,
                "need_more_context": need_more_context,
                "requested_context": requested_context or [],
                "downstream_result_ref": downstream_result_ref,
            },
        )

    def _request(
        self,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        query_string = urlencode(query or {}, doseq=True)
        url = f"{self.base_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"

        data = None
        headers = {"Accept": "application/json"}
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
                return self._parse_json(payload, url)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ContextServiceError(
                f"Context service HTTP {exc.code} for {method} {url}: {body}"
            ) from exc
        except URLError as exc:
            raise ContextServiceError(
                f"Context service request failed for {method} {url}: {exc.reason}"
            ) from exc

    def _parse_json(self, payload: str, url: str) -> Dict[str, Any]:
        if not payload:
            return {}
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ContextServiceError(
                f"Context service returned invalid JSON for {url}: {payload[:200]}"
            ) from exc
        if not isinstance(data, dict):
            raise ContextServiceError(
                f"Context service returned non-object JSON for {url}"
            )
        return data

    def _add_usage_params(
        self,
        query: Dict[str, Any],
        task_id: Optional[str],
        review_dimension: Optional[str],
    ):
        if task_id:
            query["task_id"] = task_id
        if review_dimension:
            query["review_dimension"] = review_dimension
