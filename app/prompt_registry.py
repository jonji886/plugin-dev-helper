"""Git-based prompt registry with explicit versions.

Prompts are deliberately kept as repository files.  This makes every prompt
change reviewable and lets an evaluation run refer to an immutable version.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

class PromptRegistry:
    def __init__(self, root: Path | str = "prompts"):
        path = Path(root)
        self.root = path if path.is_absolute() else PROJECT_ROOT / path
        self.manifest_path = self.root / "manifest.json"

    def _manifest(self) -> dict:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def get(self, name: str, version: str = "v1", fallback: str = "") -> str:
        path = self.root / name / f"{version}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        # Keep the legacy single-file prompt usable during migration.
        if name == "developer_qa":
            legacy = self.root / "system.md"
            if legacy.exists() and version == "v1":
                return legacy.read_text(encoding="utf-8")
        if fallback:
            return fallback
        raise FileNotFoundError(f"未找到 Prompt: {name}/{version}")

    def metadata(self, name: str, version: str) -> dict[str, str]:
        entry = self._manifest().get(name, {}).get(version, {})
        if not isinstance(entry, dict):
            entry = {}
        return {
            "prompt_name": name,
            "prompt_version": version,
            "status": str(entry.get("status", "unknown")),
            "created_at": str(entry.get("created_at", "")),
            "description": str(entry.get("description", "")),
        }

    def list_versions(self, name: str) -> list[dict[str, str]]:
        """Return reviewable metadata for all versions of one prompt."""
        versions = self._manifest().get(name, {})
        if not isinstance(versions, dict):
            return []
        return [self.metadata(name, version) for version in sorted(versions)]
