"""管理员路由: 设备远程控制(运维+运营管控)、全量查看、操作留痕。

访问要求: admin 角色 token;每次变更写入 operation_log。
"""
from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field

from .. import db
from ..mqtt_publisher import publish_to_device
from ..security import require_role, require_scope, SCOPE_DEVICE_CTRL, SCOPE_DEVICE_MGMT

router = APIRouter(prefix="/api/admin", tags=["admin"])

require_admin = require_role("admin")


class DeviceControlBody(BaseModel):
    device_id: str
    action: str = Field(..., description="reboot | upgrade | push_speak | set_config | lock | unlock")
    message: str | None = None
    payload: dict = Field(default_factory=dict)


@router.get("/devices")
def admin_devices(claims=Depends(require_admin)):
    """全量设备/账号概览(管理台)。"""
    require_scope(claims, SCOPE_DEVICE_MGMT)
    return {"ok": True, "devices": []}   # 占位: 可扩展查询 accounts 表


@router.post("/device/control")
def device_control(body: DeviceControlBody, claims=Depends(require_admin)):
    """远程控制设备(运维/upgrade 或运营/push_speak 等),并写入 operation_log。"""
    require_scope(claims, SCOPE_DEVICE_CTRL)
    msg = {"type": "config", "action": body.action}
    if body.message:
        msg["text"] = body.message
    if body.payload:
        msg["payload"] = body.payload
    ok = publish_to_device(body.device_id, msg)
    db.log_operation(operator=claims["sub"], action=f"device_control:{body.action}",
                     target=body.device_id, detail={"ok": ok, "action": body.action})
    return {"ok": ok, "action": body.action, "device_id": body.device_id}


@router.get("/operations")
def admin_operations(claims=Depends(require_admin)):
    """查看管理员操作留痕。"""
    require_scope(claims, SCOPE_DEVICE_CTRL)
    return {"ok": True, "logs": db.list_operations()}
