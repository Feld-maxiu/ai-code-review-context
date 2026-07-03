import json
import os
from typing import Any, Dict, List, Optional


class SASTRuleBase:
    def __init__(self, rules_file: Optional[str] = None, auto_load_defaults: bool = True):
        self.rules: List[Dict[str, Any]] = []
        self._rule_index: Dict[str, List[int]] = {}
        if auto_load_defaults:
            self._load_default_rules()
        if rules_file and os.path.exists(rules_file):
            self.load_from_file(rules_file)

    def load_from_file(self, file_path: str):
        with open(file_path, "r", encoding="utf-8") as f:
            if file_path.endswith(".json"):
                rules = json.load(f)
            elif file_path.endswith(".jsonl"):
                rules = [json.loads(line) for line in f if line.strip()]
            else:
                raise ValueError(f"不支持的文件格式: {file_path}")

        for rule in rules:
            self.add_rule(rule)

    def add_rule(self, rule: Dict[str, Any]):
        rule_id = rule.get("rule_id", f"SAST-{len(self.rules):04d}")
        rule["rule_id"] = rule_id

        standardized = {
            "rule_id": rule_id,
            "name": rule.get("name", ""),
            "cwe_id": rule.get("cwe_id", ""),
            "severity": rule.get("severity", "medium"),
            "description": rule.get("description", ""),
            "pattern": rule.get("pattern", ""),
            "ql_query": rule.get("ql_query", ""),
            "language": rule.get("language", "cpp"),
            "precision": rule.get("precision", "medium"),
            "tags": rule.get("tags", []),
            "preconditions": rule.get("preconditions", []),
            "examples": rule.get("examples", []),
            "false_positive_patterns": rule.get("false_positive_patterns", []),
            "references": rule.get("references", []),
            "source": rule.get("source", "codeql"),
        }
        self.rules.append(standardized)

        idx = len(self.rules) - 1
        self._index_rule(idx, standardized)

    def _index_rule(self, idx: int, rule: Dict[str, Any]):
        cwe_id = rule.get("cwe_id", "")
        if cwe_id:
            self._rule_index.setdefault(cwe_id, []).append(idx)

        severity = rule.get("severity", "")
        if severity:
            self._rule_index.setdefault(f"severity:{severity}", []).append(idx)

        language = rule.get("language", "")
        if language:
            self._rule_index.setdefault(f"language:{language}", []).append(idx)

        for tag in rule.get("tags", []):
            self._rule_index.setdefault(f"tag:{tag}", []).append(idx)

    def search_by_cwe(self, cwe_id: str) -> List[Dict[str, Any]]:
        indices = self._rule_index.get(cwe_id, [])
        return [self.rules[i] for i in indices]

    def search_by_severity(self, severity: str) -> List[Dict[str, Any]]:
        indices = self._rule_index.get(f"severity:{severity}", [])
        return [self.rules[i] for i in indices]

    def search_by_language(self, language: str) -> List[Dict[str, Any]]:
        indices = self._rule_index.get(f"language:{language}", [])
        return [self.rules[i] for i in indices]

    def search_by_tag(self, tag: str) -> List[Dict[str, Any]]:
        indices = self._rule_index.get(f"tag:{tag}", [])
        return [self.rules[i] for i in indices]

    def get_all_rules(self) -> List[Dict[str, Any]]:
        return self.rules

    def export_to_json(self, file_path: str):
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.rules, f, indent=2, ensure_ascii=False)

    def _load_default_rules(self):
        default_rules = [
            {
                "rule_id": "SAST-0001",
                "name": "SQL注入检测",
                "cwe_id": "CWE-89",
                "severity": "critical",
                "description": "检测未经过参数化的SQL查询构造，可能导致SQL注入攻击",
                "pattern": "字符串拼接构建SQL查询",
                "ql_query": "import cpp\nfrom SqlConcatenation sql\nwhere sql.isUserInput()\nselect sql, \"SQL注入风险\"",
                "language": "cpp",
                "precision": "high",
                "tags": ["injection", "sql", "tuning"],
                "preconditions": ["用户输入可达SQL查询构造点"],
                "examples": [
                    "sprintf(query, \"SELECT * FROM users WHERE name='%s'\", userInput)"
                ],
                "false_positive_patterns": ["使用预编译语句", "已做输入校验"],
                "references": ["https://cwe.mitre.org/data/definitions/89.html"],
                "source": "codeql"
            },
            {
                "rule_id": "SAST-0002",
                "name": "命令注入检测",
                "cwe_id": "CWE-77",
                "severity": "critical",
                "description": "检测外部输入直接拼接到系统命令中执行",
                "pattern": "外部输入直接传入system/popen/exec函数族",
                "ql_query": "import cpp\nfrom FunctionCall fc\nwhere fc.getTarget().getName() in [\"system\", \"popen\", \"exec\"]\nand fc.getArgument(0).isUserInput()\nselect fc, \"命令注入风险\"",
                "language": "cpp",
                "precision": "high",
                "tags": ["injection", "command"],
                "preconditions": ["用户输入可达命令执行函数"],
                "examples": [
                    "system(\"ping \" + userInput)"
                ],
                "false_positive_patterns": ["使用execve带参数数组", "白名单校验"],
                "references": ["https://cwe.mitre.org/data/definitions/77.html"],
                "source": "codeql"
            },
            {
                "rule_id": "SAST-0003",
                "name": "缓冲区溢出检测",
                "cwe_id": "CWE-120",
                "severity": "high",
                "description": "检测使用不安全的字符串操作函数，如strcpy、sprintf等，可能导致缓冲区溢出",
                "pattern": "使用strcpy/strcat/sprintf等函数，目标缓冲区大小可能不足",
                "ql_query": "import cpp\nfrom FunctionCall fc\nwhere fc.getTarget().getName() in [\"strcpy\", \"strcat\", \"sprintf\", \"gets\"]\nselect fc, \"缓冲区溢出风险\"",
                "language": "cpp",
                "precision": "medium",
                "tags": ["memory", "buffer-overflow"],
                "preconditions": ["使用不安全字符串函数", "目标缓冲区大小不确定"],
                "examples": [
                    "char buf[10]; strcpy(buf, userInput);"
                ],
                "false_positive_patterns": ["已做长度检查", "使用strncpy等安全替代"],
                "references": ["https://cwe.mitre.org/data/definitions/120.html"],
                "source": "codeql"
            },
            {
                "rule_id": "SAST-0004",
                "name": "Use-After-Free检测",
                "cwe_id": "CWE-416",
                "severity": "high",
                "description": "检测内存在释放后被继续使用的情况",
                "pattern": "free/delete后对指针的引用",
                "ql_query": "import cpp\nfrom Variable v, FunctionCall free\nwhere free.getTarget().getName() = \"free\"\nand v.getAnAccess() = free.getArgument(0)\nand v.getAnAccess().getASuccessor+() instanceof Dereference\nselect free, \"Use-After-Free风险\"",
                "language": "cpp",
                "precision": "medium",
                "tags": ["memory", "uaf"],
                "preconditions": ["指针在free后仍有引用"],
                "examples": ["free(ptr); *ptr = 0;"],
                "false_positive_patterns": ["指针置NULL"],
                "references": ["https://cwe.mitre.org/data/definitions/416.html"],
                "source": "codeql"
            },
            {
                "rule_id": "SAST-0005",
                "name": "整数溢出检测",
                "cwe_id": "CWE-190",
                "severity": "high",
                "description": "检测整数运算中可能发生的溢出情况",
                "pattern": "算术运算或类型转换可能导致整数溢出",
                "ql_query": "import cpp\nfrom Operation op\nwhere op instanceof ArithmeticOperation\nand op.getType().getSize() <= 4\nand not op.hasOverflowGuard()\nselect op, \"整数溢出风险\"",
                "language": "cpp",
                "precision": "low",
                "tags": ["numeric", "overflow"],
                "preconditions": ["涉及小尺寸整数运算", "无溢出检查"],
                "examples": ["int result = a * b; // 如果a、b很大"],
                "false_positive_patterns": ["已有范围检查", "使用安全整数库"],
                "references": ["https://cwe.mitre.org/data/definitions/190.html"],
                "source": "codeql"
            },
            {
                "rule_id": "SAST-0006",
                "name": "格式化字符串漏洞",
                "cwe_id": "CWE-134",
                "severity": "high",
                "description": "检测printf等格式化函数中格式字符串可控的情况",
                "pattern": "printf系列函数的格式字符串参数来自外部",
                "ql_query": "import cpp\nfrom FunctionCall fc\nwhere fc.getTarget().getName() in [\"printf\", \"sprintf\", \"fprintf\"]\nand fc.getArgument(0).isUserInput()\nselect fc, \"格式化字符串漏洞\"",
                "language": "cpp",
                "precision": "high",
                "tags": ["injection", "format-string"],
                "preconditions": ["格式字符串由用户控制"],
                "examples": ["printf(userInput);"],
                "false_positive_patterns": ["使用固定格式字符串"],
                "references": ["https://cwe.mitre.org/data/definitions/134.html"],
                "source": "codeql"
            },
            {
                "rule_id": "SAST-0007",
                "name": "空指针解引用",
                "cwe_id": "CWE-476",
                "severity": "medium",
                "description": "检测指针在使用前未做NULL检查的情况",
                "pattern": "函数返回的指针未检查NULL即使用",
                "ql_query": "import cpp\nfrom Variable v, FunctionCall fc\nwhere fc.getTarget().getName() = \"malloc\"\nand v = fc\nand not exists(GuardCondition g | g.ensures(v, not isNull()))\nand v.getAnAccess() instanceof Dereference\nselect fc, \"空指针解引用风险\"",
                "language": "cpp",
                "precision": "medium",
                "tags": ["memory", "null-pointer"],
                "preconditions": ["分配可能失败", "未做NULL检查"],
                "examples": ["char *p = malloc(100); strcpy(p, data);"],
                "false_positive_patterns": ["有NULL检查", "使用了g_malloc等保证不为NULL的分配器"],
                "references": ["https://cwe.mitre.org/data/definitions/476.html"],
                "source": "codeql"
            },
            {
                "rule_id": "SAST-0008",
                "name": "竞态条件-TOCTOU",
                "cwe_id": "CWE-367",
                "severity": "high",
                "description": "检测文件操作中的Time-of-Check-Time-of-Use竞态条件",
                "pattern": "先检查文件状态再操作，中间存在时间窗口",
                "ql_query": "import cpp\nfrom FunctionCall access, FunctionCall open\nwhere access.getTarget().getName() = \"access\"\nand open.getTarget().getName() = \"open\"\nand access.getArgument(0) = open.getArgument(0)\nand access.getASuccessor() = open\nselect access, \"TOCTOU竞态条件\"",
                "language": "cpp",
                "precision": "medium",
                "tags": ["concurrency", "toctou"],
                "preconditions": ["access和open之间无锁保护"],
                "examples": ["if(access(file, F_OK)==0) { fd = open(file, O_RDONLY); }"],
                "false_positive_patterns": ["使用fstat替代", "文件描述符复用"],
                "references": ["https://cwe.mitre.org/data/definitions/367.html"],
                "source": "codeql"
            },
            {
                "rule_id": "SAST-0009",
                "name": "路径遍历检测",
                "cwe_id": "CWE-22",
                "severity": "high",
                "description": "检测文件路径构造中使用用户输入，可能导致目录遍历攻击",
                "pattern": "用户输入参与文件路径构造",
                "ql_query": "import cpp\nfrom FunctionCall fc\nwhere fc.getTarget().getName() in [\"fopen\", \"open\", \"ifstream\"]\nand fc.getArgument(0).isUserInput()\nselect fc, \"路径遍历风险\"",
                "language": "cpp",
                "precision": "high",
                "tags": ["injection", "path-traversal"],
                "preconditions": ["用户输入未过滤即可达文件操作函数"],
                "examples": ["fopen(userPath, \"r\")"],
                "false_positive_patterns": ["路径前缀固定", "已做..过滤"],
                "references": ["https://cwe.mitre.org/data/definitions/22.html"],
                "source": "codeql"
            },
            {
                "rule_id": "SAST-0010",
                "name": "硬编码密钥/密码",
                "cwe_id": "CWE-798",
                "severity": "high",
                "description": "检测代码中存在硬编码的密码、密钥或凭证",
                "pattern": "字符串常量包含疑似密码或密钥",
                "ql_query": "import cpp\nfrom StringLiteral sl\nwhere sl.getValue().regexpMatch(\"(?i).*(password|secret|key|token|api_key).*\")",
                "language": "cpp",
                "precision": "low",
                "tags": ["credentials", "hardcoded"],
                "preconditions": ["字符串内容可能是凭据"],
                "examples": ["const char* password = \"admin123\";"],
                "false_positive_patterns": ["注释中的示例", "配置文件读取"],
                "references": ["https://cwe.mitre.org/data/definitions/798.html"],
                "source": "codeql"
            }
        ]
        for rule in default_rules:
            self.add_rule(rule)