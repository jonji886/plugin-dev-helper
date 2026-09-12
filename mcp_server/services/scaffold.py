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
                "package.json": json.dumps(
                    {
                        "name": "kujiale-tool-plugin",
                        "private": True,
                        "version": "0.1.0",
                        "scripts": {"start": "node dev-server.js"},
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                "dev-server.js": """const http = require("http");
const fs = require("fs");
const path = require("path");

const root = __dirname;
const port = Number(process.env.PORT || 8082);
const allowedOrigin = process.env.KUJIALE_ALLOWED_ORIGIN ||
  "https://miniapp-1258830046.file.myqcloud.com";
const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".css": "text/css; charset=utf-8"
};

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": allowedOrigin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
  };
}

const server = http.createServer((request, response) => {
  const headers = corsHeaders();
  if (request.method === "OPTIONS") {
    response.writeHead(204, headers);
    response.end();
    return;
  }
  if (request.method !== "GET") {
    response.writeHead(405, { ...headers, "Content-Type": "text/plain; charset=utf-8" });
    response.end("Method Not Allowed");
    return;
  }

  const requestPath = decodeURIComponent((request.url || "/").split("?")[0]);
  const relativePath = path.posix.normalize(requestPath).replace(/^\\/+/, "");
  const filePath = path.resolve(root, relativePath);
  if (filePath !== root && !filePath.startsWith(`${root}${path.sep}`)) {
    response.writeHead(403, headers);
    response.end("Forbidden");
    return;
  }
  fs.readFile(filePath, (error, content) => {
    if (error) {
      response.writeHead(error.code === "ENOENT" ? 404 : 500, {
        ...headers,
        "Content-Type": "text/plain; charset=utf-8"
      });
      response.end(error.code === "ENOENT" ? "Not Found" : "Server Error");
      return;
    }
    response.writeHead(200, {
      ...headers,
      "Content-Type": contentTypes[path.extname(filePath).toLowerCase()] ||
        "application/octet-stream"
    });
    response.end(content);
  });
});

server.listen(port, "127.0.0.1", () => {
  console.log(`[kujiale-plugin] http://127.0.0.1:${port}`);
});
""",
            },
            "responsibilities": {
                "UI": ["DOM", "网络请求", "用户交互", "window.parent.postMessage"],
                "VM": ["IDP API", "平台能力调用", "defaultFrame.postMessage"],
                "manifest": ["frame", "main"],
                "dev_server": ["package.json scripts.start", "CORS", "OPTIONS", "manifest/frame/main 可访问"],
            },
            "communication_pattern": {
                "ui_to_vm": 'window.parent.postMessage({ action: "..." }, "*")',
                "vm_receive": "IDP.Miniapp.view.defaultFrame.onMessageReceive(...) ",
                "vm_to_ui": 'IDP.Miniapp.view.defaultFrame.postMessage({ action: "..." }, "*")',
                "ui_receive": 'window.addEventListener("message", handler)',
            },
            "constraints": constraints,
        }
