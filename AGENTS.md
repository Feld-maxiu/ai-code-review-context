# AGENTS.md

## 项目背景

本项目是 AI 仓库级代码评审系统中的“上下文处理模块”。

模块目标：
- 接收或读取用户提供的 Python 仓库；
- 扫描并过滤有效文件；
- 使用 Python AST 抽取函数、类、方法、import、decorator 和基础调用关系；
- 构建 SQLite 上下文索引；
- 生成仓库摘要、评审任务卡片；
- 向评审 Agent 暴露按需查询代码上下文的工具接口；
- 记录上下文使用情况并生成覆盖率报告。

## 模块边界

本模块负责：
- 仓库输入与文件扫描；
- Python 代码解析；
- 上下文索引构建；
- 调用关系与路由识别；
- 评审任务生成；
- 上下文工具接口；
- 覆盖率追踪。

本模块不负责：
- 不判断代码是否存在漏洞；
- 不生成最终评审报告；
- 不实现前端页面；
- 不负责多 Agent 调度；
- 第一版不实现完整 MCP Server；
- 第一版不实现多语言解析；
- 第一版不引入向量数据库；
- 第一版不让任务生成依赖 LLM。

## 技术约束

- 开发语言：Python；
- Web 框架：FastAPI；
- 测试框架：pytest；
- 存储：SQLite；
- 第一版解析方式：Python 标准库 ast；
- 所有工具接口必须返回 JSON serializable 对象。

## 推荐目录结构

```text
context/
├── repo_context/
│   ├── ingest/
│   ├── parser/
│   ├── index/
│   ├── store/
│   ├── task/
│   ├── tools/
│   ├── service/
│   └── api/
└── docs/

agent/
└── review_agent/

tests/
├── fixtures/
└── test_xxx.py
```

说明：
- `context/repo_context/` 只存放上下文处理模块代码；
- `context/docs/` 存放上下文模块对外接口、联调和使用说明；
- `agent/review_agent/` 存放下游 agent 调用模块代码；
- `tests/` 存放测试代码；
- `dev_docs/`、`tasks/`、`prompts/` 属于开发说明材料，不属于运行时模块代码；
- 后期接入总项目时，通常只需要交付 `context/repo_context/`、`agent/review_agent/`、必要测试、依赖文件和使用说明，不需要交付开发提示词文件。

## Git 分支工作规则

每当用户要求开始一个新的阶段任务时，必须先执行 Git 分支检查和阶段分支创建/切换。

### 开始阶段前必须执行

1. 先查看当前状态和当前分支：

```bash
git status
git branch --show-current
```

2. 如果当前目录还没有初始化 Git 仓库，必须停止并提醒用户先执行 `git init`，不要自行假设仓库状态。

3. 如果工作区存在未提交修改、未跟踪文件或冲突，不要继续开发，也不要自动覆盖，必须先停止并向用户说明当前状态。

4. 如果工作区干净，则根据当前阶段创建或切换到对应分支。分支命名规则如下：

```text
stage-0-project-skeleton
stage-1-file-scanner
stage-2-ast-parser
stage-3-sqlite-index
stage-4-call-graph-routes
stage-5-context-tools
stage-6-review-tasks
stage-7-coverage-acceptance
```

5. 如果阶段分支不存在，创建新分支：

```bash
git checkout -b <stage-branch-name>
```

6. 如果阶段分支已经存在，切换到该分支：

```bash
git checkout <stage-branch-name>
```

### 开发完成后

- 必须运行该阶段要求的测试；
- 必须再次汇报当前分支和工作区状态；
- 必须汇报修改文件、测试结果和验收结果；
- 默认不要自动 commit。只有用户明确要求“提交”或“自动 commit”时，才可以执行 `git commit`；
- 默认不要自动 push。只有用户明确要求“推送”时，才可以执行 `git push`。

### 重要约束

- 一个阶段只在一个对应分支中完成；
- 不要在 `main` / `master` 分支上直接开发阶段功能；
- 不要跨阶段实现后续功能；
- 如果发现当前分支与当前阶段不匹配，先停止并询问用户；
- 如果用户要求切换新阶段，但上一个阶段仍有未处理修改，先停止并说明，不要强行切换分支。

## 开发要求

- 每次只完成用户指定阶段，不要提前实现后续阶段；
- 保持模块职责单一，不把评审判断逻辑写入上下文模块；
- 任务生成使用确定性规则，不调用 LLM；
- 代码需要有必要中文注释，尤其是核心流程、异常处理和边界判断；
- AST 解析失败不能中断整个仓库解析，应记录错误并继续处理其他文件；
- zip 解压必须防止路径穿越；
- 文件扫描必须跳过 `.git`、`.venv`、`venv`、`__pycache__`、`dist`、`build`、`site-packages`、`node_modules` 等目录；
- 后续阶段需要的能力可以预留 TODO 或接口占位，但不要提前完整实现；
- 除非用户明确要求，不要修改 `dev_docs/`、`context/docs/`、`tasks/`、`prompts/` 下的开发说明文件。

## 测试要求

每个阶段完成后必须：
- 补充或更新 pytest 测试；
- 运行 pytest；
- 给出测试命令和结果；
- 按该阶段任务文件中的验收标准逐条说明是否满足。

## 完成后输出格式

每次完成后，请输出：

1. 本次完成的功能；
2. 新增或修改了哪些文件；
3. 每个文件的作用；
4. 如何运行测试；
5. 测试结果；
6. 当前 Git 分支和工作区状态；
7. 是否有已知问题；
8. 下一阶段建议。
