# Buggy Kujiale Plugin

这个 Demo 故意包含 UI 直接调用 IDP、VM 使用 DOM/fetch/setTimeout、Promise 缺少
`catch` 以及 UI/VM action 不匹配等问题。

本 Demo 故意包含违反 Guardrail 的写法，可用 `get_plugin_constraints`（平台组件选 UI/VM、任务描述对应能力）对照学习相关规则
（如 UI 直接调用 IDP、VM 使用 DOM/fetch/setTimeout、Promise 缺少 `catch`、UI/VM action 不匹配等）。
静态扫描工具 `validate_plugin_project` 已移除，运行期问题请按上述约束在本地自行验证。
