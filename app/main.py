"""
FastAPI 后端 - 插件开发 AI 助手 API
"""

import os
import sys
import re
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
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
from typing import Optional, Literal

from agent import AgentRunner
from app.config import get_settings
from app.metrics_store import MetricsStore
from app.model_router import ModelRouter

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Warm local retrieval dependencies before the first production request."""
    try:
        result = await asyncio.to_thread(get_agent().warmup)
        _app.state.embedding_warmup = result
    except Exception as error:
        _app.state.embedding_warmup = {"ready": False, "warmup_ms": 0.0, "error": str(error)}
        print(f"[startup] embedding warmup failed: {error}")
    yield

app = FastAPI(
    title="插件开发 AI 助手",
    description="SDK 智能问答 API",
    version="0.1.0",
    lifespan=lifespan,
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
metrics_store = MetricsStore(settings.database_path, PROJECT_ROOT / "eval" / "regression_cases.json")
model_router = ModelRouter()


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
            prompt_version=settings.prompt_version,
        )
    return agent_runner


# ========== 请求/响应模型 ==========

class ChatRequest(BaseModel):
    query: str = Field(default="", max_length=2000)
    session_id: Optional[str] = Field(default=None, max_length=64)
    images: list[str] = Field(default_factory=list, max_length=3)


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
    trace_id: str = ""
    provider: str = ""
    model: str = ""
    model_role: str = ""
    route_reason: str = ""
    image_count: int = 0
    prompt_version: str = ""
    estimated_cost: float = 0.0


class SessionInfo(BaseModel):
    id: str
    message_count: int = 0
    last_message: str = ""


class FeedbackRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    helpful: bool
    reason: Literal["wrong_answer", "retrieval_wrong", "citation_wrong", "code_wrong", "incomplete", "other", ""] = ""
    comment: str = Field(default="", max_length=1000)


class BadcaseStatusRequest(BaseModel):
    status: Literal["NEW", "REVIEWED", "PROMOTED", "IGNORED"]


# ========== API 路由 ==========

SESSION_ID_PATTERN = re.compile(r"^[a-f0-9]{8}$")


def validate_session_id(session_id: Optional[str]) -> None:
    """校验当前内存会话 ID 的格式，避免异常输入进入会话字典。"""
    if session_id and not SESSION_ID_PATTERN.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="无效的会话 ID")


def validate_images(images: list[str]) -> None:
    """Restrict multimodal inputs to bounded data URLs or HTTPS URLs."""
    max_chars = 12_000_000
    for image in images:
        if len(image) > max_chars:
            raise HTTPException(status_code=413, detail="单张图片过大，请压缩后重试")
        if not (image.startswith("https://") or image.startswith("data:image/")):
            raise HTTPException(status_code=400, detail="图片仅支持 HTTPS URL 或 data:image URL")

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

    routes = model_router.role_routes() if model_router.uses_role_config() else model_router.profile_routes()
    runtime_agent = get_agent()
    runtime_llm = getattr(getattr(runtime_agent, "agent", None), "_llm", None)
    fallback_routes = {}
    for profile, adapter in getattr(runtime_llm, "clients", {}).items():
        fallback = getattr(adapter, "fallback", None)
        if fallback is not None:
            fallback_routes[profile] = {
                "provider": fallback.route.provider,
                "model": fallback.route.model,
            }
    embedding_warmup = getattr(app.state, "embedding_warmup", {"ready": False, "warmup_ms": 0.0})
    return {
        "status": "ready" if vector_count and knowledge_count else "degraded",
        "vector_documents": vector_count,
        "knowledge_entries": knowledge_count,
        "llm_configured": model_router.is_ready(),
        "embedding_ready": bool(embedding_warmup.get("ready")),
        "embedding_warmup_ms": embedding_warmup.get("warmup_ms", 0.0),
        "model_role_status": model_router.role_status(),
        "model_routes": {
            profile: {"provider": route.provider, "model": route.model}
            for profile, route in routes.items()
        },
        "fallback_routes": fallback_routes,
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
    query = request.query.strip()
    validate_images(request.images)
    if not query and not request.images:
        raise HTTPException(status_code=400, detail="问题或图片至少提供一项")
    validate_session_id(request.session_id)

    agent = get_agent()
    request_id = str(uuid.uuid4())
    started_at = perf_counter()
    try:
        # Agent 内部包含 embedding 与同步 LLM 调用，放入工作线程避免阻塞事件循环。
        chat_args = (query, request.session_id, request_id)
        chat_kwargs = {"images": request.images} if request.images else {}
        result = await asyncio.to_thread(agent.chat, *chat_args, **chat_kwargs)
        status = "success"
        error_message = None
    except Exception as error:
        status = "error"
        error_message = str(error)
        total_ms = (perf_counter() - started_at) * 1000
        metrics_store.record_request({
            "request_id": request_id, "session_id": request.session_id or "",
            "query": query, "status": status, "error_message": error_message,
            "total_ms": total_ms,
        })
        raise HTTPException(status_code=500, detail="处理问题时发生内部错误") from error

    total_ms = (perf_counter() - started_at) * 1000
    metrics_store.record_request({
        "request_id": request_id, "session_id": result["session_id"],
        "query": query, "intent": result.get("intent", ""),
        "retrieved_count": result.get("retrieved_count", 0),
        "citation_count": len(result.get("citations", [])),
        "retrieval_ms": result.get("retrieval_ms", 0.0),
        "llm_ms": result.get("llm_ms", 0.0), "total_ms": total_ms,
        "status": status,
        "answer": result.get("answer", ""),
        "rewritten_query": result.get("rewritten_query", query),
        "prompt_name": result.get("prompt_name", "developer_qa"),
        "prompt_version": result.get("prompt_version", settings.prompt_version),
        "provider": result.get("provider", ""), "model": result.get("model", ""),
        "model_role": result.get("model_role", ""), "image_count": result.get("image_count", len(request.images)),
        "route_reason": result.get("route_reason", ""),
        "input_tokens": result.get("input_tokens", 0), "output_tokens": result.get("output_tokens", 0),
        "total_tokens": result.get("total_tokens", 0), "estimated_cost": result.get("estimated_cost", 0.0),
        "retrieved_documents": result.get("retrieved_documents", []),
    })

    return ChatResponse(
        answer=result["answer"],
        session_id=result["session_id"],
        intent=result.get("intent", ""),
        retrieved_count=result.get("retrieved_count", 0),
        citations=result.get("citations", []),
        request_id=request_id,
        trace_id=result.get("trace_id", request_id),
        provider=result.get("provider", ""),
        model=result.get("model", ""),
        model_role=result.get("model_role", ""),
        route_reason=result.get("route_reason", ""),
        image_count=result.get("image_count", len(request.images)),
        prompt_version=result.get("prompt_version", ""),
        estimated_cost=result.get("estimated_cost", 0.0),
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
@app.post("/api/feedback", status_code=204)
async def submit_feedback(feedback: FeedbackRequest):
    """保存用户对某一次回答的反馈。"""
    if not metrics_store.record_feedback(feedback.request_id, feedback.helpful, feedback.comment.strip(), feedback.reason):
        raise HTTPException(status_code=404, detail="未找到对应请求")


@app.get("/api/badcases")
async def badcases(limit: int = Query(default=100, ge=1, le=1000)):
    """列出负反馈生成的待复核案例。"""
    return metrics_store.list_badcases(limit)


@app.post("/api/badcases/{badcase_id}/promote")
async def promote_badcase(badcase_id: int):
    """把人工确认的 badcase 追加到回归评测集。"""
    case = metrics_store.promote_badcase(badcase_id)
    if not case:
        raise HTTPException(status_code=404, detail="未找到 badcase")
    return {"status": "PROMOTED", "case": case}


@app.patch("/api/badcases/{badcase_id}")
async def update_badcase_status(badcase_id: int, request: BadcaseStatusRequest):
    if request.status == "PROMOTED":
        case = metrics_store.promote_badcase(badcase_id)
        if not case:
            raise HTTPException(status_code=404, detail="未找到 badcase")
        return {"status": "PROMOTED", "case": case}
    if not metrics_store.update_badcase_status(badcase_id, request.status):
        raise HTTPException(status_code=404, detail="未找到 badcase")
    return {"status": request.status}
