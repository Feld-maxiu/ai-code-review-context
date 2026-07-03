import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from agent.security_agent.context_client import ContextServiceClient, ContextServiceError


class _RecordingHandler(BaseHTTPRequestHandler):
    calls = []

    def do_GET(self):
        self._record()
        parsed = urlparse(self.path)
        if parsed.path == "/broken":
            self._send(500, {"error": "boom"})
            return
        self._send(200, {"path": parsed.path, "query": parse_qs(parsed.query)})

    def do_POST(self):
        self._record()
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        self._send(200, {"path": urlparse(self.path).path, "body": json.loads(body)})

    def log_message(self, *_args):
        return

    def _record(self):
        _RecordingHandler.calls.append(self.path)

    def _send(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ContextServiceClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)

    def setUp(self):
        _RecordingHandler.calls.clear()
        self.client = ContextServiceClient(self.base_url, timeout=2)

    def test_get_methods_use_documented_paths_and_usage_params(self):
        package = self.client.get_task_package("task_login", "repo-1")
        graph = self.client.get_task_graph_slice("task_login", "repo-1", depth=2)
        snippet = self.client.get_file_snippet(
            "repo-1", "app/auth.py", 1, 20,
            task_id="task_login", review_dimension="security"
        )
        node = self.client.get_node_detail(
            "repo-1", "login",
            task_id="task_login", review_dimension="security"
        )
        callees = self.client.get_callees(
            "repo-1", "login", depth=1,
            task_id="task_login", review_dimension="security"
        )
        callers = self.client.get_callers(
            "repo-1", "authenticate", depth=1,
            task_id="task_login", review_dimension="security"
        )

        self.assertEqual(package["path"], "/context/task-package/task_login")
        self.assertEqual(graph["path"], "/context/tasks/task_login/graph-slice")
        self.assertEqual(snippet["query"]["task_id"], ["task_login"])
        self.assertEqual(snippet["query"]["review_dimension"], ["security"])
        self.assertEqual(node["query"]["symbol_name"], ["login"])
        self.assertEqual(callees["path"], "/context/callees")
        self.assertEqual(callers["path"], "/context/callers")

    def test_post_methods_send_documented_json_bodies(self):
        index = self.client.build_index(
            repo_id="repo-1",
            repo_path="tests/fixtures/sample_repo",
            db_path=".demo_data/repo-1.db",
        )
        related = self.client.get_related_context(
            repo_id="repo-1",
            task_id="task_login",
            target_file="app/auth.py",
            review_dimension="security",
            tags=["api_entry", "auth"],
            max_depth=1,
            max_files=3,
        )
        feedback = self.client.post_task_feedback(
            repo_id="repo-1",
            task_id="task_login",
            agent="security-review-agent",
            status="blocked",
            context_sufficient=False,
            feedback_type="context_request",
            message="missing callers",
            need_more_context=True,
            requested_context=[{"type": "callers", "symbol_name": "authenticate", "depth": 2}],
            downstream_result_ref=None,
        )

        self.assertEqual(index["path"], "/context/index")
        self.assertEqual(index["body"]["repo_id"], "repo-1")
        self.assertEqual(index["body"]["repo_path"], "tests/fixtures/sample_repo")
        self.assertEqual(index["body"]["db_path"], ".demo_data/repo-1.db")
        self.assertEqual(related["path"], "/context/related-context")
        self.assertEqual(related["body"]["max_files"], 3)
        self.assertEqual(feedback["path"], "/context/task-feedback")
        self.assertEqual(feedback["body"]["status"], "blocked")
        self.assertTrue(feedback["body"]["need_more_context"])

    def test_list_tasks_uses_documented_path_and_query_params(self):
        tasks = self.client.list_tasks(
            repo_id="repo-1",
            review_dimension="security",
        )
        self.assertEqual(tasks["path"], "/context/tasks")
        self.assertEqual(tasks["query"]["repo_id"], ["repo-1"])
        self.assertEqual(tasks["query"]["review_dimension"], ["security"])

    def test_http_errors_raise_context_service_error(self):
        with self.assertRaises(ContextServiceError):
            self.client._request("GET", "/broken")


if __name__ == "__main__":
    unittest.main()
