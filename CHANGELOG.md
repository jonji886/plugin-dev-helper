# Changelog

本项目所有重要改动均记录在此文件中。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/)。

---

## [Unreleased]

### Added
- 新增 **CodeBuddy Agent Driver 抽象层** `scripts/agent_drivers/`（`base.py` 定义 `BenchmarkDriver` / `AgentRunResult` / `CodeBuddyConfig` / trace normalizer；`reference.py` 为确定性 stand-in；`codebuddy.py` 提供 `CodeBuddyManualDriver`（prepare/import 真实回灌）与 `CodeBuddyCLIDriver`（headless 不可采集时显式报错，不伪造）），把真实 Coding Agent 与 Benchmark Harness 解耦
- `scripts/run_coding_agent_benchmark.py` 重构：新增 `--driver reference|codebuddy-manual|codebuddy-cli`、`--runs N`、隔离工作区（`results/workspaces/<mode>/<task>-r<run>`）、baseline/MCP 的 `.codebuddy/mcp.json` 切换、CodeBuddy trace → canonical schema 归一化、`--prepare/--import` 手动工作流，以及 `aggregate.json` / `per-task` / `Failure Analysis` / `comparison.md` / `metadata.json` 输出；不可得数据标记 UNAVAILABLE
- **执行真实 CodeBuddy Baseline vs MCP 实验**（10 任务 × 2 条件 × 1 run/task，manual/import）：结果 `benchmark/results/codebuddy-20260921-155307/`（Task Success Rate 0.6 → 1.0，Hallucination 0.3 → 0.0，Abstention 0.5 → 1.0；token/cost/latency 标记 UNAVAILABLE）
- 新增 `tests/test_agent_drivers.py`（全部 mock，不启动真实 CodeBuddy）：config 解析、工作区隔离、baseline/MCP 切换、trace 归一化、timeout、agent 失败、非法输出、缺失 token、聚合与报告生成
- 重构 `README.md` 与 `spec.md` 以匹配当前真实代码（MCP 9 工具、Task Runtime / Repair / Validator / Observability / Evaluation 分层、真实 Benchmark 结果），删除已失真的旧描述（如「真实 Agent 实验恒为 NOT_RUN」「external driver / --endpoint」）
- 更新 `benchmark/README.md`：L2/L3 状态改为「已有真实轨迹 / 已执行（manual）」并记录真实 Baseline vs MCP 结果

### Changed
- `scripts/agent_drivers/reference.py` 承接原 harness 内 `ReferenceDriver`；旧 `ExternalDriver`（`--endpoint` stub）移除

### Added
- 新增 **P0 Coding Agent 工程化**（见 `docs/architecture-coding-agent-p0.md` 与 `docs/adr/ADR-001~004`）：项目级确定性校验、有界 Repair Loop、Task Runtime、Coding Agent Benchmark harness
- 重新引入 `validate_plugin_project` MCP 工具（新实现 `mcp_server/services/project_validator.py`）：STRUCTURE / MANIFEST / RULE / API / BUILD 五类确定性检查，复用与查询侧同一份知识索引（`data/knowledge`）和规则层（`rules/kujiale`）；宿主运行期规则（CORS / OPTIONS / 探活）显式标记“需宿主验证”，不伪造通过；`dist` / `build` / `node_modules` 不参与校验
- 新增 `agent/runtime` 包：`Task / TaskStep / Checkpoint / ArtifactVersion / TraceEvent` 模型、`TaskStateMachine`（非法迁移显式报错）、SQLite repositories（重启可恢复）、`Error Taxonomy`（Retry 按分类而非裸 except）
- 新增有界 Repair Loop `agent/runtime/repair.py`：以 Validator Issue 为证据的最小必要修复，超过 `max_repair_attempts` 进入 FAILED，每轮产生新 ArtifactVersion 与 TraceEvent
- 新增 Coding Agent Benchmark harness `scripts/run_coding_agent_benchmark.py`：Baseline vs MCP 控制变量对比、确定性评分（acceptance + 源文件级 API/RULE 判定，不依赖 LLM Judge）；`reference` 驱动结果仅为管线自检（**不是 Agent 能力分数**）；自检产物见 `benchmark/results/agent_benchmark_{runs,summary,comparison}`
- 新增 MCP 工具级黄金评测集 `benchmark/mcp_golden.json`（58 条用例，覆盖 8 个工具的 happy_path / not_found / boundary / degradation / unsupported / error_safety）与执行器 `scripts/run_mcp_eval.py`：输出 Contract Pass Rate、Status Accuracy、Determinism Rate（同参重复调用比对，剔除 `request_id`/`duration_ms`）、Not-Found Precision、Task Symbol Coverage 与分工具 P50/P95，并按 `benchmark/mcp_gate.json` 判定 GATE
- 新增 Agent 调用轨迹评分器 `scripts/check_agent_trace.py`，首次真正消费 `tasks.json` 中的 `mcp_tools_expected` 与 `reference_symbols`：输出工具选择 P/R/F1、符号覆盖率、冗余调用率、序列合规（constraints 先于 scaffold、get_api/get_type 先于 validate_api_usage）与拒答正确性；`--self-test` 用明确标注的合成轨迹验证评分器自身
- 新增 10 个 benchmark 参考解 `benchmark/solutions/T01..T10/vm.ts` 与轨迹落盘规范 `benchmark/traces/README.md`
- 新增 MCP 多轮调用序列评测 `benchmark/mcp_sequences.json`（6 条链路）与执行器 `scripts/run_mcp_sequences.py`：入参支持 `{{steps.N.path}}` 占位符引用前序步骤返回，输出 Sequence Pass Rate、Step Pass Rate 与 Cross-Step Reference Rate
- 新增多次调用稳定性验收 `scripts/check_mcp_stability.py`：串行多轮 + 并发、成功率、跨轮次幂等、P50/P95/P99 与漂移、故障注入后的恢复、状态泄漏、request_id 唯一性与遥测落库一致性；默认写临时 SQLite，不污染生产 telemetry
- `MetricsStore.mcp_metrics()` 新增每工具 `p50/p95/p99_duration_ms` 与 `min/max_duration_ms`（此前只有 `avg_duration_ms`，无法判断延迟漂移），并新增 `tests/test_mcp_metrics.py` 覆盖单样本与多样本场景

### Changed
- `scripts/check_benchmark_task.py` 支持 `--mode initial|solution|both`：`initial` 校验 fixture 初始态按预期失败（证明 acceptance 非真空），`solution` 写入参考解验收后自动还原；开始尊重此前被忽略的 `acceptance.typecheck` / `acceptance.build` 开关；新增 `--json` 导出结构化结果
- `KJL-COMM-001 / 002` 规则语义修正为 require 型（“存在通信行为时必须使用合法方式”，detection 新增 `mode: require` + `trigger`，scope 分别限定 ui / vm）：原实现把“发现合法模式 `window.parent.postMessage` / `defaultFrame.postMessage`”判为违规，导致合法工程被误报 HIGH
- `KJL-VM-006` 检测收紧：仅“明显的 IDP Promise 链（`.then`）缺 catch”上报，裸 `await IDP.xAsync()` 不做静态判定（宁可少报）
- `benchmark/tasks.json` 新增 `reference_solution` 与 `acceptance.initial_expect_fail` 字段，并在 `meta.field_notes` 中说明各字段由哪个脚本消费
- CI 在构建知识库后新增两步确定性评测：`scripts/run_mcp_eval.py --repeat 3` 与 `scripts/run_mcp_sequences.py`；`check_mcp_stability.py` 因含时间与漂移阈值、在共享 runner 上易误报，保持手动执行

### Fixed
- 修复 T08 验收断言 `not_contains:dat` 与 `contains:data` 自相矛盾（`data` 含子串 `dat`，任何正确解都必然失败），改为 `not_contains:dat:`
- 修复 `object_literal_keys` 不支持 ES6 简写属性（`{ miniappId, data }`），此前会误报 `uploadDataAsync` 缺少必填字段
- 修复 API / RULE 校验扫描到 `dist` / `build` 编译残留产物的问题（fixture 常含过期的错误编译 JS，会产生无法修复的假问题），一律排除
- 修复 Benchmark harness 误用测试用小型知识库导致合法 API（`IDP.Miniapp.view.*`、`IDP.Custom.*`）被判“幻觉”的问题：评测与线上 MCP 现在共用同一份 `data/knowledge` 与规则层
- 修复 T09 验收断言 `not_contains:IDP.Miniapp.closeMiniapp` 与 `expected_behavior` 冲突（需求允许用注释说明，但注释中出现该符号名即判失败），改为 `not_contains:IDP.Miniapp.closeMiniapp(`，只拦截真实调用
- `run_mcp_eval.py` 在评测前预热 embedding 检索，避免首个 `search_docs` 把本地模型加载时间计入延迟指标

### Removed
- 删除 `validate_plugin_project` 工具及其底层 `PluginProjectValidator`（`mcp_server/services/kujiale.py`）与 `KujialeDevServerService`（`mcp_server/services/dev_server.py`，仅 `inspect_project` 被前者依赖）；插件工程的静态校验改为由开发者本地自行验证 manifest/frame/main、CORS 与 OPTIONS 预检

### Added
- `get_plugin_scaffold` 新增 `stack` 参数，支持 `react-ts-webpack` 技术栈（React 17 + TypeScript + Webpack 5 + `@manycore/idp-sdk`），生成与官方 webpack-react-ts 样例一致的 `manifest.json`/`src/main.ts`/`src/view.tsx`/`src/page.html`/`webpack.config.js`/`tsconfig.json`/`package.json`/`README.md`
- `get_plugin_scaffold` 的 `vanilla` 原生 HTML 骨架替换为与官方 `miniapp-template` 一致的 `page.html`/`page.js`/`vm.js`（本地服务由 `http-server --cors` 提供，移除自建 `dev-server.js`）；开发插件时按用户选择传入 `stack=vanilla`（原生 HTML）或 `react-ts-webpack`（React）
- 知识库文档（`docs/rag/工具插件代码结构.md`、`data/knowledge/*工具插件代码结构.md`）的 UI 文件命名由 `ui.html` 对齐为 `page.html`，并明确 UI 逻辑脚本 `page.js`（与官方模板一致）；`main` 示例由 `code.js` 对齐为 `vm.js`
- `dev_server.inspect_project` 识别 `http-server --cors` 等 CORS 能力（含 webpack-dev-server/vite/serve），避免对使用静态服务的 React 栈误报 `KJL-DEV-003`/`KJL-DEV-004`
- Rule Layer 新增 `KJL-MANIFEST-007`：使用 webpack/vite/rollup 等打包工具时，`main` 必须指向构建产物（如 `build/main.js`），且 `validate_plugin_project`/`probe_plugin_dev_server` 应针对 `build/` 目录而非源码 `src/`；该约束在 `react-ts-webpack` 脚手架中显式透出
- `demos/webpack-react-ts/` 新增与官方样例一致的真实 golden template（manifest/main/view/webpack 配置齐全），供参考与端到端校验
- `react-ts-webpack` 脚手架新增 `guidance`：VM 中调用的 IDP API 应先 `get_api` 核实是否存在，否则 `validate_plugin_project` 会对 `main.js` 报 `KJL-API-001`（unknown_api）
- 新增酷家乐工具插件 Guardrail Rule Layer、`get_plugin_constraints`、`get_plugin_scaffold` 和 `validate_plugin_project`，覆盖 Manifest、UI/VM 运行时、API 使用、Promise 异常与消息 action 匹配，并增加合法/违规 Fixture 与 Buggy Demo
- 新增 `LLMAdapter`、`OpenAICompatibleAdapter`、`InstrumentedAdapter` 和 `FailoverAdapter`，将 SiliconFlow 与官方 DeepSeek 的客户端、计费、可观测性和瞬时错误故障转移从角色路由中解耦
- 新增请求级故障转移验收测试，覆盖超时切换、响应路由元数据、token/cost 指标隔离和认证错误失败率；修复 Agent 异常被错误记为成功的问题
- 增加官方 DeepSeek `deepseek-v4-flash` 兜底映射、超时/重试配置和价格记录；Vision 默认不做不确定的图片能力兜底
- 更新 SiliconFlow 四角色价格配置：Router/Main/Reason/Vision 采用官方价格中心数据，GLM-5.1 Pro 支持 32K 输入分档，并记录价格来源与抓取日期
- 新增 `prompts/manifest.json`，为每个 Prompt 版本维护 status、created_at 和 description；Trace 同步记录 Prompt 元数据
- 完善 Langfuse Trace 的检索 scores/top-k/source/knowledge version、Citation validity、错误类型、温度和总耗时字段
- Evaluation 页面展示 Badcase 创建时间；专项测试覆盖 Prompt Registry、成本分档、Feedback 异常和 Langfuse Fail Open
- Prompt A/B 报告增加数据集哈希、当前模型路由、价格配置、执行时间、硬超时和失败样本信息；完成当前四角色配置下 24 条 Golden Dataset 的真实 v1/v2 评测
- 正式落地 `ROUTER/MAIN/REASON/VISION` 四角色模型路由：意图分类、常规问答、复杂推理和图片理解分别使用独立模型；新增 `/api/chat` 图片输入、角色状态和路由指标
- 前端增加图片选择、预览和移除能力；后端限制单次最多 3 张图片，并限制为 HTTPS 或 `data:image` 输入，图片不写入会话历史
- 按硅基流动实际模型目录校正 Router 为 `Qwen/Qwen3-8B`，并将中转冷启动超时调整为 60 秒；四角色均完成真实请求冒烟验证
- 新增四类模型路由验收集与 `scripts/check_model_routing.py`，支持校验角色命中率、批次失败率及 `/api/metrics` 的分角色延迟/token/成本统计
- 服务启动阶段预热 embedding；Router 增加 15 秒、0 重试、256 token 的独立预算，远程分类失败时回退到确定性 `infer_task_type()`
- 复杂推理回退规则覆盖“分析/排查/分步骤/可能原因”等表达，`/api/chat` 返回 provider 与 route reason，便于路由验收和运行归因
- 增加可解释答案评分、人工确认回归集加载、失败请求查询接口和 SQLite 反馈候选 JSONL 导出脚本，形成“反馈 → 归因 → 回归”的评测闭环
- 统一知识构建流水线：SDK 与 `docs/rag/` 文档合并为同一索引后统一构建 Chroma 向量库
- 聊天接口增加结构化 `citations` 字段，包含知识单元 ID、来源、SDK 版本和源行号；前端新增来源展示与复制
- 新增 5 个回归测试，覆盖分块解析行号、RAG 增量同步、知识索引合并、引用装配和会话 API
- 增加 Python 3.11 与开发测试依赖配置
- 前端接入安全的 Markdown 富文本渲染，支持标题、列表、表格、引用、链接、行内代码和代码块复制
- SDK 检索增加语义与词法混合召回，并新增“保存设计方案接口”回归评测样本
- SQLite 持久化会话、请求指标与用户反馈闭环；新增 `/api/ready`、`/api/metrics` 和反馈接口
- GitHub Actions CI：后端单元测试、前端 lint 与生产构建
- 独立检索质量门禁脚本，CI 重建索引后校验 Recall@5 ≥ 85%
- Golden Dataset 按当前 `index.d.ts v1.83.0` 与 RAG 文档重新校准，增加显式关键词和知识不足时的拒答样例
- 增加可选 Langfuse Observability、Prompt 版本目录、应用层模型路由和 token/cost 追踪
- 扩展 SQLite 反馈模型，支持负反馈自动生成 badcase、人工状态管理和晋升回归评测集
- 增加离线 Prompt A/B 评测与 Regression Gate，以及 `/evaluation` 管理页面
- 约束 NumPy / PyTorch / Transformers / Sentence Transformers 兼容版本，修复干净环境无法构建 embedding 的问题
- 评测入口自动加载 `.env`；答案评分增加 Markdown、API 标识和自然语言变体归一化
- 正式接入 SiliconFlow OpenAI-compatible API，支持 DeepSeek、千问、GLM 三模型按任务路由并保留显式 profile 覆盖
- 路由任务类型改由原始用户问题确定，避免 LLM 重写内容或固定提示词导致模型 profile 漂移

### Removed
- 删除 `probe_plugin_dev_server` 工具及其底层的 `KujialeDevServerService.probe`/`_start_process` 等运行探测代码（含 `npm start` 执行面）。该工具依赖同机本地回环地址，远端 MCP 下不可用；运行期 manifest/frame/main、CORS 与 OPTIONS 预检改由开发者本地自行验证

### Fixed
- 修复共享 LLM 客户端累计 token/成本被重复写入单请求指标的问题；增加请求级上下文隔离，并限制 RAG 证据上下文预算，降低长文档请求的延迟与成本放大
- 修复宽窗口下前端根节点按内容收缩导致主聊天区域只占左侧、右侧出现空白的问题，并完善侧栏和输入区的响应式宽度
- 移除未使用且已从新版 LangChain 删除的 `langchain.callbacks.base` 导入及冗余的 `langchain`、`langchain-community` 依赖，修复 Python 3.11 CI 单测失败
- 修复前端将反馈接口的 `204 No Content` 当作 JSON 解析，点击“有帮助 / 无帮助”时报错的问题
- 修复 `CHROMA_PATH`、`KNOWLEDGE_PATH` 和 `GRAPH_PATH` 未传入 Agent 检索与图扩展链路的问题
- 统一 `/api/metrics` 的统计窗口为最近 1,000 条请求，并将同一回答的重复反馈改为更新最新选择
- 移除过期的评测运行产物，避免历史结果被误判为当前质量基线
- 修复 CI 在干净环境执行 `pip install -e '.[dev]'` 时无法自动发现多个顶级 Python 包的问题
- 修复 SDK 多分块解析时对已解析符号重复累计行偏移的问题
- 修复 SDK 重建覆盖 RAG 文档索引、RAG 内容未变仍触发重建的问题
- 修复 `DELETE /api/chat/history` 未传会话 ID 时未实际清除全部会话的问题
- 将 CORS 从任意来源收紧为通过 `FRONTEND_ORIGINS` 显式配置
- 修复 `export function` 包装下 JSDoc 丢失，导致中文 API 描述无法进入知识库的问题
- 修复 RAG 文档命中时只使用向量 chunk，导致使用规范、代码示例和并发限制被截断的问题
- 修复拒答样例携带合法证据引用时被误判为 Citation Invalid 的评测逻辑
- `/api/ready` 改为检查实际生效 provider，并返回脱敏后的模型路由摘要

### Changed
- `.env.example`、README 和运行时说明从三模型 profile 更新为四角色配置，并保留旧 `MODEL_*` 与 `DEFAULT/FAST/STRONG_LLM_*` 兼容路径
- 重构 README 的新人阅读路径：将完整首次运行指南前置，新增首次运行与项目阅读图示、需求价值与量化衡量方式，明确 RAG 同步和完整重建的适用场景，并统一 `.env` 格式说明
- 聊天请求在线程中执行，避免 embedding 与同步 LLM 调用阻塞 FastAPI 事件循环
- README 新增 Docker 部署章节：双容器架构、部署步骤、环境变量与踩坑记录；项目结构补充 `deploy/` 目录说明
- `deploy/docker-compose.yml` 前端构建显式指定 `dockerfile: ../deploy/frontend/Dockerfile`（context 指向项目根 `frontend/` 源码），前端 Dockerfile 独立于源码目录维护，避免同步源码时误删；服务器部署目录同步重组为 `deploy/` 布局
- 系统提示词改为禁止模型自行虚构来源，来源由后端基于检索结果附加
- 移除未在当前评测脚本中使用的 RAGAS 与 datasets 运行时依赖
- 重构 README，补充 Enterprise Developer Copilot 定位、架构、质量闭环和工程取舍

## [0.1.0] - 2026-06-09

### Added

#### 项目初始化
- 项目目录结构搭建：`app/`、`agent/`、`sdk_parser/`、`knowledge_builder/`、`graph_builder/`、`vector_store/`、`frontend/`、`eval/`、`scripts/`、`prompts/`、`tests/`
- Python 依赖配置 `pyproject.toml`：FastAPI、LangGraph、Chroma、sentence-transformers、tree-sitter、Ragas 等
- Node.js 依赖配置 `package.json`：`@manycore/idp-sdk@^1.83.0`
- 环境变量配置 `.env`：DeepSeek API Key、HF_HUB_OFFLINE、TRANSFORMERS_OFFLINE

#### SDK 解析器 (`sdk_parser/`)
- 基于 tree-sitter 的 TypeScript `.d.ts` AST 解析器，支持 ~19,000 行 SDK 文件分块解析
- 符号数据模型 (`models.py`)：`Symbol`、`Parameter`、`Property`、`Method`、`TypeParameter`、`JSDocComment`
- 支持的 AST 节点类型：`InterfaceDeclaration`、`FunctionDeclaration`、`ClassDeclaration`、`TypeAliasDeclaration`、`EnumDeclaration`、`NamespaceDeclaration`、`lexical_declaration`（const）
- 处理 `declare global { ... }` 分块解析、命名空间嵌套、属性名提取、ERROR 节点容错
- JSDoc 注释解析，支持 `@deprecated`、`@vm-type` 等标签
- 自动生成符号别名（短名称 + 命名空间路径组合）
- 类型引用提取（`extract_type_refs_from_text`），过滤 TypeScript 内置类型
- 成功解析 1032 个 SDK 符号

#### 知识库构建 (`knowledge_builder/`)
- `KnowledgeBuilder`：为每个符号生成 Markdown 文档和 JSON metadata 文件
- Markdown 输出包含：类型、来源、命名空间、参数表、属性表、方法列表、枚举值、泛型参数、引用关系
- JSON metadata 包含：完整结构化信息（参数、属性、方法、引用、别名等）
- 生成知识库索引文件 `_index.json`
- `GraphBuilder`：基于 NetworkX 构建类型依赖图（DiGraph），序列化 JSON 格式
- 依赖图展开（`expand`）：支持按深度展开指定符号的引用链
- 输出：1032 个 Markdown 文件 + 1032 个 JSON 文件 + `dependency_graph.json`

#### 向量存储 (`vector_store/`)
- 基于 Chroma 的持久化向量存储（`PersistentClient`）
- Embedding 模型：`sentence-transformers/all-MiniLM-L6-v2`（本地缓存，延迟加载）
- 支持去重索引（同一 ID 多次出现时去重）
- 批量向量化（batch_size=100），避免内存溢出
- 语义检索（`search`）：TopK 查询，返回 ID、metadata、文档内容、距离

#### LangGraph Agent (`agent/`)
- 5 节点 Agent 流程：`IntentRouter` → `QueryRewrite` → `Retriever` → `GraphExpander` → `AnswerGenerator`
- `IntentRouter`：基于 LLM 识别问题意图（api/sdk/param/code/general）
- `QueryRewrite`：多轮对话时重写查询，补全上下文
- `Retriever`：知识库向量检索 TopK
- `GraphExpander`：依赖链展开，读取相关 Markdown 文件扩展上下文
- `AnswerGenerator`：基于系统 Prompt + 知识库上下文生成答案，要求代码示例和来源引用
- `SessionManager`：内存会话管理，支持创建/查询/删除会话
- `AgentRunner`：统一运行入口，整合 Agent 与会话管理
- LLM 集成：DeepSeek v4（通过 `ChatOpenAI` 兼容接口）

#### FastAPI 后端 (`app/`)
- `GET /api/health`：健康检查
- `POST /api/chat`：对话接口，支持 `query` + `session_id`
- `GET /api/chat/history`：获取会话历史（支持按 session_id 查询或列出所有会话）
- `DELETE /api/chat/history`：清除会话历史
- CORS 配置：允许所有来源

#### Next.js 前端 (`frontend/`)
- 聊天界面组件：`ChatMessage`（消息展示 + 代码高亮）、`ChatInput`（输入框）、`ChatHistory`（历史会话侧边栏）
- 类型定义 `types/chat.ts`：消息、会话等接口
- API 服务封装 `services/chatService.ts`：封装后端 API 调用
- 响应式布局，Tailwind CSS 样式

#### 评测体系 (`eval/`)
- 测试数据集 `test_data.json`：覆盖 API/SDK/参数/代码等类别
- 检索评测：`Recall@1/3/5`，目标 Recall@5 ≥ 85%
- 答案评测：Answer Correctness（关键词匹配，目标 ≥ 80%）、Faithfulness（引用检测，目标 ≥ 90%）、Source Reference Rate
- 评测结果输出到 `eval_results.json`

#### 数据处理流水线 (`scripts/`)
- `run_pipeline.py`：Phase 1 完整流水线（SDK 解析 → 知识构建 → 依赖图 → 向量索引）

#### Prompt 模板 (`prompts/`)
- `system.md`：系统 Prompt，定义 AI 角色为某设计平台开放平台官方技术支持

### Changed
- 初始化 Git 仓库，提交 Phase 1 初始版本（51 个文件）
- 更新 `.gitignore`：添加 `.next/`、`frontend/.npm-cache/` 排除规则
- `README.md` 添加更新日志链接

### Fixed
- sentence-transformers 联网超时导致知识库检索阻塞：通过设置 `HF_HUB_OFFLINE=1` 和 `TRANSFORMERS_OFFLINE=1` 强制使用本地缓存模型
- tree-sitter 解析 `declare global` 分块问题：采用按 `export {}; declare global {` 边界分块解析策略
- 向量索引重复 ID 问题：构建索引时按 ID 去重
- NumPy 兼容性：Chroma + sentence-transformers 的 Embedding 数据类型适配
