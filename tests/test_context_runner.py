import unittest

from agent.security_agent.context_runner import ContextAgentRunner
from agent.security_agent.main import PipelineResult


class FakeContextClient:
    def __init__(self, fail_related=False, tasks=None):
        self.calls = []
        self.feedback = []
        self.fail_related = fail_related
        self.tasks = tasks or [
            {"task_id": "task_login"},
            {"task_id": "task_signup"},
        ]

    def build_index(self, repo_id, repo_path, db_path=None):
        self.calls.append(("index", repo_id, repo_path, db_path))
        return {"repo_id": repo_id, "repo_summary": {"files": 1}}

    def list_tasks(self, repo_id, review_dimension):
        self.calls.append(("list-tasks", repo_id, review_dimension))
        return {"tasks": self.tasks}

    def get_task_package(self, task_id, repo_id):
        self.calls.append(("task-package", task_id, repo_id))
        return {
            "target": {"file_path": "app/auth.py", "symbol_name": "login"},
            "focus_points": ["input validation"],
            "initial_context": {
                "suggested_next_tool": "get_task_graph_slice",
                "snippets": [
                    {"file_path": "app/auth.py", "start_line": 1, "end_line": 3,
                     "content": "def login(request):\n    return authenticate(request)\n"}
                ],
            },
            "available_tools": ["related-context", "file-snippet", "node-detail"],
            "context_policy": {"max_files": 3},
        }

    def get_task_graph_slice(self, task_id, repo_id, depth=2):
        self.calls.append(("graph-slice", task_id, repo_id, depth))
        return {
            "target": {"file_path": "app/auth.py", "symbol_name": "login"},
            "nodes": [
                {"node_id": "n1", "qualified_name": "login", "file_path": "app/auth.py",
                 "start_line": 1, "end_line": 2, "code": "def login(request): ...", "risk_score": 0.9},
                {"node_id": "n2", "qualified_name": "authenticate", "file_path": "app/auth.py",
                 "start_line": 2, "end_line": 2, "code": "authenticate(request)", "risk_score": 0.3},
            ],
            "edges": [{"source": "n1", "target": "n2"}],
        }

    def get_node_detail(self, repo_id, symbol_name, task_id=None, review_dimension=None):
        self.calls.append(("node-detail", repo_id, symbol_name, task_id, review_dimension))
        return {
            "qualified_name": symbol_name,
            "file_path": "app/auth.py",
            "start_line": 1,
            "end_line": 3,
            "code": "def login(request):\n    return authenticate(request)\n",
            "callers": [],
            "callees": [{"qualified_name": "authenticate"}],
        }

    def get_related_context(self, **kwargs):
        self.calls.append(("related-context", kwargs))
        if self.fail_related:
            raise RuntimeError("context service unavailable")
        return {
            "snippets": [
                {"file_path": "app/auth.py", "start_line": 4, "end_line": 5,
                 "content": "def authenticate(request):\n    return True\n"}
            ],
            "related_symbols": [{"qualified_name": "authenticate"}],
            "call_graph_slice": {"nodes": [], "edges": []},
        }

    def post_task_feedback(self, **kwargs):
        self.calls.append(("task-feedback", kwargs))
        self.feedback.append(kwargs)
        return {"accepted": True, "feedback_id": "fb-1"}


class FakeOrchestrator:
    def __init__(self):
        self.contexts = []
        self.exported = []

    def run_with_preprocessed_context(self, context, target_binary=None):
        self.contexts.append((context, target_binary))
        result = PipelineResult()
        result.summary = {"ok": True}
        return result

    def export_results(self, result, output_file, metadata=None):
        self.exported.append((result, output_file, metadata))


class ContextAgentRunnerTests(unittest.TestCase):
    def test_runner_calls_context_tools_in_order_and_posts_completed_feedback(self):
        output = "result.json"
        client = FakeContextClient()
        orchestrator = FakeOrchestrator()

        runner = ContextAgentRunner(client, orchestrator)
        result = runner.run_task(
            repo_id="repo-1",
            task_id="task_login",
            review_dimension="security",
            agent_name="security-agent",
            security_config={"enable_dynamic_validation": False, "sarif_output": False},
            output_file=output,
        )

        self.assertEqual(result.summary, {"ok": True})
        self.assertEqual([call[0] for call in client.calls[:4]], [
            "task-package", "graph-slice", "node-detail", "related-context"
        ])
        self.assertEqual(client.calls[4][0], "task-feedback")
        self.assertEqual(client.feedback[-1]["status"], "completed")
        self.assertEqual(client.feedback[-1]["agent"], "security-agent")
        self.assertTrue(client.feedback[-1]["context_sufficient"])
        self.assertEqual(client.feedback[-1]["downstream_result_ref"], output)
        context = orchestrator.contexts[0][0]
        self.assertEqual(context.diffs[0].file_path, "app/auth.py")
        self.assertIn("authenticate", context.repository_files["app/auth.py"])
        self.assertTrue(context.cfgs)
        self.assertEqual(orchestrator.exported[0][2]["agent_name"], "security-agent")

    def test_runner_posts_blocked_feedback_when_context_expansion_fails(self):
        client = FakeContextClient(fail_related=True)
        orchestrator = FakeOrchestrator()

        runner = ContextAgentRunner(client, orchestrator)
        with self.assertRaises(RuntimeError):
            runner.run_task(
                repo_id="repo-1",
                task_id="task_login",
                review_dimension="security",
                agent_name="security-agent",
                security_config={"enable_dynamic_validation": False, "sarif_output": False},
                output_file="result.json",
            )

        self.assertEqual(client.feedback[-1]["status"], "blocked")
        self.assertFalse(client.feedback[-1]["context_sufficient"])
        self.assertTrue(client.feedback[-1]["need_more_context"])
        self.assertEqual(client.feedback[-1]["requested_context"][0]["type"], "related-context")

    def test_run_tasks_for_repo_processes_all_security_tasks(self):
        client = FakeContextClient()
        orchestrator = FakeOrchestrator()

        runner = ContextAgentRunner(client, orchestrator)
        result = runner.run_tasks_for_repo(
            repo_id="repo-1",
            review_dimension="security",
            agent_name="security-agent",
            output_dir="test_results",
        )

        self.assertEqual(result["repo_id"], "repo-1")
        self.assertEqual(result["review_dimension"], "security")
        self.assertEqual(result["total_tasks"], 2)
        self.assertEqual(len(result["completed"]), 2)
        self.assertEqual(len(result["failed"]), 0)
        self.assertEqual([c["task_id"] for c in result["completed"]], ["task_login", "task_signup"])

    def test_run_tasks_for_repo_continues_when_one_task_fails(self):
        client = FakeContextClient(fail_related=True, tasks=[
            {"task_id": "task_login"},
            {"task_id": "task_signup"},
        ])
        orchestrator = FakeOrchestrator()

        runner = ContextAgentRunner(client, orchestrator)
        result = runner.run_tasks_for_repo(
            repo_id="repo-1",
            review_dimension="security",
            output_dir="test_results",
        )

        self.assertEqual(result["total_tasks"], 2)
        self.assertEqual(len(result["completed"]), 0)
        self.assertEqual(len(result["failed"]), 2)
        self.assertEqual(client.calls[0][0], "list-tasks")


if __name__ == "__main__":
    unittest.main()
