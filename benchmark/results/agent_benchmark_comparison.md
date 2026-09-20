# Coding Agent Benchmark: Baseline vs MCP

> 控制变量：唯一差异为是否访问 plugin-dev-helper MCP。
> 本结果为 **reference driver（确定性 stand-in）** 的真实校验/评分结果；
> 真实 LLM Coding Agent Benchmark 状态：**NOT_RUN**（见 README / benchmark/README.md）。

| Metric | BASELINE | MCP | Delta |
|---:|---:|---:|
| Task Success | 0.2 | 1.0 | 0.8 |
| First Pass | 0.2 | 1.0 | 0.8 |
| API Correctness | 0.8 | 1.0 | 0.2 |
| Constraint Violation | 0.0 | 0.0 | 0.0 |
| Hallucination | 0.2 | 0.0 | -0.2 |
| Latency(ms) | 18.57 | 10.86 | -7.71 |
| Token | UNAVAILABLE | UNAVAILABLE | - |

**真实 Coding Agent 执行状态：NOT_RUN**