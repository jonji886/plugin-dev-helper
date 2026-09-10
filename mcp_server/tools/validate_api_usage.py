"""validate_api_usage：静态检查代码中的插件 SDK API 用法。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field


def register(mcp, container) -> None:
    settings = container.settings

    @mcp.tool(
        name="validate_api_usage",
        description=(
            "校验一段代码中插件 SDK API 的使用是否明显错误：不存在的 API、"
            "错误的 namespace、错误的参数名、缺失的必填参数、SDK 版本不一致。"
            "第一版只做高置信度静态检查，宁可少报，改完代码后可再次调用确认。"
        ),
    )
    async def validate_api_usage(
        code: Annotated[str, Field(description="待校验的 TypeScript / JavaScript 源码", min_length=1)],
        sdk_version: Annotated[str, Field(description="目标 SDK 版本，例如 1.83.0；留空表示不校验版本")] = "",
    ) -> dict[str, Any]:
        def build() -> dict:
            if len(code) > settings.max_code_chars:
                code_to_check = code[: settings.max_code_chars]
                truncated = True
            else:
                code_to_check = code
                truncated = False
            result = container.validator.validate(code_to_check, sdk_version=sdk_version)
            result["sdk_version"] = sdk_version
            result["status"] = "ok" if result["valid"] else "invalid"
            result["truncated"] = truncated
            result["message"] = (
                "未发现明显问题。" if result["valid"]
                else f"发现 {result['issue_count']} 个问题，请结合 get_api / get_type 修正后再调用本工具复核。"
            )
            return result

        return container.telemetry.guarded(
            "validate_api_usage", build, sdk_version=sdk_version, query="code"
        )
