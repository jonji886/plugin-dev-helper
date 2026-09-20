# Coding Agent P0 架构：验证 → 修复 → 追踪 → 评估

> 对应 ADR-001 ~ ADR-004。目标：让「Agent 产出是否可交付」成为可确定性判定、
> 可闭环修复、可追踪归因、可回归评估的工程能力，而不是依赖人工目测。

## 组件总览

```
                         ┌───────────────────────────────┐
   插件工程 (Artifact) ──▶│ ProjectValidator（确定性判定） │
                         │  STRUCTURE / MANIFEST / RULE   │
                         │  API(知识索引) / BUILD         │
                         └──────────────┬────────────────┘
                                        │ Issues(统一结构, repairable)
                         ┌──────────────▼────────────────┐
                         │ Repair Loop（有界修复,≤N轮）   │──▶ ArtifactVersion+1
                         └──────────────┬────────────────┘
                                        │ TraceEvent(含 artifact_version)
                         ┌──────────────▼────────────────┐
                         │ Task Runtime（业务状态事实来源）│
                         │ StateMachine / Checkpoint /    │
                         │ SQLite 持久化 / Error Taxonomy │
                         └──────────────┬────────────────┘
                                        │ runs / summary
                         ┌──────────────▼────────────────┐
                         │ Benchmark Harness             │
                         │ Baseline vs MCP（控制变量）    │
                         │ reference=自检 / external=真实 │
                         └───────────────────────────────┘
```

## 模块与文件

| 模块 | 位置 | 职责 |
|---|---|---|
| ProjectValidator | `mcp_server/services/project_validator.py` | 项目级确定性校验，输出统一 Issue |
| MCP 工具 | `mcp_server/tools/validate_plugin_project.py` | 对外暴露 `validate_plugin_project(project_dir)` |
| API 符号校验 | `mcp_server/services/validator.py` + `data/knowledge` | 幻觉 API 检测（复用线上同一知识索引） |
| 规则层 | `rules/kujiale/*.yaml` + `mcp_server/rules.py` | 平台硬约束事实来源（含 require 型通信规则） |
| Repair Loop | `agent/runtime/repair.py` | 基于 Evidence 的有界最小修复（ADR-003） |
| Task Runtime | `agent/runtime/`（models/state_machine/repositories/trace/task_runtime/errors） | 生命周期、持久化、恢复、Trace（ADR-002） |
| Benchmark | `scripts/run_coding_agent_benchmark.py` + `benchmark/` | 控制变量评测与诚实性约束（ADR-004） |

## 关键约定

1. **Issue 是跨组件契约**：`severity / category / code / file / line / evidence /
   suggested_fix / repairable`。Repair、RCA、Benchmark 消费的都是同一结构。
2. **源码优先**：一切断言与校验排除 `dist` / `build` / `node_modules`（编译产物可能残留旧错误）。
3. **宿主相关不伪造**：CORS / OPTIONS / HTTP 探活标记 `runtime_required` WARNING，
   只有在宿主环境验证后才能声明通过。
4. **诚实性红线**：reference driver 的分数仅代表管线自检通过；真实 Coding Agent
   Baseline vs MCP 实验在接入外部 Agent 前永远是 `NOT_RUN`。

## 运行与验证入口

```bash
# 全量测试（含 P0 新增：validator / runtime / trace / state machine / benchmark）
.venv/bin/python -m pytest tests/ -q

# Benchmark harness 自检（reference driver，真实 KB + 规则层）
.venv/bin/python scripts/run_coding_agent_benchmark.py --mode both --out-dir benchmark/results

# 外部真实 Agent 接入（未提供 endpoint 时输出 NOT_RUN，不产伪造数据）
.venv/bin/python scripts/run_coding_agent_benchmark.py --driver external
```
