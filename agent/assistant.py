"""
LangGraph Agent - 插件开发 AI 助手

Agent 节点:
1. Intent Router: 识别问题类型
2. Query Rewrite: 上下文补全
3. Retrieve: 知识库检索
4. Graph Expansion: 依赖链展开
5. Answer Generator: 生成答案
6. Memory: 会话管理
"""

from __future__ import annotations

import json
import os
import sqlite3
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict, Optional, Annotated, Sequence
from datetime import datetime, timezone
from time import perf_counter

import operator

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage

from vector_store import VectorStore
from app.llm_usage import (
    current_request_usage,
    reset_request_usage,
    start_request_usage,
)
from app.llm_adapter import (
    FailoverAdapter,
    create_instrumented_adapter,
    provider_runtime_config,
)
from app.model_router import ModelRoute, ModelRouter, infer_task_type
from app.observability import Observability, get_observability
from app.prompt_registry import PromptRegistry


def _answer_context_limit(default: int = 6000) -> int:
    """Bound the evidence sent to the answer model."""
    try:
        return max(2000, int(os.getenv("ANSWER_CONTEXT_MAX_CHARS", str(default))))
    except (TypeError, ValueError):
        return default


@contextmanager
def _node_span(state: dict, name: str):
    observability = state.get("observability") or get_observability()
    with observability.span(state.get("trace") or object(), name, {
        "query": state.get("current_query", ""),
        "session_id": state.get("session_id", ""),
        "trace_id": state.get("trace_id", ""),
    }) as span:
        yield span


def _retrieval_observation(results: list[dict]) -> dict:
    """Extract bounded, non-sensitive retrieval facts for trace inspection."""
    document_ids = []
    scores = []
    source_types = []
    knowledge_versions = []
    for result in results:
        metadata = result.get("metadata", {}) or {}
        document_ids.append(result.get("id") or metadata.get("id", ""))
        score = result.get("score", metadata.get("score"))
        if score is not None:
            scores.append(score)
        source_type = metadata.get("type") or metadata.get("source", "")
        if source_type and source_type not in source_types:
            source_types.append(source_type)
        version = metadata.get("sdkVersion") or metadata.get("sdk_version", "")
        if version and version not in knowledge_versions:
            knowledge_versions.append(version)
    return {
        "retrieved_document_ids": document_ids,
        "retrieved_chunks": len(results),
        "retrieval_scores": scores,
        "source_type": source_types,
        "knowledge_version": knowledge_versions,
        "rerank_result": {"strategy": "hybrid_merge", "applied": False, "count": len(results)},
    }


def _citation_validity(citations: list[dict], retrieved_docs: list[dict]) -> bool:
    """Citations are valid when every citation points to a retrieved document."""
    retrieved_ids = {
        doc.get("id") or doc.get("metadata", {}).get("id", "")
        for doc in retrieved_docs
    }
    return all(citation.get("id") in retrieved_ids for citation in citations)


# ========== 辅助函数 ==========

def is_overview_query(query: str) -> bool:
    """
    判断是否为总览型问题。

    总览型问题通常问"能做什么"、"有什么用"等，
    应该优先召回概述/介绍类文档而不是具体 API 文档。
    """
    keywords = [
        "可以做什么", "有什么用", "能做什么", "支持哪些",
        "能力介绍", "介绍一下", "概述", "介绍下", "是什么",
        "能干嘛", "做什么用", "功能介绍", "使用场景",
    ]
    return any(kw in query for kw in keywords)


def build_citations(
    retrieved_docs: list[dict],
    knowledge_index_path: Path = Path("data/knowledge/_index.json"),
    limit: int = 3,
) -> list[dict]:
    """根据实际检索结果生成可验证的结构化来源引用。"""
    try:
        index = json.loads(knowledge_index_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    index_by_id = {entry.get("id"): entry for entry in index}
    citations = []
    seen_ids = set()

    for doc in retrieved_docs:
        doc_id = doc.get("id") or doc.get("metadata", {}).get("id")
        if not doc_id or doc_id in seen_ids:
            continue

        entry = index_by_id.get(doc_id)
        if not entry:
            continue

        seen_ids.add(doc_id)
        citations.append({
            "id": doc_id,
            "source": entry.get("source", ""),
            "sdk_version": entry.get("sdkVersion", ""),
            "start_line": entry.get("startLine", 0),
            "end_line": entry.get("endLine", 0),
        })
        if len(citations) >= limit:
            break

    return citations


# ========== 状态定义 ==========

class AgentState(TypedDict):
    """Agent 状态"""
    messages: Annotated[Sequence[BaseMessage], operator.add]  # 对话历史
    current_query: str  # 当前用户问题
    rewritten_query: str  # 重写后的问题
    intent: str  # 意图: api/sdk/param/code/general
    complexity: str
    route_confidence: float
    need_reason: bool
    images: list[str]
    retrieved_docs: list[dict]  # 检索到的文档
    expanded_context: str  # 展开后的上下文
    answer: str  # 生成的回答
    citations: list[dict]  # 基于检索结果生成的结构化来源
    retrieval_ms: float
    llm_ms: float
    session_id: str  # 会话 ID
    trace_id: str
    trace: object
    observability: Observability
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost: float


class RoutedLLM:
    """Selects a configured profile per invocation while preserving one app API."""

    def __init__(self, clients: dict[str, FailoverAdapter], router: ModelRouter):
        self.clients = clients
        self.router = router
        self.last_route = next(iter(clients.values())).route if clients else None

    @property
    def stats(self) -> dict:
        request_usage = current_request_usage()
        if request_usage is not None:
            return request_usage
        stats = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost": 0.0}
        for client in self.clients.values():
            for key in stats:
                stats[key] += client.stats.get(key, 0)
        return stats

    def invoke(self, messages, prompt_name: str = "developer_qa", trace: object | None = None,
               task_type: str | None = None, has_images: bool = False,
               complexity: str = "normal", confidence: float = 1.0,
               need_reason: bool = False):
        text = "\n".join(str(getattr(message, "content", "")) for message in messages)
        task = task_type or "general"
        if prompt_name == "intent_classifier":
            task = "intent_classifier"
        route = self.router.route(
            task,
            len(text),
            complexity=complexity,
            has_image=has_images,
            confidence=confidence,
            need_reason=need_reason,
        )
        client = self.clients.get(route.profile) or self.clients.get("default")
        if client is None:
            client = next(iter(self.clients.values()))
        response = client.invoke(messages, prompt_name=prompt_name, trace=trace, task_type=task)
        actual_route = getattr(client, "last_route", client.route)
        self.last_route = ModelRoute(
            provider=actual_route.provider,
            model=actual_route.model,
            reason=actual_route.reason or route.reason,
            profile=route.profile,
            role=route.role,
        )
        return response


def _provider_runtime_config(provider: str) -> tuple[str, str | None]:
    """Backward-compatible import for existing callers/tests."""
    return provider_runtime_config(provider)


def get_llm(timeout_seconds: float = 30.0, max_retries: int = 2,
            route: ModelRoute | None = None, prompt_registry: PromptRegistry | None = None,
            observability: Observability | None = None, trace: object | None = None,
            prompt_version: str = "v1", max_tokens: int = 4096,
            image_support: bool | None = None):
    """Create an instrumented Provider Adapter."""
    route = route or ModelRoute("deepseek", "deepseek-v4-flash", "default", "default")
    return create_instrumented_adapter(
        route=route,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_tokens=max_tokens,
        prompt_registry=prompt_registry or PromptRegistry(),
        observability=observability or get_observability(),
        trace=trace,
        prompt_version=prompt_version,
        image_support=image_support,
    )


# ========== Agent 节点 ==========

class IntentRouter:
    """意图识别节点"""

    def __init__(self, llm):
        self.llm = llm

    def __call__(self, state: AgentState) -> dict:
        with _node_span(state, "intent_router") as span:
            result = self._run(state)
            observability = state.get("observability") or get_observability()
            observability.update(span, intent=result.get("intent", ""),
                                 complexity=result.get("complexity", ""),
                                 confidence=result.get("route_confidence", 0.0),
                                 need_reason=result.get("need_reason", False))
            return result

    @staticmethod
    def _deterministic_fallback(query: str) -> dict:
        """Keep routing available when the remote Router times out or misformats JSON."""
        task = infer_task_type(query)
        if task == "code":
            return {"intent": "code", "complexity": "high", "route_confidence": 0.85, "need_reason": True}
        if task == "api":
            return {"intent": "api", "complexity": "normal", "route_confidence": 0.85, "need_reason": False}
        return {"intent": "general", "complexity": "normal", "route_confidence": 0.8, "need_reason": False}

    def _run(self, state: AgentState) -> dict:
        query = state["current_query"]
        prompt = f"""分析以下问题，并严格只返回 JSON，不要 Markdown：
{{"intent":"general","complexity":"normal","confidence":0.9,"need_reason":false}}

- api: 询问 API 使用方法
- sdk: 询问 SDK 功能
- param: 询问参数说明
- code: 需要代码示例
- general: 其他一般问题
- complexity 只能是 low/normal/high；需要复杂推理或多步分析时 need_reason 为 true

问题: {query}
        JSON:"""
        if self.llm is None:
            return self._deterministic_fallback(query)

        try:
            response = self.llm.invoke(
                [HumanMessage(content=prompt)],
                prompt_name="intent_classifier",
                trace=state.get("trace"),
                task_type="intent_classifier",
            )
            raw = response.content.strip()
            match = __import__("re").search(r"\{.*\}", raw, __import__("re").DOTALL)
            parsed = json.loads(match.group(0) if match else raw)
            intent = str(parsed.get("intent", "general")).strip().lower()
            if intent not in ("api", "sdk", "param", "code", "general"):
                intent = "general"
            complexity = str(parsed.get("complexity", "normal")).strip().lower()
            if complexity not in {"low", "normal", "high"}:
                complexity = "normal"
            confidence = float(parsed.get("confidence", 0.8))
            confidence = min(max(confidence, 0.0), 1.0)
            need_reason = bool(parsed.get("need_reason", False))
        except Exception as e:
            print(f"[intent] LLM call failed: {e}")
            print(traceback.format_exc())
            return self._deterministic_fallback(query)

        return {
            "intent": intent,
            "complexity": complexity,
            "route_confidence": confidence,
            "need_reason": need_reason,
        }


class QueryRewrite:
    """查询重写节点（支持多轮对话）"""

    def __init__(self, llm):
        self.llm = llm

    def __call__(self, state: AgentState) -> dict:
        with _node_span(state, "query_rewrite") as span:
            result = self._run(state)
            observability = state.get("observability") or get_observability()
            observability.update(span, rewritten_query=result.get("rewritten_query", "")[:2000])
            return result

    def _run(self, state: AgentState) -> dict:
        query = state["current_query"]
        messages = state.get("messages", [])

        if len(messages) <= 2:  # 只有第一轮对话
            return {"rewritten_query": query}

        # 有多轮对话时，补全上下文
        history_text = "\n".join([
            f"{'用户' if isinstance(m, HumanMessage) else 'AI'}: {m.content[:200]}"
            for m in messages[-6:]  # 最近3轮
        ])

        prompt = f"""基于对话历史重写用户问题，使其在脱离上下文时也能理解。

对话历史:
{history_text}

当前问题: {query}

重写后的问题（简洁准确）:"""
        if self.llm is None:
            return {"rewritten_query": query}

        try:
            response = self.llm.invoke(
                [HumanMessage(content=prompt)],
                prompt_name="query_rewrite",
                trace=state.get("trace"),
                task_type="rewrite",
            )
            rewritten = response.content.strip()
        except Exception as e:
            print(f"[rewrite] LLM call failed: {e}")
            print(traceback.format_exc())
            rewritten = query

        return {"rewritten_query": rewritten}


class Retriever:
    """知识库检索节点"""

    def __init__(self, vector_store: VectorStore, top_k: int = 5):
        self.vs = vector_store
        self.top_k = top_k

    def __call__(self, state: AgentState) -> dict:
        with _node_span(state, "retrieval") as span:
            result = self._run(state)
            observability = state.get("observability") or get_observability()
            query = state.get("rewritten_query") or state.get("current_query", "")
            observability.update(
                span,
                retrieval_query=query,
                retrieval_top_k=self.top_k,
                retrieval_latency_ms=result.get("retrieval_ms", 0.0),
                **_retrieval_observation(result.get("retrieved_docs", [])),
            )
            return result

    def _run(self, state: AgentState) -> dict:
        query = state.get("rewritten_query") or state["current_query"]
        start = perf_counter()

        # 判断是否为总览型问题，若是则对 overview 文档加权
        boost_overview = is_overview_query(query)

        try:
            results = self.vs.search_hybrid(
                query,
                top_k=self.top_k,
                boost_overview=boost_overview,
            )
        except Exception as e:
            print(f"[retrieve] Search failed: {e}")
            print(traceback.format_exc())
            results = []

        return {"retrieved_docs": results, "retrieval_ms": (perf_counter() - start) * 1000}


class GraphExpander:
    """依赖图展开节点"""

    def __init__(
        self,
        graph_path: str = "data/graph/dependency_graph.json",
        knowledge_dir: str = "data/knowledge",
    ):
        self.graph_path = graph_path
        self.knowledge_dir = Path(knowledge_dir)
        self.graph_data = self._load_graph()

    def _load_graph(self) -> dict:
        path = Path(self.graph_path)
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {"nodes": [], "edges": []}

    def __call__(self, state: AgentState) -> dict:
        with _node_span(state, "graph_expansion") as span:
            result = self._run(state)
            observability = state.get("observability") or get_observability()
            observability.update(span,
                                 context_chars=len(result.get("expanded_context", "")),
                                 context_builder="graph_expansion")
            return result

    def _run(self, state: AgentState) -> dict:
        docs = state.get("retrieved_docs", [])
        context_budget = _answer_context_limit()
        expanded_ids = set()
        retrieved_ids = set()

        # 对每个检索到的文档，展开其引用链
        for doc in docs:
            doc_id = doc.get("id") or doc.get("metadata", {}).get("id", "")
            if doc_id:
                retrieved_ids.add(doc_id)
                expanded_ids.add(doc_id)
                # 查找引用此文档的其他文档
                for edge in self.graph_data.get("edges", []):
                    if edge.get("source") == doc_id:
                        expanded_ids.add(edge.get("target"))
                    elif edge.get("target") == doc_id:
                        expanded_ids.add(edge.get("source"))

        # 构建展开后的上下文
        context_parts = []
        context_chars = 0

        def append_context(doc_id: str, content: str) -> bool:
            nonlocal context_chars
            if not content or len(content) <= 50 or context_chars >= context_budget:
                return False
            separator = "\n\n---\n\n" if context_parts else ""
            heading = f"## {doc_id}\n"
            remaining = context_budget - context_chars - len(separator) - len(heading)
            if remaining <= 50:
                return False
            section = separator + heading + content[:remaining]
            context_parts.append(section)
            context_chars += len(section)
            return context_chars >= context_budget

        for doc in docs:
            doc_id = doc.get("id") or doc.get("metadata", {}).get("id", "")
            metadata = doc.get("metadata", {})
            safe_name = doc_id.replace(".", "_").replace("/", "_")
            md_file = self.knowledge_dir / f"{safe_name}.md"
            # Vector chunks are intentionally small. For document/RAG hits, use
            # the canonical Markdown file so usage rules and examples are not
            # silently truncated before answer generation.
            if md_file.exists() and (
                metadata.get("type") == "document"
                or str(metadata.get("source", "")).startswith("docs/rag/")
            ):
                content = md_file.read_text(encoding="utf-8")
            else:
                content = doc.get("document", "")
            if append_context(doc_id, content[:4000]):
                break

        # 尝试读取完整的知识库文档
        for sym_id in expanded_ids:
            if context_chars >= context_budget:
                break
            safe_name = sym_id.replace(".", "_").replace("/", "_")
            md_file = self.knowledge_dir / f"{safe_name}.md"
            if md_file.exists() and sym_id not in retrieved_ids:
                content = md_file.read_text(encoding="utf-8")
                append_context(sym_id, content)

        expanded_context = "".join(context_parts)

        return {"expanded_context": expanded_context}


class AnswerGenerator:
    """答案生成节点"""

    def __init__(self, llm, prompt_registry: PromptRegistry | None = None, prompt_version: str = "v1"):
        self.llm = llm
        self.prompt_registry = prompt_registry or PromptRegistry()
        self.prompt_version = prompt_version
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        return self.prompt_registry.get("developer_qa", self.prompt_version, fallback="")

    def __call__(self, state: AgentState) -> dict:
        with _node_span(state, "answer_generation") as span:
            result = self._run(state)
            observability = state.get("observability") or get_observability()
            observability.update(span,
                                 image_count=len(state.get("images", [])),
                                 context_chars=len(state.get("expanded_context", "")),
                                 llm_latency_ms=result.get("llm_ms", 0.0))
            return result

    def _run(self, state: AgentState) -> dict:
        query = state.get("rewritten_query") or state["current_query"]
        context = state.get("expanded_context", "")
        start = perf_counter()

        images = state.get("images", [])
        if not context and not images:
            answer = "抱歉，我在知识库中没有找到相关的信息。请尝试用其他方式描述你的问题，或查阅某设计平台开放平台官方文档。"
            return {"answer": answer, "llm_ms": (perf_counter() - start) * 1000,
                    **self._usage()}

        system_content = self.system_prompt or "你是一个严谨的 SDK 问答助手，请根据提供的知识库内容回答问题。"
        context_text = context[:_answer_context_limit()] if context else "（本次问题没有检索到知识库内容，请结合图片本身进行分析，并明确区分图片观察与知识库事实。）"

        if self.llm is None:
            return {
                "answer": f"当前未配置模型密钥，以下是知识库中的相关信息：\n\n{context_text[:2000]}",
                "llm_ms": (perf_counter() - start) * 1000,
                **self._usage(),
            }

        try:
            prompt = f"""请基于以下知识库内容回答问题。

## 知识库内容
{context_text}

## 用户问题
{query}

## 要求
1. 直接回答问题
2. 如果涉及函数/API，提供代码示例
3. 仅基于提供的知识库内容回答，不得虚构来源、版本或行号
4. 列出参数说明
5. 如果知识库信息不足，明确说明"""
            if images:
                content = [{"type": "text", "text": prompt}]
                content.extend({"type": "image_url", "image_url": {"url": image}} for image in images)
                human_message = HumanMessage(content=content)
            else:
                human_message = HumanMessage(content=prompt)
            response = self.llm.invoke([
                SystemMessage(content=system_content),
                human_message,
            ], prompt_name="developer_qa", trace=state.get("trace"),
                # Route from the original user wording; rewritten prompts may
                # contain generic words such as “代码示例” and distort routing.
                task_type=infer_task_type(state.get("current_query", query)),
                has_images=bool(images),
                complexity=state.get("complexity", "normal"),
                confidence=state.get("route_confidence", 1.0),
                need_reason=state.get("need_reason", False))
            answer = response.content
        except Exception as e:
            print(f"[answer] LLM call failed: {e}")
            print(traceback.format_exc())
            answer = f"抱歉，回答生成失败，请稍后重试。以下是我在知识库中找到的相关信息：\n\n{context_text[:2000]}"

        return {"answer": answer, "llm_ms": (perf_counter() - start) * 1000,
                **self._usage()}

    def _usage(self) -> dict:
        stats = getattr(self.llm, "stats", {}) if self.llm else {}
        return {
            "input_tokens": stats.get("input_tokens", 0),
            "output_tokens": stats.get("output_tokens", 0),
            "total_tokens": stats.get("total_tokens", 0),
            "estimated_cost": round(stats.get("estimated_cost", 0.0), 8),
        }


# ========== Agent 构建 ==========

def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _nonnegative_env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return max(0, default)


def _deepseek_fallback_route(primary: ModelRoute) -> ModelRoute | None:
    """Build the official DeepSeek backup route for a SiliconFlow role."""
    if not _env_bool("DEEPSEEK_FALLBACK_ENABLED", True):
        return None
    if primary.provider.lower().replace("-", "").replace("_", "") != "siliconflow":
        return None
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        return None

    # DeepSeek's official API is text-only for this fallback path by default;
    # leave Vision without a backup unless the operator explicitly provides a
    # compatible model through the role-specific variable.
    profile_key = primary.profile.lower()
    default_models = {
        "router": "deepseek-v4-flash",
        "main": "deepseek-v4-flash",
        "reason": "deepseek-v4-flash",
        "vision": "",
        "default": "deepseek-v4-flash",
        "fast": "deepseek-v4-flash",
        "strong": "deepseek-v4-flash",
    }
    model = os.getenv(f"DEEPSEEK_FALLBACK_{profile_key.upper()}_MODEL", "").strip()
    if not model:
        model = default_models.get(profile_key, default_models.get(primary.role, ""))
    if not model:
        return None
    return ModelRoute(
        provider="deepseek",
        model=model,
        reason=f"DeepSeek official fallback for {primary.role or primary.profile} role",
        profile=primary.profile,
        role=primary.role,
    )


def _primary_retry_limit(route: ModelRoute, configured_retries: int,
                        fallback_route: ModelRoute | None) -> int:
    """Avoid multiplying the primary timeout before an available backup runs."""
    if fallback_route is None:
        return configured_retries
    try:
        return max(0, int(os.getenv("SILICONFLOW_MAX_RETRIES", "0")))
    except ValueError:
        return 0


def _fallback_timeout(primary_timeout: float) -> float:
    try:
        configured = max(0.1, float(os.getenv("DEEPSEEK_FALLBACK_TIMEOUT_SECONDS", "30")))
    except ValueError:
        configured = 30.0
    return min(primary_timeout, configured)

def build_agent(
    top_k: int = 5,
    timeout_seconds: float = 30.0,
    max_retries: int = 2,
    chroma_path: str = "data/chroma",
    knowledge_path: str = "data/knowledge",
    graph_path: str = "data/graph/dependency_graph.json",
    prompt_version: str = "v1",
    observability: Observability | None = None,
    trace: object | None = None,
    deterministic_router: bool = False,
):
    """构建 LangGraph Agent"""
    prompt_registry = PromptRegistry()
    model_router = ModelRouter()
    # Routing is based on the task after intent classification.  The default
    # route is constructed here; nodes can still classify the request without
    # an LLM and the configured single-model fallback remains valid.
    observability = observability or get_observability()
    clients: dict[str, FailoverAdapter] = {}
    configured_routes = model_router.role_routes() if model_router.uses_role_config() else model_router.profile_routes()
    profiles = ("router", "main", "reason", "vision") if model_router.uses_role_config() else ("default", "fast", "strong")
    for profile in profiles:
        profile_route = configured_routes[profile]
        route_timeout, route_retries, route_max_tokens = _llm_runtime_limits(
            profile_route, timeout_seconds, max_retries
        )
        fallback_route = _deepseek_fallback_route(profile_route)
        primary = get_llm(
            timeout_seconds=route_timeout,
            max_retries=_primary_retry_limit(profile_route, route_retries, fallback_route),
            max_tokens=route_max_tokens,
            route=profile_route,
            prompt_registry=prompt_registry,
            observability=observability,
            trace=trace,
            prompt_version=prompt_version,
        )
        fallback = None
        if fallback_route:
            fallback = get_llm(
                timeout_seconds=_fallback_timeout(route_timeout),
                max_retries=_nonnegative_env_int("DEEPSEEK_FALLBACK_MAX_RETRIES", 0),
                max_tokens=route_max_tokens,
                route=fallback_route,
                prompt_registry=prompt_registry,
                observability=observability,
                trace=trace,
                prompt_version=prompt_version,
                image_support=False,
            )
        selected = FailoverAdapter(primary, fallback, observability) if primary else None
        if selected is None and fallback:
            selected = FailoverAdapter(fallback, None, observability)
        if selected and profile_route.profile not in clients:
            clients[profile_route.profile] = selected
    llm = RoutedLLM(clients, model_router) if clients else None
    vector_store = VectorStore(persist_dir=chroma_path, knowledge_dir=knowledge_path)

    # 创建节点实例
    intent_router = IntentRouter(None if deterministic_router else llm)
    query_rewrite = QueryRewrite(llm)
    retriever = Retriever(vector_store, top_k=top_k)
    graph_expander = GraphExpander(graph_path=graph_path, knowledge_dir=knowledge_path)
    answer_generator = AnswerGenerator(llm, prompt_registry, prompt_version)

    # 构建图
    workflow = StateGraph(AgentState)

    # 添加节点
    workflow.add_node("intent_router", intent_router)
    workflow.add_node("query_rewrite", query_rewrite)
    workflow.add_node("retrieve", retriever)
    workflow.add_node("graph_expansion", graph_expander)
    workflow.add_node("answer_generator", answer_generator)

    # 设置入口
    workflow.set_entry_point("intent_router")

    # 添加边
    workflow.add_edge("intent_router", "query_rewrite")
    workflow.add_edge("query_rewrite", "retrieve")
    workflow.add_edge("retrieve", "graph_expansion")
    workflow.add_edge("graph_expansion", "answer_generator")
    workflow.add_edge("answer_generator", END)

    # 编译
    agent = workflow.compile()
    # Expose only non-sensitive runtime metadata for request logging; the
    # compiled workflow remains the sole execution interface.
    agent._model_router = model_router
    agent._llm = llm
    agent._vector_store = vector_store
    return agent


def _llm_runtime_limits(route: ModelRoute, timeout_seconds: float, max_retries: int) -> tuple[float, int, int]:
    """Apply a bounded runtime budget to the lightweight Router role."""
    if route.role != "router":
        return timeout_seconds, max_retries, 4096

    try:
        router_timeout = float(os.getenv("ROUTER_TIMEOUT_SECONDS", "15"))
    except ValueError:
        router_timeout = 15.0
    try:
        router_retries = max(0, int(os.getenv("ROUTER_MAX_RETRIES", "0")))
    except ValueError:
        router_retries = 0
    try:
        router_max_tokens = max(1, int(os.getenv("ROUTER_MAX_TOKENS", "256")))
    except ValueError:
        router_max_tokens = 256
    return router_timeout, router_retries, router_max_tokens


# ========== 会话管理 ==========

class SessionManager:
    """会话管理器；传入 SQLite 路径后，消息可跨服务重启保留。"""

    def __init__(self, database_path: Optional[str] = None):
        self.sessions: dict[str, list[dict]] = {}
        self.database_path = Path(database_path) if database_path else None
        if self.database_path:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                        citations_json TEXT NOT NULL DEFAULT '[]', request_id TEXT,
                        created_at TEXT NOT NULL
                    );
                """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def create_session(self) -> str:
        import uuid
        session_id = str(uuid.uuid4())[:8]
        if self.database_path:
            now = datetime.now(timezone.utc).isoformat()
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO sessions (id, created_at, updated_at) VALUES (?, ?, ?)",
                    (session_id, now, now),
                )
            return session_id
        self.sessions[session_id] = []
        return session_id

    def get_history(self, session_id: str) -> list[dict]:
        if self.database_path:
            with self._connect() as connection:
                rows = connection.execute("""
                    SELECT role, content, citations_json, request_id, created_at
                    FROM messages WHERE session_id = ? ORDER BY id
                """, (session_id,)).fetchall()
            return [{
                "role": row["role"], "content": row["content"],
                "citations": json.loads(row["citations_json"]), "request_id": row["request_id"],
                "timestamp": row["created_at"],
            } for row in rows]
        return self.sessions.get(session_id, [])

    def add_message(self, session_id: str, role: str, content: str,
                    citations: Optional[list[dict]] = None, request_id: Optional[str] = None):
        now = datetime.now(timezone.utc).isoformat()
        if self.database_path:
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO sessions (id, created_at, updated_at) VALUES (?, ?, ?)",
                    (session_id, now, now),
                )
                connection.execute("""
                    INSERT INTO messages (session_id, role, content, citations_json, request_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (session_id, role, content, json.dumps(citations or [], ensure_ascii=False), request_id, now))
                connection.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            return
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        self.sessions[session_id].append({
            "role": role, "content": content, "citations": citations or [],
            "request_id": request_id, "timestamp": now,
        })

    def delete_session(self, session_id: str):
        if self.database_path:
            with self._connect() as connection:
                connection.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return
        self.sessions.pop(session_id, None)

    def clear_all_sessions(self):
        if self.database_path:
            with self._connect() as connection:
                connection.execute("DELETE FROM messages")
                connection.execute("DELETE FROM sessions")
            return
        self.sessions.clear()

    def get_all_sessions(self) -> list[dict]:
        if self.database_path:
            with self._connect() as connection:
                rows = connection.execute("""
                    SELECT s.id, COUNT(m.id) AS message_count,
                        COALESCE((SELECT content FROM messages WHERE session_id = s.id ORDER BY id DESC LIMIT 1), '') AS last_message
                    FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
                    GROUP BY s.id ORDER BY s.updated_at DESC
                """).fetchall()
            return [{"id": row["id"], "message_count": row["message_count"],
                     "last_message": row["last_message"][:50]} for row in rows]
        return [
            {"id": sid, "message_count": len(msgs), "last_message": msgs[-1]["content"][:50] if msgs else ""}
            for sid, msgs in self.sessions.items()
        ]


# ========== Agent Runner ==========

class AgentRunner:
    """Agent 运行器"""

    def __init__(self, database_path: Optional[str] = None, top_k: int = 5,
                 timeout_seconds: float = 30.0, max_retries: int = 2,
                 chroma_path: str = "data/chroma", knowledge_path: str = "data/knowledge",
                 graph_path: str = "data/graph/dependency_graph.json",
                 prompt_version: str = "v1", deterministic_router: bool = False):
        self.prompt_version = prompt_version
        self.prompt_registry = PromptRegistry()
        self.observability = get_observability()
        self.model_router = ModelRouter()
        build_kwargs = {
            "top_k": top_k, "timeout_seconds": timeout_seconds, "max_retries": max_retries,
            "chroma_path": chroma_path, "knowledge_path": knowledge_path, "graph_path": graph_path,
        }
        if prompt_version != "v1":
            build_kwargs["prompt_version"] = prompt_version
        if deterministic_router:
            build_kwargs["deterministic_router"] = True
        self.agent = build_agent(**build_kwargs)
        self.session_manager = SessionManager(database_path)
        self.knowledge_index_path = Path(knowledge_path) / "_index.json"

    def warmup(self) -> dict[str, float | bool]:
        """Warm the vector encoder before accepting the first user request."""
        vector_store = getattr(self.agent, "_vector_store", None)
        if vector_store is None:
            return {"ready": False, "warmup_ms": 0.0}
        return vector_store.warmup()

    def chat(self, query: str, session_id: Optional[str] = None,
             request_id: Optional[str] = None, images: Optional[list[str]] = None) -> dict:
        """处理用户消息"""
        images = images or []
        request_id = request_id or __import__("uuid").uuid4().hex
        # 创建或获取会话
        if not session_id or (not self.session_manager.database_path and session_id not in self.session_manager.sessions):
            session_id = self.session_manager.create_session()

        # 添加用户消息
        self.session_manager.add_message(session_id, "user", query, request_id=request_id)

        # 构建消息历史
        history = self.session_manager.get_history(session_id)
        messages = []
        for msg in history[:-1]:  # 不包括当前
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            else:
                messages.append(AIMessage(content=msg["content"]))

        result = {
            "answer": "",
            "intent": "",
            "retrieved_docs": [],
            "citations": [],
            "retrieval_ms": 0.0,
            "llm_ms": 0.0,
        }

        # 运行 Agent；request_id 同时作为可关联的 trace_id，不把远程
        # observability 作为业务依赖。
        started_at = perf_counter()
        task_type = infer_task_type(query)
        route = self.model_router.route(task_type, context_length=len(query), has_image=bool(images))
        prompt_metadata = self.prompt_registry.metadata("developer_qa", self.prompt_version)
        usage_token = start_request_usage()
        with self.observability.trace(request_id, {
            "session_id": session_id, "user_query": query,
            "trace_id": request_id, "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt_name": "developer_qa", "prompt_version": self.prompt_version,
            "prompt_status": prompt_metadata.get("status", ""),
            "task_type": route.profile,
            "model_role": route.role, "image_count": len(images),
            "provider": route.provider, "model": route.model,
            "route_reason": route.reason,
        }) as trace:
            try:
                result = self.agent.invoke({
                "messages": messages,
                "current_query": query,
                "rewritten_query": "",
                "intent": "",
                "complexity": "normal",
                "route_confidence": 1.0,
                "need_reason": False,
                "images": images,
                "retrieved_docs": [],
                "expanded_context": "",
                "answer": "",
                "citations": [],
                "retrieval_ms": 0.0,
                "llm_ms": 0.0,
                "session_id": session_id,
                "trace_id": request_id,
                "trace": trace,
                "observability": self.observability,
                })
                answer = result.get("answer", "")
            except Exception as e:
                print(f"[agent] Error: {e}")
                print(traceback.format_exc())
                answer = f"抱歉，处理您的问题时出现错误，请稍后重试。错误信息: {str(e)}"
                result["error_type"] = type(e).__name__
            request_usage = current_request_usage() or {
                "input_tokens": 0, "output_tokens": 0,
                "total_tokens": 0, "estimated_cost": 0.0,
            }
            result.update(request_usage)
            citations = build_citations(result.get("retrieved_docs", []), self.knowledge_index_path)
            retrieved_docs = result.get("retrieved_docs", [])
            retrieval_observation = _retrieval_observation(retrieved_docs)
            self.observability.update(trace,
                trace_id=request_id, timestamp=datetime.now(timezone.utc).isoformat(),
                rewritten_query=result.get("rewritten_query", query), intent=result.get("intent", ""),
                retrieval_query=result.get("rewritten_query", query),
                retrieval_top_k=len(retrieved_docs),
                **retrieval_observation,
                retrieval_latency_ms=result.get("retrieval_ms", 0.0), llm_latency_ms=result.get("llm_ms", 0.0),
                total_latency_ms=(perf_counter() - started_at) * 1000,
                citation_count=len(citations),
                citation_validity=_citation_validity(citations, retrieved_docs),
                input_tokens=result.get("input_tokens", 0),
                output_tokens=result.get("output_tokens", 0),
                total_tokens=result.get("total_tokens", 0),
                estimated_cost=result.get("estimated_cost", 0.0),
                success="error_type" not in result,
                error_type=result.get("error_type", ""),
                answer=result.get("answer", answer)[:4000] if result.get("answer", answer) else "")

        reset_request_usage(usage_token)
        active_route = getattr(getattr(self.agent, "_llm", None), "last_route", None) or route
        self.observability.update(trace if "trace" in locals() else object(),
                                  provider=active_route.provider, model=active_route.model,
                                  route_reason=active_route.reason, model_role=active_route.role,
                                  prompt_name="developer_qa", prompt_version=self.prompt_version,
                                  image_count=len(images))
        self.session_manager.add_message(
            session_id, "assistant", answer, citations=citations, request_id=request_id
        )

        return {
            "answer": answer,
            "session_id": session_id,
            "intent": result.get("intent", ""),
            "retrieved_count": len(result.get("retrieved_docs", [])),
            "citations": citations,
            "retrieval_ms": result.get("retrieval_ms", 0.0),
            "llm_ms": result.get("llm_ms", 0.0),
            "trace_id": request_id,
            "rewritten_query": result.get("rewritten_query", query),
            "prompt_name": "developer_qa",
            "prompt_version": self.prompt_version,
            "provider": active_route.provider,
            "model": active_route.model,
            "model_role": active_route.role,
            "route_reason": active_route.reason,
            "image_count": len(images),
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
            "total_tokens": result.get("total_tokens", 0),
            "estimated_cost": result.get("estimated_cost", 0.0),
            "retrieved_documents": [
                doc.get("id") or doc.get("metadata", {}).get("id", "")
                for doc in result.get("retrieved_docs", [])
            ],
        }
