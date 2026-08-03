"""
FastAPI 后端 - 插件开发 AI 助手 API
"""

import os
import sys
import re
import asyncio
import json
import uuid
from time import perf_counter
from pathlib import Path

from dotenv import load_dotenv

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 自动加载项目根目录 .env
load_dotenv(PROJECT_ROOT / ".env")
print(f"[env] .env 加载: {'已找到' if (PROJECT_ROOT / '.env').exists() else '未找到'}")

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional

from agent import AgentRunner
from app.config import get_settings
from app.metrics_store import MetricsStore

settings = get_settings()

app = FastAPI(
    title="插件开发 AI 助手",
    description="SDK 智能问答 API",
    version="0.1.0",
)

# CORS 配置：默认只允许本地前端；部署时通过 FRONTEND_ORIGINS 配置多个域名。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局 Agent Runner
agent_runner: Optional[AgentRunner] = None
metrics_store = MetricsStore(settings.database_path)


def get_agent() -> AgentRunner:
    global agent_runner
    if agent_runner is None:
        agent_runner = AgentRunner(
            database_path=str(settings.database_path),
            top_k=settings.retrieval_top_k,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            chroma_path=str(settings.chroma_path),
            knowledge_path=str(settings.knowledge_path),
            graph_path=str(settings.graph_path),
        )
    return agent_runner


# ========== 请求/响应模型 ==========

class ChatRequest(BaseModel):
    query: str = Field(max_length=2000)
    session_id: Optional[str] = Field(default=None, max_length=64)


class Citation(BaseModel):
    """回答使用的、可由知识库索引验证的来源。"""

    id: str
    source: str
    sdk_version: str = ""
    start_line: int = 0
    end_line: int = 0


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    intent: str = ""
    retrieved_count: int = 0
    citations: list[Citation] = Field(default_factory=list)
    request_id: str


class SessionInfo(BaseModel):
    id: str
    message_count: int = 0
    last_message: str = ""


class FeedbackRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)
    helpful: bool
    comment: str = Field(default="", max_length=1000)


# ========== API 路由 ==========

SESSION_ID_PATTERN = re.compile(r"^[a-f0-9]{8}$")


def validate_session_id(session_id: Optional[str]) -> None:
    """校验当前内存会话 ID 的格式，避免异常输入进入会话字典。"""
    if session_id and not SESSION_ID_PATTERN.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="无效的会话 ID")

@app.get("/api/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "service": "插件开发 AI 助手",
        "version": "0.1.0",
    }


@app.get("/api/ready")
async def ready():
    """检查知识库和模型配置是否具备处理请求的条件。"""
    try:
        from vector_store import VectorStore
        vector_count = VectorStore(
            persist_dir=str(settings.chroma_path),
            knowledge_dir=str(settings.knowledge_path),
        ).count()
        index_path = settings.knowledge_path / "_index.json"
        knowledge_count = len(json.loads(index_path.read_text(encoding="utf-8")))
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"服务未就绪: {error}") from error

    return {
        "status": "ready" if vector_count and knowledge_count else "degraded",
        "vector_documents": vector_count,
        "knowledge_entries": knowledge_count,
        "llm_configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
    }


@app.get("/api/metrics")
async def metrics():
    """返回最近 1,000 个请求的聚合运行指标。"""
    return metrics_store.metrics()


@app.get("/api/metrics/failures")
async def metric_failures(limit: int = Query(default=50, ge=1, le=1000)):
    """返回低质量候选请求，便于人工归因和沉淀回归案例。"""
    return metrics_store.failure_cases(limit)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """对话接口"""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")
    validate_session_id(request.session_id)

    agent = get_agent()
    request_id = str(uuid.uuid4())
    started_at = perf_counter()
    try:
        # Agent 内部包含 embedding 与同步 LLM 调用，放入工作线程避免阻塞事件循环。
        result = await asyncio.to_thread(
            agent.chat,
            request.query.strip(),
            request.session_id,
            request_id,
        )
        status = "success"
        error_message = None
    except Exception as error:
        status = "error"
        error_message = str(error)
        total_ms = (perf_counter() - started_at) * 1000
        metrics_store.record_request({
            "request_id": request_id, "session_id": request.session_id or "",
            "query": request.query.strip(), "status": status, "error_message": error_message,
            "total_ms": total_ms,
        })
        raise HTTPException(status_code=500, detail="处理问题时发生内部错误") from error

    total_ms = (perf_counter() - started_at) * 1000
    metrics_store.record_request({
        "request_id": request_id, "session_id": result["session_id"],
        "query": request.query.strip(), "intent": result.get("intent", ""),
        "retrieved_count": result.get("retrieved_count", 0),
        "citation_count": len(result.get("citations", [])),
        "retrieval_ms": result.get("retrieval_ms", 0.0),
        "llm_ms": result.get("llm_ms", 0.0), "total_ms": total_ms,
        "status": status,
    })

    return ChatResponse(
        answer=result["answer"],
        session_id=result["session_id"],
        intent=result.get("intent", ""),
        retrieved_count=result.get("retrieved_count", 0),
        citations=result.get("citations", []),
        request_id=request_id,
    )


@app.get("/api/chat/history", response_model=list[dict])
async def get_history(session_id: Optional[str] = None):
    """获取会话历史"""
    agent = get_agent()
    validate_session_id(session_id)

    if session_id:
        history = agent.session_manager.get_history(session_id)
        return history

    return agent.session_manager.get_all_sessions()


@app.delete("/api/chat/history")
async def clear_history(session_id: Optional[str] = None):
    """清除会话历史"""
    agent = get_agent()
    validate_session_id(session_id)

    if session_id:
        agent.session_manager.delete_session(session_id)
        return {"message": f"会话 {session_id} 已清除"}

    agent.session_manager.clear_all_sessions()
    return {"message": "所有会话已清除"}


@app.post("/api/chat/feedback", status_code=204)
async def submit_feedback(feedback: FeedbackRequest):
    """保存用户对某一次回答的反馈。"""
    if not metrics_store.record_feedback(feedback.request_id, feedback.helpful, feedback.comment.strip()):
        raise HTTPException(status_code=404, detail="未找到对应请求")
