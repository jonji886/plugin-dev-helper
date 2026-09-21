# ADR-004：Coding Agent Benchmark 控制变量与诚实性原则

- 状态：已采纳
- 日期：2026-09
- 相关代码：`scripts/run_coding_agent_benchmark.py`、`benchmark/`

## 背景

要回答「MCP 对 Coding Agent 到底有多大增益」，实验必须控制变量且结果必须诚实：
在没有接入真实 LLM Coding Agent 之前，任何“效果数字”都不能冒充真实 Agent 表现。

## 决策

1. **控制变量**：Baseline 与 MCP 两组唯一差异是「是否可访问 plugin-dev-helper MCP」；
   相同任务集（`benchmark/tasks.json`）、相同初始 fixture、相同 ProjectValidator、相同 Evaluator。
2. **确定性评分**：成功判定 = acceptance 断言（contains / not_contains / regex）
   + 源文件级 API/RULE 校验，不引入 LLM Judge。
   - 源码级任务不评估工程完整性规则（KJL-DEV-\* / KJL-MANIFEST-\*），避免与任务目标无关的失败；
   - `dist/`、`build/`、`node_modules/` 一律不参与断言与校验。
3. **驱动分层与诚实性（核心）**：
   - `reference` 驱动：mcp 模式复制参考解、baseline 模式原样使用 fixture。
     它真实执行校验与评分，但 Agent 是确定性 stand-in，结果**只能解读为
     harness 自检（管线可用性 + 指标口径正确性）**，不是 Agent 能力分数；
   - `codebuddy-manual` 驱动：真实 CodeBuddy 执行。Harness 准备隔离工作区 + prompt + MCP 配置
     （baseline 不写 `.codebuddy/mcp.json`、mcp 写入），真实 Agent 执行后回灌 artifact + trace，
     标记 `manual_execution=true`；每次执行均从干净 fixture 开始，禁止复用上次结果；
   - `codebuddy-cli` 驱动：真实调用 `buddycn chat`，但本环境该 CLI 不向 stdout 返回结果/轨迹
     （仅拉起 GUI），无法 headless 采集，故显式判 `NOT_RUN`，绝不伪造输出；
   - **不可得数据（token / cost / latency）标记 `UNAVAILABLE`；未执行标记 `NOT_RUN`**，
     所有产出物（comparison.md 等）必须如实标注真实 Coding Agent 实验状态。
4. **知识库一致性**：Benchmark 的 API/RULE 判定必须使用与线上 MCP 相同的真实知识索引
   （`data/knowledge`）与规则层（`rules/kujiale`），不得使用测试用小知识库。
   *教训记录*：首版自检误用 4 符号玩具索引，导致合法 API（`IDP.Miniapp.view.*`、
   `IDP.Custom.*`）被判“幻觉”，指标完全失真——评测器与线上判定口径不一致时，
   Benchmark 数字没有意义。

## 后果

- 正面：管线正确性可持续回归验证；真实实验一旦接入即可直接产出可比数据。
- 代价：reference 模式指标天然接近上下界（mcp=参考解应全过、baseline=保守下界），
  对外引用时必须限定为“自检”，避免误读。
- 已执行：2026-09-21 通过 `codebuddy-manual` 完成一轮真实实验（10 任务 × 2 条件 × 1 run/task），
  结果见 `benchmark/results/codebuddy-20260921-155307/`；样本量小，不声称统计稳定。
