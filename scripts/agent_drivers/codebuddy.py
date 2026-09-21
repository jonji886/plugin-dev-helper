"""CodeBuddy 真实 Driver。

经真实检查（见任务调查报告）：
- 本机存在 `buddycn` CLI（CodeBuddy CN 1.106.1），支持 `buddycn chat -m agent "<prompt>"`。
- 但该 CLI 不向 stdout 返回 Agent 结果或工具轨迹（只拉起 GUI 窗口），无法 headless 采集
  结果/trace。因此 **不能** 用作可程序化采集结果的自动接口（否则只能伪造）。

依据任务书 §16，本文件提供两种真实 Driver：
1. `CodeBuddyCLIDriver`：真实调用 `buddycn chat`，但当无法采集结果时明确报错，
   不伪造任何 stdout / trace / token。适合在已安排 headless 采集（如 GUI transcript 管道）
   的环境使用。
2. `CodeBuddyManualDriver`：Harness 负责隔离工作区 + prompt + MCP 配置（baseline 不写、
   mcp 写 `.codebuddy/mcp.json`），由真实 CodeBuddy（人工或本会话 Agent）执行并回灌
   artifact + trace。所有结果标记 `manual_execution=true`，不冒充自动化能力。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from .base import (
    AgentRunResult,
    BenchmarkDriver,
    CodeBuddyConfig,
    STATUS_ERROR,
    STATUS_MANUAL_PENDING,
    STATUS_MANUAL_REQUIRED,
    STATUS_OK,
    STATUS_TIMEOUT,
    UNAVAILABLE,
    copy_tree,
    normalize_codebuddy_trace,
)


class CodeBuddyManualDriver(BenchmarkDriver):
    """Manual / Import Driver：Harness 准备工作区，真实 CodeBuddy 执行并回灌。

    控制变量（baseline vs MCP）的真实实现层：
    - baseline 工作区：不写入任何 `.codebuddy/mcp.json`（CodeBuddy 无法访问 Plugin Dev Helper MCP）。
    - mcp 工作区：写入 `.codebuddy/mcp.json`，仅含 plugin-dev-helper MCP（指向真实已部署服务）。
    说明：若 CodeBuddy 合并用户级 mcp.json 提供同名为 MCP，则 baseline 仍可能读到；
    该环境因素在 benchmark metadata 中如实披露（见 ADR-004）。
    """

    def __init__(self, benchmark_dir: Path, config: CodeBuddyConfig):
        self.benchmark_dir = Path(benchmark_dir)
        self.config = config

    # ------------------------------------------------------------------ 准备

    def produce(self, case, mode: str, workspace: Path) -> dict[str, str]:
        """复制 fixture 到隔离工作区，写入 prompt，并按条件写入 MCP 配置。"""
        fixture_dir = self.benchmark_dir / "fixtures" / case.fixture
        if fixture_dir.exists():
            copy_tree(fixture_dir, workspace)

        # 写 task prompt（真实 Agent 执行时使用）
        (workspace / "task_prompt.md").write_text(
            f"# {case.id} {case.name}\n\n{case.user_request}\n", encoding="utf-8"
        )

        cb_dir = workspace / ".codebuddy"
        mcp_json = cb_dir / "mcp.json"
        if mode == "mcp" and self.config.mcp_enabled and self.config.mcp_servers:
            cb_dir.mkdir(parents=True, exist_ok=True)
            mcp_json.write_text(
                json.dumps({"mcpServers": self.config.mcp_servers}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            # baseline（或 mcp 未配置）：确保工作区不含 Plugin Dev Helper MCP 配置
            if mcp_json.exists():
                mcp_json.unlink()

        files: dict[str, str] = {}
        for p in sorted(workspace.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(workspace)
            if "node_modules" in rel.parts or "dist" in rel.parts or "build" in rel.parts:
                continue
            files[str(rel)] = p.read_text(encoding="utf-8", errors="ignore")
        return files

    def prepare_run(self, case, mode: str, workspace: Path) -> AgentRunResult:
        """准备一次 Manual 执行：复制 fixture + 写 prompt + MCP 配置，等待真实 Agent 执行。"""
        files = self.produce(case, mode, workspace)
        mcp_on = bool(mode == "mcp" and self.config.mcp_enabled and self.config.mcp_servers)
        return AgentRunResult(
            status=STATUS_MANUAL_PENDING,
            workspace=str(workspace),
            started_at=_now(),
            tool_calls=[],
            token_usage=UNAVAILABLE,
            metadata={
                "driver": "codebuddy-manual",
                "agent": self.config.agent,
                "model": self.config.model,
                "manual_execution": True,
                "mode": mode,
                "mcp_enabled": mcp_on,
                "mcp_servers": list(self.config.mcp_servers.keys()) if mcp_on else [],
                "prompt_path": str(workspace / "task_prompt.md"),
                "files": sorted(files.keys()),
                "note": "manual run: solve task in real CodeBuddy, then import result",
            },
        )

    # ------------------------------------------------------------------ 回灌

    @staticmethod
    def import_run(
        workspace: Path,
        trace_raw: Optional[dict] = None,
        accepted: Optional[bool] = None,
        config: Optional[CodeBuddyConfig] = None,
    ) -> AgentRunResult:
        """从真实 Agent 已执行的工作区回灌结果。

        - 读取工作区真实产出文件（源码级 vm.ts 等）。
        - 若提供 trace_raw（CodeBuddy MCP 调用记录），归一化到 canonical schema 并落到
          `<workspace>/trace.json`，作为 tool_calls 来源。
        - token_usage 始终 UNAVAILABLE（CodeBuddy 不提供程序化 token 统计）。
        """
        workspace = Path(workspace)
        files: dict[str, str] = {}
        for p in sorted(workspace.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(workspace)
            if "node_modules" in rel.parts or "dist" in rel.parts or "build" in rel.parts:
                continue
            if rel.name == "trace.json":
                continue
            files[str(rel)] = p.read_text(encoding="utf-8", errors="ignore")

        tool_calls: list[dict] = []
        normalized_trace: dict[str, Any] = {"run_id": "", "agent": "", "condition": "", "tasks": []}
        if trace_raw:
            normalized_trace = normalize_codebuddy_trace(
                trace_raw,
                agent=(config.agent if config else "codebuddy"),
            )
            # 把所有 task 的调用合并为本次 run 的工具调用清单（供 AgentRunResult 展示）
            for t in normalized_trace.get("tasks", []):
                tool_calls.extend(t.get("calls", []))
            (workspace / "trace.json").write_text(
                json.dumps(normalized_trace, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        return AgentRunResult(
            status=STATUS_OK,
            workspace=str(workspace),
            finished_at=_now(),
            tool_calls=tool_calls,
            token_usage=UNAVAILABLE,
            trace_path=str(workspace / "trace.json") if trace_raw else "",
            metadata={
                "driver": "codebuddy-manual",
                "manual_execution": True,
                "accepted": accepted,
                "mcp_enabled": bool(config.mcp_enabled) if config else None,
                "file_count": len(files),
                "trace_normalized": bool(trace_raw),
                "token_usage": UNAVAILABLE,
            },
        )


class CodeBuddyCLIDriver(BenchmarkDriver):
    """真实 CLI Driver：调用 `buddycn chat -m agent`。

    现状（本环境实测）：`buddycn chat` 不向 stdout 返回结果/轨迹，只拉起 GUI。
    因此 produce 在无法采集结果时明确抛出 RuntimeError，并提示改用 manual driver——
    **绝不** 用估计值或脚本生成内容冒充 CodeBuddy 输出。
    """

    def __init__(self, benchmark_dir: Path, config: CodeBuddyConfig):
        self.benchmark_dir = Path(benchmark_dir)
        self.config = config

    def produce(self, case, mode: str, workspace: Path) -> dict[str, str]:
        fixture_dir = self.benchmark_dir / "fixtures" / case.fixture
        if fixture_dir.exists():
            copy_tree(fixture_dir, workspace)
        (workspace / "task_prompt.md").write_text(
            f"# {case.id} {case.name}\n\n{case.user_request}\n", encoding="utf-8"
        )
        # baseline 不写 MCP；mcp 写配置（若无法控制全局 MCP，则披露）。
        if mode == "mcp" and self.config.mcp_enabled and self.config.mcp_servers:
            cb_dir = workspace / ".codebuddy"
            cb_dir.mkdir(parents=True, exist_ok=True)
            (cb_dir / "mcp.json").write_text(
                json.dumps({"mcpServers": self.config.mcp_servers}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        cli = self.config.cli_path or "buddycn"
        cmd = [cli, "chat", "-m", "agent", case.user_request]
        started = time.time()
        try:
            proc = subprocess.run(
                cmd, cwd=str(workspace), capture_output=True, text=True,
                timeout=self.config.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"CodeBuddy CLI 超时（{self.config.timeout_seconds}s），headless 采集不可用；"
                f"请改用 --driver codebuddy-manual。"
            )
        latency = (time.time() - started) * 1000

        if not proc.stdout.strip():
            raise RuntimeError(
                "CodeBuddy `chat` CLI 在本环境不向 stdout 返回结果/轨迹（仅拉起 GUI），"
                "无法 headless 采集。不要伪造输出——请改用 --driver codebuddy-manual，"
                "由真实 CodeBuddy 执行后回灌 artifact + trace。"
            )
        # 若未来环境支持 stdout 采集，这里解析真实输出；当前分支不会到达。
        return {
            "task_prompt.md": case.user_request,
            "cli_stdout.txt": proc.stdout,
        }


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
