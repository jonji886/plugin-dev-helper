# 评测结果对比

> 状态：**工具级已执行；Agent 级（无 MCP vs 有 MCP）已执行（CodeBuddy manual，1 run/task）→ 见 [`codebuddy-20260921-155307/comparison.md`](codebuddy-20260921-155307/comparison.md)**

本文件区分两类结果，不要混淆：

| 类别 | 是否已执行 | 数据来源 | 能说明什么 |
|---|---|---|---|
| **任务可解性验收** | 已执行 | `scripts/check_benchmark_task.py` 实测 | 任务是否可解、acceptance 是否非真空 |
| **MCP 工具级黄金评测** | 已执行 | `scripts/run_mcp_eval.py` 实测 | MCP 工具契约、边界、降级、幂等性 |
| **多轮调用序列评测** | 已执行 | `scripts/run_mcp_sequences.py` 实测 | 跨步骤引用（MCP 侧指代）是否成立 |
| **多次调用稳定性** | 已执行 | `scripts/check_mcp_stability.py` 实测 | 反复/并发/出错后是否仍稳定 |
| **Agent 增益对比**（无 MCP vs 有 MCP） | **未执行** | 需真实 Agent 跑任务并落盘轨迹 | MCP 对 Agent 的实际增益 |

`无 MCP` / `有 MCP` 两列至今没有任何实测数据。填入前必须用真实 Coding Agent
按 `../README.md` 的协议跑完，并用 `scripts/check_agent_trace.py` 落盘评分。
在此之前这两列保持 `N/A`，不用参考解或脚本生成的调用序列冒充 Agent 结果。

---

## 1. 任务可解性验收（已执行）

```bash
python scripts/check_benchmark_task.py --all --mode both \
  --json benchmark/results/acceptance.json
```

结果：**10/10 通过（mode=both）**，即「初始态按预期失败 + 参考解通过」同时成立。

| 任务 | 类别 | 初始态 | 参考解 | 说明 |
|------|------|--------|--------|------|
| T01 | API 查找 | FAIL（符合预期） | PASS | 初始 `throw not implemented`，未含 `IDP.Miniapp.exit` |
| T02 | 类型构造 | FAIL（符合预期） | PASS | 初始未调用 API |
| T03 | 枚举使用 | FAIL（符合预期） | PASS | 初始未调用 API |
| T04 | UI/VM 通信 | FAIL（符合预期） | PASS | 初始未调用 API |
| T05 | 事件监听 | FAIL（符合预期） | PASS | 初始未调用 API |
| T06 | 修改既有代码 | FAIL（符合预期） | PASS | 初始 `setContainerOptions` 缺 `frame` 参数，tsc 报错 |
| T07 | 修复错误 API | FAIL（符合预期） | PASS | 初始 `IDP.Miniapp.exitMiniapp` 不存在，tsc TS2339 |
| T08 | 修复错误参数 | FAIL（符合预期） | PASS | 初始 `dat` 字段，tsc TS2561 |
| T09 | 拒绝编造 | PASS（初始态即通过） | PASS | 见下方说明 |
| T10 | 信息不足 | FAIL（符合预期） | PASS | 初始未调用 API |

### 修复的两处不可满足断言

原始 `tasks.json` 有两条断言与正确解自相矛盾，任务无论如何都不可能通过：

| 任务 | 原断言 | 问题 | 现断言 |
|---|---|---|---|
| T08 | `not_contains:dat` | 与 `contains:data` 冲突：`data` 本身包含子串 `dat`，任何正确解都会被判失败 | `not_contains:dat:`（只拦截 `dat:` 这种错误字段名写法） |
| T09 | `not_contains:IDP.Miniapp.closeMiniapp` | `expected_behavior` 允许「用注释说明」，但注释里出现该符号名就会判失败，与需求自相矛盾 | `not_contains:IDP.Miniapp.closeMiniapp(`（只拦截真实调用） |

### T09 的特殊性

T09 初始态就通过自动验收——因为「什么都不做」同样满足「代码里没有不存在的 API 调用」。
自动断言无法区分「Agent 查证后明确拒绝」与「Agent 躺平不动」。
该任务的真实信号在 `check_agent_trace.py` 的 **Abstention Correctness** 指标，
必须在轨迹中记录 `abstained: true` 才算完成。`acceptance.initial_expect_fail: false` 已显式标注这一点。

---

## 2. MCP 工具级黄金评测（已执行）

```bash
python scripts/run_mcp_eval.py --repeat 3
```

数据集 [`../mcp_golden.json`](../mcp_golden.json)，58 条用例，覆盖 8 个工具 ×
happy_path / not_found / boundary / degradation / unsupported / error_safety 六类。

| 指标 | 结果 | 门禁阈值 | 结论 |
|---|---|---|---|
| Contract Pass Rate | **58/58 = 100.00%** | 100% | PASS |
| Status Accuracy | **100.00%** | 100% | PASS |
| Determinism Rate（同参重复 3 次） | **100.00%** | ≥ 99% | PASS |
| Not-Found Precision | **6/6 = 100.00%** | 100% | PASS |
| Task Symbol Coverage | **14/14 = 100.00%** | 100% | PASS |
| **GATE** | — | — | **PASS** |

分类通过率：happy_path 21/21、boundary 24/24、not_found 6/6、degradation 1/1、unsupported 4/4、error_safety 2/2。

耗时（ms，单进程本地）：

| 工具 | 调用数 | avg | p50 | p95 |
|---|---:|---:|---:|---:|
| get_api | 13 | 4.02 | 1.61 | 15.03 |
| get_type | 6 | 0.85 | 0.59 | 3.06 |
| get_related_symbols | 3 | 0.17 | 0.05 | 0.43 |
| search_docs | 7 | 915.10 | 130.17 | 5437.73 |
| get_examples | 4 | 4.33 | 3.05 | 10.93 |
| validate_api_usage | 6 | 0.72 | 0.15 | 2.33 |
| get_plugin_constraints | 5 | 20.16 | 0.03 | 100.69 |
| get_plugin_scaffold | 5 | 0.06 | 0.01 | 0.17 |

> `search_docs` 明显是长尾：p50 130ms 但 p95 5.4s。其中含首次向量检索与 500 字长 query，
> 属本地 embedding（CPU）环境的固有成本，不等于生产远程部署表现。

### 评测暴露的一致性问题

- `MCP-G05`：`get_plugin_constraints` 传入非法 `component` 时返回 `status: error`（异常被 `guarded` 收敛），
  而 `MCP-H03` 的不支持技术栈返回 `unsupported_stack`、`MCP-G04` 的不支持平台返回 `not_supported`。
  同为「入参不受支持」，三种语义并存。已按现状断言（避免评测集被实现细节塞住），
  记为待改进项，不在此次改动中擅自统一。

---

## 3. 多轮调用序列评测（已执行）

```bash
python scripts/run_mcp_sequences.py
```

| 指标 | 结果 |
|---|---|
| Sequence Pass Rate | 6/6 = 100% |
| Step Pass Rate | 19/19 = 100% |
| Cross-Step Reference Rate | 5/5 = 100% |

| 序列 | 链路 | 结果 |
|---|---|---|
| SEQ-01 | get_plugin_constraints → get_plugin_scaffold → get_api → validate_api_usage | PASS |
| SEQ-02 | get_api → get_type(`{{steps.0.related_types.0}}`) → validate_api_usage(`${{steps.0.symbol}}`) | PASS |
| SEQ-03 | validate_api_usage → get_api(not_found) → get_api → validate_api_usage(`${{steps.2.symbol}}`) | PASS |
| SEQ-04 | get_api → get_type → validate_api_usage | PASS |
| SEQ-05 | get_api(not_found) → search_docs → get_api(`${{steps.1.results.0.symbol}}`) | PASS |
| SEQ-06 | get_related_symbols → get_type(`${{steps.0.related_symbols.0}}`) | PASS |

> 覆盖的是 **MCP 侧指代**（用上一步返回的符号/候选/关联类型继续查询）。
> **Agent 侧指代**（用户说「这个 API」时能否还原成完整符号）不在 MCP 层，需真实轨迹。

## 4. 多次调用稳定性（已执行）

```bash
python scripts/check_mcp_stability.py --rounds 30 --concurrency 5
```

240 次调用（30 轮 × 8 工具），第 15 轮注入异常，注入轮不计入成功率/幂等/漂移。

| 检查项 | 结果 | 结论 |
|---|---|---|
| Success Rate | 100.00%（232 次有效调用） | PASS |
| Determinism Rate | 100.00%（跨轮次返回指纹一致） | PASS |
| Max P50 Drift | +2.20 ms | PASS（阈值 100ms） |
| Recovery After Error | 异常结构化暴露（status=error），后续 14 轮 / 112 次调用全部恢复 | PASS |
| State Leak | 知识单元缓存 0 → 3（有界），跑完后 readiness 正常 | PASS |
| Telemetry Integrity | request_id 240/240 唯一，无冲突 | PASS |
| Concurrency | 5 路并发无异常、无交叉污染 | PASS |
| **GATE** | — | **PASS** |

延迟与漂移（ms）：

| 工具 | p50 | p95 | p99 | Δp50 |
|---|---:|---:|---:|---:|
| get_api | 0.03 | 0.08 | 32.57 | +0.00 |
| get_type | 0.02 | 0.04 | 0.82 | +0.00 |
| get_related_symbols | 0.03 | 0.04 | 1.05 | +0.00 |
| search_docs | 84.48 | 145.03 | 6244.94 | +2.20 |
| get_examples | 0.52 | 1.73 | 3.49 | +0.02 |
| validate_api_usage | 0.05 | 0.06 | 0.16 | +0.00 |
| get_plugin_constraints | 0.05 | 0.21 | 115.86 | -0.01 |
| get_plugin_scaffold | 0.11 | 0.19 | 0.20 | +0.01 |

> `search_docs` 是唯一的长尾工具（本地 CPU embedding）。p99 6.2s 由个别离群调用造成，
> 中位数稳定在 84ms 且漂移仅 +2.2ms，因此判定为「无退化」而非「性能问题」。
> 若用 P95 衡量漂移，单次离群会被放大成 -96% 的假退化——这是改用中位数的原因。

## 5. Agent 增益对比（未执行）

占位模板。跑完后按 `../traces/README.md` 落盘轨迹，再用
`python scripts/check_agent_trace.py --dir benchmark/traces` 评分，把结果填回这里。

| 任务 | 类别 | 无 MCP | 有 MCP | 工具选择 F1 | 符号覆盖 | 冗余调用率 |
|------|------|--------|--------|---|---|---|
| T01 | API 查找 | N/A | N/A | | | |
| T02 | 类型构造 | N/A | N/A | | | |
| T03 | 枚举使用 | N/A | N/A | | | |
| T04 | UI/VM 通信 | N/A | N/A | | | |
| T05 | 事件监听 | N/A | N/A | | | |
| T06 | 修改既有代码 | N/A | N/A | | | |
| T07 | 修复错误 API | N/A | N/A | | | |
| T08 | 修复错误参数 | N/A | N/A | | | |
| T09 | 拒绝编造 | N/A | N/A | | | |
| T10 | 信息不足 | N/A | N/A | | | |

## 各任务记录

<!-- 每个任务按协议执行后，在此记录：
- 无 MCP：是否通过 acceptance、Agent 最终代码、编造/错误点
- 有 MCP：调用工具序列、是否通过 acceptance、代码正确性
- 对比结论
-->
