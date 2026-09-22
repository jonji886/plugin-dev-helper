# Benchmark 结果总览（Summary / Index）

> 本文件是评测结果索引。各层详细数据见下方分节；**Agent 层（CodeBuddy E2E）的真实实测结果**
> 单独存放在 `codebuddy-<timestamp>/comparison.md`，不在此重复维护易过期的逐任务 Agent 指标。

## 分层评测状态

| Layer | 名称 | 状态 | 最新结果 / 链接 |
|-------|------|------|----------------|
| L0 | MCP 工具级黄金评测 | EXECUTED（PASS） | 见 § 2，数据集 58/58 = 100% |
| L1 | MCP 多轮序列评测 | EXECUTED（PASS） | 见 § 3，Sequence 6/6 = 100% |
| L2 | Agent 工具行为评测 | TOOLING READY | `scripts/check_agent_trace.py` 已就绪，需真实轨迹落盘后评分 |
| L3 | CodeBuddy 端到端（无 MCP vs 有 MCP） | EXECUTED（manual, 1 run/task） | [`codebuddy-20260921-155307/comparison.md`](codebuddy-20260921-155307/comparison.md) |
| L4 | MCP 稳定性评测 | EXECUTED（PASS） | 见 § 4，240 次调用 100% |

### 关键语义说明（本次工程改造后）

- **Final Success ≠ First Pass Success**：CodeBuddy 真实轨迹无法可靠区分「首次候选实现」与
  「后续自修复」，因此 L3 的 First Pass Success Rate 标记为 **UNAVAILABLE**，不伪造成功率，
  也不把 Final Success 当作 First Pass。历史 run（`codebuddy-20260921-155307`）使用旧语义
  （`first_pass = final_acceptance`），已在其 metadata 中标注，不篡改历史。
- **Baseline 隔离**：Baseline 工作区不写入 `.codebuddy/mcp.json`；import 时对每个 baseline run
  做 `verify_isolation`（检查工作区是否意外含 MCP 配置），若检测到 MCP 则该 run 标记为
  `INVALID_ENVIRONMENT` 并排除出正式 Baseline 聚合。
- **结果路径**：公开结果统一使用相对仓库根的路径，不暴露本机绝对路径。

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

| 任务 | 原断言 | 问题 | 现断言 |
|---|---|---|---|
| T08 | `not_contains:dat` | 与 `contains:data` 冲突：`data` 本身包含子串 `dat` | `not_contains:dat:` |
| T09 | `not_contains:IDP.Miniapp.closeMiniapp` | 与「可用注释说明」自相矛盾 | `not_contains:IDP.Miniapp.closeMiniapp(` |

### T09 的特殊性

T09 初始态就通过自动验收——「什么都不做」同样满足「代码里没有不存在的 API 调用」。
真实信号在 `check_agent_trace.py` 的 **Abstention Correctness** 指标，必须在轨迹中记录
`abstained: true` 才算完成。`acceptance.initial_expect_fail: false` 已显式标注这一点。

---

## 2. MCP 工具级黄金评测（L0，已执行）

```bash
python scripts/run_mcp_eval.py --repeat 3
```

数据集 `../mcp_golden.json`，58 条用例，覆盖 8 个工具 × happy_path / not_found / boundary /
degradation / unsupported / error_safety 六类。

| 指标 | 结果 | 门禁阈值 | 结论 |
|---|---|---|---|
| Contract Pass Rate | **58/58 = 100.00%** | 100% | PASS |
| Status Accuracy | **100.00%** | 100% | PASS |
| Determinism Rate（同参重复 3 次） | **100.00%** | ≥ 99% | PASS |
| Not-Found Precision | **6/6 = 100.00%** | 100% | PASS |
| Task Symbol Coverage | **14/14 = 100.00%** | 100% | PASS |
| **GATE** | — | — | **PASS** |

分类通过率：happy_path 21/21、boundary 24/24、not_found 6/6、degradation 1/1、unsupported 4/4、error_safety 2/2。

> `search_docs` 明显是长尾：p50 130ms 但 p95 5.4s。含首次向量检索与 500 字长 query，属本地
> embedding（CPU）环境的固有成本，不等于生产远程部署表现。

---

## 3. 多轮调用序列评测（L1，已执行）

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
| SEQ-02 | get_api → get_type → validate_api_usage | PASS |
| SEQ-03 | validate_api_usage → get_api(not_found) → get_api → validate_api_usage | PASS |
| SEQ-04 | get_api → get_type → validate_api_usage | PASS |
| SEQ-05 | get_api(not_found) → search_docs → get_api | PASS |
| SEQ-06 | get_related_symbols → get_type | PASS |

> 覆盖的是 **MCP 侧指代**（用上一步返回的符号/候选/关联类型继续查询）。**Agent 侧指代**不在 MCP 层。

---

## 4. 多次调用稳定性（L4，已执行）

```bash
python scripts/check_mcp_stability.py --rounds 30 --concurrency 5
```

240 次调用（30 轮 × 8 工具），第 15 轮注入异常，注入轮不计入成功率/幂等/漂移。

| 检查项 | 结果 | 结论 |
|---|---|---|
| Success Rate | 100.00%（232 次有效调用） | PASS |
| Determinism Rate | 100.00% | PASS |
| Max P50 Drift | +2.20 ms | PASS（阈值 100ms） |
| Recovery After Error | 异常结构化暴露，后续 14 轮 / 112 次调用全部恢复 | PASS |
| State Leak | 知识单元缓存有界，跑完后 readiness 正常 | PASS |
| Telemetry Integrity | request_id 240/240 唯一 | PASS |
| Concurrency | 5 路并发无交叉污染 | PASS |
| **GATE** | — | **PASS** |

---

## 5. Agent 增益对比（L3，CodeBuddy E2E）

真实 Agent（CodeBuddy）端到端结果 **已执行**，详见
[`codebuddy-20260921-155307/comparison.md`](codebuddy-20260921-155307/comparison.md)。

- 实验变量：仅 **Plugin Dev Helper MCP 是否可用**（baseline = 不可用，mcp = 可用）。
- 当前公开结果：**1 run/task**（非 3 runs），样本量小，不声称统计稳定。
- **First Pass Success Rate = UNAVAILABLE**：当前 CodeBuddy 轨迹无法可靠区分首次候选实现与自修复。
- 如需重新执行并提升样本量，见 `../README.md` 与 `../benchmark/README.md` 的协议（`--runs N`）。
