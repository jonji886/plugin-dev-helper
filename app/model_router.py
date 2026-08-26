"""Application-level model routing.

The router only chooses among configured profiles; it is intentionally not a
gateway.  With one configured model every task falls back to that model.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str
    reason: str
    profile: str
    role: str = "main"


def infer_task_type(text: str) -> str:
    """Infer routing task from the user's text, avoiding LLM classification drift."""
    content = str(text)
    if any(marker in content for marker in ("代码", "示例", "TypeScript", "JavaScript", "怎么调用", "如何调用")):
        return "code"
    if any(marker in content for marker in (
        "分析", "排查", "可能原因", "分步骤", "推理", "权衡", "对比", "诊断", "为什么",
    )):
        return "reason"
    if any(marker in content for marker in ("参数", "接口", "API", "函数", "枚举", "字段", "返回值", "属性")):
        return "api"
    if re.search(r"\bIDP(?:\.[A-Za-z_][\w]*)+", content):
        return "api"
    return "general"


class ModelRouter:
    """Choose a model profile from task characteristics and environment config.

    When any of ``ROUTER/MAIN/REASON/VISION`` is configured, the four-role
    strategy takes precedence. Otherwise the legacy SiliconFlow convention
    maps ``MODEL_GLM``, ``MODEL_QWEN`` and ``MODEL_DEEPSEEK`` to the three
    compatible profiles respectively.
    """

    _INFERRED_MODEL_VARS = {
        "default": "MODEL_GLM",
        "fast": "MODEL_QWEN",
        "strong": "MODEL_DEEPSEEK",
    }
    _ROLE_NAMES = ("router", "main", "reason", "vision")

    def __init__(self, environ: dict[str, str] | None = None):
        self.environ = os.environ if environ is None else environ

    def _profile(self, profile: str) -> tuple[str, str]:
        profile_name = profile.lower()
        prefix = profile_name.upper()
        provider = self.environ.get(f"{prefix}_LLM_PROVIDER", "").strip()
        model = self.environ.get(f"{prefix}_LLM_MODEL", "").strip()
        if not model:
            inferred_model_var = self._INFERRED_MODEL_VARS.get(profile_name, "")
            model = self.environ.get(inferred_model_var, "").strip()
            if model and not provider:
                provider = "siliconflow"
        return provider, model

    def _role(self, role: str) -> tuple[str, str]:
        """Resolve the four-role convention while allowing explicit aliases."""
        role_name = role.lower()
        prefix = role_name.upper()
        provider = (
            self.environ.get(f"{prefix}_PROVIDER", "").strip()
            or self.environ.get(f"{prefix}_LLM_PROVIDER", "").strip()
        )
        model = (
            self.environ.get(prefix, "").strip()
            or self.environ.get(f"{prefix}_MODEL", "").strip()
            or self.environ.get(f"{prefix}_LLM_MODEL", "").strip()
        )
        if model and not provider:
            provider = "siliconflow"
        return provider, model

    def uses_role_config(self) -> bool:
        """Whether at least one ROUTER/MAIN/REASON/VISION variable is set."""
        return any(self._role(role)[1] for role in self._ROLE_NAMES)

    def has_role_config(self, role: str) -> bool:
        return bool(self._role(role)[1])

    def _role_route(self, role: str) -> ModelRoute:
        provider, model = self._role(role)
        if model:
            return ModelRoute(provider=provider, model=model,
                              reason=f"configured {role} role", profile=role, role=role)

        # MAIN is the safe fallback for an optional role. Keep the requested
        # role in metadata so /api/ready and request logs show the degraded path.
        main_provider, main_model = self._role("main")
        if main_model:
            return ModelRoute(
                provider=main_provider,
                model=main_model,
                reason=f"{role} role unavailable, fallback to main role",
                profile=role,
                role=role,
            )
        provider, model = self._profile("DEFAULT")
        if not model:
            provider = provider or "deepseek"
            model = model or "deepseek-v4-flash"
        return ModelRoute(
            provider=provider,
            model=model,
            reason=f"{role} and main roles unavailable, fallback to default model",
            profile=role,
            role=role,
        )

    def _role_routes(self) -> dict[str, ModelRoute]:
        return {role: self._role_route(role) for role in self._ROLE_NAMES}

    def route(
        self,
        task_type: str,
        context_length: int = 0,
        complexity: str = "normal",
        *,
        has_image: bool = False,
        confidence: float = 1.0,
        need_reason: bool = False,
    ) -> ModelRoute:
        if self.uses_role_config():
            return self._route_roles(
                task_type=task_type,
                complexity=complexity,
                has_image=has_image,
                confidence=confidence,
                need_reason=need_reason,
            )

        default_provider, default_model = self._profile("DEFAULT")
        if not default_model:
            default_provider = "deepseek"
            default_model = "deepseek-v4-flash"

        profile = "default"
        reason = "default model for general developer QA"
        if task_type in {"api", "param"} and context_length < 3000:
            profile = "fast"
            reason = "short context + deterministic API lookup + low complexity"
        elif task_type == "code" or complexity == "high":
            profile = "strong"
            reason = "code generation or explicit high complexity requires stronger reasoning"

        provider, model = self._profile(profile.upper())
        if not model:
            profile = "default"
            provider, model = default_provider, default_model
            reason = f"{reason}; profile unavailable, fallback to default model"
        return ModelRoute(provider=provider or default_provider, model=model or default_model,
                          reason=reason, profile=profile)

    def _route_roles(
        self,
        task_type: str,
        complexity: str,
        has_image: bool,
        confidence: float,
        need_reason: bool,
    ) -> ModelRoute:
        task = task_type.lower()
        if task in {"intent_classifier", "router"}:
            return self._role_route("router")
        if has_image:
            return self._role_route("vision")
        if task in {"reason", "code"} or complexity.lower() == "high" or need_reason or confidence < 0.75:
            route = self._role_route("reason")
            if confidence < 0.75 and "fallback" not in route.reason:
                return ModelRoute(route.provider, route.model,
                                  f"low intent confidence; {route.reason}", route.profile, route.role)
            return route
        return self._role_route("main")

    def profile_routes(self) -> dict[str, ModelRoute]:
        """Return the effective routes without exposing credentials."""
        if self.uses_role_config():
            return self._role_routes()
        return {
            "default": self.route("general"),
            "fast": self.route("api", context_length=100),
            "strong": self.route("code", context_length=9000),
        }

    def role_routes(self) -> dict[str, ModelRoute]:
        """Return effective Router/Main/Reason/Vision routes."""
        return self._role_routes()

    def role_status(self) -> dict[str, dict[str, object]]:
        """Return non-sensitive configured/effective status for readiness checks."""
        routes = self.role_routes()
        return {
            role: {
                "configured": self.has_role_config(role),
                "provider": route.provider,
                "model": route.model,
                "available": self.has_credentials(route.provider),
                "fallback": not self.has_role_config(role),
            }
            for role, route in routes.items()
        }

    def has_credentials(self, provider: str) -> bool:
        """Whether the provider has an API key configured in the environment."""
        normalized = provider.lower().replace("-", "").replace("_", "")
        key_names = {
            "siliconflow": "SILICONFLOW_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "openai": "OPENAI_API_KEY",
        }
        key_name = key_names.get(normalized, f"{provider.upper()}_API_KEY")
        return bool(self.environ.get(key_name, "").strip())

    def is_ready(self) -> bool:
        """Return whether at least one effective route can call its provider."""
        routes = self.role_routes() if self.uses_role_config() else self.profile_routes()
        return any(self.has_credentials(route.provider) for route in routes.values())
