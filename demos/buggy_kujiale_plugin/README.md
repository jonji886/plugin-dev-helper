# Buggy Kujiale Plugin

这个 Demo 故意包含 UI 直接调用 IDP、VM 使用 DOM/fetch/setTimeout、Promise 缺少
`catch` 以及 UI/VM action 不匹配等问题。

用 MCP 的 `validate_plugin_project` 扫描此目录：

```json
{
  "platform": "kujiale",
  "path": "demos/buggy_kujiale_plugin"
}
```

结果中的 `findings` 会给出规则 ID、文件、行号、风险、建议和 confidence。
修复后再次扫描，目标是 `passed: true`、`score: 100`。评分由当前 Rule Layer
和 Validator 实际计算，不在 Demo 文档中预填固定分数。
