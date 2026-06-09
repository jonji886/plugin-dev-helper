# SPEC-DS-Full-002：插件开发 AI Agent

版本: v1.0
负责人: Season
状态: Draft

---

# 1. 项目背景

## 1.1 业务背景

某设计平台开放平台提供插件二次开发能力：

* OpenAPI
* JS/TS SDK
* 插件开发框架

问题：

* 开发者咨询时等待人工回复，效率低
* 技术支持需重复回答常规问题，成本高
* 新人插件开发上手慢

## 1.2 项目目标

* 自动回答 API / SDK / 插件开发问题
* 支持多轮对话
* 代码示例 + 参数说明
* 来源引用，保证可信
* 支持持续优化，降低人工成本

---

# 2. 核心能力

1. 文档问答
2. SDK问答
3. 参数说明
4. 代码生成
5. 多轮对话
6. 前端即时聊天界面

---

# 3. 技术架构

```text
TypeScript SDK (*.d.ts)
        ↓
AST Parser (Python)
        ↓
Knowledge Builder
        ↓
Type Dependency Graph
        ↓
Vector Store (Chroma / 免费Embedding)
        ↓
LangGraph Agent
        ↓
DeepSeek v4
        ↓
FastAPI 后端
        ↓
Next.js / React 前端聊天界面
```

---

# 4. 技术栈

* 后端：Python 3.11 + FastAPI
* LLM：DeepSeek v4
* Agent：LangGraph + LangChain
* Embedding：`sentence-transformers/all-MiniLM-L6-v2`（免费）
* Vector Store：Chroma
* 前端：Next.js + React + Tailwind CSS
* Eval：Ragas

---

# 5. SDK知识库构建

## 5.1 输入

* SDK路径：`node_modules/@manycore/idp-sdk/`
* 文件类型：`*.d.ts`

## 5.2 AST解析对象

* InterfaceDeclaration
* FunctionDeclaration
* ClassDeclaration
* TypeAliasDeclaration
* EnumDeclaration
* NamespaceDeclaration

## 5.3 知识单元拆分

* Method级
* Interface级
* Type级
* Enum级

每个生成 **Markdown + metadata JSON**：

```json
{
  "id": "IDP.Miniapp.exit",
  "type": "function",
  "references": [],
  "source": "miniapp.d.ts",
  "sdkVersion": "1.83.0",
  "aliases":["exit","Miniapp.exit","退出小程序"]
}
```

---

# 6. SDK 符号命名规则

### 6.1 命名规则

全局唯一 ID：

```
Namespace.Path.SymbolName
```

### 6.2 示例

| 类型        | 原始                            | 生成全名                     |
| --------- | ----------------------------- | ------------------------ |
| Function  | `exit()` in `IDP.Miniapp`     | `IDP.Miniapp.exit`       |
| Const     | `view` in `IDP.Miniapp`       | `IDP.Miniapp.view`       |
| Interface | `ElementId` in `IDP.DB.Types` | `IDP.DB.Types.ElementId` |
| Enum      | `DetectionRuleType` in `IDP`  | `IDP.DetectionRuleType`  |

### 6.3 引用链

```text
DetectionResult.pass
      ↓
IDP.DB.Types.ElementId
```

### 6.4 Alias

每个 Symbol 生成别名，用于自然语言映射：

```json
{
  "id": "IDP.Miniapp.exit",
  "aliases":["exit","Miniapp.exit","退出小程序"]
}
```

---

# 7. Type Dependency Graph

* 分析引用关系
* 构建 Graph 用于 LangGraph Graph Expansion
* 确保多轮问答可展开所有相关类型

---

# 8. 向量化与检索

* Embedding：`sentence-transformers/all-MiniLM-L6-v2`
* Vector Store：Chroma
* TopK 检索：5
* Graph Expansion：引用链展开上下文

---

# 9. LangGraph Agent 节点

1. Intent Router：识别问题类型（API/SDK/参数/代码）
2. Query Rewrite：补全上下文
3. Retrieve：知识库检索 TopK
4. Graph Expansion：依赖链展开
5. Answer Generator：生成答案 + 代码示例 + 来源
6. Memory：会话历史管理

---

# 10. Prompt规则

```text
你是某设计平台开放平台官方 AI 技术支持。
职责：
- 帮助开发者解决 API/SDK/插件问题
规则：
1. 优先使用知识库回答
2. 不允许编造不存在的 API
3. 必须返回来源文件和 SDK 版本
4. 不确定时明确说明
```

---

# 11. 前端设计

* Next.js + React
* 聊天界面：消息列表 + 输入框 + 历史会话
* 代码高亮
* 显示来源引用

示例组件：

```tsx
<ChatMessage user="developer" text="IDP.Miniapp.exit怎么用？" />
<ChatMessage user="agent"
 text={`IDP.Miniapp.exit 退出小程序，无参数
示例代码：
IDP.Miniapp.exit()
来源: miniapp.d.ts v1.83.0`} />
```

---

# 12. Eval评测体系（带示例）

### 12.1 检索评测

* Recall@1/3/5
* 示例：

```json
{
  "question":"如何获取方案ID",
  "expected_documents":["调用接口 IDP.Design.getDesignId()"]
}
{
  "question":"如何退出小程序怎么用？",
  "reference_answer":"调用 IDP.Miniapp.exit() ，退出小程序"
}
```

目标：Recall@5 ≥ 85%
说明：保证 RAG 检索到正确文档

---

### 12.2 答案正确率

* Answer Correctness ≥ 80%
* Faithfulness ≥ 90%

示例：

```json
{
  "question":"如何获取方案ID",
  "expected_documents":["调用接口 IDP.Design.getDesignId()"]
}
{
  "question":"如何获取方案JSON？",
  "reference_answer":"调用 IDP.Custom.Design.Export.getDesignFullJsonAsync()"
}
```


---


# 13. 项目目录结构

```text
project/
├── app/                # FastAPI后端
├── frontend/           # Next.js前端
├── sdk_parser/         # d.ts解析
├── knowledge_builder/  # Markdown + metadata生成
├── graph_builder/      # Type Dependency Graph
├── vector_store/       # Chroma管理
├── agent/              # LangGraph + DeepSeek v4
├── prompts/            # Prompt模板
├── eval/               # 测试集 + 自动评测
├── scripts/            # 数据处理脚本
└── tests/              # 单元测试
```

---

# 14. MVP验收标准

* SDK解析 + AST抽取完成
* Method/Interface/Type/Enum生成知识单元
* Dependency Graph完成
* Chroma向量库搭建
* LangGraph Agent + DeepSeek v4完成多轮问答
* 前端聊天界面可展示答案 + 代码 + 来源
* Eval可执行，输出指标

---

# 15. V2规划

* Neo4j + GraphRAG
* OpenAPI解析
* SDK版本差异分析
* 自动生成开发者文档
* 自动生成代码示例
* 工单系统集成
* 企业微信机器人接入
