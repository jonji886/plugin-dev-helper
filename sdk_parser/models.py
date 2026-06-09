"""
SDK 符号数据模型
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class JSDocComment:
    """JSDoc 注释"""
    text: str = ""
    tags: dict = field(default_factory=dict)
    deprecated: bool = False
    vm_type: Optional[str] = None


@dataclass
class Parameter:
    """函数/方法参数"""
    name: str
    type_name: str
    optional: bool = False
    description: str = ""


@dataclass
class Property:
    """接口/类的属性"""
    name: str
    type_name: str
    optional: bool = False
    description: str = ""
    readonly: bool = False


@dataclass
class Method:
    """接口/类的方法"""
    name: str
    parameters: list[Parameter] = field(default_factory=list)
    return_type: str = "void"
    description: str = ""
    deprecated: bool = False


@dataclass
class TypeParameter:
    """泛型类型参数"""
    name: str
    constraint: Optional[str] = None


@dataclass
class Symbol:
    """解析后的符号"""
    id: str  # 全局唯一 ID, e.g. IDP.Miniapp.exit
    name: str  # 符号短名称
    symbol_type: str  # function, interface, class, type_alias, enum, const, variable
    namespace_path: list[str]  # 命名空间路径
    source: str  # 源文件名
    sdk_version: str = "1.83.0"

    # 文档
    description: str = ""
    jsdoc: Optional[JSDocComment] = None

    # 结构信息
    type_name: str = ""  # 类型名（for type_alias）
    parameters: list[Parameter] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)
    methods: list[Method] = field(default_factory=list)
    type_parameters: list[TypeParameter] = field(default_factory=list)
    enum_members: list[dict] = field(default_factory=list)  # [{name, value, description}]

    # 引用关系
    references: list[str] = field(default_factory=list)  # 引用的其他符号ID

    # 别名
    aliases: list[str] = field(default_factory=list)

    # 源文件位置
    start_line: int = 0
    end_line: int = 0

    def __post_init__(self):
        """自动生成别名"""
        if not self.aliases:
            self.aliases = [self.name]
            if self.namespace_path:
                partial = []
                for part in self.namespace_path:
                    partial.append(part)
                    self.aliases.append(f"{'.'.join(partial)}.{self.name}")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.symbol_type,
            "namespace": ".".join(self.namespace_path),
            "source": self.source,
            "sdkVersion": self.sdk_version,
            "description": self.description,
            "parameters": [{"name": p.name, "type": p.type_name, "optional": p.optional, "description": p.description}
                           for p in self.parameters],
            "properties": [{"name": p.name, "type": p.type_name, "optional": p.optional, "description": p.description,
                            "readonly": p.readonly} for p in self.properties],
            "methods": [{"name": m.name, "parameters": [{"name": p.name, "type": p.type_name} for p in m.parameters],
                         "returnType": m.return_type, "description": m.description} for m in self.methods],
            "typeParameters": [{"name": tp.name, "constraint": tp.constraint} for tp in self.type_parameters],
            "enumMembers": self.enum_members,
            "references": self.references,
            "aliases": self.aliases,
            "startLine": self.start_line,
            "endLine": self.end_line,
        }

    def to_markdown(self) -> str:
        """生成 Markdown 描述"""
        lines = []
        lines.append(f"# {self.id}")
        lines.append("")
        if self.description:
            lines.append(self.description)
            lines.append("")

        lines.append(f"- **类型**: {self.symbol_type}")
        lines.append(f"- **来源**: {self.source} (v{self.sdk_version})")

        if self.namespace_path:
            lines.append(f"- **命名空间**: {' → '.join(self.namespace_path)}")

        if self.parameters:
            lines.append("")
            lines.append("## 参数")
            lines.append("")
            lines.append("| 名称 | 类型 | 必填 | 说明 |")
            lines.append("|------|------|------|------|")
            for p in self.parameters:
                required = "否" if p.optional else "是"
                lines.append(f"| {p.name} | `{p.type_name}` | {required} | {p.description} |")

        if self.properties:
            lines.append("")
            lines.append("## 属性")
            lines.append("")
            lines.append("| 名称 | 类型 | 必填 | 只读 | 说明 |")
            lines.append("|------|------|------|------|------|")
            for p in self.properties:
                required = "否" if p.optional else "是"
                readonly = "是" if p.readonly else "否"
                lines.append(f"| {p.name} | `{p.type_name}` | {required} | {readonly} | {p.description} |")

        if self.methods:
            lines.append("")
            lines.append("## 方法")
            lines.append("")
            for m in self.methods:
                params_str = ", ".join(f"{p.name}: {p.type_name}" for p in m.parameters)
                lines.append(f"- `{m.name}({params_str}): {m.return_type}`")
                if m.description:
                    lines.append(f"  - {m.description}")

        if self.enum_members:
            lines.append("")
            lines.append("## 枚举值")
            lines.append("")
            lines.append("| 名称 | 值 | 说明 |")
            lines.append("|------|-----|------|")
            for m in self.enum_members:
                desc = m.get("description", "")
                val = m.get("value", "")
                lines.append(f"| {m['name']} | {val} | {desc} |")

        if self.type_parameters:
            lines.append("")
            lines.append("## 泛型参数")
            for tp in self.type_parameters:
                constraint = f" extends {tp.constraint}" if tp.constraint else ""
                lines.append(f"- `{tp.name}{constraint}`")

        if self.references:
            lines.append("")
            lines.append("## 引用")
            for ref in self.references:
                lines.append(f"- `{ref}`")

        return "\n".join(lines)
