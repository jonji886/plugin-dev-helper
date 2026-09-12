"""酷家乐工具插件项目 Guardrail 校验器。

这里仅实现面向工具插件的轻量静态检查，不尝试成为通用 JavaScript SAST。
所有规则元数据（severity、风险、建议、评分分类）均来自 Rule Engine。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from mcp_server.rules import KujialeRuleEngine
from mcp_server.services.dev_server import KujialeDevServerService
from mcp_server.services.validator import (
    balanced_arguments,
    line_of,
    strip_comments,
)

SEVERITY_FACTORS = {"critical": 1.0, "high": 0.7, "medium": 0.4, "low": 0.2}
SCORE_WEIGHTS = {
    "structure": 20,
    "runtime": 35,
    "api_usage": 20,
    "communication": 15,
    "error_handling": 10,
}
SCORE_LABELS = {
    "structure": "Structure / Manifest",
    "runtime": "Runtime Compatibility",
    "api_usage": "API Usage",
    "communication": "UI / VM Communication",
    "error_handling": "Error Handling",
}

IDP_PATH = r"\bIDP\s*(?:\.\s*[A-Za-z_$][A-Za-z0-9_$]*)+"
IDP_USAGE_PATTERN = re.compile(IDP_PATH)
OPEN_API_HOST_PATTERN = re.compile(r"openapi\.kujiale\.com", re.IGNORECASE)
EXTERNAL_HTTP_URL_PATTERN = re.compile(
    r"http://(?!localhost(?::|/)|127\.0\.0\.1(?::|/)|\[::1\](?::|/))",
    re.IGNORECASE,
)
ACTION_LITERAL_PATTERN = re.compile(r"\baction\s*:\s*([\"'])([^\"']+)\1")
ACTION_COMPARE_PATTERN = re.compile(
    r"(?:\bdata|\b[A-Za-z_$][A-Za-z0-9_$]*\s*\.\s*data)\s*\.\s*action"
    r"\s*(?:===|==|:)\s*([\"'])([^\"']+)\1"
)
PLAIN_ACTION_COMPARE_PATTERN = re.compile(
    r"\baction\s*(?:===|==)\s*([\"'])([^\"']+)\1"
)
CASE_ACTION_PATTERN = re.compile(r"\bcase\s*([\"'])([^\"']+)\1\s*:")
WINDOW_PARENT_POST_PATTERN = re.compile(
    r"(?:\bwindow\s*\.\s*parent|\bparent)\s*\.\s*postMessage\s*\(",
)
DEFAULT_FRAME_POST_PATTERN = re.compile(
    r"(?:\bIDP(?:\s*\.\s*[A-Za-z_$][A-Za-z0-9_$]*)+\s*\.\s*)?"
    r"defaultFrame\s*\.\s*postMessage\s*\(",
)
VM_RECEIVE_PATTERN = re.compile(
    r"(?:\bIDP(?:\s*\.\s*[A-Za-z_$][A-Za-z0-9_$]*)+\s*\.\s*)?"
    r"defaultFrame\s*\.\s*onMessageReceive\s*\(",
)
UI_RECEIVE_PATTERN = re.compile(
    r"\bwindow\s*\.\s*addEventListener\s*\(\s*([\"'])message\1\s*,",
)


class PluginFinding(BaseModel):
    """对 Coding Agent 友好的、可定位的校验发现。"""

    model_config = ConfigDict(extra="ignore")

    rule_id: str
    severity: str
    file: str
    line: int = Field(ge=1)
    message: str
    risk: str
    suggestion: str
    confidence: str
    location: str = ""
    score_category: str = "runtime"
    details: dict[str, Any] = Field(default_factory=dict)


def mask_js_non_code(source: str) -> str:
    """把注释和字符串替换为空白，保留换行和字符位置。

    这不是完整 JS Lexer，但足以避免 `"document.title"` 或注释造成明显误报。
    """
    chars = list(source)
    index = 0
    state = "code"
    quote = ""
    while index < len(chars):
        current = chars[index]
        next_char = chars[index + 1] if index + 1 < len(chars) else ""
        if state == "code":
            if current == "/" and next_char == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if current == "/" and next_char == "*":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if current in {"'", '"', "`"}:
                quote = current
                chars[index] = " "
                index += 1
                state = "string"
                continue
            index += 1
            continue
        if state == "line_comment":
            if current == "\n":
                state = "code"
            else:
                chars[index] = " "
            index += 1
            continue
        if state == "block_comment":
            if current == "*" and next_char == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "code"
                continue
            if current != "\n":
                chars[index] = " "
            index += 1
            continue
        # string / template literal：保留换行，避免改变后续行号。
        if current == "\\":
            chars[index] = " "
            if index + 1 < len(chars) and chars[index + 1] != "\n":
                chars[index + 1] = " "
                index += 2
            else:
                index += 1
            continue
        if current == quote:
            chars[index] = " "
            index += 1
            state = "code"
            continue
        if current != "\n":
            chars[index] = " "
        index += 1
    return "".join(chars)


def _balanced_call_end(source: str, open_index: int) -> int | None:
    """找到调用括号结束位置；忽略字符串中的括号。"""
    if open_index >= len(source) or source[open_index] != "(":
        return None
    depth = 0
    index = open_index
    quote = ""
    while index < len(source):
        char = source[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _call_fragment(source: str, match: re.Match[str]) -> tuple[str, int] | None:
    open_index = source.find("(", match.start(), match.end())
    if open_index < 0:
        return None
    end_index = _balanced_call_end(source, open_index)
    if end_index is None:
        return None
    return source[open_index + 1:end_index], open_index + 1


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _line_for_key(text: str, key: str) -> int:
    match = re.search(rf"[\"']{re.escape(key)}[\"']\s*:", text)
    return line_of(text, match.start()) if match else 1


class PluginProjectValidator:
    """酷家乐工具插件（manifest + HTML UI + JS VM）校验器。"""

    def __init__(
        self,
        rule_engine: KujialeRuleEngine,
        usage_validator: Any | None = None,
        dev_server: KujialeDevServerService | None = None,
    ):
        self.rules = rule_engine
        self.usage_validator = usage_validator
        self.dev_server = dev_server or KujialeDevServerService(rule_engine)

    def validate(self, project_path: str | Path) -> dict[str, Any]:
        root = Path(project_path).expanduser().resolve()
        findings: list[PluginFinding] = []
        manifest_rel = "manifest.json"
        manifest_path = root / manifest_rel

        if not root.is_dir():
            self._add(
                findings,
                "KJL-MANIFEST-001",
                manifest_rel,
                1,
                f"插件项目目录不存在或不是目录：`{project_path}`。",
                details={"project_path": str(project_path)},
            )
            return self._result(root, findings)

        if not manifest_path.is_file():
            self._add(
                findings,
                "KJL-MANIFEST-001",
                manifest_rel,
                1,
                "插件根目录缺少 manifest.json。",
            )
            return self._result(root, findings)

        try:
            manifest_text = manifest_path.read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
        except json.JSONDecodeError as error:
            self._add(
                findings,
                "KJL-MANIFEST-004",
                manifest_rel,
                int(error.lineno or 1),
                f"manifest.json 不是合法 JSON：{error.msg}。",
            )
            return self._result(root, findings)
        except (OSError, UnicodeError) as error:
            self._add(
                findings,
                "KJL-MANIFEST-004",
                manifest_rel,
                1,
                f"manifest.json 无法读取：{error}。",
            )
            return self._result(root, findings)

        if not isinstance(manifest, dict):
            self._add(
                findings,
                "KJL-MANIFEST-004",
                manifest_rel,
                1,
                "manifest.json 的根值必须是对象。",
            )
            return self._result(root, findings)

        dev_server_result = self.dev_server.inspect_project(root)
        for issue in dev_server_result["issues"]:
            self._add(
                findings,
                issue["rule_id"],
                issue["file"],
                issue["line"],
                issue["message"],
                confidence=issue.get("confidence"),
                details=issue.get("details"),
            )

        missing_metadata = [
            key for key in ("name", "version")
            if not isinstance(manifest.get(key), str) or not manifest.get(key, "").strip()
        ]
        if missing_metadata:
            self._add(
                findings,
                "KJL-MANIFEST-005",
                manifest_rel,
                _line_for_key(manifest_text, missing_metadata[0]),
                f"manifest 缺少非空基本字段：{', '.join(missing_metadata)}。",
                details={"fields": missing_metadata},
            )

        frame_path = self._check_manifest_entry(
            root, manifest, manifest_text, "frame", ".html", "KJL-MANIFEST-002", findings
        )
        main_path = self._check_manifest_entry(
            root, manifest, manifest_text, "main", ".js", "KJL-MANIFEST-003", findings
        )

        if frame_path is not None and frame_path.is_file():
            self._validate_ui(root, frame_path, findings)
        if main_path is not None and main_path.is_file():
            self._validate_vm(root, main_path, findings)

        communication = self._validate_communication(root, frame_path, main_path, findings)
        return self._result(root, findings, communication, dev_server_result)

    def _check_manifest_entry(
        self,
        root: Path,
        manifest: dict[str, Any],
        manifest_text: str,
        field: str,
        extension: str,
        rule_id: str,
        findings: list[PluginFinding],
    ) -> Path | None:
        value = manifest.get(field)
        line = _line_for_key(manifest_text, field)
        if not isinstance(value, str) or not value.strip():
            self._add(
                findings,
                rule_id,
                "manifest.json",
                line,
                f"manifest.{field} 未声明。",
            )
            return None

        relative = Path(value)
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            self._add(
                findings,
                "KJL-MANIFEST-006",
                "manifest.json",
                line,
                f"manifest.{field} 指向插件目录之外的路径 `{value}`。",
                details={"field": field, "value": value},
            )
            return None

        if not candidate.is_file():
            self._add(
                findings,
                rule_id,
                "manifest.json",
                line,
                f"manifest.{field} 指向的文件不存在：`{value}`。",
                details={"field": field, "value": value},
            )
            return None

        accepted_extensions = {extension}
        if extension == ".html":
            accepted_extensions.add(".htm")
        if extension == ".js":
            accepted_extensions |= {".mjs", ".cjs"}
        if candidate.suffix.lower() not in accepted_extensions:
            expected = "HTML" if extension == ".html" else "JavaScript"
            self._add(
                findings,
                rule_id,
                "manifest.json",
                line,
                f"manifest.{field} 必须指向 {expected} 文件，当前为 `{value}`。",
                details={"field": field, "value": value},
            )
            return None
        return candidate

    def _validate_ui(self, root: Path, path: Path, findings: list[PluginFinding]) -> None:
        relative = _relative_path(root, path)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            self._add(
                findings,
                "KJL-UI-001",
                relative,
                1,
                f"UI 文件无法读取：{error}。",
                confidence="low",
            )
            return
        masked = mask_js_non_code(source)
        for match in IDP_USAGE_PATTERN.finditer(masked):
            usage = re.sub(r"\s*\.\s*", ".", match.group(0))
            self._add(
                findings,
                "KJL-UI-001",
                relative,
                line_of(source, match.start()),
                f"UI 中直接调用了酷家乐插件 API `{usage}`。",
                details={"symbol": usage},
            )
        for match in OPEN_API_HOST_PATTERN.finditer(source):
            self._add(
                findings,
                "KJL-NET-001",
                relative,
                line_of(source, match.start()),
                "插件 UI 直接引用了酷家乐 Open API 地址。",
                confidence="medium",
                details={"host": "openapi.kujiale.com"},
            )
        for match in EXTERNAL_HTTP_URL_PATTERN.finditer(source):
            self._add(
                findings,
                "KJL-NET-002",
                relative,
                line_of(source, match.start()),
                "插件 UI 引用了非本地 HTTP 地址，外部服务请求必须使用 HTTPS。",
                confidence="medium",
                details={"scheme": "http"},
            )

    def _validate_vm(self, root: Path, path: Path, findings: list[PluginFinding]) -> None:
        relative = _relative_path(root, path)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            self._add(
                findings,
                "KJL-VM-001",
                relative,
                1,
                f"VM 文件无法读取：{error}。",
                confidence="low",
            )
            return

        masked = mask_js_non_code(source)
        self._check_patterns(
            root,
            relative,
            source,
            masked,
            findings,
            "KJL-VM-001",
            [
                re.compile(r"\bdocument\s*(?:\.\s*[A-Za-z_$]|\[)"),
                re.compile(r"\bwindow\s*(?:\.\s*[A-Za-z_$]|\[)"),
                re.compile(r"\blocalStorage\b"),
                re.compile(r"\bsessionStorage\b"),
            ],
            "VM 中使用了浏览器 DOM/Window API `{token}`。",
        )
        self._check_patterns(
            root,
            relative,
            source,
            masked,
            findings,
            "KJL-VM-002",
            [re.compile(r"\bfetch\s*\("), re.compile(r"\bXMLHttpRequest\b")],
            "VM 中直接使用了浏览器网络能力 `{token}`。",
        )
        self._check_patterns(
            root,
            relative,
            source,
            masked,
            findings,
            "KJL-VM-003",
            [re.compile(r"\bsetTimeout\s*\("), re.compile(r"\bsetInterval\s*\(")],
            "VM 中使用了不支持的延迟执行函数 `{token}`。",
        )
        self._check_patterns(
            root,
            relative,
            source,
            masked,
            findings,
            "KJL-VM-005",
            [re.compile(r"\bconsole\s*\.\s*(?:log|error|warn|info|debug)\s*\(")],
            "VM 中使用 console 作为运行反馈 `{token}`，生产环境可能没有有效实现。",
        )
        self._check_readonly_assignments(root, relative, source, masked, findings)
        self._check_promise_chains(root, relative, source, masked, findings)
        self._check_known_api_usage(root, relative, source, findings)

    def _check_patterns(
        self,
        _root: Path,
        relative: str,
        source: str,
        masked: str,
        findings: list[PluginFinding],
        rule_id: str,
        patterns: Iterable[re.Pattern[str]],
        message_template: str,
    ) -> None:
        seen: set[tuple[int, str]] = set()
        for pattern in patterns:
            for match in pattern.finditer(masked):
                token = re.sub(r"\s+", "", match.group(0)).rstrip("(")
                marker = (match.start(), token)
                if marker in seen:
                    continue
                seen.add(marker)
                self._add(
                    findings,
                    rule_id,
                    relative,
                    line_of(source, match.start()),
                    message_template.format(token=token),
                    details={"token": token},
                )

    def _check_readonly_assignments(
        self,
        _root: Path,
        relative: str,
        source: str,
        masked: str,
        findings: list[PluginFinding],
    ) -> None:
        declaration_pattern = re.compile(
            rf"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
            rf"(?:await\s+)?{IDP_PATH}\s*\(",
        )
        for declaration in declaration_pattern.finditer(masked):
            variable = declaration.group(1)
            assignment_pattern = re.compile(
                rf"\b{re.escape(variable)}\s*\.\s*[A-Za-z_$][A-Za-z0-9_$]*\s*=",
            )
            for assignment in assignment_pattern.finditer(masked, declaration.end()):
                property_match = re.search(r"\.\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*=", assignment.group(0))
                property_name = property_match.group(1) if property_match else "property"
                self._add(
                    findings,
                    "KJL-VM-004",
                    relative,
                    line_of(source, assignment.start()),
                    f"疑似修改 IDP API 返回对象 `{variable}.{property_name}`。",
                    confidence="medium",
                    details={"variable": variable, "property": property_name},
                )

    def _check_promise_chains(
        self,
        _root: Path,
        relative: str,
        source: str,
        masked: str,
        findings: list[PluginFinding],
    ) -> None:
        for api_match in IDP_USAGE_PATTERN.finditer(masked):
            open_index = masked.find("(", api_match.end())
            if open_index < 0:
                continue
            call_end = _balanced_call_end(source, open_index)
            if call_end is None:
                continue
            tail = masked[call_end + 1:]
            then_match = re.match(r"\s*\.\s*then\s*\(", tail)
            if then_match is None:
                continue
            then_open = call_end + 1 + then_match.end() - 1
            then_end = _balanced_call_end(source, then_open)
            if then_end is None:
                continue
            after_then = masked[then_end + 1:]
            catch_match = re.match(
                r"\s*(?:\.\s*finally\s*\([^)]*\)\s*)?\.\s*catch\s*\(",
                after_then,
            )
            if catch_match is not None:
                continue
            self._add(
                findings,
                "KJL-VM-006",
                relative,
                line_of(source, api_match.start()),
                "IDP Promise 链调用了 then，但未发现对应的 catch 异常处理。",
                details={"symbol": re.sub(r"\s*\.\s*", ".", api_match.group(0))},
            )

    def _check_known_api_usage(
        self,
        _root: Path,
        relative: str,
        source: str,
        findings: list[PluginFinding],
    ) -> None:
        if self.usage_validator is None:
            return
        # defaultFrame 是通信运行时对象，不是 SDK 知识索引中的业务 API；
        # 去掉它后复用上一版本的 API Validator，避免重复实现参数校验。
        code = strip_comments(source)
        code = re.sub(
            r"\bIDP\s*(?:\.\s*[A-Za-z_$][A-Za-z0-9_$]*)*\s*\.\s*defaultFrame\b",
            "DEFAULT_FRAME",
            code,
        )
        result = self.usage_validator.validate(code)
        for issue in result.get("issues", []):
            line = int(issue.get("line", 1) or 1)
            issue_type = str(issue.get("type", "api_usage"))
            message = str(issue.get("message", "VM 中的 API 用法无法确认。"))
            self._add(
                findings,
                "KJL-API-001",
                relative,
                line,
                message,
                details={
                    "issue_type": issue_type,
                    "symbol": issue.get("symbol", ""),
                    "suggestions": issue.get("suggestions", []),
                },
            )

    def _validate_communication(
        self,
        root: Path,
        frame_path: Path | None,
        main_path: Path | None,
        findings: list[PluginFinding],
    ) -> dict[str, list[str]]:
        empty = {
            "ui_sends": [],
            "vm_receives": [],
            "vm_sends": [],
            "ui_receives": [],
        }
        if frame_path is None or main_path is None or not frame_path.is_file() or not main_path.is_file():
            return empty
        try:
            ui_source = frame_path.read_text(encoding="utf-8")
            vm_source = main_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return empty
        ui_clean = strip_comments(ui_source)
        vm_clean = strip_comments(vm_source)

        ui_send_matches = list(WINDOW_PARENT_POST_PATTERN.finditer(ui_clean))
        ui_all_post = list(re.finditer(r"\bpostMessage\s*\(", ui_clean))
        if ui_all_post and not ui_send_matches:
            match = ui_all_post[0]
            self._add(
                findings,
                "KJL-COMM-001",
                _relative_path(root, frame_path),
                line_of(ui_source, match.start()),
                "UI 使用了非 window.parent.postMessage 的消息发送方式。",
            )

        vm_send_matches = list(DEFAULT_FRAME_POST_PATTERN.finditer(vm_clean))
        vm_all_post = list(re.finditer(r"\bpostMessage\s*\(", vm_clean))
        if vm_all_post and not vm_send_matches:
            match = vm_all_post[0]
            self._add(
                findings,
                "KJL-COMM-002",
                _relative_path(root, main_path),
                line_of(vm_source, match.start()),
                "VM 使用了非 defaultFrame.postMessage 的消息发送方式。",
            )

        ui_sends = self._action_records(ui_clean, ui_send_matches, sent=True)
        vm_receives = self._action_records(
            vm_clean, list(VM_RECEIVE_PATTERN.finditer(vm_clean)), sent=False
        )
        vm_sends = self._action_records(vm_clean, vm_send_matches, sent=True)
        ui_receives = self._action_records(
            ui_clean, list(UI_RECEIVE_PATTERN.finditer(ui_clean)), sent=False
        )

        communication = {
            "ui_sends": self._unique_actions(ui_sends),
            "vm_receives": self._unique_actions(vm_receives),
            "vm_sends": self._unique_actions(vm_sends),
            "ui_receives": self._unique_actions(ui_receives),
        }

        self._check_action_pairs(
            root,
            frame_path,
            main_path,
            ui_sends,
            vm_receives,
            "UI 发送但 VM 未处理 action `{action}`。",
            "UI",
            findings,
        )
        self._check_action_pairs(
            root,
            main_path,
            frame_path,
            vm_receives,
            ui_sends,
            "VM 接收但 UI 未发送 action `{action}`。",
            "VM",
            findings,
        )
        self._check_action_pairs(
            root,
            main_path,
            frame_path,
            vm_sends,
            ui_receives,
            "VM 返回但 UI 未监听 action `{action}`。",
            "VM",
            findings,
        )
        self._check_action_pairs(
            root,
            frame_path,
            main_path,
            ui_receives,
            vm_sends,
            "UI 监听但 VM 未返回 action `{action}`。",
            "UI",
            findings,
        )
        return communication

    @staticmethod
    def _unique_actions(records: list[tuple[str, int]]) -> list[str]:
        actions: list[str] = []
        for action, _ in records:
            if action not in actions:
                actions.append(action)
        return actions

    @staticmethod
    def _action_records(
        source: str,
        matches: list[re.Match[str]],
        sent: bool,
    ) -> list[tuple[str, int]]:
        records: list[tuple[str, int]] = []
        for match in matches:
            fragment = _call_fragment(source, match)
            if fragment is None:
                continue
            body, offset = fragment
            pattern = ACTION_LITERAL_PATTERN if sent else ACTION_COMPARE_PATTERN
            action_matches = list(pattern.finditer(body))
            if not sent:
                action_matches += list(PLAIN_ACTION_COMPARE_PATTERN.finditer(body))
                action_matches += list(CASE_ACTION_PATTERN.finditer(body))
            for action_match in action_matches:
                records.append((action_match.group(2), offset + action_match.start()))
        return records

    def _check_action_pairs(
        self,
        root: Path,
        source_path: Path,
        _other_path: Path,
        source_records: list[tuple[str, int]],
        other_records: list[tuple[str, int]],
        message_template: str,
        _side: str,
        findings: list[PluginFinding],
    ) -> None:
        other_actions = {action for action, _ in other_records}
        relative = _relative_path(root, source_path)
        seen: set[str] = set()
        try:
            source = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return
        for action, index in source_records:
            if action in other_actions or action in seen:
                continue
            seen.add(action)
            self._add(
                findings,
                "KJL-COMM-003",
                relative,
                line_of(source, index),
                message_template.format(action=action),
                details={"action": action},
            )

    def _add(
        self,
        findings: list[PluginFinding],
        rule_id: str,
        file: str,
        line: int,
        message: str,
        confidence: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        rule = self.rules.get(rule_id)
        if rule is None:
            # 规则文件缺失属于部署配置问题，保留可读结果而不是让扫描崩溃。
            severity = "high"
            risk = "规则元数据缺失，无法确认该发现的完整平台风险。"
            suggestion = "检查 rules/kujiale 规则目录是否完整。"
            default_confidence = "low"
            score_category = "runtime"
        else:
            severity = rule.severity
            risk = rule.description
            suggestion = rule.suggestion
            default_confidence = rule.confidence
            score_category = rule.score_category
        finding = PluginFinding(
            rule_id=rule_id,
            severity=severity,
            file=file,
            line=max(1, int(line or 1)),
            message=message,
            risk=risk,
            suggestion=suggestion,
            confidence=confidence or default_confidence,
            location=f"{file}:{max(1, int(line or 1))}" if file else str(max(1, int(line or 1))),
            score_category=score_category,
            details=details or {},
        )
        findings.append(finding)

    def _result(
        self,
        root: Path,
        findings: list[PluginFinding],
        communication: dict[str, list[str]] | None = None,
        dev_server: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        summary = {severity: 0 for severity in ("critical", "high", "medium", "low")}
        for finding in findings:
            summary[finding.severity] = summary.get(finding.severity, 0) + 1

        penalties = {category: 0.0 for category in SCORE_WEIGHTS}
        for finding in findings:
            category = finding.score_category if finding.score_category in SCORE_WEIGHTS else "runtime"
            penalties[category] += SCORE_WEIGHTS[category] * SEVERITY_FACTORS.get(finding.severity, 0.5)
        category_scores: dict[str, dict[str, Any]] = {}
        total_penalty = 0.0
        for category, weight in SCORE_WEIGHTS.items():
            penalty = min(float(weight), penalties[category])
            total_penalty += penalty
            category_scores[category] = {
                "label": SCORE_LABELS[category],
                "weight": weight,
                "score": round(weight - penalty, 2),
            }
        passed = not findings
        return {
            "platform": "kujiale",
            "project_path": str(root),
            "valid": summary["critical"] == 0,
            "passed": passed,
            "status": "passed" if passed else "failed",
            "score": max(0, round(100 - total_penalty, 2)),
            "summary": summary,
            "category_scores": category_scores,
            "communication": communication or {
                "ui_sends": [],
                "vm_receives": [],
                "vm_sends": [],
                "ui_receives": [],
            },
            "dev_server": dev_server or {
                "package_json": False,
                "start_script": "",
                "server_source_files": [],
                "issues": [],
            },
            "finding_count": len(findings),
            "findings": [finding.model_dump() for finding in findings],
        }
