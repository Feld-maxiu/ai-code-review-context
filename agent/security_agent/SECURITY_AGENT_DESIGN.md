# Security Agent 技术文档（简略版）

> 模块路径：`agent/security_agent/`
> 流水线：ad → av → af（Detector → Verifier → Fuzzer，可选）

---

## 1. 职责

对上游「上下文服务」分发的 `security` 维度任务，按 三阶段流水线 完成漏洞检测、静态验证、（可选）动态模糊测试，输出结构化漏洞报告（含 SARIF）。

- 静态发现潜在漏洞（Detector）
- 基于 CWE 知识 + CFG 过滤误报（Verifier）
- 对高可疑漏洞做定向模糊测试以确认可触发性（Fuzzer，可选）
- 区分「静态候选」与「已验证问题」，并据此决定是否阻断合并

---

## 2. 输入

| 项 | 来源 | 说明 |
|---|---|---|
| `CodeDiff` | 上下文服务 / 本地 | 文件级新增/删除行 + raw_diff |
| `ControlFlowGraph` | `PythonCFGGenerator`（基于 Python `ast`）/ `CFGGenerator`（基于 clang，C/C++） | 函数级控制流，含节点行号、前后继、入口/条件标记、`calls`/`assignments`（供 taint 分析）。按文件后缀自动选择生成器（`.py` → Python ast，`.c/.cpp` → clang） |
| `repository_files` | 本地仓库 / 上下文服务 | 全文件源码，用于上下文回填 |
| `environment_profile` | CLI `--env-profile` | 环境依赖声明（JSON 文件或 `key=value,...` 字符串），三态化 CWE 环境检查 |
| `target_binary`（可选） | CLI `--target-binary` | Fuzzer 动态验证所需的 C/C++ 可执行文件 |
| `target_script`（可选） | CLI `--target-script` | Python 项目入口脚本，Fuzzer 逐种子执行 `python <script>` |
| SAST 规则库 | `knowledge/sast_rules.py`（默认内置 10 条，可外挂 CodeQL JSON） | 每条 taint 型规则含 `sources/sinks/sanitizers/propagators` |
| CWE 知识树 | `knowledge/cwe_tree.py`（默认内置，可外挂 JSON） | |

---

## 3. 处理流程

```
Preprocess(CFG) → Detector(ad) → Verifier(av) → Fuzzer(af, 可选) → Export
```

1. 预处理：按文件后缀自动选择 CFG 生成器（`.py` → `PythonCFGGenerator` 基于 `ast`；`.c/.cpp` → `CFGGenerator` 基于 clang）。由 diff / 源码生成 CFG（含 `calls`/`assignments` 字段），写入 working memory。
2. Detector：对每个 diff，检索 top-k SAST 规则，LLM 结合 diff + CFG + 规则产出 `VulnerabilityReport` 列表。
3. Verifier：逐条跑 intraprocedural taint 分析（`TaintAnalyzer`）判定 source→sink 可达性，再结合 CWE 树校验（前置条件 / 环境依赖三态 / 可利用性），标记 `is_false_positive` 与置信度。taint 事实写入 `report.coverage["taint_analysis"]`。
4. Fuzzer（可选，`enable_fuzzing=True` 且 `confidence >= 0.3`）：
   - C/C++ 项目：`_understand_and_reason`（推理触发路径）→ `_generate_directed_seeds` → `_execute_directed_fuzzing`（AFL）
   - Python 项目：`_execute_python_fuzzing`（逐种子 `python <script>`，通过非 0 退出码 / 超时识别潜在问题）
   - 无论哪种模式，均默认不执行 LLM 生成的代码（`allow_code_execution=False`）
5. 导出：按 `cwe_focus` 过滤、计算 risk_score、生成 JSON + SARIF。

---

## 4. 依赖工具

| 工具 | 用途 | 默认 |
|---|---|---|
| LLM（OpenAI 兼容） | Detector / Verifier / Fuzzer 推理与代码生成 | `gpt-4` |
| Python `ast`（标准库） | Python 项目 CFG 生成 | 内置 |
| `clang`（可选） | C/C++ 项目 CFG 生成 | `clang` |
| `afl-fuzz`（可选） | C/C++ 项目定向模糊测试 | `afl-fuzz` |
| `subprocess` + `python` | Python 项目动态验证 | 内置 |
| 上下文服务 HTTP | 任务领取 / 反馈（`http://127.0.0.1:8000`） | 必需 |

---

## 5. 输出

- `security_results/task_{task_id}.json`：findings 列表 + summary + recommendations + `block_merge`
- `.sarif.json`（`sarif_output=True` 时）：SARIF 2.1.0 报告
- `run_summary.json`：本次运行汇总

每个 finding 字段：`cwe_id / cwe_name / severity / confidence / location / code_snippet / evidence.runtime_validation / remediation`。

---

## 6. 失败降级

| 故障点 | 降级策略 |
|---|---|
| LLM 调用失败（Detector） | `_fallback_pattern_match`：纯字符串/关键词匹配规则，置信度封顶 0.5（[detector.py:168](file:///c:/Users/曾/Desktop/ai-code-review-context/agent/security_agent/agents/detector.py#L168)） |
| Verifier taint 分析遇复杂表达式 | LLM 不可用时降级为 `needs_llm_review=True`、confidence=0.3 的 suspicious 结果，不阻断（[taint_analyzer.py](file:///c:/Users/曾/Desktop/ai-code-review-context/agent/security_agent/utils/taint_analyzer.py) `_llm_review_taint`） |
| LLM 调用失败（Verifier 兜底 `_llm_verify`） | 不做过滤，原报告透传 |
| LLM 调用失败（Fuzzer 推理） | `_fallback_trigger_info`：返回默认触发信息，继续后续种子生成 |
| 无 `target_binary` 且无 `target_script` | Fuzzer 直接标 `unverified`，不执行 |
| AFL 不可用 / 超时 | `_dry_run_fuzzing`：LLM 基于种子评估可触发性 |
| Python 目标脚本不存在 | `_execute_python_fuzzing` 返回 `unverified`，reason = `target_script_not_found` |
| LLM 代码执行被禁（默认） | 返回兜底种子 `b"FUZZ"...` |
| 流水线整体超时 | 抛 `PipelineTimeoutError`，记录阻塞阶段 |
| 上下文服务不可达 | 抛 `ContextServiceError`，需先启服务 |

---

## 7. 重点概念

### 7.1 Source / Sink

本模块通过 SAST 规则的四个函数角色字段 + `TaintAnalyzer` 显式建模污点流：

| 角色 | 字段 | 结构 | 语义 |
|---|---|---|---|
| Source | `sources` | `{"function": str, "arg_index": int, "label": str}` | `arg_index=-1` 表示返回值携带污点（如 `getenv`），否则指定参数为污点源 |
| Sink | `sinks` | 同上 | 危险函数/构造点，指定参数若被染污则可达 |
| Sanitizer | `sanitizers` | `{"function", "arg_index", "clears_taint": True}` | 清除指定参数/返回值的污点 |
| Propagator | `propagators` | `{"function": str, "to_arg": int}` | 输出参数传播：若除 `to_arg` 外任一参数被染污，则 `to_arg` 染污（如 `sprintf(dst, fmt, src)`） |

典型规则（CWE-77 命令注入）：
- sources: `getenv / recv / fgets / scanf / read ...`
- sinks: `system / popen / execve ...`（`arg_index=0`）
- propagators: `sprintf / strcpy / memcpy ...`（`to_arg=0`）

`TaintAnalyzer`（[taint_analyzer.py](file:///c:/Users/曾/Desktop/ai-code-review-context/agent/security_agent/utils/taint_analyzer.py)）按 CFG 顺序连接边做 intraprocedural 传播：sanitizer 清污 → source 染污 → propagator 输出参数传播 → 赋值传播 → sink 参数检查。静态能判定时 confidence=0.8；sink 参数为复杂表达式时降级到 LLM 辅助（confidence=0.9 确认 / 0.1 否定 / 0.3 LLM 不可用）。

> 等价关系：Source = 外部输入函数，Sink = 危险函数指定参数，可达性 = taint 静态传播结果（LLM 仅在复杂表达式时辅助，不再作为主要判定依据）。仅支持函数内分析，不跨函数。

### 7.2 CWE

- `CWETree` 维护 `entries + hierarchy`（父子链，如 `CWE-74 → CWE-89/77/134`）。
- 每条 CWE 条目含：`preconditions / environment_dependencies / exploitability / severity / likelihood_of_exploit / potential_mitigations`。
- Verifier 调 `validate_vulnerability(cwe_id, context)`：
  - 前置条件不满足 → 过滤；其中"用户输入可达..."类前置条件由 taint 分析结果判定（不再依赖 `report.confidence`）。
  - 环境依赖**三态化**（`True` 满足 / `False` 明确不满足 / `None` 未知）：明确不满足 → 标假阳；未知 → 不阻断但按数量降权 confidence（每个 unknown 降 0.1，最低 0.6）；满足 → 正常计算。
  - 环境依赖值来自 `environment_profile`，由 `VerifierAgent._resolve_env_dep` 把中文描述（如"系统shell可用"）映射到 profile key（如 `shell_available`），复合条件（如"栈/堆布局可预测" = `aslr is False AND stack_canary is False`）自动合成。
  - 否则按 `(exploit + severity + likelihood)/3` 算置信度，valid 阈值 0.3。
- 导出时附带 `https://cwe.mitre.org/...` 链接与首条 mitigation 作为修复建议。

### 7.3 Detector（ad）

- 输入：diff + 关联 CFG + top-k SAST 规则
- 输出：`VulnerabilityReport`（含 `cwe_id / severity / confidence / line_start-end / code_snippet`）
- 特点：召回优先，允许低置信度候选进入下一阶段；LLM 失败时回退到关键词匹配。

### 7.4 Verifier（av）

- 输入：Detector 的全部报告 + CWE 树 + CFG + `environment_profile`
- 动作：
  1. 查 CWE 路径是否存在，不存在 → 直接假阳
  2. 跑 `TaintAnalyzer`：按 `cwe_id` 取规则，在 `report.file_path` 对应 CFG 上做 intraprocedural 污点分析，判定 source→sink 可达性（替换原 `report.confidence > 0.3` 的循环论证）
  3. taint 明确不可达（`reachable=False` 且 `confidence<=0.1`）→ 直接假阳，优先于前置条件检查
  4. 构造 `cwe_context`：`user_input_reachable` 取自 taint 结果；"用户输入可达..."类前置条件用 taint 判定；环境依赖用 `_resolve_env_dep` 从 `environment_profile` 三态解析；交 CWE 树校验
  5. taint 静态可达且 `confidence>=0.8` → 提升 `report.confidence`；taint 事实写入 `report.coverage["taint_analysis"]`
- 输出：标 `is_false_positive / filter_reason / confidence`
- 特点：精确率优先，做假阳过滤；taint 分析纯静态不依赖 LLM（复杂表达式时 LLM 辅助）；无 CWE 树时退化为 LLM 验证。

### 7.5 Fuzzer（af，可选）

- 触发条件：`enable_fuzzing=True` 且 `confidence >= 0.3`
- C/C++ 项目（`target_binary` 提供）：
  - 流程：`_understand_and_reason`（推理触发路径）→ `_generate_directed_seeds`（LLM 生成种子脚本）→ `_execute_directed_fuzzing`（AFL）
- Python 项目（`target_script` 提供）：
  - 流程：`_understand_and_reason` → `_generate_directed_seeds` → `_execute_python_fuzzing`
  - `_execute_python_fuzzing`：逐种子执行 `python <script>`，通过非 0 退出码（crash）或超时识别潜在问题
  - 使用最小化环境变量（仅 `SYSTEMROOT` + `PATH`），不继承 `os.environ`
  - 每个种子独立运行，独立超时（默认 60s）
  - 最多测试 `max_seeds` 个种子
- 输出 `runtime_status`：`confirmed`（有 crash）/ `suspicious`（测试过但无 crash）/ `unverified`（无目标或无路径）/ `filtered_by_av`
- 安全约束：`allow_code_execution=False` 默认不执行 LLM 生成的种子脚本；即使开启也用最小化 env，不继承 `os.environ`，避免泄露 API Key。

### 7.6 静态候选 vs 已验证问题

| 维度 | 静态候选（Static Candidate） | 已验证问题（Verified） |
|---|---|---|
| 产生阶段 | Detector（ad）输出 | Fuzzer（af）`runtime_status=confirmed`，或 Verifier 后保留且无动态验证 |
| 置信度依据 | LLM/规则模式匹配，未经环境与可达性核实 | 经过 taint 静态可达 + CWE 前置条件 + 环境依赖三态 + （可选）运行时 crash 证据 |
| 字段标识 | `is_false_positive=False`，`runtime_status` 为空或 `unverified` | `runtime_status=confirmed/suspicious`，带 `runtime_input / stack_trace / coverage` |
| 用途 | 人工复核参考 | 直接驱动 `block_merge`（critical+conf≥0.8 或 high+confirmed） |
| 可信度 | 低～中 | 高 |

> 流水线计数：`detector_raw`（候选）→ `verifier_filtered`（过滤掉的假阳）→ `verifier_confirmed`（静态保留）→ `fuzzer_confirmed`（动态确认）→ `runtime_verified`。
