# Changelog

本项目所有重要改动均记录在此文件中。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/)。

---

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
