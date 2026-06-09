# 插件开发 AI Agent

> 基于 LangGraph + DeepSeek 的 SDK 智能问答助手

## 项目简介

插件开发 AI Agent 是一个面向设计平台开放平台插件开发者的智能问答助手。它能自动回答关于 SDK、API 和插件开发的问题，支持多轮对话、代码示例生成、参数说明和来源引用，帮助开发者减少等待人工回复的时间，降低技术支持成本。

### 核心能力

- **文档问答**：基于 SDK 知识库自动回答开发问题
- **SDK 问答**：解析 `@manycore/idp-sdk` 类型定义，提供精准的 API / 接口 / 类型说明
- **代码生成**：根据问题生成可直接运行的 TypeScript/JavaScript 代码示例
- **参数说明**：详细解释函数参数、返回值类型和枚举值含义
- **多轮对话**：支持上下文感知的连续对话
- **来源引用**：所有回答附带 SDK 版本和源文件引用，保证可信

## 技术架构

```
TypeScript SDK (*.d.ts)
        ↓
AST Parser (tree-sitter)
        ↓
Knowledge Builder
        ↓
Type Dependency Graph
        ↓
Vector Store (Chroma + all-MiniLM-L6-v2)
        ↓
LangGraph Agent
        ↓
DeepSeek v4 (ChatOpenAI)
        ↓
FastAPI 后端
        ↓
Next.js / React 前端聊天界面
```

## 技术栈

| 模块 | 技术 |
|------|------|
| 后端框架 | Python 3.11 + FastAPI |
| LLM | DeepSeek v4 (deepseek-chat) |
| Agent 框架 | LangGraph + LangChain |
| 知识库检索 | Chroma + sentence-transformers (all-MiniLM-L6-v2) |
| SDK 解析 | tree-sitter (TypeScript AST) |
| 前端 | Next.js 16 + React 19 + Tailwind CSS 4 |
| 代码展示 | Monaco Editor |
| 评测 | RAGAS |

## 项目结构

```
├── agent/                  # LangGraph Agent 核心逻辑
│   ├── assistant.py        # Agent 节点定义与状态图
│   └── __init__.py         # Agent Runner 入口
├── app/                    # FastAPI 后端服务
│   └── main.py             # API 路由定义
├── frontend/               # Next.js 前端聊天界面
│   └── src/
│       ├── app/            # Next.js App Router
│       ├── components/     # 聊天 UI 组件
│       ├── services/       # 前端 API 服务
│       └── types/          # TypeScript 类型定义
├── sdk_parser/             # TypeScript AST 解析器
│   ├── parser.py           # tree-sitter 解析逻辑
│   └── models.py           # 符号模型定义
├── knowledge_builder/      # 知识库构建
│   └── builder.py          # Markdown + JSON 生成
├── graph_builder/          # 类型依赖图构建
│   └── __init__.py
├── vector_store/           # 向量存储与检索
│   └── store.py            # Chroma 封装
├── prompts/                # 系统提示词
│   └── system.md           # Agent 系统提示词
├── scripts/                # 工具脚本
│   └── run_pipeline.py     # 端到端流水线
├── eval/                   # 自动评测
│   ├── run_eval.py         # 评测脚本
│   └── test_data.json      # 测试数据集
├── data/                   # 数据目录
│   ├── chroma/             # Chroma 向量数据库
│   ├── knowledge/          # 知识库（Markdown + JSON）
│   └── graph/              # 依赖图数据
├── spec.md                 # 产品规格文档
└── package.json            # Node.js SDK 依赖
```

## 快速开始

### 前置要求

- Python 3.11+
- Node.js 18+
- DeepSeek API Key

### 1. 安装依赖

```bash
# 后端依赖
pip install -r requirements.txt

# 如需使用国内镜像源加速，例如清华源：
# pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 前端依赖
cd frontend && npm install

# SDK 依赖
npm install
```

### 2. 配置环境变量

创建 `.env` 文件：

```env
DEEPSEEK_API_KEY=your_api_key_here
```

### 3. 运行知识库流水线

构建 SDK 知识库（解析 SDK → 构建知识 → 生成依赖图 → 向量索引）：

```bash
python scripts/run_pipeline.py
```

### 4. 启动后端

```bash
uvicorn app.main:app --reload --port 8000
```

### 5. 启动前端

```bash
cd frontend && npm run dev
```

访问 `http://localhost:3000` 即可开始对话。

## LangGraph Agent 节点

1. **Intent Router** — 识别问题类型（API / SDK / 参数 / 代码 / 其他）
2. **Query Rewrite** — 补全多轮对话上下文
3. **Retrieve** — 知识库 Top-K 检索
4. **Graph Expansion** — 类型依赖链展开
5. **Answer Generator** — 生成回答 + 代码示例 + 来源引用
6. **Memory** — 会话历史管理

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/chat` | 对话接口 |
| GET | `/api/chat/history` | 获取会话历史 |

## 评测

项目包含基于 RAGAS 的自动评测框架：

```bash
python eval/run_eval.py
```

评测指标：
- **Recall@1/3/5**：检索召回率
- **Answer Correctness**：答案正确性
- **Faithfulness**：答案忠实度（基于引用检测）

## 更新日志

详见 [CHANGELOG.md](CHANGELOG.md)

## 许可证

本项目仅供内部使用。
