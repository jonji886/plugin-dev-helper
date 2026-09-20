# ADR-002：Task Runtime 与 LangGraph 内部状态解耦

- 状态：已采纳
- 日期：2026-09
- 相关代码：`agent/runtime/`（models / state_machine / repositories / trace / task_runtime）

## 背景

LangGraph 的 Graph State 面向单次图执行，不能表达业务任务的完整生命周期
（等待确认、失败恢复、产物版本、跨进程重启后的续跑）。把业务状态寄托在图状态上，
会导致无法恢复、无法审计、无法评估。

## 决策

1. 新建 `agent/runtime` 包作为**业务任务状态的唯一事实来源**：
   - `Task / TaskStep / Checkpoint / ArtifactVersion / TraceEvent / FailureRecord` 均为可序列化 dataclass；
   - `TaskStateMachine` 显式定义合法状态迁移，非法迁移抛 `IllegalTransitionError`（不做静默容忍）；
   - `repositories.py` 以 SQLite 持久化 Task / Step / Trace，进程重启后可恢复；
   - `errors.py` 提供 Error Taxonomy（RETRIEVAL / API_HALLUCINATION / RULE_VIOLATION / …），
     Retry 必须依据分类而非裸 except。
2. LangGraph 继续承担 Agent 推理图的编排，但其执行结果必须回写 Runtime 才视为任务状态。
3. 每个关键动作记录 `TraceEvent`（含 `artifact_version`），供 Task Trace 回放与
   Root Cause Analysis 使用。

## 后果

- 正面：任务可恢复、可重试、可审计；RCA 有结构化数据而非日志大海捞针。
- 代价：存在「图状态 vs 任务状态」双状态，必须以 Runtime 为准并及时回写；
  Trace schema 演进需要迁移策略（列均带默认值，新增列向后兼容）。
