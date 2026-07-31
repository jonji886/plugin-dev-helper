"""
FastAPI 后端 - 插件开发 AI 助手 API
"""

import os
import sys
import re
from pathlib import Path

from dotenv import load_dotenv

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 自动加载项目根目录 .env
load_dotenv(PROJECT_ROOT / ".env")
print(f"[env] .env 加载: {'已找到' if (PROJECT_ROOT / '.env').exists() else '未找到'}")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional

from agent import AgentRunner

app = FastAPI(
    title="插件开发 AI 助手",
    description="SDK 智能问答 API",
    version="0.1.0",
)

# CORS 配置：默认只允许本地前端；部署时通过 FRONTEND_ORIGINS 配置多个域名。
allowed_origins = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
if "*" in allowed_origins:
    raise ValueError("FRONTEND_ORIGINS 不能包含通配符 *")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局 Agent Runner
agent_runner: Optional[AgentRunner] = None


def get_agent() -> AgentRunner:
    global agent_runner
    if agent_runner is None:
        agent_runner = AgentRunner()
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


class SessionInfo(BaseModel):
    id: str
    message_count: int = 0
    last_message: str = ""


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


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """对话接口"""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")
    validate_session_id(request.session_id)

    agent = get_agent()
    result = agent.chat(
        query=request.query.strip(),
        session_id=request.session_id,
    )

    return ChatResponse(
        answer=result["answer"],
        session_id=result["session_id"],
        intent=result.get("intent", ""),
        retrieved_count=result.get("retrieved_count", 0),
        citations=result.get("citations", []),
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
