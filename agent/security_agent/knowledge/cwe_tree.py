import json
import os
from typing import Any, Dict, List, Optional


class CWETree:
    def __init__(self, cwe_file: Optional[str] = None, auto_load_defaults: bool = True):
        self.entries: List[Dict[str, Any]] = []
        self.hierarchy: Dict[str, List[str]] = {}
        self._entry_index: Dict[str, int] = {}
        if auto_load_defaults:
            self._load_default_cwe_tree()
        if cwe_file and os.path.exists(cwe_file):
            self.load_from_file(cwe_file)

    def load_from_file(self, file_path: str):
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            self.entries = data
        elif isinstance(data, dict):
            self.entries = data.get("entries", [])
            self.hierarchy = data.get("hierarchy", {})

        self._rebuild_index()

    def _rebuild_index(self):
        self._entry_index = {}
        for i, entry in enumerate(self.entries):
            cwe_id = entry.get("cwe_id", "")
            if cwe_id:
                self._entry_index[cwe_id] = i

    def add_entry(self, entry: Dict[str, Any]):
        cwe_id = entry.get("cwe_id", "")
        standardized = {
            "cwe_id": cwe_id,
            "name": entry.get("name", ""),
            "description": entry.get("description", ""),
            "extended_description": entry.get("extended_description", ""),
            "preconditions": entry.get("preconditions", []),
            "consequences": entry.get("consequences", []),
            "detection_methods": entry.get("detection_methods", []),
            "potential_mitigations": entry.get("potential_mitigations", []),
            "exploitability": entry.get("exploitability", "medium"),
            "environment_dependencies": entry.get("environment_dependencies", []),
            "code_examples": entry.get("code_examples", []),
            "parent_cwe": entry.get("parent_cwe", ""),
            "related_cwes": entry.get("related_cwes", []),
            "severity": entry.get("severity", "medium"),
            "likelihood_of_exploit": entry.get("likelihood_of_exploit", "medium"),
            "phase": entry.get("phase", "implementation"),
        }
        self.entries.append(standardized)
        if cwe_id:
            self._entry_index[cwe_id] = len(self.entries) - 1

        parent = standardized.get("parent_cwe", "")
        if parent:
            self.hierarchy.setdefault(parent, []).append(cwe_id)

    def get_entry(self, cwe_id: str) -> Optional[Dict[str, Any]]:
        idx = self._entry_index.get(cwe_id)
        if idx is not None:
            return self.entries[idx]
        return None

    def get_children(self, cwe_id: str) -> List[Dict[str, Any]]:
        children_ids = self.hierarchy.get(cwe_id, [])
        return [self.entries[self._entry_index[cid]]
                for cid in children_ids if cid in self._entry_index]

    def get_ancestors(self, cwe_id: str) -> List[Dict[str, Any]]:
        ancestors = []
        parent_map = {}
        for pid, children in self.hierarchy.items():
            for cid in children:
                parent_map[cid] = pid

        current = cwe_id
        visited = set()
        while current in parent_map and current not in visited:
            visited.add(current)
            current = parent_map[current]
            entry = self.get_entry(current)
            if entry:
                ancestors.append(entry)

        return list(reversed(ancestors))

    def get_full_path(self, cwe_id: str) -> List[Dict[str, Any]]:
        ancestors = self.get_ancestors(cwe_id)
        entry = self.get_entry(cwe_id)
        if entry:
            ancestors.append(entry)
        return ancestors

    def validate_vulnerability(
        self,
        cwe_id: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        entry = self.get_entry(cwe_id)
        if not entry:
            return {
                "valid": False,
                "reason": f"未找到CWE条目: {cwe_id}",
                "confidence": 0.0
            }

        preconditions = entry.get("preconditions", [])
        unmet_preconditions = []
        for precond in preconditions:
            precond_key = precond.lower().replace(" ", "_")
            if not context.get(precond_key, False):
                unmet_preconditions.append(precond)

        if unmet_preconditions:
            return {
                "valid": False,
                "reason": f"不满足前置条件: {unmet_preconditions}",
                "confidence": 0.0,
                "filter_stage": "precondition_check"
            }

        exploitability = entry.get("exploitability", "medium")
        exploitability_scores = {"low": 0.3, "medium": 0.6, "high": 0.9, "critical": 1.0}
        exploit_score = exploitability_scores.get(exploitability, 0.5)

        if context.get("exploit_mitigated", False):
            exploit_score *= 0.3

        env_deps = entry.get("environment_dependencies", [])
        env_satisfied = True
        for dep in env_deps:
            dep_key = dep.lower().replace(" ", "_")
            if not context.get(dep_key, True):
                env_satisfied = False
                break

        if not env_satisfied:
            return {
                "valid": False,
                "reason": "环境依赖不满足，标记为假阳",
                "confidence": exploit_score,
                "filter_stage": "environment_check",
                "is_false_positive": True
            }

        severity_scores = {"low": 0.3, "medium": 0.5, "high": 0.7, "critical": 0.9}
        severity_score = severity_scores.get(entry.get("severity", "medium"), 0.5)

        likelihood_scores = {"low": 0.3, "medium": 0.5, "high": 0.7}
        likelihood_score = likelihood_scores.get(
            entry.get("likelihood_of_exploit", "medium"), 0.5
        )

        confidence = (exploit_score + severity_score + likelihood_score) / 3.0

        return {
            "valid": confidence > 0.2,
            "reason": f"CWE验证通过，置信度: {confidence:.2f}",
            "confidence": confidence,
            "filter_stage": "passed"
        }

    def search(self, keyword: str) -> List[Dict[str, Any]]:
        keyword_lower = keyword.lower()
        results = []
        for entry in self.entries:
            text = json.dumps(entry, ensure_ascii=False).lower()
            if keyword_lower in text:
                results.append(entry)
        return results

    def get_all_entries(self) -> List[Dict[str, Any]]:
        return self.entries

    def export_to_json(self, file_path: str):
        data = {
            "entries": self.entries,
            "hierarchy": self.hierarchy
        }
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _load_default_cwe_tree(self):
        default_entries = [
            {
                "cwe_id": "CWE-1000",
                "name": "Research Concepts",
                "description": "CWE-1000研究概念视图，包含所有弱点类别",
                "extended_description": "该视图旨在促进对弱点的研究，包含所有弱点及其关系",
                "preconditions": [],
                "consequences": [],
                "detection_methods": [],
                "potential_mitigations": [],
                "exploitability": "low",
                "environment_dependencies": [],
                "code_examples": [],
                "parent_cwe": "",
                "related_cwes": [],
                "severity": "low",
                "likelihood_of_exploit": "low",
                "phase": "research"
            },
            {
                "cwe_id": "CWE-89",
                "name": "SQL注入",
                "description": "在SQL命令中使用的特殊元素未被正确中和",
                "extended_description": "当应用程序将用户输入直接拼接到SQL查询中而不做适当处理时，攻击者可以注入恶意SQL代码",
                "preconditions": ["用户输入可达SQL查询构造点"],
                "consequences": ["数据泄露", "数据篡改", "权限提升"],
                "detection_methods": ["静态分析", "动态测试", "模糊测试"],
                "potential_mitigations": ["使用参数化查询", "输入校验", "最小权限原则"],
                "exploitability": "high",
                "environment_dependencies": ["数据库连接可用"],
                "code_examples": [
                    {
                        "vulnerable": 'sprintf(query, "SELECT * FROM users WHERE name=\'%s\'", userInput)',
                        "fixed": 'stmt = prepare("SELECT * FROM users WHERE name=?"); bind(stmt, userInput)'
                    }
                ],
                "parent_cwe": "CWE-74",
                "related_cwes": ["CWE-77", "CWE-943"],
                "severity": "critical",
                "likelihood_of_exploit": "high",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-77",
                "name": "命令注入",
                "description": "在操作系统命令中使用的特殊元素未被正确中和",
                "extended_description": "应用程序将用户输入拼接到系统命令中执行，攻击者可以注入额外的命令",
                "preconditions": ["用户输入可达命令执行函数"],
                "consequences": ["远程代码执行", "系统控制"],
                "detection_methods": ["静态分析", "模糊测试"],
                "potential_mitigations": ["避免使用shell命令", "使用参数化API", "输入校验"],
                "exploitability": "high",
                "environment_dependencies": ["系统shell可用"],
                "code_examples": [
                    {
                        "vulnerable": 'system("ping " + userInput)',
                        "fixed": 'execve("/bin/ping", ["ping", userInput], NULL)'
                    }
                ],
                "parent_cwe": "CWE-74",
                "related_cwes": ["CWE-78", "CWE-89"],
                "severity": "critical",
                "likelihood_of_exploit": "high",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-120",
                "name": "缓冲区溢出",
                "description": "复制缓冲区时未检查输入大小，导致越界写入",
                "extended_description": "使用strcpy等不安全函数时，如果源数据大于目标缓冲区，会覆盖相邻内存",
                "preconditions": ["使用不安全字符串拷贝函数", "源数据大小超过目标缓冲区"],
                "consequences": ["代码执行", "程序崩溃", "信息泄露"],
                "detection_methods": ["静态分析", "模糊测试", "动态分析"],
                "potential_mitigations": ["使用安全函数", "边界检查", "栈保护"],
                "exploitability": "high",
                "environment_dependencies": ["栈/堆布局可预测"],
                "code_examples": [
                    {
                        "vulnerable": "char buf[10]; strcpy(buf, userInput);",
                        "fixed": "char buf[10]; strncpy(buf, userInput, sizeof(buf)-1); buf[9]='\\0';"
                    }
                ],
                "parent_cwe": "CWE-119",
                "related_cwes": ["CWE-121", "CWE-122"],
                "severity": "high",
                "likelihood_of_exploit": "high",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-416",
                "name": "Use-After-Free",
                "description": "引用已释放的内存",
                "extended_description": "内存被释放后，指针仍然被使用，可能导致任意代码执行或信息泄露",
                "preconditions": ["内存被释放后仍有引用"],
                "consequences": ["代码执行", "程序崩溃"],
                "detection_methods": ["静态分析", "动态分析", "模糊测试"],
                "potential_mitigations": ["指针置NULL", "使用智能指针", "避免悬垂指针"],
                "exploitability": "medium",
                "environment_dependencies": ["堆分配器行为可预测"],
                "code_examples": [
                    {
                        "vulnerable": "free(ptr); *ptr = 0;",
                        "fixed": "free(ptr); ptr = NULL;"
                    }
                ],
                "parent_cwe": "CWE-825",
                "related_cwes": ["CWE-415"],
                "severity": "high",
                "likelihood_of_exploit": "medium",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-190",
                "name": "整数溢出",
                "description": "整数运算结果超出类型的表示范围",
                "extended_description": "在算术运算或类型转换中发生溢出，可能导致内存分配不足或逻辑错误",
                "preconditions": ["涉及小尺寸整数运算", "无溢出检查"],
                "consequences": ["缓冲区溢出", "逻辑错误"],
                "detection_methods": ["静态分析", "模糊测试"],
                "potential_mitigations": ["使用安全整数库", "范围检查", "使用大尺寸类型"],
                "exploitability": "medium",
                "environment_dependencies": ["数据类型大小固定"],
                "code_examples": [
                    {
                        "vulnerable": "int result = a * b;",
                        "fixed": "long long result = (long long)a * b; if(result > INT_MAX) ..."
                    }
                ],
                "parent_cwe": "CWE-682",
                "related_cwes": ["CWE-191"],
                "severity": "high",
                "likelihood_of_exploit": "medium",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-134",
                "name": "格式化字符串漏洞",
                "description": "使用外部控制的格式字符串",
                "extended_description": "printf等函数的格式字符串由外部控制时，攻击者可以读取或写入任意内存",
                "preconditions": ["格式字符串由用户控制"],
                "consequences": ["信息泄露", "任意内存写入"],
                "detection_methods": ["静态分析", "模糊测试"],
                "potential_mitigations": ["使用固定格式字符串", "printf(\"%s\", userInput)"],
                "exploitability": "high",
                "environment_dependencies": ["栈内存布局可预测"],
                "code_examples": [
                    {
                        "vulnerable": "printf(userInput)",
                        "fixed": "printf(\"%s\", userInput)"
                    }
                ],
                "parent_cwe": "CWE-74",
                "related_cwes": ["CWE-89", "CWE-77"],
                "severity": "high",
                "likelihood_of_exploit": "high",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-476",
                "name": "空指针解引用",
                "description": "对可能为NULL的指针进行解引用操作",
                "extended_description": "当指针可能为NULL时未做检查直接解引用，导致程序崩溃",
                "preconditions": ["分配可能失败", "未做NULL检查"],
                "consequences": ["程序崩溃", "拒绝服务"],
                "detection_methods": ["静态分析", "模糊测试"],
                "potential_mitigations": ["NULL检查", "使用异常处理"],
                "exploitability": "low",
                "environment_dependencies": ["内存分配可能失败"],
                "code_examples": [
                    {
                        "vulnerable": "char *p = malloc(100); strcpy(p, data);",
                        "fixed": "char *p = malloc(100); if(p) strcpy(p, data);"
                    }
                ],
                "parent_cwe": "CWE-252",
                "related_cwes": [],
                "severity": "medium",
                "likelihood_of_exploit": "low",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-367",
                "name": "TOCTOU竞态条件",
                "description": "检查和使用之间存在的竞态条件",
                "extended_description": "在文件操作中，检查文件状态和使用文件之间存在时间窗口，攻击者可在此窗口内修改文件",
                "preconditions": ["access和open之间无锁保护"],
                "consequences": ["权限绕过", "文件篡改"],
                "detection_methods": ["静态分析", "动态分析"],
                "potential_mitigations": ["使用文件描述符操作", "原子操作"],
                "exploitability": "medium",
                "environment_dependencies": ["并发执行环境", "共享文件系统"],
                "code_examples": [
                    {
                        "vulnerable": "if(access(file, F_OK)==0) fd = open(file, O_RDONLY);",
                        "fixed": "fd = open(file, O_RDONLY); if(fd >= 0) fstat(fd, &st);"
                    }
                ],
                "parent_cwe": "CWE-362",
                "related_cwes": [],
                "severity": "high",
                "likelihood_of_exploit": "medium",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-22",
                "name": "路径遍历",
                "description": "对外部输入的文件路径名限制不当",
                "extended_description": "用户输入参与文件路径构造时，可能使用../等特殊字符访问预期之外的目录",
                "preconditions": ["用户输入未过滤可达文件操作函数"],
                "consequences": ["信息泄露", "文件覆盖"],
                "detection_methods": ["静态分析", "模糊测试"],
                "potential_mitigations": ["路径规范化", "白名单校验", "chroot"],
                "exploitability": "high",
                "environment_dependencies": ["文件系统可访问"],
                "code_examples": [
                    {
                        "vulnerable": "fopen(userPath, \"r\")",
                        "fixed": "realpath(base_dir + userPath, resolved); if(strncmp(resolved, base_dir, len)==0) fopen(resolved, \"r\")"
                    }
                ],
                "parent_cwe": "CWE-73",
                "related_cwes": [],
                "severity": "high",
                "likelihood_of_exploit": "high",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-798",
                "name": "硬编码凭据",
                "description": "代码中包含硬编码的凭据",
                "extended_description": "密码、密钥等敏感信息直接硬编码在代码中，可能被逆向工程或代码审查泄露",
                "preconditions": ["字符串内容可能是凭据"],
                "consequences": ["凭据泄露", "未授权访问"],
                "detection_methods": ["静态分析", "密钥扫描"],
                "potential_mitigations": ["使用环境变量", "使用密钥管理服务", "配置文件加密"],
                "exploitability": "high",
                "environment_dependencies": ["代码可被访问"],
                "code_examples": [
                    {
                        "vulnerable": 'const char* password = "admin123";',
                        "fixed": "const char* password = getenv(\"DB_PASSWORD\");"
                    }
                ],
                "parent_cwe": "CWE-259",
                "related_cwes": ["CWE-257"],
                "severity": "high",
                "likelihood_of_exploit": "high",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-74",
                "name": "注入",
                "description": "特殊元素在输出中未被正确中和",
                "extended_description": "各种注入漏洞的父类，包括SQL注入、命令注入等",
                "preconditions": ["外部输入可达解释器/处理器"],
                "consequences": ["代码执行", "数据泄露"],
                "detection_methods": ["静态分析", "模糊测试"],
                "potential_mitigations": ["输入校验", "输出编码", "参数化"],
                "exploitability": "high",
                "environment_dependencies": ["对应解释器可用"],
                "code_examples": [],
                "parent_cwe": "CWE-1000",
                "related_cwes": ["CWE-77", "CWE-89", "CWE-134"],
                "severity": "high",
                "likelihood_of_exploit": "high",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-119",
                "name": "内存缓冲区边界操作不当",
                "description": "在内存缓冲区边界内操作不当",
                "extended_description": "内存操作的通用弱点类别，包括所有类型的缓冲区错误",
                "preconditions": ["内存操作未做边界检查"],
                "consequences": ["代码执行", "信息泄露"],
                "detection_methods": ["静态分析", "模糊测试", "动态分析"],
                "potential_mitigations": ["边界检查", "安全库函数"],
                "exploitability": "high",
                "environment_dependencies": ["内存布局"],
                "code_examples": [],
                "parent_cwe": "CWE-1000",
                "related_cwes": ["CWE-120", "CWE-121", "CWE-122"],
                "severity": "high",
                "likelihood_of_exploit": "high",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-682",
                "name": "数值计算不正确",
                "description": "数值计算中的错误",
                "extended_description": "包括整数溢出、下溢、截断等数值计算问题",
                "preconditions": ["涉及数值运算"],
                "consequences": ["逻辑错误", "缓冲区溢出"],
                "detection_methods": ["静态分析", "模糊测试"],
                "potential_mitigations": ["范围检查", "安全数值库"],
                "exploitability": "medium",
                "environment_dependencies": ["数据类型定义"],
                "code_examples": [],
                "parent_cwe": "CWE-1000",
                "related_cwes": ["CWE-190", "CWE-191"],
                "severity": "medium",
                "likelihood_of_exploit": "medium",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-362",
                "name": "竞态条件",
                "description": "使用共享资源的并发执行中的竞态条件",
                "extended_description": "多个线程或进程访问共享资源时未正确同步，导致意外行为",
                "preconditions": ["并发执行环境", "共享资源"],
                "consequences": ["数据损坏", "权限绕过"],
                "detection_methods": ["静态分析", "动态分析", "压力测试"],
                "potential_mitigations": ["加锁", "原子操作", "事务处理"],
                "exploitability": "medium",
                "environment_dependencies": ["多核/多线程环境"],
                "code_examples": [],
                "parent_cwe": "CWE-1000",
                "related_cwes": ["CWE-367"],
                "severity": "high",
                "likelihood_of_exploit": "medium",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-73",
                "name": "文件名或路径的外部控制",
                "description": "允许用户控制文件系统操作中使用的路径名",
                "extended_description": "文件操作中使用的路径名由外部控制时的安全风险",
                "preconditions": ["用户可控制文件路径", "文件操作未做限制"],
                "consequences": ["任意文件读写", "信息泄露"],
                "detection_methods": ["静态分析", "模糊测试"],
                "potential_mitigations": ["路径白名单", "规范化后校验"],
                "exploitability": "high",
                "environment_dependencies": ["文件系统权限"],
                "code_examples": [],
                "parent_cwe": "CWE-1000",
                "related_cwes": ["CWE-22"],
                "severity": "high",
                "likelihood_of_exploit": "high",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-259",
                "name": "硬编码密码的使用",
                "description": "使用硬编码的密码或密钥",
                "extended_description": "代码中包含硬编码密码的通用类别",
                "preconditions": ["含固定凭据字符串"],
                "consequences": ["凭据泄露"],
                "detection_methods": ["静态分析"],
                "potential_mitigations": ["密钥管理服务", "环境变量"],
                "exploitability": "high",
                "environment_dependencies": ["代码可被审查"],
                "code_examples": [],
                "parent_cwe": "CWE-1000",
                "related_cwes": ["CWE-798"],
                "severity": "high",
                "likelihood_of_exploit": "high",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-825",
                "name": "过期指针解引用",
                "description": "对过期或无效的指针进行解引用",
                "extended_description": "包括Use-After-Free等指针生命周期管理问题",
                "preconditions": ["指针已失效"],
                "consequences": ["任意代码执行"],
                "detection_methods": ["静态分析", "动态分析"],
                "potential_mitigations": ["指针置NULL", "智能指针"],
                "exploitability": "medium",
                "environment_dependencies": ["内存分配器行为"],
                "code_examples": [],
                "parent_cwe": "CWE-1000",
                "related_cwes": ["CWE-416"],
                "severity": "high",
                "likelihood_of_exploit": "medium",
                "phase": "implementation"
            },
            {
                "cwe_id": "CWE-252",
                "name": "未检查返回值",
                "description": "函数返回值未被检查",
                "extended_description": "函数调用后未检查返回值，可能导致未处理的错误状态",
                "preconditions": ["函数可能返回错误"],
                "consequences": ["未处理异常", "程序崩溃"],
                "detection_methods": ["静态分析"],
                "potential_mitigations": ["检查所有返回值"],
                "exploitability": "low",
                "environment_dependencies": [],
                "code_examples": [],
                "parent_cwe": "CWE-1000",
                "related_cwes": ["CWE-476"],
                "severity": "medium",
                "likelihood_of_exploit": "low",
                "phase": "implementation"
            }
        ]

        for entry in default_entries:
            self.add_entry(entry)