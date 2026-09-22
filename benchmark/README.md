# MCP 效果评测（Benchmark）

评估「Plugin Developer MCP Server」对本地 Coding Agent 的增益：Agent 能否借助 MCP
正确查找 SDK/API/类型、修改插件工程并通过 Build/Test。

## 目录结构

```
benchmark/
├── README.md            # 本文件：评测协议
├── tasks.json           # 任务集（10 个任务，含验收标准与参考解路径）
├── mcp_golden.json      # MCP 工具级黄金评测集（58 条，确定性，可进 CI）
├── mcp_sequences.json   # MCP 多轮调用序列集（6 条，验证跨步骤引用/指代）
├── mcp_gate.json        # 评测门禁阈值（工具级 + 序列级）
├── fixtures/            # 每个任务的最小插件工程（初始态）
│   ├── tsconfig.base.json
│   └── T01-*/src/vm.ts
├── solutions/           # 每个任务的参考解（solutions/T01/vm.ts ...）
├── traces/              # Agent 调用轨迹落盘目录（README.md 定义格式）
├── results/
│   ├── comparison.md              # 结果总览 / 索引（L0–L4 状态 + 链接）
│   ├── acceptance.json            # 任务可解性验收结果
│   ├── mcp_eval_results.json      # 工具级评测结果
│   ├── mcp_sequences_results.json # 序列评测结果
│   ├── mcp_stability.json         # 稳定性验收结果
│   ├── workspaces/                # 每次 run 的独立工作区（baseline/<task>-r<k>、mcp/<task>-r<k>）
│   └── codebuddy-<timestamp>/     # 真实 CodeBuddy 端到端结果（含 comparison.md）
└── badcases/
    └── README.md        # Bad Case 收集规范
```

## 三层评测

| 层 | 对象 | 是否需要 Agent / LLM | 入口 | 状态 |
|---|---|---|---|---|
| **L0** | MCP 工具契约/边界 | 否 | `python scripts/run_mcp_eval.py` | 已执行，GATE PASS |
| **L1** | MCP 多轮序列（跨步骤引用） | 否 | `python scripts/run_mcp_sequences.py` | 已执行，6/6 |
| **L2** | Agent 调用行为 | 是（需轨迹） | `python scripts/check_agent_trace.py --dir benchmark/traces` | 已有真实轨迹（CodeBuddy MCP 条件） |
| **L3** | 端到端任务增益（Baseline vs MCP） | 是（需真实 Agent） | `python scripts/run_coding_agent_benchmark.py --driver codebuddy-manual` | 已执行（manual，1 run/task）；First Pass = UNAVAILABLE |
| **L4** | 多次调用稳定性 | 否（但耗时，建议发布前跑） | `python scripts/check_mcp_stability.py` | 已执行，GATE PASS |

> 任务可解性验收（`check_benchmark_task.py --all --mode both`，10/10）是 L3 的前置自检：
> 证明任务可解且初始态有区分度。参考解**不是** Agent 产出，不能用来冒充跑分。

## 评测维度

1. **API 查找准确性**：能否找到正确的 API，不张冠李戴。
2. **参数/类型构造正确性**：能否按 `MiniappUploadDataOption` 等类型构造合法参数。
3. **UI/VM 通信正确性**：`postMessage` / `onMessageReceive` / `setContainerOptions` 用法。
4. **错误修复能力**：修复不存在的 API 名、错误的参数名。
5. **拒绝编造**：任务要求调用不存在的 API 时，明确说明而非编造。
6. **信息不足先查询**：需求模糊时，先查类型定义再写代码。

## 1. 工具级黄金评测（不需要 Agent）

```bash
python scripts/run_mcp_eval.py              # 58 条用例，默认同参重复 3 次验幂等
python scripts/run_mcp_eval.py --repeat 5
python scripts/run_mcp_eval.py --case MCP-A07
```

指标与阈值见 `mcp_gate.json`：

| 指标 | 含义 | 阈值 |
|---|---|---|
| Contract Pass Rate | 返回结构与状态全部符合契约 | 100% |
| Status Accuracy | `ok` / `not_found` / `not_supported` / `error` 判定正确 | 100% |
| Determinism Rate | 同参重复 N 次（剔除 `request_id`/`duration_ms`）结果一致 | ≥ 99% |
| Not-Found Precision | 不存在的符号必须明确 `not_found`，不编造 | 100% |
| Task Symbol Coverage | 任务的 `reference_symbols` 真能被 MCP 解析 | 100% |

用例覆盖：happy_path 21、boundary 24（空串 / 超长 / top_k 越界 / depth 越界 /
非法 SDK 版本 / 大小写 / 前后缀 / 注入字符）、not_found 6、degradation 1（向量库
不可用降级 `keyword_fallback`）、unsupported 4、error_safety 2（异常收敛 + 故障隔离）。

## 2. 多轮调用序列评测

```bash
python scripts/run_mcp_sequences.py
python scripts/run_mcp_sequences.py --sequence SEQ-02
```

`mcp_sequences.json` 定义 6 条序列，重点验证「上一步的返回能否驱动下一步」。
入参支持占位符 `{{steps.<序号>.<点号路径>}}`，解析失败即该步骤失败——
这正是在测 MCP 是否返回了足够结构化、足够稳定的字段供 Agent 接着用。

| 序列 | 覆盖能力 | 跨步骤引用 |
|---|---|---|
| SEQ-01 | 完整开发链路（约束 → 骨架 → 查 API → 校验代码） | 0 |
| SEQ-02 | 用 `related_types` 继续查类型，再用上一步符号构造代码 | 2 |
| SEQ-03 | 自检报错 → 确认 API 不存在 → 查到正确 API → 用同一符号复核 | 1 |
| SEQ-04 | 信息不足时先查类型再构造参数 | 0 |
| SEQ-05 | 确认不存在 → 检索最接近的真实 API → 不得回到编造符号 | 1 |
| SEQ-06 | 依赖图关联符号 → 展开类型定义 | 1 |

指标：Sequence Pass Rate、Step Pass Rate、Cross-Step Reference Rate。

> **边界**：这里覆盖的是 **MCP 侧的指代**（用上一步拿到的符号/候选/关联类型继续查询）。
> **Agent 侧的指代**（用户说「这个 API」时 Agent 能否还原成完整符号）无法在 MCP 层验证，
> 必须由 `benchmark/traces` 的真实 Agent 轨迹 + `check_agent_trace.py` 覆盖。

## 3. 多次调用稳定性

```bash
python scripts/check_mcp_stability.py --rounds 30 --concurrency 5
```

> 建议发布前或配置变更后执行，不进 CI：门禁含时间与漂移阈值，
> 在共享 CI runner 上容易因调度抖动误报（与 `scripts/check_model_routing.py` 同样的定位）。

| 检查项 | 口径 |
|---|---|
| Success Rate | 注入轮之外的全部调用中 `status` 符合预期的比例 |
| Determinism | 跨轮次返回指纹一致（剔除 `request_id` / `duration_ms`） |
| Latency P50/P95/P99 + Drift | 排除 1 个预热轮，前半轮 vs 后半轮；**用中位数**衡量漂移，因 P95 在小样本下会被单个离群值主导 |
| Recovery After Error | 中途注入一次工具异常，验证异常被结构化暴露且后续调用自动恢复 |
| State Leak | 前后 readiness 一致、知识单元缓存增长有界、无 error 累积 |
| Telemetry Integrity | `request_id` 全局唯一、调用次数与落库一致 |
| Concurrency | N 路并发执行完整序列，无异常、无交叉污染 |

默认写临时 SQLite，不污染生产 telemetry；用 `--database` 可指定。

配套改动：`MetricsStore.mcp_metrics()` 新增每工具 `p50/p95/p99_duration_ms`、`min/max_duration_ms`
（此前只有 `avg_duration_ms`，无法判断延迟漂移）。

## 4. 任务可解性验收

```bash
python scripts/check_benchmark_task.py --all --mode both      # 初始态 + 参考解
python scripts/check_benchmark_task.py --all --mode initial   # 只看初始态
python scripts/check_benchmark_task.py --all --mode solution  # 只验收参考解
python scripts/check_benchmark_task.py --clean                # 清理 dist 产物
```

- `initial`：fixture 初始态应当**失败**（`acceptance.initial_expect_fail: true`）。
  若初始态就通过，说明该任务对 Agent 没有区分度，acceptance 是真空的。
- `solution`：把 `solutions/<id>/vm.ts` 写进 fixture 后验收，验收完自动还原。
  用于证明任务可解。**参考解不是 Agent 产出的结果**，不能用来冒充 Agent 跑分。

验收 = `tsc --noEmit`（typecheck）+ `tsc --outDir dist`（build）+ 字符串断言
（`contains:` / `not_contains:`），且现在真正尊重 `acceptance.typecheck` / `acceptance.build` 开关。

## 5. Agent 调用行为（需要真实轨迹）

`tasks.json` 里的 `tool_expectation` 与 `reference_symbols` 由本层消费。

```bash
python scripts/check_agent_trace.py benchmark/traces/<run>.json
python scripts/check_agent_trace.py --dir benchmark/traces
python scripts/check_agent_trace.py --self-test   # 用合成轨迹验证评分器自身
```

**工具期望语义**（`tool_expectation`）：

| 字段 | 含义 |
|---|---|
| `required_tools` | 完成该任务**必须**覆盖的工具（`recall` 只考核其覆盖） |
| `optional_tools` | 允许但不强制的工具（调用它们**不**扣 precision） |
| `acceptable_tool_sets` | 可接受的工具路径集合（命中任一即视为路径合理） |
| `forbidden_tools` | 禁止使用的工具（出现即 `forbidden_violation`） |

核心原则：**Task Outcome > Prescribed Tool Path**。评测优先级为
任务是否完成 → 最终代码/API 是否正确 → 是否幻觉 → 是否违反约束 → 工具路径是否合理 → 是否冗余调用。
**高效 Agent 用更少工具完成任务不应被惩罚**；`precision` 只惩罚「超出 `required ∪ optional`」的调用。
不为了提升 Tool Recall 而修改 Agent Prompt（会污染实验）。

指标：Tool Selection P/R/F1、Symbol Coverage、Redundant Call Rate、
`required_satisfied` / `forbidden_violation` / `redundant_tools`、
Sequence Compliance（`constraints` 先于 `scaffold`；`get_api`/`get_type` 先于 `validate_api_usage`）、
Abstention Correctness（T09 类任务必须明确说明不存在）。

轨迹格式与落盘规范见 [`traces/README.md`](traces/README.md)。

## 6. 端到端协议（无 MCP vs 有 MCP）

对 `tasks.json` 中每个任务（`--runs N`，默认 `N=1`）：

1. 从 `fixtures/<task>/` 复制到**独立工作区** `results/workspaces/<mode>/<task>-r<k>`。
   每次 run 都从干净 fixture 开始，**不复用上次被修改的工作区**；正式建议 `10 tasks × 3 runs × 2 conditions`。
2. 使用同一份 `task_prompt` 驱动同一 CodeBuddy。
3. **基线（无 MCP）**：工作区**不**写入 `.codebuddy/mcp.json`，Agent 不能调用 Plugin Dev Helper MCP。
4. **实验（有 MCP）**：工作区写入 `.codebuddy/mcp.json`（plugin-dev-helper），Agent 真实接入 MCP。
5. 采集：`accepted`、MCP 调用轨迹、最终代码正确性、token / 耗时（不可得者标记 UNAVAILABLE）。

**Baseline 隔离校验（Isolation Verification）**：仅「不写工作区配置」不足以强隔离（CodeBuddy 可能合并
用户级 / 全局配置）。import 时对每个 run 执行 `verify_isolation`，检查工作区是否含 `.codebuddy/mcp.json`：

- baseline 检测到 MCP 配置 → 该 run 标记 `INVALID_ENVIRONMENT`，**排除出正式 Baseline 聚合**；
- 工作区缺失无法检查 → `isolation_verified = UNAVAILABLE`；
- 每个 run 的 `mode / mcp_expected / mcp_detected / isolation_verified` 写入结果 metadata。

结果汇总写入 `results/codebuddy-<timestamp>/`（`metadata.json` / `raw_results.json` / `aggregate.json` /
`comparison.md` / `agent_trace_results.json`）与 `traces/codebuddy-<timestamp>/`（canonical trace）。
公开结果统一使用**相对仓库根的路径**，metadata 时间字段为 ISO 8601，不暴露本机绝对路径。

> 说明：fixture 是最小工程，仅用于类型层面验收。真实插件运行还需要
> `manifest.json` + `page.html`，不在本评测范围内。

## 7. Coding Agent 端到端 Benchmark（真实 CodeBuddy）

### Driver 分层（`scripts/agent_drivers/`）

| Driver | 说明 |
|---|---|
| `reference` | 确定性 stand-in：mcp 应用参考解、baseline 原样 fixture。仅用于 **harness / evaluator 自检**，不是 Agent 分数 |
| `codebuddy-manual` | **真实执行**：Harness 准备隔离工作区 + prompt + MCP 配置，由真实 CodeBuddy 执行后回灌 artifact + trace（标记 `manual_execution=true`） |
| `codebuddy-cli` | 真实调用 `buddycn chat`；本环境该 CLI 不向 stdout 返回结果，无法 headless 采集 → 自动判 **NOT_RUN**，不伪造任何输出 |

运行：

```bash
# 自检（确定性，非 Agent 分数）
python scripts/run_coding_agent_benchmark.py --driver reference --mode both

# 真实 CodeBuddy：准备 → 在真实 CodeBuddy 中执行 → 回灌评估
python scripts/run_coding_agent_benchmark.py --driver codebuddy-manual --prepare --runs 1 --mode both
python scripts/run_coding_agent_benchmark.py --driver codebuddy-manual --import  --runs 1 --mode both
```

### 控制变量与诚实性

- **唯一变量**：是否可访问 plugin-dev-helper MCP（其余：Agent / 任务 / fixture / evaluator / validator / acceptance 固定）。
- **runs**：支持 `--runs N`；每次 run 独立 workspace，从同一 fixture 干净复制。
- **隔离**：baseline 工作区不写 MCP 配置，并在 import 时用 `verify_isolation` 校验；被污染 run 不计入正式 Baseline。
- API / RULE 判定与线上 MCP 共用同一份 `data/knowledge` 与 `rules/kujiale`；`dist` / `build` / `node_modules` 不参与；源码级任务不评估工程完整性规则（`KJL-DEV-*` / `KJL-MANIFEST-*`）。
- **First Pass Success 定义**：首次候选实现**未经修复**即通过验收。当前 CodeBuddy 轨迹无法可靠区分
  「首次候选实现」与「自修复」→ **`UNAVAILABLE`**，不把 Final Success 当作 First Pass，不伪造。
  数据模型 `first_pass ∈ {true,false,null}`，`first_pass_basis ∈ {trace_observed, unavailable}`。
- 不可得数据标记 **UNAVAILABLE**（token / cost / latency / first-pass），未执行标记 **NOT_RUN**。`reference` 产物只能解读为管线自检。

### 已执行的真实结果（`results/codebuddy-20260921-155307/`）

Agent：CodeBuddy（manual / import）· 任务 10 · **1 run/task** · Evaluator：ProjectValidator + acceptance（非 LLM Judge）

| Metric | Baseline | MCP | Delta |
|---|---:|---:|---:|
| Task Success Rate | 0.6 | 1.0 | +0.4 |
| API Correctness | 0.7 | 1.0 | +0.3 |
| Hallucination Rate | 0.3 | 0.0 | -0.3 |
| Constraint Violation Rate | 0.1 | 0.0 | -0.1 |
| Abstention Correct Rate | 0.5 | 1.0 | +0.5 |
| First Pass Success Rate | UNAVAILABLE | UNAVAILABLE | — |
| Latency / Token | UNAVAILABLE | UNAVAILABLE | — |

> 局限：1 run/task 样本量小，不代表统计稳定（协议支持 `--runs N`，推荐 3）；baseline 是否完全禁用全局
> MCP 取决于 CodeBuddy 配置合并行为（已在 `metadata.json` 披露，且 import 时做 `verify_isolation`）；
> 该历史 run 使用旧 First Pass 语义（`first_pass = final_acceptance`），已标注 legacy，不篡改历史；
> acceptance 为字符串断言，注释中的被禁符号也会触发失败。
