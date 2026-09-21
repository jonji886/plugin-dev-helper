"""Agent Driver 抽象层：把「真实 Coding Agent」与 Benchmark Harness 解耦。

设计目标（对应 benchmark/README.md 与 ADR-004 的诚实性原则）：
- 同一套 Harness 可对接 ReferenceDriver（确定性自检）与真实 CodeBuddy Driver。
- 所有真实 Agent 调用都通过 Driver 完成，不把 Agent 逻辑写死在 Harness 里。
- 任何无法获取的数据标记 UNAVAILABLE；绝不以估计值冒充真实值。

当前真实可用的 CodeBuddy 接入方式见 `codebuddy.py`：
- `CodeBuddyCLIDriver`：`buddycn chat -m agent` 真实 CLI 调用；本环境该 CLI 不向
  stdout 返回结果/轨迹（只拉起 GUI），无法headless采集，捕获失败时明确报错。
- `CodeBuddyManualDriver`：Harness 准备隔离工作区 + prompt + MCP 配置，由真实
  CodeBuddy（人工/本会话 Agent）执行并回灌 artifact + trace；不伪造自动能力。
"""

from __future__ import annotations

from .base import (
    AgentRunResult,
    BenchmarkDriver,
    CodeBuddyConfig,
    UNAVAILABLE,
    normalize_codebuddy_trace,
)
from .codebuddy import CodeBuddyCLIDriver, CodeBuddyManualDriver
from .reference import ReferenceDriver

__all__ = [
    "AgentRunResult",
    "BenchmarkDriver",
    "CodeBuddyConfig",
    "UNAVAILABLE",
    "normalize_codebuddy_trace",
    "ReferenceDriver",
    "CodeBuddyManualDriver",
    "CodeBuddyCLIDriver",
]
