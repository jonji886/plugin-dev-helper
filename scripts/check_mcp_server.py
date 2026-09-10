"""MCP Server 远程冒烟测试。

    MCP_SERVER_URL=http://127.0.0.1:8000/mcp python3 scripts/check_mcp_server.py

检查项：
1. /health 正常
2. MCP tool 可发现
3. search_docs 可用
4. get_api 可用
失败时以非 0 退出码结束，便于 CI / 部署后验收。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_QUERY = "工具插件 UI 和 VM 怎么通信"
DEFAULT_SYMBOL = "IDP.Miniapp.exit"

EXPECTED_TOOLS = {
    "search_docs",
    "get_api",
    "get_type",
    "get_related_symbols",
    "get_examples",
    "validate_api_usage",
}


def health_url(mcp_url: str) -> str:
    if mcp_url.endswith("/mcp"):
        return mcp_url[: -len("/mcp")] + "/health"
    return mcp_url.rstrip("/") + "/health"


def check_health(mcp_url: str) -> bool:
    import urllib.request

    url = health_url(mcp_url)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        print(f"FAIL health : {url} 请求失败: {error}")
        return False
    status = payload.get("status")
    print(f"PASS health : status={status} service={payload.get('service')} "
          f"knowledge={payload.get('knowledge_entries')} vector={payload.get('vector_available')} "
          f"graph={payload.get('graph_available')}")
    return status == "ok"


async def check_tools(mcp_url: str, query: str, symbol: str) -> bool:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    passed = True
    async with streamable_http_client(mcp_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            if EXPECTED_TOOLS <= names:
                print(f"PASS tools  : 发现 {len(names)} 个 tool -> {sorted(names)}")
            else:
                passed = False
                missing = sorted(EXPECTED_TOOLS - names)
                print(f"FAIL tools  : 缺少 tool {missing}，实际 {sorted(names)}")

            search_result = await session.call_tool("search_docs", {"query": query, "top_k": 3})
            search_payload = _payload(search_result)
            count = (search_payload or {}).get("result_count", 0)
            first_source = ""
            if (search_payload or {}).get("results"):
                first_source = search_payload["results"][0].get("source", "")
            if search_payload and count > 0 and first_source:
                print(f"PASS search : result_count={count} first_source={first_source}")
            else:
                passed = False
                print(f"FAIL search : search_docs 未返回可信结果 -> {json.dumps(search_payload, ensure_ascii=False)[:300]}")

            api_result = await session.call_tool("get_api", {"symbol": symbol})
            api_payload = _payload(api_result)
            if api_payload and api_payload.get("found"):
                print(
                    f"PASS get_api: {api_payload.get('symbol')} sdk_version={api_payload.get('sdk_version')} "
                    f"source={api_payload.get('source_file')}:{api_payload.get('source_lines', {}).get('start')}"
                )
            else:
                passed = False
                print(f"FAIL get_api: {symbol} 未命中 -> {json.dumps(api_payload, ensure_ascii=False)[:300]}")
    return passed


def _payload(result) -> dict:
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    for content in getattr(result, "content", []) or []:
        text = getattr(content, "text", "")
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text[:300]}
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Plugin Developer MCP 冒烟测试")
    parser.add_argument("--url", default=os.getenv("MCP_SERVER_URL", DEFAULT_URL),
                        help=f"MCP endpoint，默认读取环境变量 MCP_SERVER_URL，缺省 {DEFAULT_URL}")
    parser.add_argument("--query", default=os.getenv("MCP_SMOKE_QUERY", DEFAULT_QUERY))
    parser.add_argument("--symbol", default=os.getenv("MCP_SMOKE_SYMBOL", DEFAULT_SYMBOL))
    args = parser.parse_args()

    print(f"[smoke] MCP_SERVER_URL = {args.url}")
    health_ok = check_health(args.url)
    tools_ok = asyncio.run(check_tools(args.url, args.query, args.symbol))
    overall = health_ok and tools_ok
    print(f"[smoke] RESULT: {'PASS' if overall else 'FAIL'}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
