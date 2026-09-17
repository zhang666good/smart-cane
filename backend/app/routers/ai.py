"""通用 AI 对话接口(供 App / 各端统一调用大模型)。

POST /api/ai/chat : 任三角色 JWT 均可调用; role 以 JWT 为准(不允许客户端覆盖),
仅返回回复文本(不做 TTS/下发——播放由调用方决定), mock 标记用于开发期。
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .. import ai_service
from ..security import (ROLE_ADMIN, can_access_device, require_role)

router = APIRouter(prefix="/api/ai", tags=["ai"])


class AiChatBody(BaseModel):
    text: str = Field(..., description="用户文本")
    session_id: str = Field(default="", description="可选会话标识")
    device_id: str = Field(default="", description="绑定设备 id(admin 可空/任意)")


@router.post("/chat")
async def ai_chat(body: AiChatBody, claims=Depends(require_role())):
    """通用 AI 对话: 按 JWT 角色选提示词与可用工具, 返回回复文本。

    role 取自 claims.role; admin 可访问任意 device, 其余须 device_id 绑定。
    """
    role = claims.get("role", "")
    device_id = body.device_id or claims.get("device_id") or ""
    if body.device_id:
        can_access_device(claims, body.device_id,
                          scope_for_owner="user_own")   # admin 放行, 其余须绑定
    elif role != ROLE_ADMIN and not device_id:
        raise HTTPException(status_code=400, detail="device_id required for this role")

    result = await run_in_threadpool(
        ai_service.chat, role, body.text, device_id)
    return {"ok": True, "text": result.text, "mock": result.mock,
            "action": result.action, "role": role, "tool_calls": result.tool_calls}