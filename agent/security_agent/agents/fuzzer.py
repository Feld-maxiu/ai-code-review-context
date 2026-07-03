import json
import os
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
from ..models import VulnerabilityReport, PreprocessedContext, ControlFlowGraph
from ..memory.semantic_memory import SemanticMemory
from ..memory.working_memory import WorkingMemory
from ..utils.llm_client import LLMClient


class FuzzerAgent:
    def __init__(
        self,
        llm_client: LLMClient,
        semantic_memory: SemanticMemory,
        working_memory: WorkingMemory,
        afl_path: str = "afl-fuzz",
        seed_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
        timeout: int = 300,
        max_seeds: int = 50
    ):
        self.llm_client = llm_client
        self.semantic_memory = semantic_memory
        self.working_memory = working_memory
        self.afl_path = afl_path
        self.seed_dir = seed_dir
        self.output_dir = output_dir
        self.timeout = timeout
        self.max_seeds = max_seeds

    def verify(
        self,
        suspicious_vulnerabilities: List[VulnerabilityReport],
        context: PreprocessedContext,
        target_binary: Optional[str] = None
    ) -> List[VulnerabilityReport]:
        if not suspicious_vulnerabilities:
            return []

        results = []
        for vuln in suspicious_vulnerabilities:
            if vuln.is_false_positive:
                vuln.runtime_status = "filtered_by_av"
                results.append(vuln)
                continue

            result = self._verify_single(vuln, context, target_binary)
            results.append(result)

        self.working_memory.store_fuzzer_results(results)
        return results

    def _verify_single(
        self,
        vuln: VulnerabilityReport,
        context: PreprocessedContext,
        target_binary: Optional[str]
    ) -> VulnerabilityReport:
        if not target_binary:
            vuln.runtime_status = "unverified"
            vuln.runtime_input = "未提供目标二进制，无法执行动态验证"
            vuln.coverage = {"reason": "target_binary_not_provided"}
            return vuln

        trigger_info = self._understand_and_reason(vuln, context)
        if not trigger_info:
            vuln.runtime_status = "suspicious"
            vuln.runtime_input = "无法推理触发路径"
            return vuln

        seeds = self._generate_directed_seeds(vuln, trigger_info)
        if not seeds:
            vuln.runtime_status = "suspicious"
            vuln.runtime_input = "无法生成有效种子"
            return vuln

        fuzz_result = self._execute_directed_fuzzing(
            vuln, seeds, trigger_info, target_binary
        )

        vuln.runtime_status = fuzz_result["status"]
        vuln.runtime_input = fuzz_result.get("input", "")
        vuln.stack_trace = fuzz_result.get("stack_trace", "")
        vuln.coverage = fuzz_result.get("coverage", {})

        return vuln

    def _understand_and_reason(
        self,
        vuln: VulnerabilityReport,
        context: PreprocessedContext
    ) -> Optional[Dict[str, Any]]:
        related_cfgs = [c for c in context.cfgs if c.file_path == vuln.file_path]
        if not related_cfgs:
            return None

        target_node = None
        for cfg in related_cfgs:
            node = cfg.find_node_by_line(vuln.line_start)
            if node:
                target_node = node
                break

        related_diffs = [d for d in context.diffs if d.file_path == vuln.file_path]
        code_context = related_diffs[0].raw_diff if related_diffs else vuln.code_snippet

        system_prompt = """你是一位漏洞挖掘专家。请分析漏洞代码并推理触发路径。

输出JSON:
{
  "target_basic_block": "漏洞所在基本块描述",
  "trigger_paths": ["路径1", "路径2"],
  "input_format": "目标程序期望的输入格式",
  "input_fields": [{"name": "字段名", "type": "类型", "constraint": "约束条件"}],
  "vulnerability_trigger_condition": "触发漏洞需要的条件",
  "constraint_analysis": "关键约束分析",
  "suggested_mutation_strategy": "建议的变异策略"
}"""

        prompt = f"""请分析以下漏洞的触发路径和约束条件:

## 漏洞信息:
- CWE: {vuln.cwe_id} - {vuln.cwe_name}
- 文件: {vuln.file_path}
- 行号: {vuln.line_start}-{vuln.line_end}
- 描述: {vuln.description}

## 漏洞代码:
```
{vuln.code_snippet or code_context}
```

## 所在函数CFG:
{self._format_target_cfg(related_cfgs, target_node)}

请推理漏洞的触发路径和输入约束。"""

        try:
            return self.llm_client.chat_with_json_output(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.0
            )
        except Exception:
            return self._fallback_trigger_info(vuln, target_node)

    def _fallback_trigger_info(
        self,
        vuln: VulnerabilityReport,
        target_node: Optional[Any]
    ) -> Dict[str, Any]:
        return {
            "target_basic_block": f"行{vuln.line_start}-{vuln.line_end}",
            "trigger_paths": ["直接执行路径"],
            "input_format": "binary/raw",
            "input_fields": [{"name": "data", "type": "raw", "constraint": "任意输入"}],
            "vulnerability_trigger_condition": f"{vuln.cwe_name}触发条件",
            "constraint_analysis": "无特定约束",
            "suggested_mutation_strategy": "随机变异"
        }

    def _generate_directed_seeds(
        self,
        vuln: VulnerabilityReport,
        trigger_info: Dict[str, Any]
    ) -> List[bytes]:
        seed_generation_spec = self._build_seed_generation_spec(vuln, trigger_info)
        seed_script = self.llm_client.generate_code(
            specification=seed_generation_spec,
            language="python"
        )

        if not seed_script:
            return []

        seeds = self._execute_seed_generation(seed_script)
        valid_seeds = self._validate_seeds(seeds, trigger_info)
        return valid_seeds

    def _build_seed_generation_spec(
        self,
        vuln: VulnerabilityReport,
        trigger_info: Dict[str, Any]
    ) -> str:
        input_fields = trigger_info.get("input_fields", [])
        trigger_condition = trigger_info.get("vulnerability_trigger_condition", "")
        constraint_analysis = trigger_info.get("constraint_analysis", "")

        spec = f"""编写一个Python函数，生成用于模糊测试的种子文件，目标是触发以下漏洞:

漏洞类型: {vuln.cwe_id} - {vuln.cwe_name}
漏洞描述: {vuln.description}
触发条件: {trigger_condition}
输入约束: {constraint_analysis}

函数签名: def generate_seeds(num_seeds: int = {self.max_seeds}) -> list[bytes]:
- 返回bytes列表，每个元素是一个种子
- 种子大小限制: 1-4096字节
- 应当包含能够触发目标漏洞的输入模式

输入字段要求:
{json.dumps(input_fields, indent=2, ensure_ascii=False)}

策略建议:
- 生成能覆盖边界条件的种子（如最小/最大值、空值、超长值）
- 生成包含特殊字符的种子（如格式字符串%n%s、SQL注入字符等）
- 根据CWE类型定向构造恶意输入"""
        return spec

    def _execute_seed_generation(self, script: str) -> List[bytes]:
        seeds = []
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                script_path = os.path.join(tmpdir, "seed_gen.py")
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write(script)

                env = os.environ.copy()
                env["PYTHONPATH"] = tmpdir

                result = subprocess.run(
                    ["python", script_path],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=tmpdir,
                    env=env
                )

                try:
                    data = json.loads(result.stdout)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, str):
                                seeds.append(item.encode("utf-8", errors="replace"))
                            elif isinstance(item, (int, float)):
                                seeds.append(str(item).encode())
                    elif isinstance(data, dict):
                        for v in data.values():
                            if isinstance(v, str):
                                seeds.append(v.encode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    seeds.append(result.stdout.encode("utf-8", errors="replace"))

        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

        return seeds if seeds else [b"FUZZ" + bytes([i % 256]) * 32 for i in range(10)]

    def _validate_seeds(
        self,
        seeds: List[bytes],
        trigger_info: Dict[str, Any]
    ) -> List[bytes]:
        valid_seeds = []
        for seed in seeds:
            if not seed:
                continue
            if len(seed) > 4096:
                seed = seed[:4096]
            if self._syntax_check(seed, trigger_info):
                valid_seeds.append(seed)

        return valid_seeds[:self.max_seeds] if valid_seeds else seeds[:self.max_seeds]

    def _syntax_check(self, seed: bytes, trigger_info: Dict[str, Any]) -> bool:
        input_format = trigger_info.get("input_format", "binary")
        if input_format in ("json", "JSON"):
            try:
                json.loads(seed)
                return True
            except (json.JSONDecodeError, UnicodeDecodeError):
                return False
        return True

    def _execute_directed_fuzzing(
        self,
        vuln: VulnerabilityReport,
        seeds: List[bytes],
        trigger_info: Dict[str, Any],
        target_binary: Optional[str]
    ) -> Dict[str, Any]:
        if not target_binary:
            return {
                "status": "unverified",
                "input": seeds[0].decode("utf-8", errors="replace")[:500] if seeds else "",
                "stack_trace": "",
                "coverage": {
                    "reason": "target_binary_not_provided",
                    "seed_count": len(seeds),
                }
            }

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                seed_dir = os.path.join(tmpdir, "seeds")
                os.makedirs(seed_dir, exist_ok=True)

                for i, seed in enumerate(seeds):
                    seed_path = os.path.join(seed_dir, f"seed_{i:04d}")
                    with open(seed_path, "wb") as f:
                        f.write(seed)

                output_dir = os.path.join(tmpdir, "output")

                afl_cmd = [
                    self.afl_path,
                    "-i", seed_dir,
                    "-o", output_dir,
                    "-t", f"{self.timeout // 10}000",
                    "-V", f"{self.timeout}",
                    "--",
                    target_binary
                ]

                try:
                    proc = subprocess.Popen(
                        afl_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    try:
                        proc.wait(timeout=self.timeout)
                    except subprocess.TimeoutExpired:
                        proc.terminate()
                        proc.wait(timeout=10)

                    crashes_dir = os.path.join(output_dir, "crashes")
                    if os.path.exists(crashes_dir):
                        crash_files = [
                            f for f in os.listdir(crashes_dir)
                            if f != "README.txt"
                        ]
                        if crash_files:
                            crash_path = os.path.join(crashes_dir, crash_files[0])
                            with open(crash_path, "rb") as f:
                                crash_input = f.read()

                            return {
                                "status": "confirmed",
                                "input": crash_input.decode("utf-8", errors="replace")[:500],
                                "stack_trace": self._extract_stack_trace(output_dir),
                                "coverage": self._extract_coverage(output_dir)
                            }

                    queue_dir = os.path.join(output_dir, "queue")
                    coverage_info = {"paths_found": 0}
                    if os.path.exists(queue_dir):
                        paths = [f for f in os.listdir(queue_dir) if f != ".state"]
                        coverage_info["paths_found"] = len(paths)

                    return {
                        "status": "suspicious" if coverage_info.get("paths_found", 0) > 0 else "unverified",
                        "input": "",
                        "stack_trace": "",
                        "coverage": coverage_info
                    }

                except FileNotFoundError:
                    pass

        except Exception:
            pass

        return self._dry_run_fuzzing(vuln, seeds, trigger_info)

    def _dry_run_fuzzing(
        self,
        vuln: VulnerabilityReport,
        seeds: List[bytes],
        trigger_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        system_prompt = """你是一位模糊测试专家。基于种子文件和漏洞信息，请评估漏洞是否可被触发。

输出JSON:
{
  "status": "confirmed/suspicious/new_vulnerability",
  "reasoning": "推理过程",
  "confidence": 0.0-1.0
}"""

        seeds_preview = "\n".join([
            f"种子{i}: {s[:100].hex()}" for i, s in enumerate(seeds[:5])
        ])

        prompt = f"""评估以下漏洞是否可被定向模糊测试触发:

## 漏洞信息:
- CWE: {vuln.cwe_id}
- 描述: {vuln.description}
- 代码: {vuln.code_snippet}

## 触发条件:
{trigger_info.get('vulnerability_trigger_condition', '')}

## 生成的种子:
{seeds_preview}

## 输入约束:
{trigger_info.get('constraint_analysis', '')}

请评估漏洞触发可能性。"""

        try:
            result = self.llm_client.chat_with_json_output(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.0
            )
            return {
                "status": result.get("status", "suspicious"),
                "input": seeds[0].decode("utf-8", errors="replace")[:500] if seeds else "",
                "stack_trace": "",
                "coverage": {"llm_confidence": result.get("confidence", 0.5)}
            }
        except Exception:
            return {
                "status": "suspicious",
                "input": seeds[0].decode("utf-8", errors="replace")[:500] if seeds else "",
                "stack_trace": "",
                "coverage": {}
            }

    def _format_target_cfg(self, cfgs: List[ControlFlowGraph], target_node: Any) -> str:
        if not cfgs:
            return "无CFG信息"

        parts = []
        for cfg in cfgs:
            parts.append(f"函数: {cfg.function_name}")
            if target_node:
                paths = cfg.find_path_to(target_node.id)
                if paths:
                    parts.append("到达目标节点的路径:")
                    for i, path in enumerate(paths):
                        path_str = " -> ".join(str(nid) for nid in path)
                        parts.append(f"  路径{i + 1}: {path_str}")
                else:
                    parts.append("未找到到达目标节点的路径")

            parts.append("控制流节点:")
            for node in cfg.nodes:
                marker = " <-- 漏洞点" if target_node and node.id == target_node.id else ""
                parts.append(
                    f"  [{node.id}] {node.label} "
                    f"行{node.line_start}-{node.line_end}"
                    f"{' [条件]' if node.is_conditional else ''}"
                    f"{marker}"
                )
        return "\n".join(parts)

    def _extract_stack_trace(self, output_dir: str) -> str:
        crashes_dir = os.path.join(output_dir, "crashes")
        if not os.path.exists(crashes_dir):
            return ""

        readme_path = os.path.join(crashes_dir, "README.txt")
        if os.path.exists(readme_path):
            with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()[:2000]
        return ""

    def _extract_coverage(self, output_dir: str) -> Dict[str, Any]:
        coverage = {"crashes": 0, "hangs": 0, "paths_found": 0}

        crashes_dir = os.path.join(output_dir, "crashes")
        if os.path.exists(crashes_dir):
            coverage["crashes"] = len([
                f for f in os.listdir(crashes_dir) if f != "README.txt"
            ])

        hangs_dir = os.path.join(output_dir, "hangs")
        if os.path.exists(hangs_dir):
            coverage["hangs"] = len([
                f for f in os.listdir(hangs_dir) if f != "README.txt"
            ])

        queue_dir = os.path.join(output_dir, "queue")
        if os.path.exists(queue_dir):
            coverage["paths_found"] = len([
                f for f in os.listdir(queue_dir) if f != ".state"
            ])

        return coverage

    def get_confirmed_vulnerabilities(self) -> List[VulnerabilityReport]:
        results = self.working_memory.get_fuzzer_results()
        return [r for r in results if r.runtime_status == "confirmed"]

    def get_suspicious_vulnerabilities(self) -> List[VulnerabilityReport]:
        results = self.working_memory.get_fuzzer_results()
        return [r for r in results if r.runtime_status == "suspicious"]

    def get_new_vulnerabilities(self) -> List[VulnerabilityReport]:
        results = self.working_memory.get_fuzzer_results()
        return [r for r in results if r.runtime_status == "new_vulnerability"]
