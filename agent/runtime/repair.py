"""有界 Repair Loop：基于 Validator Evidence 的最小必要修复。

约束（ADR-003）：
- Repair 必须 bounded：超过 max_repair_attempts 进入 FAILED。
- Repair 依据 Validator Evidence，不盲目重新生成整个工程。
- 每次 Repair 产生一个新的 Artifact Version。
- 不无限循环、不无限 token 消耗、不对无关代码做修改。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from agent.runtime.models import RepairContext
from mcp_server.services.project_validator import Issue


class RepairDriver(Protocol):
    """修复驱动：输入 RepairContext，返回修复后的文件字典（相对路径 -> 内容）。"""

    def repair(self, context: RepairContext) -> dict[str, str]:
        ...


@dataclass
class RepairOutcome:
    files: dict[str, str]
    changed: bool
    summary: str


class EvidenceBasedRepairDriver:
    """确定性证据驱动修复驱动（无需 LLM，用于本地闭环与测试）。

    仅对 evidence 充分的 repairable issue 做最小文本替换：
    - API_UNKNOWN_API：把幻觉符号替换为 validator 给出的候选符号。
    其余类型（缺字段 / 类型错误等）不在此驱动内硬改，交由真实 LLM 驱动处理。
    """

    def repair(self, context: RepairContext) -> dict[str, str]:
        files = dict(context.current_files)
        applied: list[str] = []

        for issue in context.repairable_issues:
            if issue.code in ("API_UNKNOWN_API", "unknown_api"):
                candidate = self._first_candidate(issue)
                if not candidate:
                    continue
                bad = self._bad_symbol(issue)
                if bad and bad != candidate and issue.file in files:
                    before = files[issue.file]
                    after = before.replace(bad, candidate)
                    if after != before:
                        files[issue.file] = after
                        applied.append(f"{issue.file}: {bad} -> {candidate}")

        changed = bool(applied)
        summary = "; ".join(applied) if applied else "未对可修复 issue 应用确定性修复。"
        return files if changed else context.current_files

    @staticmethod
    def _bad_symbol(issue: Issue) -> str:
        m = re.search(r"symbol=([\S]+)", issue.evidence or "")
        return m.group(1) if m else ""

    @staticmethod
    def _first_candidate(issue: Issue) -> str:
        # suggested_fix 形如 "IDP.Miniapp.exitMiniapp => IDP.Miniapp.exit" 或 "a, b"
        fix = (issue.suggested_fix or "").strip()
        if not fix:
            return ""
        # 优先取 "=>" 右侧的候选
        if "=>" in fix:
            return fix.split("=>", 1)[1].strip().split(",")[0].strip()
        # 否则取首个逗号 / 分号片段
        for token in re.split(r"[,;，；]", fix):
            token = token.strip()
            if token and not token.startswith("候选"):
                return token
        return ""


class BoundedRepairLoop:
    """执行有界修复循环。"""

    def __init__(self, driver: RepairDriver, max_attempts: int = 2):
        self.driver = driver
        self.max_attempts = max_attempts

    def can_repair(self, attempt: int) -> bool:
        return attempt < self.max_attempts

    def repair(self, context: RepairContext, attempt: int) -> RepairOutcome:
        files = self.driver.repair(context)
        changed = files != context.current_files
        summary = f"repair-{attempt + 1}: " + (
            "applied evidence-driven edits" if changed else "no deterministic edit applied"
        )
        return RepairOutcome(files=files, changed=changed, summary=summary)
