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

import json
import os
import traceback
from pathlib import Path
from typing import TypedDict, Optional, Annotated, Sequence
from datetime import datetime

import operator

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from langchain_openai import ChatOpenAI
from langchain.callbacks.base import BaseCallbackHandler

from vector_store import VectorStore


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


# ========== 状态定义 ==========

class AgentState(TypedDict):
    """Agent 状态"""
    messages: Annotated[Sequence[BaseMessage], operator.add]  # 对话历史
    current_query: str  # 当前用户问题
    rewritten_query: str  # 重写后的问题
    intent: str  # 意图: api/sdk/param/code/general
    retrieved_docs: list[dict]  # 检索到的文档
    expanded_context: str  # 展开后的上下文
    answer: str  # 生成的回答
    session_id: str  # 会话 ID


# ========== DeepSeek LLM 初始化 ==========

def get_llm():
    """获取 DeepSeek LLM 实例"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if api_key:
        print("[env] DEEPSEEK_API_KEY 已加载")
    else:
        print("[env] DEEPSEEK_API_KEY 未加载")
        print("[warn] DEEPSEEK_API_KEY 未设置，使用本地兜底模式")
        return None

    return ChatOpenAI(
        model="deepseek-chat",
        api_key=api_key,
        base_url="https://api.deepseek.com/v1",
        temperature=0.1,
        max_tokens=4096,
    )


# ========== Agent 节点 ==========

class IntentRouter:
    """意图识别节点"""

    def __init__(self, llm):
        self.llm = llm

    def __call__(self, state: AgentState) -> dict:
        query = state["current_query"]
        prompt = f"""分析以下问题的意图，只返回一个词:
- api: 询问 API 使用方法
- sdk: 询问 SDK 功能
- param: 询问参数说明
- code: 需要代码示例
- general: 其他一般问题

问题: {query}
意图:"""
        if self.llm is None:
            return {"intent": "general"}

        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
            intent = response.content.strip().lower()
            if intent not in ("api", "sdk", "param", "code", "general"):
                intent = "general"
        except Exception as e:
            print(f"[intent] LLM call failed: {e}")
            print(traceback.format_exc())
            intent = "general"

        return {"intent": intent}


class QueryRewrite:
    """查询重写节点（支持多轮对话）"""

    def __init__(self, llm):
        self.llm = llm

    def __call__(self, state: AgentState) -> dict:
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
            response = self.llm.invoke([HumanMessage(content=prompt)])
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
        query = state.get("rewritten_query") or state["current_query"]

        # 判断是否为总览型问题，若是则对 overview 文档加权
        boost_overview = is_overview_query(query)

        try:
            if boost_overview:
                results = self.vs.search_with_boost(query, top_k=self.top_k, boost_overview=True)
            else:
                results = self.vs.search(query, top_k=self.top_k)
        except Exception as e:
            print(f"[retrieve] Search failed: {e}")
            print(traceback.format_exc())
            results = []

        return {"retrieved_docs": results}


class GraphExpander:
    """依赖图展开节点"""

    def __init__(self, graph_path: str = "data/graph/dependency_graph.json"):
        self.graph_path = graph_path
        self.graph_data = self._load_graph()

    def _load_graph(self) -> dict:
        path = Path(self.graph_path)
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {"nodes": [], "edges": []}

    def __call__(self, state: AgentState) -> dict:
        docs = state.get("retrieved_docs", [])
        expanded_ids = set()

        # 对每个检索到的文档，展开其引用链
        for doc in docs:
            doc_id = doc.get("id") or doc.get("metadata", {}).get("id", "")
            if doc_id:
                expanded_ids.add(doc_id)
                # 查找引用此文档的其他文档
                for edge in self.graph_data.get("edges", []):
                    if edge.get("source") == doc_id:
                        expanded_ids.add(edge.get("target"))
                    elif edge.get("target") == doc_id:
                        expanded_ids.add(edge.get("source"))

        # 构建展开后的上下文
        context_parts = []
        for doc in docs:
            doc_id = doc.get("id") or doc.get("metadata", {}).get("id", "")
            content = doc.get("document", "")
            if content and len(content) > 50:
                context_parts.append(f"## {doc_id}\n{content[:2000]}")

        # 尝试读取完整的知识库文档
        knowledge_dir = Path("data/knowledge")
        for sym_id in expanded_ids:
            safe_name = sym_id.replace(".", "_").replace("/", "_")
            md_file = knowledge_dir / f"{safe_name}.md"
            if md_file.exists() and sym_id not in [d.get("id") or d.get("metadata", {}).get("id", "") for d in docs]:
                content = md_file.read_text(encoding="utf-8")
                context_parts.append(f"## {sym_id}\n{content}")

        expanded_context = "\n\n---\n\n".join(context_parts) if context_parts else ""

        return {"expanded_context": expanded_context}


class AnswerGenerator:
    """答案生成节点"""

    def __init__(self, llm):
        self.llm = llm
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        path = Path("prompts/system.md")
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def __call__(self, state: AgentState) -> dict:
        query = state.get("rewritten_query") or state["current_query"]
        context = state.get("expanded_context", "")

        if not context:
            answer = "抱歉，我在知识库中没有找到相关的信息。请尝试用其他方式描述你的问题，或查阅某设计平台开放平台官方文档。"
            return {"answer": answer}

        system_content = self.system_prompt or "你是一个严谨的 SDK 问答助手，请根据提供的知识库内容回答问题。"
        context_text = context[:6000]

        if self.llm is None:
            return {"answer": f"当前未配置模型密钥，以下是知识库中的相关信息：\n\n{context_text[:2000]}"}

        try:
            response = self.llm.invoke([
                SystemMessage(content=system_content),
                HumanMessage(content=f"""请基于以下知识库内容回答问题。

## 知识库内容
{context_text}

## 用户问题
{query}

## 要求
1. 直接回答问题
2. 如果涉及函数/API，提供代码示例
3. 必须引用来源（文件名和SDK版本）
4. 列出参数说明
5. 如果知识库信息不足，明确说明""")
            ])
            answer = response.content
        except Exception as e:
            print(f"[answer] LLM call failed: {e}")
            print(traceback.format_exc())
            answer = f"抱歉，回答生成失败，请稍后重试。以下是我在知识库中找到的相关信息：\n\n{context_text[:2000]}"

        return {"answer": answer}


# ========== Agent 构建 ==========

def build_agent():
    """构建 LangGraph Agent"""
    llm = get_llm()
    vector_store = VectorStore()

    # 创建节点实例
    intent_router = IntentRouter(llm)
    query_rewrite = QueryRewrite(llm)
    retriever = Retriever(vector_store)
    graph_expander = GraphExpander()
    answer_generator = AnswerGenerator(llm)

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
    return agent


# ========== 会话管理 ==========

class SessionManager:
    """会话管理器（内存实现）"""

    def __init__(self):
        self.sessions: dict[str, list[dict]] = {}

    def create_session(self) -> str:
        import uuid
        session_id = str(uuid.uuid4())[:8]
        self.sessions[session_id] = []
        return session_id

    def get_history(self, session_id: str) -> list[dict]:
        return self.sessions.get(session_id, [])

    def add_message(self, session_id: str, role: str, content: str):
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        self.sessions[session_id].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })

    def delete_session(self, session_id: str):
        if session_id in self.sessions:
            del self.sessions[session_id]

    def get_all_sessions(self) -> list[dict]:
        return [
            {"id": sid, "message_count": len(msgs), "last_message": msgs[-1]["content"][:50] if msgs else ""}
            for sid, msgs in self.sessions.items()
        ]


# ========== Agent Runner ==========

class AgentRunner:
    """Agent 运行器"""

    def __init__(self):
        self.agent = build_agent()
        self.session_manager = SessionManager()

    def chat(self, query: str, session_id: Optional[str] = None) -> dict:
        """处理用户消息"""
        # 创建或获取会话
        if not session_id or session_id not in self.session_manager.sessions:
            session_id = self.session_manager.create_session()

        # 添加用户消息
        self.session_manager.add_message(session_id, "user", query)

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
        }

        # 运行 Agent
        try:
            result = self.agent.invoke({
                "messages": messages,
                "current_query": query,
                "rewritten_query": "",
                "intent": "",
                "retrieved_docs": [],
                "expanded_context": "",
                "answer": "",
                "session_id": session_id,
            })
            answer = result.get("answer", "")
        except Exception as e:
            print(f"[agent] Error: {e}")
            print(traceback.format_exc())
            answer = f"抱歉，处理您的问题时出现错误，请稍后重试。错误信息: {str(e)}"

        # 保存 AI 回复
        self.session_manager.add_message(session_id, "assistant", answer)

        return {
            "answer": answer,
            "session_id": session_id,
            "intent": result.get("intent", ""),
            "retrieved_count": len(result.get("retrieved_docs", [])),
        }
