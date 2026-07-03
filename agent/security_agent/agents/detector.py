import json
from typing import List, Optional
from ..models import VulnerabilityReport, PreprocessedContext, ControlFlowGraph, CodeDiff
from ..memory.semantic_memory import SemanticMemory
from ..memory.working_memory import WorkingMemory
from ..utils.llm_client import LLMClient


class DetectorAgent:
    def __init__(
        self,
        llm_client: LLMClient,
        semantic_memory: SemanticMemory,
        working_memory: WorkingMemory
    ):
        self.llm_client = llm_client
        self.semantic_memory = semantic_memory
        self.working_memory = working_memory

    def analyze(
        self,
        context: PreprocessedContext,
        top_k_rules: int = 5
    ) -> List[VulnerabilityReport]:
        reports = []

        for diff in context.diffs:
            file_name = diff.file_path
            related_cfgs = [c for c in context.cfgs if c.file_path == file_name]

            diff_text = self._format_diff_for_analysis(diff)
            cfg_text = self._format_cfgs_for_analysis(related_cfgs)

            rules = self.semantic_memory.search_sast_rules(
                query=diff_text,
                top_k=top_k_rules
            )

            if not rules:
                continue

            diff_reports = self._analyze_with_rules(
                diff_text=diff_text,
                cfg_text=cfg_text,
                rules=rules,
                file_name=file_name
            )
            reports.extend(diff_reports)

        self.working_memory.store_detector_results(reports)
        return reports

    def _format_diff_for_analysis(self, diff: CodeDiff) -> str:
        parts = [f"文件: {diff.file_path}"]
        parts.append("新增代码:")
        for addition in diff.additions:
            line_no = addition.get("line", "?")
            content = addition.get("content", "")
            parts.append(f"  +{line_no}: {content}")

        parts.append("删除代码:")
        for deletion in diff.deletions:
            line_no = deletion.get("line", "?")
            content = deletion.get("content", "")
            parts.append(f"  -{line_no}: {content}")

        if diff.raw_diff:
            parts.append(f"\n原始Diff:\n{diff.raw_diff}")

        return "\n".join(parts)

    def _format_cfgs_for_analysis(self, cfgs: List[ControlFlowGraph]) -> str:
        if not cfgs:
            return "无CFG信息"
        parts = []
        for cfg in cfgs:
            parts.append(f"函数: {cfg.function_name}")
            parts.append(f"节点数: {len(cfg.nodes)}, 边数: {len(cfg.edges)}")
            parts.append("控制流路径:")
            for node in cfg.nodes:
                parts.append(f"  [{node.id}] {node.label} (行{node.line_start}-{node.line_end})")
                if node.successors:
                    parts.append(f"    -> {node.successors}")
            parts.append("")
        return "\n".join(parts)

    def _analyze_with_rules(
        self,
        diff_text: str,
        cfg_text: str,
        rules: List[dict],
        file_name: str
    ) -> List[VulnerabilityReport]:
        rules_text = json.dumps(rules, indent=2, ensure_ascii=False)

        system_prompt = """你是一个具备SAST领域知识的专业安全代码审查者(Agent Detector)。
你的任务是:
1. 分析代码Diff和CFG，结合SAST规则识别安全漏洞
2. 对每个发现的漏洞提供行级精确定位
3. 使用标准CWE分类标记漏洞类型
4. 输出结构化的审查评论

输出格式为JSON数组，每个元素包含:
{
  "cwe_id": "CWE-ID",
  "cwe_name": "漏洞类型名称",
  "severity": "critical/high/medium/low",
  "line_start": 行号,
  "line_end": 行号,
  "description": "漏洞描述",
  "code_snippet": "相关代码片段",
  "confidence": 0.0-1.0,
  "rule_id": "匹配的SAST规则ID"
}

注意:
- 只报告确实存在的漏洞，不要过度报告
- 如果代码没有安全问题，返回空数组[]
- 结合CFG分析数据流和控制流路径"""

        prompt = f"""请分析以下代码变更中的安全漏洞。

## 代码Diff:
{diff_text}

## 控制流图(CFG):
{cfg_text}

## 相关SAST规则:
{rules_text}

请基于SAST规则分析上述代码，输出发现的漏洞列表(JSON格式)。"""

        try:
            result = self.llm_client.chat_with_json_output(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.0
            )

            if isinstance(result, dict):
                result = result.get("vulnerabilities", result.get("findings", [result]))
            if not isinstance(result, list):
                result = [result] if result else []

            reports = []
            for item in result:
                if not isinstance(item, dict):
                    continue
                report = VulnerabilityReport(
                    file_path=file_name,
                    line_start=item.get("line_start", 0),
                    line_end=item.get("line_end", 0),
                    cwe_id=item.get("cwe_id", ""),
                    cwe_name=item.get("cwe_name", ""),
                    severity=item.get("severity", "medium"),
                    description=item.get("description", ""),
                    code_snippet=item.get("code_snippet", ""),
                    confidence=item.get("confidence", 0.7)
                )
                reports.append(report)

            return reports

        except Exception as e:
            return self._fallback_pattern_match(diff_text, rules, file_name)

    def _fallback_pattern_match(
        self,
        diff_text: str,
        rules: List[dict],
        file_name: str
    ) -> List[VulnerabilityReport]:
        reports = []
        diff_lower = diff_text.lower()

        for rule in rules:
            name = rule.get("name", "").lower()
            pattern = rule.get("pattern", "").lower()
            description = rule.get("description", "").lower()
            examples = rule.get("examples", [])

            score = 0
            if name and name in diff_lower:
                score += 2
            if pattern and pattern in diff_lower:
                score += 1
            if description and any(w in diff_lower for w in description.split()[:5]):
                score += 1
            for ex in examples:
                if isinstance(ex, str) and ex[:50].lower() in diff_lower:
                    score += 1

            if score >= 2:
                report = VulnerabilityReport(
                    file_path=file_name,
                    line_start=0,
                    line_end=0,
                    cwe_id=rule.get("cwe_id", ""),
                    cwe_name=rule.get("name", ""),
                    severity=rule.get("severity", "medium"),
                    description=rule.get("description", ""),
                    code_snippet="",
                    confidence=min(0.5, score / 5.0)
                )
                reports.append(report)

        return reports