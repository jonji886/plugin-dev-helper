"""MCP 运行时容器：集中创建并持有各个 Service。"""

from __future__ import annotations

from dataclasses import dataclass

from mcp_server.config import MCPSettings, get_mcp_settings
from mcp_server.services.examples import ExampleService
from mcp_server.services.graph import GraphService
from mcp_server.services.knowledge import KnowledgeService
from mcp_server.services.retrieval import RetrievalService
from mcp_server.services.validator import UsageValidator
from mcp_server.telemetry import McpTelemetry


@dataclass
class Container:
    settings: MCPSettings
    knowledge: KnowledgeService
    retrieval: RetrievalService
    graph: GraphService
    examples: ExampleService
    validator: UsageValidator
    telemetry: McpTelemetry

    def readiness(self) -> dict:
        return {
            "knowledge_entries": self.knowledge.size,
            "knowledge_available": self.knowledge.is_available(),
            "vector_available": self.retrieval.is_available(),
            "graph_available": self.graph.is_available(),
            "sdk_versions": self.knowledge.sdk_versions(),
            "graph": self.graph.stats(),
        }


def build_container(settings: MCPSettings | None = None) -> Container:
    settings = settings or get_mcp_settings()
    knowledge = KnowledgeService(settings.knowledge_path)
    return Container(
        settings=settings,
        knowledge=knowledge,
        retrieval=RetrievalService(settings.chroma_path, settings.knowledge_path),
        graph=GraphService(
            settings.graph_path,
            max_depth=settings.max_graph_depth,
            max_nodes=settings.max_graph_nodes,
        ),
        examples=ExampleService(
            settings.knowledge_path,
            settings.rag_docs_path,
            max_examples=settings.max_examples,
            max_example_chars=settings.max_example_chars,
        ),
        validator=UsageValidator(knowledge),
        telemetry=McpTelemetry(settings.database_path, enabled=settings.telemetry_enabled),
    )
