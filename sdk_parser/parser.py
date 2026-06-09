"""
基于 tree-sitter 的 TypeScript .d.ts AST 解析器

由于 .d.ts 文件过大（~19,000行），采用分块解析策略：
按 `export {}; declare global { ... }` 分割为独立块。
"""

import re
import os
from tree_sitter import Language, Parser, Node
import tree_sitter_typescript as tsts

from sdk_parser.models import (
    Symbol, Parameter, Property, Method, TypeParameter, JSDocComment
)

_parser_cache = None


def get_parser() -> Parser:
    global _parser_cache
    if _parser_cache is None:
        ts_language = Language(tsts.language_typescript())
        _parser_cache = Parser(ts_language)
    return _parser_cache


def parse_jsdoc(comment_text: str) -> JSDocComment:
    jsdoc = JSDocComment()
    if not comment_text:
        return jsdoc
    text = re.sub(r'^/\*\*\s*', '', comment_text)
    text = re.sub(r'\s*\*/\s*$', '', text)
    text = re.sub(r'^\s*\*\s?', '', text, flags=re.MULTILINE)
    text = text.strip()
    parts = re.split(r'@(\w+)', text, maxsplit=1)
    jsdoc.text = parts[0].strip()
    if len(parts) > 1:
        tag_pattern = re.finditer(r'@(\w+)(?:\s+(.*?))?(?=\s*@|\s*$)', text, re.DOTALL)
        tags = {}
        for match in tag_pattern:
            name = match.group(1)
            value = match.group(2).strip() if match.group(2) else ""
            tags[name] = value
        jsdoc.tags = tags
        jsdoc.deprecated = 'deprecated' in tags
        jsdoc.vm_type = tags.get('vm-type') or tags.get('vmType') or tags.get('vmtype')
    return jsdoc


def extract_type_refs_from_text(text: str) -> list[str]:
    if not text:
        return []
    identifiers = re.findall(r'\b([A-Z][A-Za-z0-9_]+(?:\.[A-Z][A-Za-z0-9_]+)*)', text)
    builtins = {'string', 'number', 'boolean', 'void', 'null', 'undefined', 'never',
                'any', 'unknown', 'object', 'Promise', 'Array', 'Readonly', 'Partial',
                'Required', 'Pick', 'Omit', 'Record', 'Exclude', 'Extract',
                'NonNullable', 'ReturnType', 'InstanceType', 'ThisType',
                'Parameters', 'ConstructorParameters', 'keyof', 'typeof'}
    return [ident for ident in identifiers if ident.split('.')[0] not in builtins]


class SDKParser:
    """SDK .d.ts 文件解析器（分块解析）"""

    def __init__(self, source_file: str):
        self.source_file = source_file
        self.source_name = os.path.basename(source_file)
        self.parser = get_parser()
        self.symbols: list[Symbol] = []

    # ---- Node text helpers ----

    def _node_text(self, node):
        if node is None:
            return ""
        return self._code[node.start_byte:node.end_byte].decode('utf-8')

    def _comment_before(self, node):
        prev = node.prev_sibling
        while prev and prev.type in ('\n', ';', ','):
            prev = prev.prev_sibling
        if prev and prev.type == 'comment':
            return self._node_text(prev)
        return ""

    def _type_name(self, type_node):
        """从类型节点提取类型名称（去掉冒号等前缀）"""
        if type_node is None:
            return ""
        if type_node.type == 'type_annotation':
            for child in type_node.children:
                if child.type != ':':
                    return self._node_text(child).strip()
        return self._node_text(type_node).strip()

    # ---- 主解析方法 ----

    def parse(self) -> list[Symbol]:
        self.symbols = []
        chunks = self._split_chunks()
        for line_offset, code_bytes in chunks:
            self._parse_chunk(code_bytes, line_offset)
        return self.symbols

    def _split_chunks(self):
        """按 export {}; declare global { 模式分块"""
        with open(self.source_file, 'rb') as f:
            full = f.read()
        lines = full.split(b'\n')
        chunks = []
        start = 0
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            # Split at each export {}; declare global boundary
            if stripped == b'export {};' and i + 1 < len(lines) and lines[i + 1].strip().startswith(b'declare global {'):
                # End previous chunk
                prev = b'\n'.join(lines[start:i])
                if prev.strip():
                    chunks.append((start, prev))
                start = i
                i += 1
                continue
            i += 1
        # Last chunk
        last = b'\n'.join(lines[start:])
        if last.strip():
            chunks.append((start, last))
        return chunks

    def _parse_chunk(self, code_bytes: bytes, line_offset: int):
        self._code = code_bytes
        try:
            tree = self.parser.parse(code_bytes)
        except Exception as e:
            print(f"  [warn] Chunk at line {line_offset} failed: {e}")
            return
        self._parse_node_children(tree.root_node, namespace_path=[])
        # Fix line numbers
        for sym in self.symbols:
            sym.start_line += line_offset
            sym.end_line += line_offset

    def _parse_node_children(self, node, namespace_path):
        for child in node.children:
            self._handle_declaration(child, namespace_path)

    def _handle_declaration(self, node, namespace_path):
        t = node.type

        if t == 'ambient_declaration':
            has_global = False
            for child in node.children:
                if child.type == 'global':
                    has_global = True
                elif child.type == 'statement_block' and has_global:
                    # declare global { ... } - statement_block is sibling of global
                    self._handle_namespace_block(child, namespace_path)
                elif child.type in ('interface_declaration', 'type_alias_declaration',
                                    'enum_declaration', 'class_declaration',
                                    'function_declaration', 'function_signature'):
                    self._extract_top_level(child, namespace_path)

        elif t == 'expression_statement':
            for child in node.children:
                if child.type == 'internal_module':
                    self._parse_namespace_body(child, namespace_path)
                elif child.type in ('function_declaration', 'function_signature'):
                    self._extract_function(child, namespace_path)
                elif child.type == 'lexical_declaration':
                    self._extract_lexical(child, namespace_path)
                elif child.type in ('interface_declaration', 'type_alias_declaration',
                                    'enum_declaration', 'class_declaration'):
                    self._extract_top_level(child, namespace_path)

        elif t == 'lexical_declaration':
            self._extract_lexical(node, namespace_path)
        elif t in ('interface_declaration', 'type_alias_declaration',
                    'enum_declaration', 'class_declaration'):
            self._extract_top_level(node, namespace_path)
        elif t in ('function_declaration', 'function_signature'):
            self._extract_function(node, namespace_path)
        elif t == 'export_statement':
            for child in node.children:
                self._handle_declaration(child, namespace_path)
        elif t == 'ERROR':
            self._parse_node_children(node, namespace_path)

    def _handle_namespace_block(self, node, namespace_path):
        for child in node.children:
            self._handle_declaration(child, namespace_path)

    def _parse_namespace_body(self, node, namespace_path):
        ns_name = ""
        body_node = None
        for child in node.children:
            if child.type == 'identifier':
                ns_name = self._node_text(child)
            elif child.type == 'statement_block':
                body_node = child
        if not ns_name:
            return
        new_path = namespace_path + [ns_name]
        if body_node:
            self._handle_namespace_block(body_node, new_path)

    # ---- 声明提取 ----

    def _extract_top_level(self, node, namespace_path):
        name = ""
        for child in node.children:
            if child.type in ('type_identifier', 'identifier', 'name'):
                name = self._node_text(child)
                break
        if not name:
            return

        comment = self._comment_before(node)
        full_path = namespace_path + [name] if namespace_path else [name]
        symbol_id = ".".join(full_path)

        type_map = {
            'interface_declaration': 'interface',
            'type_alias_declaration': 'type_alias',
            'enum_declaration': 'enum',
            'class_declaration': 'class',
            'function_declaration': 'function',
            'function_signature': 'function',
        }
        sym_type = type_map.get(node.type, 'unknown')

        symbol = Symbol(
            id=symbol_id, name=name, symbol_type=sym_type,
            namespace_path=namespace_path, source=self.source_name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            description=comment,
        )
        if comment:
            symbol.jsdoc = parse_jsdoc(comment)

        if node.type == 'interface_declaration':
            symbol.type_parameters = self._extract_type_params(node)
            for child in node.children:
                if child.type == 'interface_body':
                    props, methods = self._extract_props(child)
                    symbol.properties = props
                    symbol.methods = methods
                    for p in props:
                        symbol.references.extend(extract_type_refs_from_text(p.type_name))

        elif node.type == 'enum_declaration':
            for child in node.children:
                if child.type == 'enum_body':
                    symbol.enum_members = self._extract_enum_members(child)

        elif node.type == 'class_declaration':
            symbol.type_parameters = self._extract_type_params(node)
            for child in node.children:
                if child.type == 'class_body':
                    props, methods = self._extract_props(child)
                    symbol.properties = props
                    symbol.methods = methods

        elif node.type == 'type_alias_declaration':
            symbol.type_parameters = self._extract_type_params(node)
            for child in node.children:
                if child.type not in ('type', 'type_identifier', ';', '=', 'type_parameters'):
                    symbol.type_name = self._type_name(child)
                    symbol.references = extract_type_refs_from_text(symbol.type_name)

        self.symbols.append(symbol)

    def _extract_function(self, node, namespace_path):
        name = ""
        params_node = None
        for child in node.children:
            if child.type == 'identifier':
                name = self._node_text(child)
            elif child.type == 'formal_parameters':
                params_node = child
        if not name:
            return

        comment = self._comment_before(node)
        full_path = namespace_path + [name]
        symbol_id = ".".join(full_path)
        params = self._extract_params(params_node) if params_node else []
        return_type = self._get_return_type(node)
        type_params = self._extract_type_params(node)

        refs = []
        for p in params:
            refs.extend(extract_type_refs_from_text(p.type_name))
        refs.extend(extract_type_refs_from_text(return_type))

        symbol = Symbol(
            id=symbol_id, name=name, symbol_type='function',
            namespace_path=namespace_path, source=self.source_name,
            description=comment, parameters=params,
            type_parameters=type_params,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            references=list(set(refs)),
        )
        if comment:
            symbol.jsdoc = parse_jsdoc(comment)
        self.symbols.append(symbol)

    def _extract_lexical(self, node, namespace_path):
        for child in node.children:
            if child.type == 'variable_declarator':
                name_node = None
                type_node = None
                for v_child in child.children:
                    if v_child.type == 'identifier':
                        name_node = v_child
                    elif v_child.type == 'type_annotation':
                        type_node = v_child
                if name_node is None:
                    continue
                name = self._node_text(name_node)
                comment = self._comment_before(child)
                full_path = namespace_path + [name]
                symbol_id = ".".join(full_path)
                symbol = Symbol(
                    id=symbol_id, name=name, symbol_type='const',
                    namespace_path=namespace_path, source=self.source_name,
                    description=comment,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                )
                if type_node:
                    symbol.type_name = self._type_name(type_node)
                    symbol.references = extract_type_refs_from_text(symbol.type_name)
                self.symbols.append(symbol)

    # ---- 子结构提取 ----

    def _extract_params(self, formal_params):
        params = []
        if formal_params is None:
            return params
        for child in formal_params.children:
            if child.type in ('required_parameter', 'optional_parameter'):
                param = Parameter(name="", type_name="", optional=(child.type == 'optional_parameter'))
                for p_child in child.children:
                    if p_child.type == 'identifier':
                        param.name = self._node_text(p_child)
                    elif p_child.type == 'type_annotation':
                        param.type_name = self._type_name(p_child)
                comment = self._comment_before(child)
                if comment:
                    param.description = comment
                params.append(param)
        return params

    def _extract_type_params(self, node):
        type_params = []
        for child in node.children:
            if child.type == 'type_parameters':
                for tp_child in child.children:
                    if tp_child.type == 'type_parameter':
                        tp = TypeParameter(name="")
                        for tp_node in tp_child.children:
                            if tp_node.type == 'type_identifier':
                                tp.name = self._node_text(tp_node)
                            elif tp_node.type == 'type_constraint':
                                tp.constraint = self._node_text(tp_node).replace('extends ', '').strip()
                        type_params.append(tp)
        return type_params

    def _get_return_type(self, method_sig):
        for child in method_sig.children:
            if child.type == 'type_annotation':
                return self._type_name(child)
        return "void"

    def _extract_props(self, interface_body):
        properties = []
        methods = []
        for child in interface_body.children:
            if child.type == 'property_signature':
                prop = Property(name="", type_name="")
                for p_child in child.children:
                    if p_child.type == 'property_identifier':
                        prop.name = self._node_text(p_child)
                    elif p_child.type == 'string':
                        prop.name = self._node_text(p_child).strip('"')
                    elif p_child.type == 'type_annotation':
                        prop.type_name = self._type_name(p_child)
                    elif p_child.type == '?':
                        prop.optional = True
                comment = self._comment_before(child)
                if comment:
                    prop.description = comment
                properties.append(prop)
            elif child.type == 'method_signature':
                method = Method(name="")
                for m_child in child.children:
                    if m_child.type == 'property_identifier':
                        method.name = self._node_text(m_child)
                    elif m_child.type == 'formal_parameters':
                        method.parameters = self._extract_params(m_child)
                    elif m_child.type == 'type_annotation':
                        method.return_type = self._type_name(m_child)
                comment = self._comment_before(child)
                if comment:
                    method.description = comment
                methods.append(method)
        return properties, methods

    def _extract_enum_members(self, enum_body):
        members = []
        for child in enum_body.children:
            if child.type in ('enum_assignment', 'enum_member'):
                member = {"name": "", "value": "", "description": ""}
                has_equals = False
                for m_child in child.children:
                    if m_child.type in ('property_identifier', 'identifier'):
                        member['name'] = self._node_text(m_child)
                    elif m_child.type == '=':
                        has_equals = True
                    elif has_equals:
                        member['value'] = self._node_text(m_child)
                comment = self._comment_before(child)
                if comment:
                    member['description'] = comment
                members.append(member)
        return members
