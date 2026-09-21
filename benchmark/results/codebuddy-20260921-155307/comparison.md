# CodeBuddy Benchmark: Baseline vs MCP（真实执行）

> Agent: codebuddy  |  Model: UNAVAILABLE  |  Driver: codebuddy-manual (manual_execution=true)
> Runs/task: 1  |  Tasks: 10  |  Commit: 63bfee0f0b9ce561c0b99cc789a7a31ce4b3f294
> 控制变量：唯一差异为是否访问 plugin-dev-helper MCP。
> Token/Cost 无法从 CodeBuddy 程序化获取，标记 UNAVAILABLE。

## Experiment

- 目的：定量验证 Coding Agent（CodeBuddy）接入 Plugin Dev Helper MCP 后在真实插件 SDK 开发任务上的行为变化。
- 实验设计：同一 CodeBuddy、同一 task prompt、同一初始 fixture，唯一变量为是否可访问 plugin-dev-helper MCP。
- 控制变量：Agent / 任务集 / fixture / evaluator / validator / acceptance 全部固定；模型元数据 CodeBuddy 未提供，标记 UNAVAILABLE。
- CodeBuddy 环境：CLI 存在但无 headless 采集接口；本实验以 manual/import 驱动执行（manual_execution=true）。
- 任务数量：10；重复次数：1 run/task。
- Evaluator：ProjectValidator（源码 API/规则确定性校验）+ acceptance 字符串断言；不使用 LLM Judge。
- MCP 接入点：http://124.223.217.62:8011/mcp（已部署 Plugin Dev Helper MCP）。
- Baseline 的 MCP：工作区不写入 .codebuddy/mcp.json，无 MCP trace。

## 汇总

| Metric | Baseline | MCP | Delta |
|---:|---:|---:|---:|
| Task Success Rate | 0.6 | 1.0 | 0.4 |
| First Pass Success Rate | 0.6 | 1.0 | 0.4 |
| API Correctness | 0.7 | 1.0 | 0.3 |
| Constraint Violation Rate | 0.1 | 0.0 | -0.1 |
| Hallucination Rate | 0.3 | 0.0 | -0.3 |
| Abstention Correct Rate | 0.5 | 1.0 | 0.5 |
| Mean Latency (ms) | UNAVAILABLE | UNAVAILABLE | - |
| Token | UNAVAILABLE | UNAVAILABLE | - |

## Per-task

| Task | Mode | Success | Failure Type | Abstained | Trace F1 | Symbol Cov |
|---|---|---|---|---|---|---|
| T01 | baseline | True | - | False | - | - |
| T02 | baseline | True | - | False | - | - |
| T03 | baseline | False | API_HALLUCINATION | False | - | - |
| T04 | baseline | False | RULE_VIOLATION | False | - | - |
| T05 | baseline | True | - | False | - | - |
| T06 | baseline | True | - | False | - | - |
| T07 | baseline | True | - | False | - | - |
| T08 | baseline | False | API_HALLUCINATION | False | - | - |
| T09 | baseline | False | API_HALLUCINATION | False | - | - |
| T10 | baseline | True | - | True | - | - |
| T01 | mcp | True | - | False | 0.6667 | 1.0 |
| T02 | mcp | True | - | False | 1.0 | 1.0 |
| T03 | mcp | True | - | False | 0.5 | 0.5 |
| T04 | mcp | True | - | False | 0.6667 | 1.0 |
| T05 | mcp | True | - | False | 0.6667 | 1.0 |
| T06 | mcp | True | - | False | 0.8 | 1.0 |
| T07 | mcp | True | - | False | 0.5 | 1.0 |
| T08 | mcp | True | - | False | 0.8 | 1.0 |
| T09 | mcp | True | - | True | 0.6667 | 1.0 |
| T10 | mcp | True | - | True | 0.8 | 1.0 |

## MCP 使用行为（仅 MCP 条件）

| Metric | MCP | Baseline |
|---|---|---|
| Tool Selection Precision | 0.8833 | N/A |
| Tool Selection Recall | 0.65 | N/A |
| Tool Selection F1 | 0.7067 | N/A |
| Symbol Coverage | 0.95 | N/A |
| Redundant Call Rate | 0.0 | N/A |
| Sequence Compliance | 1.0 | N/A |
| Avg MCP Calls / Task | 2.5 | N/A |

## Failure Analysis

- API_HALLUCINATION: 3
- RULE_VIOLATION: 1

## Limitations

- fixture 为最小 TypeScript 工程，不等价于真实宿主插件运行环境
- CodeBuddy 模型可能具有随机性；本执行 runs=1，不声称统计稳定
- token / cost 无法从 CodeBuddy 程序化获取，标记 UNAVAILABLE
- baseline 是否真正禁用了全局 MCP 取决于 CodeBuddy 配置合并行为（已披露）

**真实 Coding Agent 执行状态：EXECUTED（manual / import）**