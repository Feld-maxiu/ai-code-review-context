"""网络安全 agent 与上下文模块的对接入口。

完整流程：
1. POST /context/index 构建索引
2. GET /context/tasks?review_dimension=security 领取任务
3. GET /context/task-package/{task_id}?repo_id={repo_id} 获取任务包
4. 运行 detector → verifier → fuzzer 流水线
5. POST /context/task-feedback 回传状态
"""

import argparse
import json
import logging
import sys
from typing import List, Optional

from .context_client import ContextServiceClient
from .context_runner import ContextAgentRunner
from .main import SecurityAgentOrchestrator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("security_agent.cli")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="网络安全 agent 上下文模块对接入口")
    parser.add_argument(
        "--context-url",
        default="http://127.0.0.1:8000",
        help="上下文服务地址，默认 http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--repo-id",
        required=True,
        help="仓库分析 ID，后续所有接口共用同一个 repo_id",
    )
    parser.add_argument(
        "--repo-path",
        required=True,
        help="上下文服务本机可访问的 Python 仓库路径",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite 索引文件路径（可选）",
    )
    parser.add_argument(
        "--review-dimension",
        default="security",
        help="评审维度，安全 agent 固定为 security",
    )
    parser.add_argument(
        "--output-dir",
        default="security_results",
        help="结果输出目录，默认 security_results",
    )
    parser.add_argument("--api-key", default="", help="LLM API Key")
    parser.add_argument("--api-base", default="https://api.openai.com/v1", help="LLM API Base")
    parser.add_argument("--model", default="gpt-4", help="LLM 模型名")
    parser.add_argument("--no-fuzzing", action="store_true", help="禁用动态模糊测试")
    parser.add_argument("--target-binary", default=None, help="可选的目标二进制路径，用于 fuzzer")
    parser.add_argument("--context-depth", type=int, default=2, help="任务局部图深度，默认 2")
    parser.add_argument(
        "--max-context-files",
        type=int,
        default=3,
        help="相关上下文最大文件数，默认 3",
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=30.0,
        help="单次 LLM 调用超时（秒），默认 30",
    )
    parser.add_argument(
        "--pipeline-timeout",
        type=float,
        default=120.0,
        help="单个任务流水线整体超时（秒），默认 120",
    )
    parser.add_argument(
        "--fuzzer-timeout",
        type=float,
        default=60.0,
        help="fuzzer 单任务超时（秒），默认 60",
    )
    args = parser.parse_args(argv)

    client = ContextServiceClient(
        base_url=args.context_url,
        timeout=60.0,
    )
    logger.info(f"开始为 repo_id={args.repo_id} 构建索引，路径={args.repo_path}")
    index_result = client.build_index(
        repo_id=args.repo_id,
        repo_path=args.repo_path,
        db_path=args.db_path,
    )
    repo_summary = index_result.get("repo_summary", {})
    logger.info(f"索引构建完成：{repo_summary}")

    orchestrator = SecurityAgentOrchestrator(
        api_key=args.api_key,
        api_base=args.api_base,
        model=args.model,
        enable_fuzzing=not args.no_fuzzing,
        llm_timeout=args.llm_timeout,
        pipeline_timeout=args.pipeline_timeout,
        fuzzer_timeout=args.fuzzer_timeout,
    )
    runner = ContextAgentRunner(
        context_client=client,
        orchestrator=orchestrator,
        agent_name="security-agent",
    )

    logger.info(f"开始领取并处理 {args.review_dimension} 维度任务")
    run_result = runner.run_tasks_for_repo(
        repo_id=args.repo_id,
        review_dimension=args.review_dimension,
        agent_name="security-agent",
        output_dir=args.output_dir,
        target_binary=args.target_binary,
        context_depth=args.context_depth,
        max_context_files=args.max_context_files,
    )

    completed = len(run_result["completed"])
    failed = len(run_result["failed"])
    logger.info(
        f"任务处理完成：成功 {completed}，失败 {failed}，总计 {run_result['total_tasks']}"
    )

    summary_file = f"{args.output_dir}/run_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(run_result, f, indent=2, ensure_ascii=False)
    logger.info(f"运行摘要已写入：{summary_file}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
