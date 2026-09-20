"""validate_plugin_project：对完整插件工程做确定性项目级校验。

组合结构 / manifest / 平台规则 / API 用法 / 构建校验，返回结构化 Issue 列表。
不依赖 LLM Judge；宿主运行相关项显式标记 HOST_VALIDATION_REQUIRED。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field


def register(mcp, container) -> None:
    settings = container.settings

    @mcp.tool(
        name="validate_plugin_project",
        description=(
            "对完整插件工程目录做确定性校验：manifest / 结构 / 平台规则 / API 用法 / 构建。"
            "返回结构化 issue 列表（含 severity、category、code、file、line、evidence、suggested_fix）。"
            "确定性规则优先在本地校验；CORS / OPTIONS / HTTP 探活等宿主项标记 HOST_VALIDATION_REQUIRED。"
        ),
    )
    async def validate_plugin_project(
        project_dir: Annotated[str, Field(description="插件工程根目录的绝对路径")],
        sdk_version: Annotated[str, Field(description="目标 SDK 版本，例如 1.83.0；留空表示不校验版本")] = "",
    ) -> dict[str, Any]:
        def build() -> dict:
            root = Path(project_dir)
            if not root.exists() or not root.is_dir():
                return {
                    "status": "error",
                    "error_type": "invalid_project_dir",
                    "message": f"工程目录不存在或不是目录：{project_dir}",
                    "valid": False,
                    "issues": [],
                }
            validator = container.project_validator
            if sdk_version:
                validator = validator.__class__(
                    validator.knowledge, validator.rules, sdk_version=sdk_version, tsc_cmd=validator.tsc_cmd
                )
            result = validator.validate_project(root)
            payload = result.as_dict()
            payload["status"] = "ok" if result.valid else "invalid"
            payload["message"] = (
                "项目校验通过（本地静态）。" if result.valid
                else f"发现 {result.error_count} 个错误 / 共 {len(result.issues)} 条问题，请按要求修复后复核。"
            )
            return payload

        return container.telemetry.guarded(
            "validate_plugin_project", build, query=Path(project_dir).name
        )
