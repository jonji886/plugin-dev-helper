"""最小酷家乐工具插件骨架。"""

from __future__ import annotations

import json
from typing import Any

from mcp_server.rules import KujialeRuleEngine


class KujialeScaffoldService:
    """返回可供 Coding Agent 继续扩展的最小合法骨架，不生成业务实现。"""

    def __init__(self, rules: KujialeRuleEngine):
        self.rules = rules

    def build(self, task: str = "") -> dict[str, Any]:
        constraints = [rule.as_constraint() for rule in self.rules.query("all", task, limit=8)]
        return {
            "platform": "kujiale",
            "plugin_type": "tool_plugin",
            "task": task,
            "files": {
                "manifest.json": json.dumps(
                    {"name": "kujiale-tool-plugin", "version": "0.1.0", "frame": "ui.html", "main": "vm.js"},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                "ui.html": """<!doctype html>
<html lang="zh-CN">
  <head><meta charset="UTF-8"><title>酷家乐工具插件</title></head>
  <body>
    <main id="app">插件 UI</main>
    <script>
      window.parent.postMessage({ action: "getDesignId" }, "*");
      window.addEventListener("message", (event) => {
        if (event.data.action === "designIdResult") {
          document.getElementById("app").textContent = event.data.data ?? "";
        }
      });
    </script>
  </body>
</html>
""",
                "vm.js": """const defaultFrame = IDP.Miniapp.view.defaultFrame;

defaultFrame.onMessageReceive((event) => {
  if (event.data.action === "getDesignId") {
    defaultFrame.postMessage({ action: "designIdResult", data: "TODO" }, "*");
  }
});
""",
            },
            "responsibilities": {
                "UI": ["DOM", "网络请求", "用户交互", "window.parent.postMessage"],
                "VM": ["IDP API", "平台能力调用", "defaultFrame.postMessage"],
                "manifest": ["frame", "main"],
            },
            "communication_pattern": {
                "ui_to_vm": 'window.parent.postMessage({ action: "..." }, "*")',
                "vm_receive": "IDP.Miniapp.view.defaultFrame.onMessageReceive(...) ",
                "vm_to_ui": 'IDP.Miniapp.view.defaultFrame.postMessage({ action: "..." }, "*")',
                "ui_receive": 'window.addEventListener("message", handler)',
            },
            "constraints": constraints,
        }
