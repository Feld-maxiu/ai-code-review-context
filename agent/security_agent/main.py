import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from .models import (
    VulnerabilityReport, CodeDiff, ControlFlowGraph,
    PreprocessedContext
)
from .agents.detector import DetectorAgent
from .agents.verifier import VerifierAgent
from .agents.fuzzer import FuzzerAgent
from .memory.semantic_memory import SemanticMemory
from .memory.working_memory import WorkingMemory
from .knowledge.sast_rules import SASTRuleBase
from .knowledge.cwe_tree import CWETree
from .utils.llm_client import LLMClient
from .utils.cfg_generator import CFGGenerator


class PipelineTimeoutError(TimeoutError):
    """安全 agent 流水线整体超时"""
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("SecurityAgent")


@dataclass
class PipelineResult:
    total_files: int = 0
    total_functions: int = 0
    detector_findings: int = 0
    verifier_filtered: int = 0
    verifier_confirmed: int = 0
    fuzzer_confirmed: int = 0
    fuzzer_suspicious: int = 0
    runtime_verified: int = 0
    final_reports: List[VulnerabilityReport] = field(default_factory=list)
    execution_time: float = 0.0
    stage_times: Dict[str, float] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)


class SecurityAgentOrchestrator:
    def __init__(
        self,
        api_key: str = "",
        api_base: str = "https://api.openai.com/v1",
        model: str = "gpt-4",
        afl_path: str = "afl-fuzz",
        clang_path: str = "clang",
        rules_file: Optional[str] = None,
        cwe_file: Optional[str] = None,
        temperature: float = 0.1,
        enable_fuzzing: bool = True,
        llm_timeout: float = 30.0,
        pipeline_timeout: float = 120.0,
        fuzzer_timeout: float = 60.0,
    ):
        self.enable_fuzzing = enable_fuzzing

        self.llm_client = LLMClient(
            api_key=api_key,
            api_base=api_base,
            model=model,
            temperature=temperature,
            timeout=llm_timeout,
        )
        self.pipeline_timeout = pipeline_timeout

        self.semantic_memory = SemanticMemory(llm_client=self.llm_client)
        self.working_memory = WorkingMemory()

        self.sast_rule_base = SASTRuleBase(rules_file=rules_file)
        self.cwe_tree = CWETree(cwe_file=cwe_file)

        self._init_memory()

        self.cfg_generator = CFGGenerator(clang_path=clang_path)

        self.detector = DetectorAgent(
            llm_client=self.llm_client,
            semantic_memory=self.semantic_memory,
            working_memory=self.working_memory
        )

        self.verifier = VerifierAgent(
            llm_client=self.llm_client,
            semantic_memory=self.semantic_memory,
            working_memory=self.working_memory,
            cwe_tree=self.cwe_tree
        )

        self.fuzzer = FuzzerAgent(
            llm_client=self.llm_client,
            semantic_memory=self.semantic_memory,
            working_memory=self.working_memory,
            afl_path=afl_path,
            timeout=fuzzer_timeout
        )

    def _init_memory(self):
        self.semantic_memory.load_sast_rules(
            self.sast_rule_base.get_all_rules()
        )
        self.semantic_memory.load_cwe_tree(
            entries=self.cwe_tree.get_all_entries(),
            hierarchy=self.cwe_tree.hierarchy
        )

    def run(
        self,
        diffs: List[CodeDiff],
        source_files: Optional[Dict[str, str]] = None,
        repository_path: str = ".",
        target_binary: Optional[str] = None,
        functions: Optional[List[str]] = None
    ) -> PipelineResult:
        start_time = time.time()
        stage_times = {}

        logger.info("=" * 60)
        logger.info("网络安全子智能体 - 三阶段验证流水线启动")
        logger.info("=" * 60)

        stage_start = time.time()
        context = self._preprocess(diffs, source_files, repository_path, functions)
        stage_times["preprocess"] = time.time() - stage_start
        logger.info(f"[Step 0] 前置处理完成: {len(context.diffs)} 个文件, "
                     f"{len(context.cfgs)} 个CFG ({stage_times['preprocess']:.2f}s)")

        return self._run_pipeline(context, target_binary, start_time, stage_times)

    def run_with_preprocessed_context(
        self,
        context: PreprocessedContext,
        target_binary: Optional[str] = None
    ) -> PipelineResult:
        start_time = time.time()
        stage_times = {"preprocess": 0.0}

        logger.info("=" * 60)
        logger.info("网络安全子智能体 - 上下文工具联调流水线启动")
        logger.info("=" * 60)
        self.working_memory.store_diff(context.diffs)
        self.working_memory.store_cfg(context.cfgs)
        logger.info(f"[Step 0] 使用上下文服务已装配内容: {len(context.diffs)} 个文件, "
                    f"{len(context.cfgs)} 个CFG")

        return self._run_pipeline(context, target_binary, start_time, stage_times)

    def _run_pipeline(
        self,
        context: PreprocessedContext,
        target_binary: Optional[str],
        start_time: float,
        stage_times: Dict[str, float]
    ) -> PipelineResult:
        result = PipelineResult()
        result.total_files = len(context.diffs)
        result.total_functions = sum(len(cfg.nodes) > 0 for cfg in context.cfgs)

        def _check_timeout(stage_name: str):
            elapsed = time.time() - start_time
            logger.info(f"[{stage_name}] 已耗时 {elapsed:.2f}s / 限 {self.pipeline_timeout:.2f}s")
            if elapsed > self.pipeline_timeout:
                raise PipelineTimeoutError(
                    f"安全 agent 流水线超时（{elapsed:.2f}s > {self.pipeline_timeout:.2f}s），"
                    f"阻塞在 {stage_name} 阶段"
                )

        _check_timeout("pipeline-start")
        stage_start = time.time()
        detector_reports = self.detector.analyze(context)
        stage_times["detector"] = time.time() - stage_start
        result.detector_findings = len(detector_reports)
        logger.info(f"[Step 1] 检测器(ad)完成: 发现 {result.detector_findings} 个潜在漏洞 "
                     f"({stage_times['detector']:.2f}s)")

        _check_timeout("detector")
        stage_start = time.time()
        verified_reports = self.verifier.verify(detector_reports, context)
        stage_times["verifier"] = time.time() - stage_start
        static_kept_reports = [r for r in verified_reports if not r.is_false_positive]
        result.verifier_filtered = len([r for r in verified_reports if r.is_false_positive])
        result.verifier_confirmed = len(static_kept_reports)
        logger.info(f"[Step 2] 验证器(av)完成: 过滤 {result.verifier_filtered} 个假阳, "
                     f"保留 {result.verifier_confirmed} 个高可疑漏洞 "
                     f"({stage_times['verifier']:.2f}s)")

        _check_timeout("verifier")
        stage_start = time.time()
        if self.enable_fuzzing:
            suspicious = [r for r in static_kept_reports if r.confidence >= 0.3]
            fuzzer_results = self.fuzzer.verify(suspicious, context, target_binary)
            stage_times["fuzzer"] = time.time() - stage_start

            result.fuzzer_confirmed = len([
                r for r in fuzzer_results if r.runtime_status == "confirmed"
            ])
            result.fuzzer_suspicious = len([
                r for r in fuzzer_results if r.runtime_status == "suspicious"
            ])
            result.runtime_verified = result.fuzzer_confirmed
            logger.info(f"[Step 3] 动态验证(af)完成: {result.fuzzer_confirmed} 确认, "
                         f"{result.fuzzer_suspicious} 可疑 "
                         f"({stage_times['fuzzer']:.2f}s)")
            result.final_reports = [r for r in fuzzer_results if not r.is_false_positive]
        else:
            stage_times["fuzzer"] = 0.0
            logger.info("[Step 3] 动态验证(af)已跳过（enable_fuzzing=False）")
            result.final_reports = static_kept_reports

        total_time = time.time() - start_time
        result.execution_time = total_time
        result.stage_times = stage_times
        result.summary = self._build_summary(result, stage_times)

        logger.info("=" * 60)
        logger.info(f"流水线完成: 总耗时 {total_time:.2f}s")
        logger.info(f"最终结果: {len(result.final_reports)} 个漏洞报告")
        logger.info("=" * 60)

        return result

    def _preprocess(
        self,
        diffs: List[CodeDiff],
        source_files: Optional[Dict[str, str]],
        repository_path: str,
        functions: Optional[List[str]]
    ) -> PreprocessedContext:
        cfgs = []

        if source_files:
            for file_name, source_code in source_files.items():
                file_cfgs = self.cfg_generator.generate_from_source(
                    source_code=source_code,
                    file_name=file_name,
                    functions=functions
                )
                cfgs.extend(file_cfgs)

        if not cfgs:
            cfgs = self.cfg_generator.generate_from_diff(diffs, repository_path)

        repo_files = source_files or {}
        if not repo_files:
            for diff in diffs:
                try:
                    with open(diff.file_path, "r", encoding="utf-8") as f:
                        repo_files[diff.file_path] = f.read()
                except Exception:
                    repo_files[diff.file_path] = ""

        context = PreprocessedContext(
            diffs=diffs,
            cfgs=cfgs,
            repository_files=repo_files
        )

        self.working_memory.store_diff(diffs)
        self.working_memory.store_cfg(cfgs)

        return context

    def _build_summary(
        self,
        result: PipelineResult,
        stage_times: Dict[str, float]
    ) -> Dict[str, Any]:
        by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        by_cwe = {}
        confirmed_details = []

        for report in result.final_reports:
            sev = report.severity.lower()
            if sev in by_severity:
                by_severity[sev] += 1

            cwe = report.cwe_id
            by_cwe[cwe] = by_cwe.get(cwe, 0) + 1

            if report.runtime_status == "confirmed":
                confirmed_details.append({
                    "cwe_id": report.cwe_id,
                    "file": report.file_path,
                    "line": f"{report.line_start}-{report.line_end}",
                    "description": report.description[:200],
                    "stack_trace": report.stack_trace[:500] if report.stack_trace else ""
                })

        return {
            "pipeline": "ad → av → af",
            "total_time": f"{result.execution_time:.2f}s",
            "stage_times": {k: f"{v:.2f}s" for k, v in stage_times.items()},
            "findings": {
                "detector_raw": result.detector_findings,
                "verifier_filtered": result.verifier_filtered,
                "verifier_confirmed": result.verifier_confirmed,
                "fuzzer_confirmed": result.fuzzer_confirmed,
                "fuzzer_suspicious": result.fuzzer_suspicious,
                "runtime_verified": result.runtime_verified
            },
            "by_severity": by_severity,
            "by_cwe": by_cwe,
            "confirmed_vulnerabilities": confirmed_details
        }

    def export_results(
        self,
        result: PipelineResult,
        output_file: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        metadata = metadata or {}
        security_config = metadata.get("security_config") or {}
        cwe_focus = security_config.get("cwe_focus") or []
        filtered_reports = self._filter_reports_by_cwe(result.final_reports, cwe_focus)
        findings = self._build_findings(
            reports=filtered_reports,
            context=metadata.get("context"),
        )
        sarif_path = None
        if security_config.get("sarif_output", False):
            sarif_path = self._write_sarif_report(
                output_file=output_file,
                task_id=str(metadata.get("task_id") or ""),
                findings=findings,
            )

        output = self._build_security_output(
            result=result,
            findings=findings,
            output_file=output_file,
            sarif_path=sarif_path,
            metadata=metadata,
            security_config=security_config,
        )

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        logger.info(f"结果已导出到: {output_file}")

    def _filter_reports_by_cwe(
        self,
        reports: List[VulnerabilityReport],
        cwe_focus: List[str],
    ) -> List[VulnerabilityReport]:
        if not cwe_focus:
            return list(reports)
        allowed = {str(cwe_id).upper() for cwe_id in cwe_focus}
        return [report for report in reports if report.cwe_id.upper() in allowed]

    def _build_security_output(
        self,
        result: PipelineResult,
        findings: List[Dict[str, Any]],
        output_file: str,
        sarif_path: Optional[str],
        metadata: Dict[str, Any],
        security_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        by_cwe: Dict[str, int] = {}
        for finding in findings:
            severity = str(finding["severity"]).lower()
            if severity in severity_counts:
                severity_counts[severity] += 1
            cwe_id = str(finding["category"])
            by_cwe[cwe_id] = by_cwe.get(cwe_id, 0) + 1

        risk_score = self._calculate_risk_score(findings)
        highest_severity = self._highest_severity(findings)
        output = {
            # 统一外层格式
            "agent": metadata.get("agent_name", "security-agent"),
            "dimension": metadata.get("review_dimension", metadata.get("dimension", "security")),
            "scan_id": metadata.get("scan_id"),
            "snapshot_id": metadata.get("snapshot_id"),
            "task_id": metadata.get("task_id"),
            "status": metadata.get("status", "completed"),
            "findings": findings,
            "message": metadata.get("message"),
            # 扩展字段（security agent 特有）
            "repo_id": metadata.get("repo_id"),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "summary": {
                "total_findings": len(findings),
                "severity_counts": severity_counts,
                "highest_severity": highest_severity,
                "by_cwe": by_cwe,
                "risk_score": risk_score,
                "dynamic_validation": {
                    "enabled": bool(security_config.get("enable_dynamic_validation", False)),
                    "confirmed": len([f for f in findings if f["verification"]["status"] == "confirmed"]),
                    "suspicious": len([f for f in findings if f["verification"]["status"] == "suspicious"]),
                    "unverified": len([f for f in findings if f["verification"]["status"] == "unverified"]),
                },
                "pipeline": result.summary.get("pipeline", "ad → av → af"),
            },
            "recommendations": self._build_recommendations(findings),
            "artifacts": {
                "result_file": output_file,
                "sarif_report": sarif_path,
            },
            "block_merge": self._should_block_merge(findings),
        }
        return output

    def _build_findings(
        self,
        reports: List[VulnerabilityReport],
        context: Optional[PreprocessedContext],
    ) -> List[Dict[str, Any]]:
        findings = []
        for index, report in enumerate(reports, start=1):
            cwe_entry = self.cwe_tree.get_entry(report.cwe_id) or {}
            suggestion = ""
            mitigations = cwe_entry.get("potential_mitigations", [])
            if mitigations:
                suggestion = str(mitigations[0])
            reference = f"https://cwe.mitre.org/data/definitions/{report.cwe_id.split('-')[-1]}.html" if report.cwe_id.startswith("CWE-") else ""
            # 构建统一 finding 结构
            finding = {
                "local_id": f"SEC-{index:03d}",
                "category": report.cwe_id,
                "severity": report.severity.lower(),
                "confidence": round(float(report.confidence), 2),
                "title": report.cwe_name or report.cwe_id,
                "description": report.description,
                "location": {
                    "file": report.file_path,
                    "line_start": report.line_start,
                    "line_end": report.line_end,
                    "symbol": self._infer_symbol_name(report, context),
                },
                "security_standard": reference,
                "verification": {
                    "status": report.runtime_status or "unverified",
                    "input": report.runtime_input,
                    "stack_trace": report.stack_trace,
                    "coverage": report.coverage,
                },
                "evidence": [],
                "suggestion": suggestion,
                "requires_human_review": self._requires_human_review(report),
            }
            # 代码片段作为第一条证据
            if report.code_snippet:
                finding["evidence"].append({
                    "type": "code_snippet",
                    "content": report.code_snippet,
                    "line_start": report.line_start,
                    "line_end": report.line_end,
                })
            # 运行时验证信息作为证据
            if report.runtime_input:
                finding["evidence"].append({
                    "type": "runtime_input",
                    "content": str(report.runtime_input),
                })
            if report.stack_trace:
                finding["evidence"].append({
                    "type": "stack_trace",
                    "content": report.stack_trace,
                })
            findings.append(finding)
        return findings

    def _requires_human_review(self, report: VulnerabilityReport) -> bool:
        """判断是否需要人工复核：低置信度或 medium 及以下的问题"""
        severity = str(report.severity).lower()
        confidence = float(report.confidence)
        if confidence < 0.5:
            return True
        if severity in ("medium", "low"):
            return True
        return False

    def _infer_symbol_name(
        self,
        report: VulnerabilityReport,
        context: Optional[PreprocessedContext],
    ) -> Optional[str]:
        if not context:
            return None
        for cfg in context.cfgs:
            if cfg.file_path != report.file_path:
                continue
            node = cfg.find_node_by_line(report.line_start)
            if node:
                return cfg.function_name
        return None

    def _calculate_risk_score(self, findings: List[Dict[str, Any]]) -> float:
        severity_weights = {"critical": 9.0, "high": 7.0, "medium": 4.0, "low": 1.0}
        score = 0.0
        for finding in findings:
            weight = severity_weights.get(str(finding["severity"]).lower(), 1.0)
            confidence = float(finding.get("confidence", 0.0))
            score += weight * confidence
        return round(min(score, 10.0), 1)

    def _highest_severity(self, findings: List[Dict[str, Any]]) -> Optional[str]:
        ranking = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        highest = None
        highest_rank = -1
        for finding in findings:
            severity = str(finding["severity"]).lower()
            rank = ranking.get(severity, 0)
            if rank > highest_rank:
                highest = severity
                highest_rank = rank
        return highest

    def _build_recommendations(self, findings: List[Dict[str, Any]]) -> List[str]:
        recommendations = []
        seen = set()
        for finding in findings:
            severity = str(finding["severity"]).lower()
            cwe_name = finding["title"]
            suggestion = finding["suggestion"]
            if severity in ("critical", "high"):
                text = f"优先修复 {cwe_name} 风险。"
                if text not in seen:
                    seen.add(text)
                    recommendations.append(text)
            if suggestion and suggestion not in seen:
                seen.add(suggestion)
                recommendations.append(suggestion)
        return recommendations

    def _should_block_merge(self, findings: List[Dict[str, Any]]) -> bool:
        for finding in findings:
            severity = str(finding["severity"]).lower()
            confidence = float(finding.get("confidence", 0.0))
            runtime_status = finding["verification"]["status"]
            if severity == "critical" and confidence >= 0.8:
                return True
            if severity == "high" and runtime_status == "confirmed":
                return True
        return False

    def _write_sarif_report(
        self,
        output_file: str,
        task_id: str,
        findings: List[Dict[str, Any]],
    ) -> str:
        base, _ = os.path.splitext(output_file)
        sarif_path = f"{base}.sarif.json"
        rules = []
        results = []
        seen_rules = set()
        for finding in findings:
            rule_id = finding["category"]
            if rule_id not in seen_rules:
                seen_rules.add(rule_id)
                rules.append({
                    "id": rule_id,
                    "name": finding["title"],
                    "shortDescription": {"text": finding["description"][:120]},
                    "helpUri": finding["security_standard"],
                })
            results.append({
                "ruleId": rule_id,
                "level": self._severity_to_sarif_level(finding["severity"]),
                "message": {"text": finding["description"]},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": finding["location"]["file"]},
                        "region": {
                            "startLine": finding["location"]["line_start"],
                            "endLine": finding["location"]["line_end"],
                        },
                    }
                }],
                "properties": {
                    "task_id": task_id,
                    "confidence": finding["confidence"],
                },
            })

        sarif = {
            "version": "2.1.0",
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [{
                "tool": {
                    "driver": {
                        "name": "security-agent",
                        "informationUri": "https://cwe.mitre.org/",
                        "rules": rules,
                    }
                },
                "results": results,
            }],
        }
        with open(sarif_path, "w", encoding="utf-8") as f:
            json.dump(sarif, f, indent=2, ensure_ascii=False)
        return sarif_path

    def _severity_to_sarif_level(self, severity: str) -> str:
        mapping = {
            "critical": "error",
            "high": "error",
            "medium": "warning",
            "low": "note",
        }
        return mapping.get(str(severity).lower(), "warning")

    def load_rules_from_codeql(self, codeql_rules_path: str):
        self.sast_rule_base.load_from_file(codeql_rules_path)
        self._init_memory()
        logger.info(f"从 {codeql_rules_path} 加载了 {len(self.sast_rule_base.rules)} 条SAST规则")

    def load_cwe_from_file(self, cwe_file_path: str):
        self.cwe_tree.load_from_file(cwe_file_path)
        self._init_memory()
        logger.info(f"从 {cwe_file_path} 加载了 {len(self.cwe_tree.entries)} 条CWE条目")
