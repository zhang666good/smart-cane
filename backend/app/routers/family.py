"""家人角色路由: 观测绑定设备传感、向老人设备发送消息/关怀。

访问要求: family 角色 token,且目标设备在家人绑定列表内(由 security 校验)。
"""
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..mqtt_publisher import publish_to_device
from ..security import (require_role, require_scope, can_access_device,
                        SCOPE_PUSH_OWN, SCOPE_SENSORS)

router = APIRouter(prefix="/api/family", tags=["family"])

require_family = require_role("family", "admin")


class DirectMessage(BaseModel):
    device_id: str
    message: str
    type: str = "speak"


class BindBody(BaseModel):
    device_id: str


@router.post("/bind")
def bind_device(body: BindBody, claims=Depends(require_family)):
    """家人手动绑定一台设备到当前账号。管理端无需校验已在绑定列表内。"""
    # 管理端可直接绑定; 家人绑定任意设备(可按需加校验)
    ok = db.bind_device(claims["sub"], body.device_id.strip(), "family")
    db.log_operation(claims["sub"], "bind_device", body.device_id.strip(),
                     {"relation": "family", "created_new": bool(ok)})
    return {"ok": True, "device_id": body.device_id.strip(),
            "created": bool(ok), "already_bound": ok == 0}


@router.get("/devices")
def my_devices(claims=Depends(require_family)):
    """返回当前家人可观测/操作的所有绑定设备。"""
    bound = db.bound_devices(claims["user_id"] if False else claims["sub"])
    return {"ok": True, "user": claims["sub"], "devices": bound}


@router.get("/sensors")
def family_sensors(device_id: str, claims=Depends(require_family)):
    """观测某绑定设备的最新传感(家人需已绑定该设备)。"""
    if claims["role"] == "admin":
        require_scope(claims, SCOPE_SENSORS)
    else:
        if not db.is_bound(claims["sub"], device_id):
            raise HTTPException(status_code=403, detail="device not bound")
        require_scope(claims, SCOPE_SENSORS)
    latest = db.latest_sensors(device_id)
    return {"ok": True, "device_id": device_id,
            "found": latest is not None, "sensors": latest}


@router.post("/message")
def family_message(body: DirectMessage, claims=Depends(require_family)):
    """家人→老人设备 发送消息/关怀(经云端 MQTT 转发)。"""
    if claims["role"] == "admin":
        require_scope(claims, SCOPE_PUSH_OWN)
    else:
        if not db.is_bound(claims["sub"], body.device_id):
            raise HTTPException(status_code=403, detail="device not bound")
        require_scope(claims, SCOPE_PUSH_OWN)
    ok = publish_to_device(body.device_id, {
        "type": body.type, "text": body.message, "source": "family",
        "from": claims["sub"],
    })
    return {"ok": ok, "published": ok, "device_id": body.device_id}
