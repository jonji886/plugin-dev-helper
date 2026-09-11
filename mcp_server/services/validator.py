"""插件 SDK 用法静态校验。

第一版不做完整 TypeScript 编译器，只做高价值、可验证的静态检查：
- 调用的 SDK symbol 是否存在
- namespace 是否正确
- API 是否属于指定 SDK version
- 已知参数名是否存在
- required 参数/字段是否明显缺失

原则：宁可少报，不要大量误报。所有判断都基于知识索引中的真实定义，
无法确认时不上报问题。
"""

from __future__ import annotations

import difflib
import re
from typing import Any

COMMENT_PATTERN = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
SDK_ROOTS = ("IDP",)
USAGE_PATTERN = re.compile(r"\b(?:IDP)(?:\s*\.\s*[A-Za-z_$][A-Za-z0-9_$]*)+")
MEMBER_PATTERN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
CALL_PATTERN = re.compile(r"^\s*\(")


def strip_comments(code: str) -> str:
    """移除注释时用等量换行替换，保证行号不变。"""
    def replace(match: re.Match) -> str:
        return "\n" * match.group(0).count("\n")

    return COMMENT_PATTERN.sub(replace, code)


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def balanced_arguments(code: str, start: int) -> str | None:
    """从 `(` 位置开始截取配对的实参文本，失败返回 None。"""
    if start >= len(code) or code[start] != "(":
        return None
    depth = 0
    for index in range(start, len(code)):
        char = code[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return code[start + 1:index]
    return None


def top_level_commas(text: str, end: int) -> int:
    """统计 [0, end) 区间内位于顶层（不在括号/字符串内）的逗号数量。"""
    depth = 0
    count = 0
    index = 0
    length = len(text)
    while index < min(end, length):
        char = text[index]
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth = max(0, depth - 1)
        elif char in "\"'`":
            quote = char
            index += 1
            while index < length and text[index] != quote:
                if text[index] == "\\":
                    index += 1
                index += 1
        elif char == "," and depth == 0:
            count += 1
        index += 1
    return count


def object_literal_keys(text: str) -> list[str]:
    """提取第一个对象字面量的顶层 key（不做完整语法解析）。"""
    start = text.find("{")
    if start == -1:
        return []
    keys: list[str] = []
    depth = 0
    index = start
    length = len(text)
    while index < length:
        char = text[index]
        if char in "{[(":
            depth += 1
            index += 1
            continue
        if char in "}])":
            depth -= 1
            if depth <= 0:
                break
            index += 1
            continue
        if char in "\"'`":
            quote = char
            index += 1
            while index < length and text[index] != quote:
                if text[index] == "\\":
                    index += 1
                index += 1
            index += 1
            continue
        if depth == 1:
            match = MEMBER_PATTERN.match(text, index)
            if match:
                cursor = match.end()
                while cursor < length and text[cursor] in " \t\r\n":
                    cursor += 1
                if cursor < length and text[cursor] == ":":
                    keys.append(match.group(0))
                index = cursor
                continue
        index += 1
    return keys


class UsageValidator:
    """基于知识索引的 SDK 用法校验器。"""

    def __init__(self, knowledge):
        self.knowledge = knowledge

    # ---------- 前缀（命名空间）集合 ----------

    def longest_known_prefix(self, symbol: str) -> str:
        prefixes = self.knowledge.known_prefixes()
        parts = symbol.split(".")
        for size in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:size])
            if candidate in prefixes:
                return candidate
        return ""

    # ---------- 主流程 ----------

    def validate(self, code: str, sdk_version: str = "") -> dict[str, Any]:
        raw_code = code or ""
        if len(raw_code) > 200_000:
            raw_code = raw_code[:200_000]
        clean = strip_comments(raw_code)

        issues: list[dict[str, Any]] = []
        checked: list[str] = []
        seen: set[str] = set()

        for match in USAGE_PATTERN.finditer(clean):
            usage = re.sub(r"\s*\.\s*", ".", match.group(0))
            if usage in seen:
                continue
            seen.add(usage)
            checked.append(usage)
            line = line_of(clean, match.start())

            resolution = self.knowledge.resolve_deep(usage)
            if not resolution.found:
                issues.append(
                    self._unknown_symbol_issue(usage, self.knowledge.resolve_path(usage), line)
                )
                continue

            entry = resolution.entry or {}
            unit = resolution.unit or {}
            self._check_version(usage, entry, sdk_version, line, issues)
            self._check_arguments(usage, unit, clean, match.end(), line, issues)

        errors = [issue for issue in issues if issue["severity"] == "error"]
        return {
            "valid": not errors,
            "checked_symbols": checked,
            "issue_count": len(issues),
            "issues": issues,
        }

    # ---------- 单项检查 ----------

    def _unknown_symbol_issue(self, usage: str, path, line: int) -> dict[str, Any]:
        prefix = path.confirmed_prefix or self.longest_known_prefix(usage)
        # 成员定义在类型中但未展开（例如 interface extends）时证据不足，只告警不报错。
        if path.uncertain:
            return {
                "type": "unverified_member",
                "severity": "warning",
                "symbol": usage,
                "line": line,
                "message": (
                    f"`{usage}` 的 `{path.failed_at}` 未在 `{prefix}` 的已知成员中找到；"
                    "该成员可能定义在未展开的父类型中，请用 get_api / get_type 人工确认。"
                ),
                "suggestions": [],
            }
        if prefix and prefix != usage:
            members = self.knowledge.namespace_members(prefix, limit=50)
            tail = (path.failed_at or usage[len(prefix) + 1:].split(".")[-1]).lower()
            suggestions = [
                member for member in members if tail in member.lower()
            ] or difflib.get_close_matches(usage, members, n=3, cutoff=0.5)
            return {
                "type": "unknown_api",
                "severity": "error",
                "symbol": usage,
                "line": line,
                "message": f"`{usage}` 不存在于当前知识库；`{prefix}` 有效，但 `{path.failed_at or usage[len(prefix) + 1:]}` 未找到。",
                "suggestions": suggestions[:5],
            }
        candidates = self.knowledge.candidates(usage, limit=5)
        return {
            "type": "unknown_api",
            "severity": "error",
            "symbol": usage,
            "line": line,
            "message": f"`{usage}` 不存在于当前知识库，请不要臆造该 API。",
            "suggestions": [candidate["symbol"] for candidate in candidates],
        }

    def _check_version(self, usage: str, entry: dict, sdk_version: str, line: int,
                       issues: list[dict[str, Any]]) -> None:
        if not sdk_version:
            return
        actual = str(entry.get("sdkVersion", "") or "")
        if actual and actual != sdk_version:
            issues.append({
                "type": "sdk_version_mismatch",
                "severity": "warning",
                "symbol": usage,
                "line": line,
                "message": f"`{usage}` 记录于 SDK v{actual}，与指定的 v{sdk_version} 不一致。",
                "suggestions": [f"v{actual}"],
            })

    def _check_arguments(self, usage: str, unit: dict, clean_code: str, end_index: int,
                         line: int, issues: list[dict[str, Any]]) -> None:
        rest = clean_code[end_index:end_index + 200]
        if not CALL_PATTERN.match(rest):
            return
        paren_index = end_index + rest.index("(")
        args = balanced_arguments(clean_code, paren_index)
        if args is None:
            return
        parameters = unit.get("parameters", []) or []
        required = [param for param in parameters if not param.get("optional", False)]

        if not args.strip():
            if required:
                issues.append({
                    "type": "missing_required_parameter",
                    "severity": "warning",
                    "symbol": usage,
                    "line": line,
                    "message": f"`{usage}` 需要参数 {', '.join(p['name'] for p in required)}，当前调用未传入。",
                    "suggestions": [param["name"] for param in required],
                })
            return

        keys = object_literal_keys(args)
        if not keys:
            return
        # 用对象字面量所在实参位置匹配对应参数类型，避免拿错参数定义造成误报
        arg_index = top_level_commas(args, args.find("{"))
        allowed, source_type = self._allowed_keys(parameters, arg_index)
        if allowed is None:
            return
        unknown = [key for key in keys if key not in allowed]
        for key in unknown:
            issues.append({
                "type": "unknown_parameter",
                "severity": "error",
                "symbol": usage,
                "line": line,
                "message": f"`{key}` 不是 `{source_type or usage}` 的已知字段。",
                "suggestions": difflib.get_close_matches(key, sorted(allowed), n=3, cutoff=0.5),
            })
        missing = [
            name for name, optional in (self._required_fields(allowed, parameters, arg_index) or [])
            if not optional and name not in keys
        ]
        if missing and not unknown:
            issues.append({
                "type": "missing_required_field",
                "severity": "warning",
                "symbol": usage,
                "line": line,
                "message": f"`{source_type or usage}` 的必填字段 {', '.join(missing)} 未传入。",
                "suggestions": missing,
            })

    def _allowed_keys(self, parameters: list[dict], arg_index: int = 0) -> tuple[set[str] | None, str]:
        """返回对象字面量允许的 key 集合；无法确定时返回 (None, "")。"""
        if not parameters or arg_index >= len(parameters):
            return None, ""
        for parameter in parameters[arg_index:arg_index + 1]:
            type_name = str(parameter.get("type", "") or "").strip()
            if not type_name:
                continue
            resolution = self.knowledge.resolve(type_name)
            if not resolution.found or resolution.match_type not in {"exact", "alias", "case_insensitive", "suffix"}:
                continue
            unit = resolution.unit or {}
            fields = unit.get("properties", []) or []
            members = unit.get("enumMembers", []) or []
            names = {field.get("name", "") for field in fields if field.get("name")}
            names |= {member.get("name", "") for member in members if member.get("name")}
            if names:
                return names, str(resolution.symbol)
        return None, ""

    def _required_fields(self, allowed: set[str], parameters: list[dict],
                         arg_index: int = 0) -> list[tuple[str, bool]]:
        if arg_index >= len(parameters):
            return []
        for parameter in parameters[arg_index:arg_index + 1]:
            type_name = str(parameter.get("type", "") or "").strip()
            if not type_name:
                continue
            resolution = self.knowledge.resolve(type_name)
            if not resolution.found or resolution.match_type not in {"exact", "alias", "case_insensitive", "suffix"}:
                continue
            unit = resolution.unit or {}
            fields = unit.get("properties", []) or []
            pairs = [
                (field.get("name", ""), bool(field.get("optional", False)))
                for field in fields
                if field.get("name") in allowed
            ]
            if pairs:
                return pairs
        return []
