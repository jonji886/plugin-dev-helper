# Changelog

本项目所有重要改动均记录在此文件中。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/)。

---

## [Unreleased]

### Added
- 新增 `LLMAdapter`、`OpenAICompatibleAdapter`、`InstrumentedAdapter` 和 `FailoverAdapter`，将 SiliconFlow 与官方 DeepSeek 的客户端、计费、可观测性和瞬时错误故障转移从角色路由中解耦
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
