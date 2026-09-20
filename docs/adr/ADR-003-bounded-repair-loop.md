# ADR-003：有界 Repair Loop（基于 Validator Evidence 的最小必要修复）

- 状态：已采纳
- 日期：2026-09
- 相关代码：`agent/runtime/repair.py`

## 背景

让 LLM「发现错误就整体重新生成」会造成：成本不可控、无关代码被顺手改坏、
错误反而震荡放大（修 A 引入 B）。需要一种收敛、可审计的修复方式。

## 决策

Repair Loop 必须满足以下硬约束：

1. **有界**：最多 `max_repair_attempts` 轮，超限进入 FAILED，绝不无限循环；
2. **证据驱动**：每轮修复输入是 Validator 产出的 Issue（code / file / line /
   evidence / suggested_fix），只处理 `repairable` 且 CRITICAL/HIGH 的项；
3. **最小必要修改**：只改 Issue 指向的文件与位置，不重写无关模块；
4. **版本化**：每轮修复产生新的 `ArtifactVersion`，配套 `TraceEvent(artifact_version=…)`，
   任意一轮的产物与判定可回放对比；
5. **终止条件**：Validator 无 CRITICAL/HIGH（且 acceptance 通过）即成功；
   连续两轮 Issue 集合无变化视为不收敛，提前止损。

## 后果

- 正面：token 消耗与修复轮次可预期；不收敛的 case 会沉淀为 Bad Case 供分析。
- 代价：对「需要大范围重构才能满足约束」的工程，有界循环会 FAILED——
  这是预期行为（交人工），不通过放宽界值来解决。
