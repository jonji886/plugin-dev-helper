"""确定性参照 Driver：用于 Harness 自检（不是 Agent Benchmark）。

设计（ADR-004）：
- mcp 模式：在 fixture 基础上叠加参考解，代表「借助 MCP 正确产出的交付物」。
- baseline 模式：直接使用 fixture 原样，代表「无 MCP 时未能修正的交付物」（保守下界）。
注意：这是真实执行了校验与评分，但「Agent」本身是确定性 stand-in，非真实 LLM。
其产物只能解读为 harness 自检，不能冒充 Agent 能力分数。
"""

from __future__ import annotations

from pathlib import Path

from .base import BenchmarkDriver, AgentRunResult, STATUS_OK, UNAVAILABLE, copy_tree


class ReferenceDriver(BenchmarkDriver):
    """确定性参照驱动：mcp 应用 solution，baseline 原样 fixture。"""

    def __init__(self, benchmark_dir: Path):
        self.benchmark_dir = Path(benchmark_dir)

    def produce(self, case, mode: str, workspace: Path) -> dict[str, str]:
        fixture_dir = self.benchmark_dir / "fixtures" / case.fixture
        solution_dir = self.benchmark_dir / "solutions" / case.id
        if fixture_dir.exists():
            copy_tree(fixture_dir, workspace)
        files: dict[str, str] = {}
        for p in sorted(workspace.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(workspace)
            if "node_modules" in rel.parts or "dist" in rel.parts or "build" in rel.parts:
                continue
            files[str(rel)] = p.read_text(encoding="utf-8", errors="ignore")
        if mode == "mcp" and solution_dir.exists():
            self._overlay_solution(files, workspace, solution_dir)
        return files

    @staticmethod
    def _overlay_solution(files: dict[str, str], workspace: Path, solution_dir: Path) -> None:
        for sol in sorted(solution_dir.rglob("*")):
            if not sol.is_file():
                continue
            rel = str(sol.relative_to(solution_dir))
            content = sol.read_text(encoding="utf-8", errors="ignore")
            target = workspace / rel
            if target.exists():
                target.write_text(content, encoding="utf-8")
                files[rel] = content
                continue
            base = sol.name
            match = next((k for k in files if Path(k).name == base), None)
            if match:
                (workspace / match).write_text(content, encoding="utf-8")
                files[match] = content
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                files[rel] = content

    def run(self, case, mode: str, workspace: Path) -> AgentRunResult:
        """ReferenceDriver 是确定性 stand-in，produce 即代表一次「执行」。"""
        files = self.produce(case, mode, workspace)
        return AgentRunResult(
            status=STATUS_OK,
            workspace=str(workspace),
            tool_calls=[],                       # 确定性 stand-in 无真实工具调用
            token_usage=UNAVAILABLE,
            metadata={
                "driver": "reference",
                "mode": mode,
                "files": sorted(files.keys()),
                "note": "deterministic stand-in, not a real agent run",
            },
        )
