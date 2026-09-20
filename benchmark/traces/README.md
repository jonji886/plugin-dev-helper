# Agent 调用轨迹（Trace）

`tasks.json` 里的 `mcp_tools_expected` 与 `reference_symbols` 由
`scripts/check_agent_trace.py` 消费。要拿到 L2 指标，必须先把真实 Agent 跑任务时
的 MCP 调用序列落盘成本文件。

> 轨迹必须是**真实 Agent 运行产物**。不要用脚本生成的调用序列冒充 Agent 结果，
> 否则 L2 指标失去意义。`check_agent_trace.py --self-test` 内置的是合成轨迹，
> 只用于验证评分器自身逻辑，已在输出中明确标注。

## 落盘位置

`benchmark/traces/<条件>-<agent>-<日期>.json`，例如：

```
benchmark/traces/with-mcp-codebuddy-2026-09-19.json
benchmark/traces/without-mcp-codebuddy-2026-09-19.json
```

## 格式

```json
{
  "run_id": "with-mcp-codebuddy-2026-09-19",
  "agent": "codebuddy/claude-sonnet-4.5",
  "condition": "with_mcp",
  "notes": "可选：本次运行的补充说明（模型温度、工具版本等）",
  "tasks": [
    {
      "task_id": "T01",
      "accepted": true,
      "abstained": false,
      "calls": [
        {"seq": 1, "tool": "get_api", "arguments": {"symbol": "IDP.Miniapp.exit"}, "status": "ok"},
        {"seq": 2, "tool": "validate_api_usage", "arguments": {"code": "IDP.Miniapp.exit();"}, "status": "ok"}
      ],
      "symbols_queried": ["IDP.Miniapp.exit"],
      "final_code_ref": "可选：最终代码落盘路径或片段"
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `run_id` | 是 | 一次运行的唯一标识 |
| `agent` | 是 | Agent 与模型标识，用于横向对比 |
| `condition` | 是 | `with_mcp` / `without_mcp` |
| `tasks[].task_id` | 是 | 对应 `tasks.json` 的任务 id |
| `tasks[].accepted` | 是 | `check_benchmark_task.py --mode solution` 之外的**实际**验收结果（Agent 改完代码后跑 typecheck/build/断言） |
| `tasks[].abstained` | 否 | 是否明确说明了「该 API 不存在」。`reject_hallucination` 类任务（T09）必须为 `true` |
| `tasks[].calls[]` | 是 | MCP 调用序列，按 `seq` 升序；`without_mcp` 条件下为空数组 |
| `tasks[].calls[].tool` | 是 | 工具名 |
| `tasks[].calls[].arguments` | 是 | 入参。用于冗余调用识别与符号提取 |
| `tasks[].calls[].status` | 否 | 工具返回的 `status`，便于事后分析 |
| `tasks[].symbols_queried` | 否 | Agent 实际查询过的符号；缺省时从 `arguments.symbol` / `arguments.name` 自动提取 |

## 评分

```bash
# 单个轨迹
python scripts/check_agent_trace.py benchmark/traces/with-mcp-codebuddy-2026-09-19.json

# 整个目录（对比无 MCP / 有 MCP）
python scripts/check_agent_trace.py --dir benchmark/traces
```

## 指标口径

| 指标 | 口径 |
|---|---|
| Tool Selection P/R/F1 | 相对 `mcp_tools_expected`：P = 命中/实际调用，R = 命中/期望 |
| Symbol Coverage | `reference_symbols` 中被实际查询到的比例 |
| Redundant Call Rate | 同 `(tool, 归一化入参)` 的重复调用 / 总调用 |
| Sequence Compliance | `get_plugin_constraints` 先于 `get_plugin_scaffold`；`get_api`/`get_type` 先于 `validate_api_usage` |
| Abstention Correctness | `reject_hallucination` 类任务必须 `abstained=true` |
