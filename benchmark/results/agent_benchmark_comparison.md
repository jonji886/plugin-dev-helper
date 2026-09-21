# Coding Agent Benchmark: Baseline vs MCP

> 控制变量：唯一差异为是否访问 plugin-dev-helper MCP。
> 本结果为 **reference driver（确定性 stand-in）** 的真实校验/评分结果，仅代表 harness 自检。
> 真实 CodeBuddy Baseline vs MCP 实验见 `benchmark/results/codebuddy-*/comparison.md`（`codebuddy-manual` 驱动，manual_execution=true）。

| Metric | BASELINE | MCP | Delta |
|---:|---:|---:|
| Task Success | 0.2 | 1.0 | 0.8 |
| First Pass | 0.2 | 1.0 | 0.8 |
| API Correctness | 0.8 | 1.0 | 0.2 |
| Constraint Violation | 0.0 | 0.0 | 0.0 |
| Hallucination | 0.2 | 0.0 | -0.2 |
| Latency(ms) | 46.72 | 21.83 | -24.89 |
| Token | UNAVAILABLE | UNAVAILABLE | - |

**说明：reference driver 仅为 harness 自检，不是 Agent 能力分数。**