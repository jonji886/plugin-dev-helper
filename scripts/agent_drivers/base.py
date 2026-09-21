"""Agent Driver 公共抽象与数据结构。

本文件不依赖任何具体 Agent 实现，是 Harness 与 Driver 之间的契约层。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# 任何无法获取的数据统一标记为 UNAVAILABLE（不估算、不伪造）。
UNAVAILABLE = "UNAVAILABLE"

# AgentRunResult.status 取值
STATUS_OK = "ok"
STATUS_MANUAL_PENDING = "manual_pending"
STATUS_MANUAL_REQUIRED = "manual_required"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"


@dataclass
class AgentRunResult:
    """单次 Agent 执行的结果（与具体 Agent 实现无关）。

    字段对应 benchmark/README.md 与任务书要求：
    status / workspace / started_at / finished_at / latency_ms / exit_code /
    stdout(response) / stderr(error) / tool_calls / token_usage / trace_path / metadata。
    无法获取的项保持 UNAVAILABLE 或空容器，绝不以估计值填充。
    """

    status: str
    workspace: str
    started_at: str = ""
    finished_at: str = ""
    latency_ms: float = 0.0
    exit_code: Optional[int] = None
    stdout: str = ""          # Agent 最终响应 / 输出
    stderr: str = ""          # Agent 错误输出
    tool_calls: list[dict] = field(default_factory=list)
    token_usage: Any = UNAVAILABLE   # str | dict；CodeBuddy 不提供则 UNAVAILABLE
    trace_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeBuddyConfig:
    """CodeBuddy 真实调用的配置（以当前本机真实机制为准）。"""

    agent: str = "codebuddy"
    model: str = UNAVAILABLE
    cli_path: str = ""                       # buddycn 可执行文件绝对路径
    mode: str = "manual"                     # cli | manual
    mcp_enabled: bool = False                # 该组实验是否允许访问 Plugin Dev Helper MCP
    mcp_servers: dict = field(default_factory=dict)   # name -> {type, url, description}
    profile: str = ""                        # CodeBuddy profile（用于 MCP 作用域隔离）
    timeout_seconds: int = 600
    runs: int = 1


class BenchmarkDriver:
    """产出 artifact（相对路径 -> 内容）。

    `produce` 负责把 fixture 复制到隔离工作区，并按条件叠加参考解 / MCP 配置。
    Harness 之后对 workspace 运行确定性 Evaluator。具体 Agent 的「执行」由派生类决定：
    - ReferenceDriver：确定性 stand-in，mcp 模式直接叠加参考解。
    - CodeBuddyManualDriver：准备工作区供真实 Agent 执行（manual_pending），再 import 结果。
    - CodeBuddyCLIDriver：真实调用 `buddycn chat`，无法采集结果时明确报错。
    """

    def produce(self, case, mode: str, workspace: Path) -> dict[str, str]:
        raise NotImplementedError


def copy_tree(src: Path, dst: Path) -> None:
    """复制目录树，排除 node_modules / dist / build 等编译产物。"""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if "node_modules" in item.parts:
            continue
        relative = item.relative_to(src)
        target = dst / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")


def normalize_codebuddy_trace(
    raw: dict, run_id: str = "", agent: str = "", condition: str = ""
) -> dict:
    """把 CodeBuddy 任意形态的 trace 归一化到 benchmark canonical schema。

    canonical schema（benchmark/traces/README.md）：
      {run_id, agent, condition, tasks:[
        {task_id, accepted, abstained,
         calls:[{seq, tool, arguments, status}], symbols_queried}]}

    评分器 `scripts/check_agent_trace.py` 只依赖 canonical schema，不直接耦合
    CodeBuddy 私有格式。适配器在这里完成字段映射。
    """
    if not isinstance(raw, dict):
        return {"run_id": run_id, "agent": agent, "condition": condition, "tasks": []}
    tasks_out: list[dict] = []
    for t in raw.get("tasks", []) or []:
        calls_raw = t.get("calls", []) or []
        calls = []
        for i, c in enumerate(calls_raw, start=1):
            calls.append({
                "seq": c.get("seq", i),
                "tool": c.get("tool") or c.get("name") or c.get("tool_name") or "",
                "arguments": c.get("arguments") or c.get("input") or c.get("params") or {},
                "status": c.get("status") or c.get("result") or c.get("outcome") or "",
            })
        symbols = t.get("symbols_queried") or t.get("symbols") or []
        tasks_out.append({
            "task_id": t.get("task_id") or t.get("id") or "",
            "accepted": bool(t.get("accepted", False)),
            "abstained": bool(t.get("abstained", False)),
            "calls": calls,
            "symbols_queried": list(symbols),
        })
    return {
        "run_id": raw.get("run_id") or run_id,
        "agent": raw.get("agent") or agent,
        "condition": raw.get("condition") or condition,
        "tasks": tasks_out,
    }
