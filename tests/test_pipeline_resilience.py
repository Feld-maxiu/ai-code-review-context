import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import run_agent
from agent.security_agent.agents.fuzzer import FuzzerAgent
from agent.security_agent.main import SecurityAgentOrchestrator
from agent.security_agent.main import PipelineResult
from agent.security_agent.models import CodeDiff, PreprocessedContext, VulnerabilityReport


class PipelineResilienceTests(unittest.TestCase):
    def test_pipeline_falls_back_when_openai_package_is_unavailable(self):
        original_openai = sys.modules.pop("openai", None)
        try:
            agent = SecurityAgentOrchestrator(api_key="dummy", enable_fuzzing=False)
            context = PreprocessedContext(
                diffs=[CodeDiff(
                    file_path="sample.c",
                    additions=[{"line": 1, "content": "printf(userInput);"}],
                    deletions=[],
                    raw_diff="+ printf(userInput);",
                )],
                cfgs=[],
                repository_files={"sample.c": "printf(userInput);"},
            )
            result = agent.run_with_preprocessed_context(context)
        finally:
            if original_openai is not None:
                sys.modules["openai"] = original_openai

        self.assertIn("findings", result.summary)

    def test_false_positive_reports_are_not_final_reports_without_fuzzing(self):
        agent = SecurityAgentOrchestrator(api_key="dummy", enable_fuzzing=False)
        raw = VulnerabilityReport(
            file_path="sample.c", line_start=1, line_end=1, cwe_id="CWE-134",
            cwe_name="格式化字符串漏洞", severity="high", description="raw",
            code_snippet="printf(userInput);",
        )
        valid = VulnerabilityReport(
            file_path="sample.c", line_start=1, line_end=1, cwe_id="CWE-134",
            cwe_name="格式化字符串漏洞", severity="high", description="valid",
            code_snippet="printf(userInput);", confidence=0.8,
        )
        false_positive = VulnerabilityReport(
            file_path="sample.c", line_start=2, line_end=2, cwe_id="CWE-476",
            cwe_name="空指针解引用", severity="medium", description="fp",
            code_snippet="*p = 1;", confidence=0.0, is_false_positive=True,
        )
        context = PreprocessedContext(
            diffs=[CodeDiff("sample.c", [], [], "diff")],
            cfgs=[],
            repository_files={"sample.c": "printf(userInput);"},
        )

        agent.detector.analyze = lambda _context: [raw]

        def fake_verify(_reports, _context):
            agent.working_memory.store_verifier_results([valid, false_positive])
            return [valid, false_positive]

        agent.verifier.verify = fake_verify

        result = agent.run_with_preprocessed_context(context)

        self.assertEqual(result.verifier_filtered, 1)
        self.assertEqual(result.verifier_confirmed, 1)
        self.assertEqual(result.final_reports, [valid])

    def test_fuzzer_without_binary_does_not_claim_confirmation(self):
        fuzzer = FuzzerAgent(llm_client=None, semantic_memory=None, working_memory=None)
        vuln = VulnerabilityReport(
            file_path="sample.c", line_start=1, line_end=1, cwe_id="CWE-120",
            cwe_name="缓冲区溢出", severity="high", description="overflow",
            code_snippet="strcpy(buf, input);",
        )

        result = fuzzer._execute_directed_fuzzing(
            vuln=vuln,
            seeds=[b"AAAA"],
            trigger_info={"input_format": "binary"},
            target_binary=None,
        )

        self.assertEqual(result["status"], "unverified")
        self.assertIn("target_binary_not_provided", result["coverage"]["reason"])

    def test_load_agent_input_from_stdin(self):
        payload = {
            "task_id": "task_route_post_login",
            "repo_id": "sample-repo",
            "review_dimension": "security",
            "agent_name": "security-agent",
            "context_api_base_url": "http://context-service:8080",
            "security_config": {
                "cwe_focus": ["CWE-89", "CWE-918"],
                "enable_dynamic_validation": False,
                "sarif_output": True,
            },
        }

        loaded = run_agent.load_agent_input(
            input_file=None,
            stdin=io.StringIO(json.dumps(payload)),
        )

        self.assertEqual(loaded["agent_name"], "security-agent")
        self.assertEqual(loaded["context_api_base_url"], "http://context-service:8080")
        self.assertEqual(loaded["security_config"]["cwe_focus"], ["CWE-89", "CWE-918"])
        self.assertFalse(loaded["security_config"]["enable_dynamic_validation"])
        self.assertTrue(loaded["security_config"]["sarif_output"])

    def test_load_agent_input_applies_defaults(self):
        payload = {
            "task_id": "task_route_post_login",
            "repo_id": "sample-repo",
            "review_dimension": "security",
            "agent_name": "security-agent",
            "context_api_base_url": "http://context-service:8080",
        }

        loaded = run_agent.load_agent_input(
            input_file=None,
            stdin=io.StringIO(json.dumps(payload)),
        )

        self.assertEqual(loaded["security_config"]["cwe_focus"], [])
        self.assertFalse(loaded["security_config"]["enable_dynamic_validation"])
        self.assertFalse(loaded["security_config"]["sarif_output"])

    def test_cli_supports_input_file_and_output_only(self):
        argv = [
            "run_agent.py",
            "--input-file", "payload.json",
            "--output", "result.json",
        ]

        with patch.object(sys, "argv", argv):
            args = run_agent.parse_args()

        self.assertEqual(args.input_file, "payload.json")
        self.assertEqual(args.output, "result.json")

    def test_export_results_matches_security_output_contract(self):
        output_path = Path("tmp_security_output.json")
        sarif_path = Path("tmp_security_output.sarif.json")
        output_path.unlink(missing_ok=True)
        sarif_path.unlink(missing_ok=True)

        try:
            agent = SecurityAgentOrchestrator(api_key="", enable_fuzzing=False)
            result = PipelineResult()
            result.execution_time = 1.23
            result.stage_times = {"preprocess": 0.0, "detector": 0.5, "verifier": 0.4, "fuzzer": 0.0}
            result.final_reports = [
                VulnerabilityReport(
                    file_path="src/payment/api.py",
                    line_start=22,
                    line_end=22,
                    cwe_id="CWE-89",
                    cwe_name="SQL注入",
                    severity="critical",
                    description="用户输入直接拼接到SQL查询中",
                    code_snippet="query = f\"SELECT * FROM payments WHERE amount={amount}\"",
                    confidence=0.95,
                    runtime_status="unverified",
                ),
                VulnerabilityReport(
                    file_path="src/payment/api.py",
                    line_start=24,
                    line_end=24,
                    cwe_id="CWE-918",
                    cwe_name="SSRF",
                    severity="high",
                    description="外部请求参数存在风险",
                    code_snippet="requests.get(...)",
                    confidence=0.70,
                    runtime_status="unverified",
                ),
            ]
            result.summary = agent._build_summary(result, result.stage_times)

            agent.export_results(
                result,
                str(output_path),
                metadata={
                    "task_id": "task_route_post_login",
                    "repo_id": "sample-repo",
                    "review_dimension": "security",
                    "agent_name": "security-agent",
                    "security_config": {
                        "cwe_focus": ["CWE-89"],
                        "enable_dynamic_validation": False,
                        "sarif_output": True,
                    },
                    "status": "completed",
                },
            )

            exported = json.loads(output_path.read_text(encoding="utf-8"))
        finally:
            output_path.unlink(missing_ok=True)
            sarif_path.unlink(missing_ok=True)

        self.assertEqual(exported["task_id"], "task_route_post_login")
        self.assertEqual(exported["agent"], "security-agent")
        self.assertEqual(exported["status"], "completed")
        # 统一外层格式字段
        self.assertIn("dimension", exported)
        self.assertIn("scan_id", exported)
        self.assertIn("snapshot_id", exported)
        self.assertIn("findings", exported)
        self.assertIn("message", exported)
        self.assertEqual(exported["summary"]["total_findings"], 1)
        self.assertEqual(exported["findings"][0]["category"], "CWE-89")
        self.assertEqual(exported["findings"][0]["severity"], "critical")
        self.assertIn("title", exported["findings"][0])
        self.assertIn("description", exported["findings"][0])
        self.assertIn("location", exported["findings"][0])
        self.assertIn("verification", exported["findings"][0])
        self.assertIsInstance(exported["findings"][0]["evidence"], list)
        self.assertIn("suggestion", exported["findings"][0])
        self.assertIn("requires_human_review", exported["findings"][0])
        self.assertIn("security_standard", exported["findings"][0])
        self.assertIn("artifacts", exported)
        self.assertTrue(exported["artifacts"]["sarif_report"])
        self.assertTrue(exported["block_merge"])


if __name__ == "__main__":
    unittest.main()
