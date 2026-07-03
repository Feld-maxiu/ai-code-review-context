import json
from typing import Any, Dict, List, Optional
from ..models import VulnerabilityReport, PreprocessedContext, ControlFlowGraph
from ..memory.semantic_memory import SemanticMemory
from ..memory.working_memory import WorkingMemory
from ..utils.llm_client import LLMClient
from ..knowledge.cwe_tree import CWETree


class VerifierAgent:
    def __init__(
        self,
        llm_client: LLMClient,
        semantic_memory: SemanticMemory,
        working_memory: WorkingMemory,
        cwe_tree: Optional[CWETree] = None
    ):
        self.llm_client = llm_client
        self.semantic_memory = semantic_memory
        self.working_memory = working_memory
        self.cwe_tree = cwe_tree

    def verify(
        self,
        detector_reports: List[VulnerabilityReport],
        context: PreprocessedContext
    ) -> List[VulnerabilityReport]:
        if not detector_reports:
            return []

        verified_reports = []
        for report in detector_reports:
            verified = self._verify_single_report(report, context)
            if verified is not None:
                verified_reports.append(verified)

        self.working_memory.store_verifier_results(verified_reports)
        return verified_reports

    def _verify_single_report(
        self,
        report: VulnerabilityReport,
        context: PreprocessedContext
    ) -> Optional[VulnerabilityReport]:
        if self.cwe_tree:
            return self._cwe_tree_verify(report, context)
        return self._llm_verify(report, context)

    def _cwe_tree_verify(
        self,
        report: VulnerabilityReport,
        context: PreprocessedContext
    ) -> Optional[VulnerabilityReport]:
        cwe_path = self.cwe_tree.get_full_path(report.cwe_id)
        if not cwe_path:
            report.is_false_positive = True
            report.filter_reason = f"CWE树中未找到对应条目: {report.cwe_id}"
            report.confidence = 0.0
            return report

        related_cfgs = [c for c in context.cfgs if c.file_path == report.file_path]
        target_node = None
        for cfg in related_cfgs:
            node = cfg.find_node_by_line(report.line_start)
            if node:
                target_node = node
                break

        paths_exist = False
        if target_node:
            for cfg in related_cfgs:
                paths = cfg.find_path_to(target_node.id)
                if paths:
                    paths_exist = True
                    break

        entry = self.cwe_tree.get_entry(report.cwe_id)
        env_deps = entry.get("environment_dependencies", []) if entry else []

        cwe_context = {
            "user_input_reachable": report.confidence > 0.3,
            "code_path_exists": paths_exist,
            "exploit_mitigated": False,
            "environment_satisfied": len(env_deps) == 0,
        }
        for dep in env_deps:
            dep_key = dep.lower().replace(" ", "_")
            cwe_context[dep_key] = True

        validation_result = self.cwe_tree.validate_vulnerability(
            report.cwe_id,
            cwe_context
        )

        if not validation_result.get("valid", False):
            report.is_false_positive = True
            report.filter_reason = validation_result.get("reason", "CWE验证失败")
            report.confidence = validation_result.get("confidence", 0.0)
            return report

        report.confidence = validation_result.get("confidence", report.confidence)
        report.is_false_positive = validation_result.get("is_false_positive", False)
        if report.is_false_positive:
            report.filter_reason = validation_result.get("reason", "环境依赖不满足")
        return report

    def _llm_verify(
        self,
        report: VulnerabilityReport,
        context: PreprocessedContext
    ) -> Optional[VulnerabilityReport]:
        related_cfgs = [c for c in context.cfgs if c.file_path == report.file_path]
        cfg_text = self._format_cfgs(related_cfgs)

        related_diffs = [d for d in context.diffs if d.file_path == report.file_path]
        diff_context = related_diffs[0].raw_diff if related_diffs else ""

        system_prompt = """你是一个基于CWE知识的静态假阳过滤器(Agent Verifier)。
你的任务是验证检测器报告的漏洞是否真实存在。

验证规则:
1. 检查前置条件: 漏洞发生的前置条件是否满足 → 不满足则直接过滤
2. 检查可利用性: 漏洞是否可以被实际利用 → 不可利用则降低置信度
3. 检查环境依赖: 漏洞是否依赖特定环境 → 环境不成立则标记为假阳

输出JSON格式:
{
  "is_valid": true/false,
  "confidence": 0.0-1.0,
  "is_false_positive": true/false,
  "filter_reason": "过滤原因（如果被过滤）",
  "analysis": "分析说明"
}"""

        prompt = f"""请验证以下漏洞报告:

## 漏洞报告:
- CWE: {report.cwe_id} - {report.cwe_name}
- 严重程度: {report.severity}
- 文件: {report.file_path}
- 行号: {report.line_start}-{report.line_end}
- 描述: {report.description}
- 代码片段: {report.code_snippet}

## 控制流图(CFG):
{cfg_text}

## Diff上下文:
{diff_context}

请验证此漏洞的真实性并输出JSON结果。"""

        try:
            result = self.llm_client.chat_with_json_output(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.0
            )

            is_valid = result.get("is_valid", True)
            if not is_valid:
                report.is_false_positive = True
                report.filter_reason = result.get("filter_reason", "LLM验证未通过")
                report.confidence = result.get("confidence", 0.0)
                return report

            report.confidence = result.get("confidence", report.confidence)
            report.is_false_positive = result.get("is_false_positive", False)
            if report.is_false_positive:
                report.filter_reason = result.get("filter_reason", "标记为假阳")

            return report

        except Exception:
            return report

    def _format_cfgs(self, cfgs: List[ControlFlowGraph]) -> str:
        if not cfgs:
            return "无CFG信息"
        parts = []
        for cfg in cfgs:
            parts.append(f"函数: {cfg.function_name} ({cfg.file_path})")
            for node in cfg.nodes:
                parts.append(
                    f"  节点[{node.id}] {node.label} "
                    f"行{node.line_start}-{node.line_end}"
                    f"{' [条件]' if node.is_conditional else ''}"
                    f"{' [入口]' if node.is_entry else ''}"
                )
        return "\n".join(parts)

    def get_high_confidence_vulnerabilities(
        self,
        threshold: float = 0.5
    ) -> List[VulnerabilityReport]:
        results = self.working_memory.get_verifier_results()
        return [r for r in results if r.confidence >= threshold and not r.is_false_positive]

    def get_filtered_count(self) -> int:
        results = self.working_memory.get_verifier_results()
        return len([r for r in results if r.is_false_positive])