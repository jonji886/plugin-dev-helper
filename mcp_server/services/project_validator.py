"""项目级确定性 Validator：判断一个完整插件 Artifact 是否满足交付条件。

设计原则（ADR-001）：
- 可确定性检查的规则（结构 / manifest / 平台规则 / API 符号）一律由 Validator 判断，
  不交给 LLM Judge。
- 复用既有的 ``UsageValidator``（单段 API 校验）与 ``KujialeRuleEngine``（平台硬约束）。
- 宿主运行相关规则（CORS / OPTIONS / 真实 HTTP 探活）无法在纯静态层验证，
  显式标记为 ``HOST_VALIDATION_REQUIRED``，不伪造「运行通过」。

Issue 统一结构（详见 README / SPEC）：
    issue_id, severity, category, code, message, file, line,
    evidence, suggested_fix, repairable

Severity: CRITICAL / HIGH / MEDIUM / WARNING
Category: STRUCTURE / MANIFEST / API / RULE / PERMISSION / TYPE / BUILD / RUNTIME / VERSION
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_server.rules import KujialeRuleEngine
from mcp_server.services.knowledge import KnowledgeService
from mcp_server.services.validator import UsageValidator

# ---------------------------------------------------------------------------
# 统一 Issue 模型
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "WARNING": 3}

# 允许宿主环境校验、本地静态无法判定的情况
HOST_VALIDATION_REQUIRED = "HOST_VALIDATION_REQUIRED"
LOCAL_VALIDATION_PASS = "LOCAL_VALIDATION_PASS"


@dataclass
class Issue:
    issue_id: str
    severity: str  # CRITICAL/HIGH/MEDIUM/WARNING
    category: str  # STRUCTURE/MANIFEST/API/RULE/PERMISSION/TYPE/BUILD/RUNTIME/VERSION
    code: str  # 机器可读错误码，例如 KJL-MANIFEST-002 / API_UNKNOWN_SYMBOL
    message: str
    file: str = ""
    line: int = 0
    evidence: str = ""
    suggested_fix: str = ""
    repairable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "severity": self.severity,
            "category": self.category,
            "code": self.code,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "evidence": self.evidence,
            "suggested_fix": self.suggested_fix,
            "repairable": self.repairable,
        }


@dataclass
class ValidationResult:
    valid: bool
    issues: list[Issue] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    # 宿主运行相关校验的结论：LOCAL_VALIDATION_PASS / HOST_VALIDATION_REQUIRED
    runtime_check: str = LOCAL_VALIDATION_PASS

    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "CRITICAL" or i.severity == "HIGH"]

    def as_dict(self) -> dict[str, Any]:
        errors = self.errors()
        by_severity: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for issue in self.issues:
            by_severity[issue.severity] = by_severity.get(issue.severity, 0) + 1
            by_category[issue.category] = by_category.get(issue.category, 0) + 1
        return {
            "valid": self.valid,
            "issue_count": len(self.issues),
            "error_count": len(errors),
            "runtime_check": self.runtime_check,
            "summary": {
                "by_severity": by_severity,
                "by_category": by_category,
                "repairable": sum(1 for i in self.issues if i.repairable),
                **self.summary,
            },
            "issues": [i.to_dict() for i in self.issues],
        }


# ---------------------------------------------------------------------------
# 项目级 Validator
# ---------------------------------------------------------------------------


class ProjectValidator:
    """组合结构 / manifest / 平台规则 / API 用法 / 静态 / 构建校验。"""

    def __init__(
        self,
        knowledge: KnowledgeService,
        rules: KujialeRuleEngine,
        sdk_version: str = "",
        tsc_cmd: list[str] | None = None,
    ):
        self.knowledge = knowledge
        self.rules = rules
        self.sdk_version = sdk_version or ""
        self.tsc_cmd = tsc_cmd  # 例如 ["node_modules/.bin/tsc"]；为 None 时跳过构建校验
        self._usage_validator = UsageValidator(knowledge)

    # ----------------------------- 主入口 -----------------------------

    def validate_project(self, project_dir: str | Path) -> ValidationResult:
        root = Path(project_dir)
        issues: list[Issue] = []

        # 1) 结构校验
        issues.extend(self._structure_check(root))
        # 2) manifest 校验
        manifest, manifest_issues = self._manifest_check(root)
        issues.extend(manifest_issues)
        # 3) 平台规则校验（确定性检测）
        issues.extend(self._rule_check(root, manifest))
        # 4) API 用法校验（复用 UsageValidator，覆盖全部 .ts/.js）
        issues.extend(self._api_check(root))
        # 5) 静态 / 构建校验（环境允许时）
        issues.extend(self._build_check(root))

        errors = [i for i in issues if i.severity in {"CRITICAL", "HIGH"}]
        runtime_check = self._runtime_check(root, manifest)
        result = ValidationResult(
            valid=not errors,
            issues=issues,
            runtime_check=runtime_check,
            summary={
                "project": str(root),
                "sdk_version": self.sdk_version,
                "manifest_present": manifest is not None,
            },
        )
        return result

    # ----------------------------- 1) 结构 -----------------------------

    def _structure_check(self, root: Path) -> list[Issue]:
        issues: list[Issue] = []
        # KJL-MANIFEST-001：manifest 必须存在
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            issues.append(Issue(
                issue_id="STRUCT-MANIFEST-MISSING",
                severity="CRITICAL",
                category="STRUCTURE",
                code="KJL-MANIFEST-001",
                message="插件根目录缺少 manifest.json，无法声明入口文件。",
                file="manifest.json",
                evidence="manifest.json not found in plugin root",
                suggested_fix="在插件根目录新增 manifest.json，并声明 name/version/frame/main。",
                repairable=True,
            ))
        # KJL-DEV-001：package.json 必须存在
        if not (root / "package.json").exists():
            issues.append(Issue(
                issue_id="STRUCT-PACKAGE-MISSING",
                severity="CRITICAL",
                category="STRUCTURE",
                code="KJL-DEV-001",
                message="本地开发工程缺少 package.json，无法提供可重复启动的 HTTP 服务。",
                file="package.json",
                evidence="package.json not found",
                suggested_fix="新增 package.json，并通过 scripts.start 声明本地 HTTP Server。",
                repairable=True,
            ))
        return issues

    # ----------------------------- 2) manifest -----------------------------

    def _manifest_check(self, root: Path) -> tuple[dict | None, list[Issue]]:
        issues: list[Issue] = []
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            return None, issues

        # KJL-MANIFEST-004：必须是合法 JSON 对象
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            issues.append(Issue(
                issue_id="MANIFEST-JSON-INVALID",
                severity="CRITICAL",
                category="MANIFEST",
                code="KJL-MANIFEST-004",
                message=f"manifest.json 不是合法 JSON：{error}",
                file="manifest.json",
                evidence=str(error),
                suggested_fix="修复 manifest.json 的 JSON 语法，确保根值为对象。",
                repairable=True,
            ))
            return None, issues
        if not isinstance(manifest, dict):
            issues.append(Issue(
                issue_id="MANIFEST-NOT-OBJECT",
                severity="CRITICAL",
                category="MANIFEST",
                code="KJL-MANIFEST-004",
                message="manifest.json 根值必须是 JSON 对象。",
                file="manifest.json",
                repairable=True,
            ))
            return None, issues

        # KJL-MANIFEST-005：基本元数据
        missing_fields = [f for f in ("name", "version") if not str(manifest.get(f, "")).strip()]
        if missing_fields:
            issues.append(Issue(
                issue_id="MANIFEST-META-MISSING",
                severity="CRITICAL",
                category="MANIFEST",
                code="KJL-MANIFEST-005",
                message=f"manifest 缺少非空字段：{', '.join(missing_fields)}。",
                file="manifest.json",
                evidence=f"missing={missing_fields}",
                suggested_fix="补充非空的 name 与 version 字段。",
                repairable=True,
            ))

        # KJL-MANIFEST-002 / 003：frame / main 存在且文件真实存在
        for field_name, ext_hint, code, sev in (
            ("frame", ".html", "KJL-MANIFEST-002", "CRITICAL"),
            ("main", ".js", "KJL-MANIFEST-003", "CRITICAL"),
        ):
            value = manifest.get(field_name)
            if not value or not str(value).strip():
                issues.append(Issue(
                    issue_id=f"MANIFEST-{field_name.upper()}-MISSING",
                    severity=sev,
                    category="MANIFEST",
                    code=code,
                    message=f"manifest.{field_name} 必须存在并指向有效文件。",
                    file="manifest.json",
                    suggested_fix=f"将 {field_name} 设置为插件内真实存在的文件。",
                    repairable=True,
                ))
                continue
            ref = str(value)
            target = root / ref
            if not target.exists():
                issues.append(Issue(
                    issue_id=f"MANIFEST-{field_name.upper()}-FILE-MISSING",
                    severity=sev,
                    category="MANIFEST",
                    code=code,
                    message=f"manifest.{field_name} 指向的文件不存在：{ref}",
                    file="manifest.json",
                    evidence=f"referenced file not found: {ref}",
                    suggested_fix=f"确认 {ref} 存在于插件目录内。",
                    repairable=True,
                ))

        # KJL-MANIFEST-006：入口路径必须位于插件目录内（不允许绝对路径 / .. 越界）
        for field_name in ("frame", "main"):
            ref = str(manifest.get(field_name, ""))
            if not ref:
                continue
            if ref.startswith("/") or ref.startswith("\\") or ".." in Path(ref).parts:
                issues.append(Issue(
                    issue_id=f"MANIFEST-{field_name.upper()}-PATH-ESCAPE",
                    severity="CRITICAL",
                    category="MANIFEST",
                    code="KJL-MANIFEST-006",
                    message=f"manifest.{field_name} 使用了绝对路径或越界路径：{ref}",
                    file="manifest.json",
                    evidence=f"path escapes plugin root: {ref}",
                    suggested_fix="使用相对于插件根目录的安全入口路径。",
                    repairable=True,
                ))

        # KJL-MANIFEST-007：打包工具下 main 必须指向 build 产物
        if self._has_bundler(root):
            main_ref = str(manifest.get("main", ""))
            build_dir = root / "build"
            if not main_ref.startswith("build/") and not (build_dir / Path(main_ref).name).exists():
                issues.append(Issue(
                    issue_id="MANIFEST-BUNDLER-ARTIFACT",
                    severity="MEDIUM",
                    category="MANIFEST",
                    code="KJL-MANIFEST-007",
                    message=(
                        f"检测到打包工具，但 manifest.main={main_ref} 未指向构建产物；"
                        "应对 build/ 目录校验。"
                    ),
                    file="manifest.json",
                    evidence="bundler config present but main not under build/",
                    suggested_fix="先 npm run build，确保 manifest.main 指向 build/ 下真实存在的文件。",
                    repairable=True,
                ))
        return manifest, issues

    # ----------------------------- 3) 平台规则 -----------------------------

    def _rule_check(self, root: Path, manifest: dict | None) -> list[Issue]:
        issues: list[Issue] = []
        # 收集代码文件文本与路径
        code_files: list[tuple[Path, str]] = []
        for pattern in ("*.js", "*.ts", "*.jsx", "*.tsx", "*.html"):
            for path in root.rglob(pattern):
                if "node_modules" in path.parts or "dist" in path.parts or "build" in path.parts:
                    continue
                try:
                    code_files.append((path, path.read_text(encoding="utf-8", errors="ignore")))
                except OSError:
                    continue

        for rule in self.rules.rules():
            detection = rule.detection or {}
            dtype = detection.get("type")
            # 文件内容类规则按 scope 限定到对应文件类型，避免跨文件误报
            scoped = self._files_for_scope(code_files, getattr(rule, "scope", "") or "")
            try:
                if dtype == "file_exists":
                    rel = detection.get("path", "")
                    if not (root / rel).exists():
                        issues.append(self._rule_issue(rule, file=rel,
                                                        msg=f"缺少必需文件：{rel}"))
                elif dtype == "manifest_field_and_file":
                    if manifest is None:
                        continue
                    field_name = detection.get("field")
                    value = manifest.get(field_name)
                    if not value or not (root / str(value)).exists():
                        issues.append(self._rule_issue(
                            rule, file="manifest.json",
                            msg=f"manifest.{field_name} 缺失或指向文件不存在：{value}"))
                elif dtype == "required_fields":
                    if manifest is None:
                        continue
                    missing = [f for f in detection.get("fields", [])
                               if not str(manifest.get(f, "")).strip()]
                    if missing:
                        issues.append(self._rule_issue(
                            rule, file="manifest.json", msg=f"manifest 缺少字段：{missing}"))
                elif dtype == "relative_path":
                    if manifest is None:
                        continue
                    for fn in ("frame", "main"):
                        ref = str(manifest.get(fn, ""))
                        if ref and (ref.startswith("/") or ".." in Path(ref).parts):
                            issues.append(self._rule_issue(
                                rule, file="manifest.json", msg=f"{fn} 路径越界：{ref}"))
                elif dtype == "static_pattern":
                    patterns = detection.get("patterns") or (
                        [detection["pattern"]] if detection.get("pattern") else [])
                    if detection.get("mode") == "require":
                        # require 语义：仅当文件中出现通信活动（trigger）
                        # 且找不到任一合法模式时才判违规（避免无通信插件误报）。
                        trigger = detection.get("trigger", "")
                        first_hit: Path | None = None
                        satisfied = False
                        for path, text in scoped:
                            if trigger and trigger not in text:
                                continue
                            if first_hit is None:
                                first_hit = path
                            if any(self._pattern_in_file(pat, text, path)
                                   for pat in patterns):
                                satisfied = True
                                break
                        if first_hit is not None and not satisfied:
                            issues.append(self._rule_issue(
                                rule, file=str(first_hit.relative_to(root)),
                                msg=f"存在通信行为但未使用合法方式："
                                    f"`{patterns[0] if patterns else ''}` 未找到"))
                    else:
                        for path, text in scoped:
                            for pat in patterns:
                                if self._pattern_in_file(pat, text, path):
                                    issues.append(self._rule_issue(
                                        rule, file=str(path.relative_to(root)),
                                        msg=f"违反规则：在 {path.name} 中发现 `{pat}`"))
                elif dtype == "static_host":
                    host = detection.get("host", "")
                    for path, text in scoped:
                        if host in text:
                            issues.append(self._rule_issue(
                                rule, file=str(path.relative_to(root)),
                                msg=f"直接请求受限主机：{host}"))
                elif dtype == "static_url":
                    for path, text in scoped:
                        for m in re.finditer(r"['\"](https?://[^\s'\"]+)['\"]", text):
                            url = m.group(1)
                            if url.startswith("http://") and not self._is_localhost(url):
                                issues.append(self._rule_issue(
                                    rule, file=str(path.relative_to(root)),
                                    line=text.count("\n", 0, m.start()) + 1,
                                    msg=f"外部服务使用明文 HTTP：{url}"))
                elif dtype == "heuristic_assignment":
                    # KJL-VM-004：VM 修改 API 返回对象（medium）
                    for path, text in code_files:
                        if self._vm_modifies_return(text):
                            issues.append(self._rule_issue(
                                rule, file=str(path.relative_to(root)),
                                msg="VM 可能直接修改 IDP 返回的只读对象"))
                elif dtype == "heuristic_promise_chain":
                    # KJL-VM-006：IDP Promise 链缺少 catch（medium）
                    for path, text in code_files:
                        if self._vm_promise_without_catch(text):
                            issues.append(self._rule_issue(
                                rule, file=str(path.relative_to(root)),
                                msg="IDP Promise 链缺少异常处理（catch / try-catch）"))
                elif dtype == "usage_validator":
                    # KJL-API-001：交由 API 校验阶段统一产出
                    continue
                elif dtype in ("package_json", "package_script"):
                    issues.extend(self._package_rules(rule, root, detection))
                elif dtype in ("cors_header", "cors_preflight", "http_probe"):
                    # 宿主运行相关：静态无法判定，显式标记
                    issues.append(self._rule_issue(
                        rule, file="(host runtime)",
                        msg="该规则需在宿主环境启动本地服务后验证（CORS / OPTIONS / HTTP 探活）。",
                        repairable=False,
                        runtime_required=True))
            except Exception as error:  # 单条规则失败不阻断整体
                issues.append(self._rule_issue(
                    rule, msg=f"规则检测异常：{error}", repairable=False))
        return issues

    def _rule_issue(self, rule, file: str = "", msg: str = "", line: int = 0,
                    repairable: bool = True, runtime_required: bool = False) -> Issue:
        severity = "WARNING" if runtime_required else self._norm_severity(rule.severity)
        return Issue(
            issue_id=f"RULE-{rule.id}",
            severity=severity,
            category="RULE" if rule.scope != "api" else "API",
            code=rule.id,
            message=msg or rule.title,
            file=file,
            line=line,
            evidence=rule.description,
            suggested_fix=rule.suggestion,
            repairable=repairable,
        )

    # ----------------------------- 4) API 用法 -----------------------------

    def _api_check(self, root: Path) -> list[Issue]:
        issues: list[Issue] = []
        for pattern in ("*.js", "*.ts", "*.jsx", "*.tsx"):
            for path in root.rglob(pattern):
                if "node_modules" in path.parts or "dist" in path.parts or "build" in path.parts:
                    continue
                try:
                    code = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                result = self._usage_validator.validate(code, sdk_version=self.sdk_version)
                rel = str(path.relative_to(root))
                for raw in result.get("issues", []):
                    issues.append(Issue(
                        issue_id=f"API-{raw.get('type', 'unknown').upper()}",
                        severity=self._norm_severity(raw.get("severity", "error")),
                        category="API",
                        code=self._api_code(raw.get("type", "unknown_api")),
                        message=raw.get("message", ""),
                        file=rel,
                        line=raw.get("line", 0),
                        evidence="symbol=" + str(raw.get("symbol", "")),
                        suggested_fix="; ".join(raw.get("suggestions", []) or []),
                        repairable=raw.get("severity", "error") != "error"
                        or raw.get("type") in {"unknown_api", "unknown_parameter"},
                    ))
        return issues

    @staticmethod
    def _api_code(raw_type: str) -> str:
        return {
            "unknown_api": "API_UNKNOWN_API",
            "unknown_parameter": "API_UNKNOWN_PARAMETER",
            "missing_required_parameter": "API_MISSING_REQUIRED_PARAMETER",
            "missing_required_field": "API_MISSING_REQUIRED_FIELD",
            "unverified_member": "API_UNVERIFIED_MEMBER",
            "sdk_version_mismatch": "API_SDK_VERSION_MISMATCH",
        }.get(raw_type, "API_" + raw_type.upper())

    # ----------------------------- 5) 静态 / 构建 -----------------------------

    def _build_check(self, root: Path) -> list[Issue]:
        issues: list[Issue] = []
        if not self.tsc_cmd:
            return issues
        tsconfig = root / "tsconfig.json"
        if not tsconfig.exists():
            return issues
        try:
            proc = subprocess.run(
                self.tsc_cmd + ["--noEmit", "-p", str(tsconfig)],
                cwd=str(root), capture_output=True, text=True, timeout=120,
            )
        except (subprocess.SubprocessError, OSError) as error:
            issues.append(Issue(
                issue_id="BUILD-RUN-ERROR",
                severity="WARNING",
                category="BUILD",
                code="BUILD_RUN_ERROR",
                message=f"构建校验未执行：{error}",
                file="tsconfig.json",
                repairable=False,
            ))
            return issues
        if proc.returncode != 0:
            snippet = "\n".join(proc.stderr.strip().splitlines()[:8])
            # 当 build 被显式配置（tsc_cmd 不为空），构建失败属于「交付阻断」级错误：
            # severity=HIGH 且 valid=False。未配置 build 时本分支不会到达（上面已提前返回）。
            issues.append(Issue(
                issue_id="BUILD-FAILED",
                severity="HIGH",
                category="BUILD",
                code="BUILD_FAILED",
                message="TypeScript 类型检查未通过。",
                file="tsconfig.json",
                evidence=snippet,
                suggested_fix="根据类型错误修正签名、参数或返回值。",
                repairable=True,
            ))
        return issues

    # ----------------------------- 宿主运行校验 -----------------------------

    def _runtime_check(self, root: Path, manifest: dict | None) -> str:
        if manifest is None:
            return HOST_VALIDATION_REQUIRED
        # 只要有 CORS / HTTP 探活类平台规则存在，即需要宿主验证
        for rule in self.rules.rules():
            dtype = (rule.detection or {}).get("type")
            if dtype in ("cors_header", "cors_preflight", "http_probe"):
                return HOST_VALIDATION_REQUIRED
        return LOCAL_VALIDATION_PASS

    # ----------------------------- 辅助 -----------------------------

    @staticmethod
    def _files_for_scope(
        code_files: list[tuple[Path, str]], scope: str
    ) -> list[tuple[Path, str]]:
        """按规则 scope 限定内容类检测作用的文件类型。"""
        if scope == "ui":
            return [(p, t) for p, t in code_files if p.suffix.lower() in {".html", ".htm", ".jsx", ".tsx"}]
        if scope == "vm":
            return [(p, t) for p, t in code_files if p.suffix.lower() in {".js", ".ts", ".jsx", ".tsx"}]
        return list(code_files)

    @staticmethod
    def _norm_severity(value: str) -> str:
        """把 UsageValidator / Rule 的 severity 归一化到统一枚举。"""
        mapping = {
            "critical": "CRITICAL", "error": "HIGH", "high": "HIGH",
            "medium": "MEDIUM", "warning": "WARNING", "warn": "WARNING",
        }
        return mapping.get(str(value).lower(), str(value).upper())

    @staticmethod
    def _has_bundler(root: Path) -> bool:
        for name in ("webpack.config.js", "vite.config.ts", "vite.config.js", "rollup.config.js"):
            if (root / name).exists():
                return True
        return False

    @staticmethod
    def _pattern_in_file(pattern: str, text: str, path: Path) -> bool:
        # 简单通配：把 `IDP.*` 这种转成正则；其余做子串匹配
        if "*" in pattern:
            regex = re.compile("^" + re.escape(pattern).replace(r"\*", ".*") + "$")
            for line in text.splitlines():
                if regex.search(line.strip()):
                    return True
            return False
        return pattern in text

    @staticmethod
    def _is_localhost(url: str) -> bool:
        return "localhost" in url or "127.0.0.1" in url or "0.0.0.0" in url

    @staticmethod
    def _vm_modifies_return(text: str) -> bool:
        # 粗略：IDP 调用后链式赋值，例如 `IDP.x().y =` 或 `result.prop =`
        return bool(re.search(r"IDP\.[A-Za-z0-9_.]+\(\)\.[A-Za-z0-9_]+\s*=", text))

    @staticmethod
    def _vm_promise_without_catch(text: str) -> bool:
        # 规则语义限定为“明显的 IDP Promise 链”缺异常处理：
        # 仅当存在 IDP.x().then( 且全文无 catch 时上报；
        # 单纯 `await IDP.xAsync()`（无 then 链）不做静态判定，遵循“宁可少报”原则。
        return bool(re.search(r"IDP\.[A-Za-z0-9_.]+\(\)\s*\.then\s*\(", text)) and "catch" not in text

    def _package_rules(self, rule, root: Path, detection: dict) -> list[Issue]:
        issues: list[Issue] = []
        pkg_path = root / "package.json"
        if not pkg_path.exists():
            issues.append(self._rule_issue(rule, file="package.json", msg="缺少 package.json"))
            return issues
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            issues.append(self._rule_issue(rule, file="package.json", msg="package.json 不是合法 JSON"))
            return issues
        if detection.get("type") == "package_script":
            field = detection.get("field", "start")
            scripts = pkg.get("scripts", {}) or {}
            if field not in scripts:
                issues.append(self._rule_issue(
                    rule, file="package.json",
                    msg=f"package.json scripts 缺少 {field} 启动入口"))
        return issues
