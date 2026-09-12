"""酷家乐插件本地 HTTP Server 的静态检查与运行探测。"""

from __future__ import annotations

import json
import os
import posixpath
import signal
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from mcp_server.rules import KujialeRuleEngine

DEFAULT_SERVER_URL = "http://127.0.0.1:8082"
DEFAULT_ORIGIN = "https://miniapp-1258830046.file.myqcloud.com"
SOURCE_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json"}
IGNORED_DIRECTORIES = {".git", "node_modules", "dist", "build", ".next"}


class KujialeDevServerService:
    """不负责长期托管服务，只检查并探测插件工程自己的开发服务。"""

    def __init__(self, rules: KujialeRuleEngine):
        self.rules = rules

    def inspect_project(self, project_path: str | Path) -> dict[str, Any]:
        root = Path(project_path).expanduser().resolve()
        issues: list[dict[str, Any]] = []
        package_path = root / "package.json"
        if not package_path.is_file():
            issues.append(self._issue(
                "KJL-DEV-001", "package.json", 1,
                "插件工程缺少 package.json，无法提供可重复启动的本地 HTTP Server。",
            ))
            return {"package_json": False, "start_script": "", "issues": issues}

        try:
            package_text = package_path.read_text(encoding="utf-8")
            package = json.loads(package_text)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            issues.append(self._issue(
                "KJL-DEV-001", "package.json", 1,
                f"package.json 无法解析：{error}。",
            ))
            return {"package_json": False, "start_script": "", "issues": issues}

        scripts = package.get("scripts") if isinstance(package, dict) else None
        start_script = scripts.get("start") if isinstance(scripts, dict) else None
        if not isinstance(start_script, str) or not start_script.strip():
            issues.append(self._issue(
                "KJL-DEV-002", "package.json", 1,
                "package.json 未声明 scripts.start，本地插件服务无法按约定启动。",
            ))
            start_script = ""

        source = self._server_source(root)
        source_label = self._source_label(root)
        if "Access-Control-Allow-Origin" not in source:
            issues.append(self._issue(
                "KJL-DEV-003", source_label, 1,
                "本地 HTTP Server 未发现 Access-Control-Allow-Origin 配置。",
            ))
        if "OPTIONS" not in source.upper():
            issues.append(self._issue(
                "KJL-DEV-004", source_label, 1,
                "本地 HTTP Server 未发现 OPTIONS 预检处理。",
            ))

        return {
            "package_json": True,
            "start_script": start_script,
            "server_source_files": self._source_files(root),
            "issues": issues,
        }

    def probe(
        self,
        project_path: str | Path,
        server_url: str = DEFAULT_SERVER_URL,
        start_server: bool = False,
        timeout_seconds: float = 10.0,
        origin: str = DEFAULT_ORIGIN,
    ) -> dict[str, Any]:
        """探测已启动服务；start_server=True 时显式启动并在完成后回收 npm start。"""
        root = Path(project_path).expanduser().resolve()
        base_url = server_url.rstrip("/") or DEFAULT_SERVER_URL
        findings: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        process: subprocess.Popen[bytes] | None = None
        manifest: dict[str, Any] | None = None

        parsed_base_url = urlsplit(base_url)
        if parsed_base_url.scheme not in {"http", "https"} or parsed_base_url.hostname not in {
            "localhost", "127.0.0.1", "::1"
        }:
            findings.append(self._issue(
                "KJL-DEV-006", "package.json", 1,
                "probe_plugin_dev_server 只允许探测 localhost/127.0.0.1/::1 的本地服务。",
                details={"server_url": base_url},
            ))
            return self._probe_result(base_url, start_server, checks, findings)

        if not root.is_dir():
            findings.append(self._issue(
                "KJL-DEV-006", "package.json", 1,
                f"插件工程目录不存在：{project_path}。",
            ))
            return self._probe_result(base_url, start_server, checks, findings)

        try:
            requested_port = parsed_base_url.port
        except ValueError:
            findings.append(self._issue(
                "KJL-DEV-006", "package.json", 1,
                f"本地服务地址端口无效：{base_url}。",
                details={"server_url": base_url},
            ))
            return self._probe_result(base_url, start_server, checks, findings)

        if start_server:
            try:
                process = self._start_process(root, requested_port)
            except (OSError, ValueError) as error:
                findings.append(self._issue(
                    "KJL-DEV-006", "package.json", 1,
                    f"无法启动 npm start：{error}。",
                ))
                return self._probe_result(base_url, start_server, checks, findings)

        try:
            manifest_response = self._wait_for_get(
                f"{base_url}/manifest.json", timeout_seconds
            )
            checks.append(self._check_http_resource(
                "manifest", "/manifest.json", manifest_response, "application/json"
            ))
            self._check_cors("/manifest.json", manifest_response, origin, findings, checks)
            if manifest_response[0] == 200:
                try:
                    remote_manifest = json.loads(manifest_response[2].decode("utf-8"))
                    if isinstance(remote_manifest, dict):
                        manifest = remote_manifest
                except (UnicodeError, json.JSONDecodeError):
                    findings.append(self._issue(
                        "KJL-DEV-005", "manifest.json", 1,
                        "HTTP 服务返回的 manifest.json 不是合法 JSON。",
                    ))

            if manifest is None:
                findings.append(self._issue(
                    "KJL-DEV-005", "manifest.json", 1,
                    "HTTP 服务未返回可解析的 manifest.json，无法继续检查 frame 和 main。",
                ))
            else:
                for field, expected, label in (
                    ("frame", "text/html", "frame"),
                    ("main", "javascript", "main"),
                ):
                    value = manifest.get(field)
                    path = self._resource_path(value)
                    if path is None:
                        findings.append(self._issue(
                            "KJL-DEV-005", "manifest.json", 1,
                            f"manifest.{field} 必须指向 HTTP Server 提供的工程内资源文件。",
                            details={"field": field, "value": value},
                        ))
                        continue
                    response = self._wait_for_get(urljoin(f"{base_url}/", path), timeout_seconds)
                    checks.append(self._check_http_resource(label, path, response, expected))
                    self._check_cors(path, response, origin, findings, checks)

            options_response = self._request(
                f"{base_url}/manifest.json",
                method="OPTIONS",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Content-Type",
                },
                timeout=max(0.5, min(timeout_seconds, 5.0)),
            )
            checks.append(self._check_http_resource("cors_preflight", "/manifest.json", options_response, ""))
            self._check_cors("/manifest.json", options_response, origin, findings, checks, preflight=True)
        finally:
            if process is not None:
                self._stop_process(process)

        manifest_ok = any(check.get("status") == 200 for check in checks if check.get("name") == "manifest")
        if not manifest_ok and not any(item["rule_id"] == "KJL-DEV-006" for item in findings):
            findings.append(self._issue(
                "KJL-DEV-006", "manifest.json", 1,
                f"本地 HTTP Server 不可访问：{base_url}。",
            ))
        return self._probe_result(base_url, start_server, checks, findings)

    def _start_process(self, root: Path, port: int | None = None) -> subprocess.Popen[bytes]:
        if not (root / "package.json").is_file():
            raise ValueError("工程缺少 package.json")
        env = os.environ.copy()
        env["PORT"] = str(port or env.get("PORT") or 8082)
        return subprocess.Popen(
            ["npm", "start"],
            cwd=root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.wait(timeout=2)

    @staticmethod
    def _resource_path(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return None
        path = parsed.path.replace("\\", "/").lstrip("/")
        normalized = posixpath.normpath(path)
        if normalized in {"", ".", ".."} or normalized.startswith("../"):
            return None
        return normalized

    def _wait_for_get(self, url: str, timeout_seconds: float) -> tuple[int, dict[str, str], bytes]:
        deadline = time.monotonic() + max(0.5, timeout_seconds)
        last_response: tuple[int, dict[str, str], bytes] = (0, {}, b"")
        while time.monotonic() < deadline:
            last_response = self._request(url, timeout=0.5)
            if last_response[0] == 200:
                return last_response
            time.sleep(0.1)
        return last_response

    @staticmethod
    def _request(
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        timeout: float = 2.0,
    ) -> tuple[int, dict[str, str], bytes]:
        request = Request(url, method=method, headers=headers or {})
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, dict(response.headers.items()), response.read(2_000_000)
        except HTTPError as error:
            try:
                body = error.read(2_000_000)
            except OSError:
                body = b""
            return error.code, dict(error.headers.items()), body
        except (OSError, URLError):
            return 0, {}, b""

    @staticmethod
    def _check_http_resource(
        name: str,
        path: str,
        response: tuple[int, dict[str, str], bytes],
        expected_content_type: str,
    ) -> dict[str, Any]:
        status, headers, _ = response
        content_type = headers.get("Content-Type", "")
        valid_type = not expected_content_type or expected_content_type in content_type.lower()
        return {
            "name": name,
            "path": path,
            "status": status,
            "content_type": content_type,
            "content_type_valid": valid_type,
            "ok": status == 200 and valid_type,
        }

    def _check_cors(
        self,
        path: str,
        response: tuple[int, dict[str, str], bytes],
        origin: str,
        findings: list[dict[str, Any]],
        checks: list[dict[str, Any]],
        preflight: bool = False,
    ) -> None:
        status, headers, _ = response
        normalized = {key.lower(): value for key, value in headers.items()}
        allow_origin = normalized.get("access-control-allow-origin", "")
        allow_credentials = normalized.get("access-control-allow-credentials", "").lower()
        allow_methods = normalized.get("access-control-allow-methods", "").upper()
        allow_headers = normalized.get("access-control-allow-headers", "").lower()
        origin_ok = allow_origin == origin or (allow_origin == "*" and allow_credentials != "true")
        cors_ok = origin_ok and (not preflight or ("GET" in allow_methods and "OPTIONS" in allow_methods))
        if preflight and "content-type" not in allow_headers:
            cors_ok = False
        checks.append({
            "name": "cors_preflight" if preflight else "cors",
            "path": path,
            "status": status,
            "allow_origin": allow_origin,
            "allow_credentials": allow_credentials,
            "allow_methods": allow_methods,
            "allow_headers": allow_headers,
            "ok": cors_ok,
        })
        if not cors_ok:
            rule_id = "KJL-DEV-004" if preflight else "KJL-DEV-003"
            findings.append(self._issue(
                rule_id, "package.json", 1,
                f"HTTP 服务的 CORS 响应不满足酷家乐插件访问要求：{path}。",
                details={
                    "path": path,
                    "status": status,
                    "allow_origin": allow_origin,
                    "allow_credentials": allow_credentials,
                    "allow_methods": allow_methods,
                    "allow_headers": allow_headers,
                },
            ))

    def _server_source(self, root: Path) -> str:
        return "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in self._source_paths(root)
        )

    def _source_files(self, root: Path) -> list[str]:
        return [self._relative(root, path) for path in self._source_paths(root)]

    def _source_paths(self, root: Path) -> list[Path]:
        paths: list[Path] = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            if any(part in IGNORED_DIRECTORIES for part in path.parts):
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
            except OSError:
                continue
            paths.append(path)
        paths.sort()
        return paths

    def _source_label(self, root: Path) -> str:
        files = self._source_files(root)
        return files[0] if files else "package.json"

    @staticmethod
    def _relative(root: Path, path: Path) -> str:
        return path.resolve().relative_to(root.resolve()).as_posix()

    def _issue(
        self,
        rule_id: str,
        file: str,
        line: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rule = self.rules.get(rule_id)
        return {
            "rule_id": rule_id,
            "severity": rule.severity if rule else "high",
            "file": file,
            "line": line,
            "message": message,
            "risk": rule.description if rule else "缺少规则元数据。",
            "suggestion": rule.suggestion if rule else "检查酷家乐规则目录。",
            "confidence": rule.confidence if rule else "low",
            "details": details or {},
        }

    @staticmethod
    def _probe_result(
        server_url: str,
        started: bool,
        checks: list[dict[str, Any]],
        findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "server_url": server_url,
            "started_by_mcp": started,
            "passed": not findings,
            "status": "passed" if not findings else "failed",
            "checks": checks,
            "finding_count": len(findings),
            "findings": findings,
        }
