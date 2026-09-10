# Bad Case 收集规范

用于沉淀 MCP 服务在真实使用中暴露的问题，反哺检索与工具设计。

## 记录字段

每个 Bad Case 建一个 Markdown 文件 `badcases/<日期>-<编号>.md`，包含：

```markdown
# <编号> <一句话标题>

- **日期**：
- **场景**：本地 Agent 调用了哪个 MCP 工具 / 查询意图是什么
- **输入**：用户的自然语言需求 / Agent 的查询词
- **期望**：正确结果应该是什么
- **实际**：MCP 返回了什么 / Agent 得到了什么
- **根因**：检索未命中 / 类型解析错误 / 工具描述误导 / 数据缺失
- **改进**：需要做什么（改工具、补数据、调 prompt 等）
- **状态**：open / fixed
```

## 常见分类

- `retrieval_miss`：语义检索未召回正确 API，但知识库里有。
- `data_gap`：知识库本身缺失该 API / 类型 / 描述。
- `resolve_error`：`get_api` / `get_type` 路径解析错误。
- `hallucination`：Agent 编造了不存在的 API，MCP 未能阻止。
- `tool_design`：工具描述或返回结构误导了 Agent。

## 流转

1. 发现后按模板记录为 `open`。
2. 定位根因并修复（改代码 / 补数据）。
3. 复测通过后标记 `fixed`，并在 `results/comparison.md` 中留痕。
