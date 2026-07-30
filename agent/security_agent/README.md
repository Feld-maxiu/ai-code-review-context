# Security Agent 使用说明

`agent/security_agent/` 是本项目中的**网络安全评审 agent**。它依赖上游的「上下文处理模块」（Context Service）提供仓库索引和任务分发，自身负责按 `security` 维度逐个完成漏洞检测、验证与动态模糊测试。

---

## 1. 前置条件

1. 已安装 Python 3.10+。
2. 已启动上下文服务。默认地址：
   ```
   http://127.0.0.1:8000
   ```
3. 你的 LLM API Key（用于 detector / verifier / fuzzer）。

---

## 2. 目录结构

```text
agent/security_agent/
├── cli.py                       # 命令行入口
├── context_client.py            # 上下文服务 HTTP 客户端
├── context_runner.py            # 任务领取与执行循环
├── main.py                      # SecurityAgentOrchestrator 流水线编排
├── models.py                    # VulnerabilityReport / ControlFlowGraph 等数据模型
├── agents/
│   ├── detector.py              # 静态漏洞检测
│   ├── verifier.py              # 漏洞验证与误报过滤
│   └── fuzzer.py                # 动态模糊测试（默认不执行 LLM 生成的脚本）
├── knowledge/
│   ├── cwe_tree.py              # CWE 知识树
│   └── sast_rules.py            # SAST 规则
├── memory/
│   ├── semantic_memory.py       # 语义记忆
│   └── working_memory.py        # 工作记忆
└── utils/
    ├── cfg_generator.py         # 基于 clang 的 C/C++ CFG 生成器
    ├── python_cfg_generator.py  # 基于 Python ast 的 CFG 生成器
    └── llm_client.py            # LLM 客户端
```

---

## 3. 使用流程

### 3.1 启动上下文服务

上下文服务由其他同学维护，启动后监听默认端口 `8000`。security agent 通过以下接口与它交互：

| 接口 | 作用 |
|---|---|
| `POST /context/index` | 为仓库构建上下文索引 |
| `GET /context/tasks?review_dimension=security` | 领取 security 维度任务 |
| `GET /context/task-package/{task_id}?repo_id={repo_id}` | 获取任务完整包 |
| `POST /context/task-feedback` | 回传任务处理结果 |

### 3.2 运行 security agent

#### 完整命令

```bash
python -m agent.security_agent.cli \
  --context-url http://127.0.0.1:8000 \
  --repo-id <repo_id> \
  --repo-path <本地仓库绝对路径> \
  --db-path <可选索引db路径> \
  --review-dimension security \
  --output-dir security_results \
  --api-key <你的LLM API Key> \
  --api-base https://api.openai.com/v1 \
  --model gpt-4 \
  --no-fuzzing
```

#### 参数说明

| 参数 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `--repo-id` | 是 | - | 仓库分析 ID，上下文服务与 agent 共用 |
| `--repo-path` | 是 | - | 上下文服务可访问的本地仓库路径 |
| `--db-path` | 否 | None | SQLite 索引文件路径 |
| `--review-dimension` | 否 | `security` | 安全 agent 固定为 `security`，不要写错 |
| `--output-dir` | 否 | `security_results` | 结果输出目录 |
| `--api-key` | 否 | "" | LLM API Key |
| `--api-base` | 否 | OpenAI 官方 | LLM API Base |
| `--model` | 否 | `gpt-4` | LLM 模型名 |
| `--no-fuzzing` | 否 | False | 禁用动态模糊测试 |
| `--target-binary` | 否 | None | 可选目标二进制路径，用于 C/C++ fuzzer（afl-fuzz） |
| `--target-script` | 否 | None | 可选目标 Python 脚本路径，用于 Python 动态验证 |
| `--context-depth` | 否 | 2 | 任务局部图深度 |
| `--max-context-files` | 否 | 3 | 相关上下文最大文件数 |
| `--llm-timeout` | 否 | 30.0 | 单次 LLM 调用超时（秒） |
| `--pipeline-timeout` | 否 | 120.0 | 单个任务流水线整体超时（秒） |
| `--fuzzer-timeout` | 否 | 60.0 | fuzzer 单任务超时（秒） |

#### 最小可运行示例

```bash
python -m agent.security_agent.cli \
  --repo-id sample-repo \
  --repo-path tests/fixtures/sample_repo \
  --no-fuzzing
```

---

## 4. 运行结果

运行结束后会在 `--output-dir` 目录下生成：

```text
security_results/
├── task_{task_id}.json       # 单个任务的评审结果
└── run_summary.json          # 本次运行汇总
```

`run_summary.json` 示例：

```json
{
  "repo_id": "sample-repo",
  "dimension": "security",
  "total_tasks": 3,
  "completed": [
    {
      "task_id": "task-001",
      "output_file": "security_results/task_task-001.json",
      "result": { ... }
    }
  ],
  "failed": []
}
```

---

## 5. 安全设计说明

### Fuzzer 默认不执行 LLM 生成的代码

`FuzzerAgent` 默认 `allow_code_execution=False`：

- 不执行 LLM 生成的种子生成脚本；
- 即使显式开启执行，也仅传递最小化环境变量，**不会继承 `os.environ`**，避免泄露 API Key 等敏感信息。

如需开启，需显式构造 `FuzzerAgent(allow_code_execution=True)`，不建议在 CI/生产环境开启。

---

## 6. Python 项目支持说明

本安全 agent 已支持 Python 代码评审：

- **CFG 生成**：根据文件后缀自动选择生成器
  - `.py` 文件使用 `PythonCFGGenerator`（基于 Python 标准库 `ast`）
  - `.c/.cpp/.cc/.h/.hpp` 文件保留使用 `CFGGenerator`（基于 clang）
- **动态验证**：
  - C/C++ 项目：使用 `--target-binary` 配合 `afl-fuzz`
  - Python 项目：使用 `--target-script` 指定入口脚本，fuzzer 会逐种子执行 `python <script>`，通过非 0 退出码或超时识别潜在问题
- **默认不执行 LLM 代码**：即使使用 `--target-script`，也需要显式设置 `allow_code_execution=True` 才会真正执行

## 7. 测试

运行 security agent 相关测试：

```bash
python -m pytest tests/test_context_client.py tests/test_context_runner.py tests/test_pipeline_resilience.py tests/test_python_cfg.py -v
```

---

## 8. 常见问题

**Q：上下文服务没启动会怎样？**

A：`ContextServiceClient` 会抛出 `ContextServiceError`，提示连接失败。请先确认上下文服务已启动。

**Q：`review_dimension` 填错了会怎样？**

A：安全 agent 应固定使用 `security`。其他值会领取到非安全任务，导致评审维度不一致。

**Q：为什么默认禁用 LLM 代码执行？**

A：LLM 生成的代码存在不可控风险，默认关闭； fuzzer 仍可基于静态规则生成种子并运行 AFL/QEMU 等受控模糊测试。

**Q：security agent 能检测 Python 吗？**

A：可以。Python 文件会自动使用基于 `ast` 的 `PythonCFGGenerator` 生成控制流图，动态验证可通过 `--target-script` 指定 Python 入口脚本。C/C++ 文件仍保留使用 clang + afl-fuzz。
