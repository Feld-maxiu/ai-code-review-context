"""agent 运行入口的辅助模块，供 tests/test_pipeline_resilience.py 使用。

本模块仅作为测试桩存在，真正的 agent 运行逻辑在 security_agent/cli.py 中。
"""

import argparse
import json
import sys
from typing import Any, Dict, Optional


def load_agent_input(
    input_file: Optional[str] = None,
    stdin: Any = None,
) -> Dict[str, Any]:
    """从文件或 stdin 加载 agent 输入 JSON，并补全默认字段。"""

    source = stdin or sys.stdin
    if input_file:
        with open(input_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.load(source)

    # 补全 security_config 默认值
    if "security_config" not in payload:
        payload["security_config"] = {}
    security_config = payload["security_config"]
    security_config.setdefault("cwe_focus", [])
    security_config.setdefault("enable_dynamic_validation", False)
    security_config.setdefault("sarif_output", False)

    return payload


def parse_args() -> argparse.Namespace:
    """解析 CLI 参数，返回 Namespace。"""

    parser = argparse.ArgumentParser(description="agent runner")
    parser.add_argument("--input-file", default=None, help="input JSON file")
    parser.add_argument("--output", default="result.json", help="output file path")
    return parser.parse_args()
