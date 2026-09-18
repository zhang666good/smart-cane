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
    """观测某绑定设备的最新传感(家人需已绑定该设备)。

    附带在线状态: 最近 120s 内有上报视为在线。
    """
    if claims["role"] == "admin":
        require_scope(claims, SCOPE_SENSORS)
    else:
        if not db.is_bound(claims["sub"], device_id):
            raise HTTPException(status_code=403, detail="device not bound")
        require_scope(claims, SCOPE_SENSORS)
    latest = db.latest_sensors(device_id)
    import time
    last_ts = (latest or {}).get("ts")
    online = bool(last_ts and time.time() - last_ts <= 120)
    return {"ok": True, "device_id": device_id,
            "found": latest is not None, "sensors": latest,
            "online": online, "last_report_ts": last_ts}


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


def _guard_bound_device(device_id: str, claims: dict) -> None:
    """非 admin 需已绑定目标设备; 对读操作要求 sensors scope。"""
    if claims["role"] == "admin":
        pass
    elif not db.is_bound(claims["sub"], device_id):
        raise HTTPException(status_code=403, detail="device not bound")
    require_scope(claims, SCOPE_SENSORS)


@router.get("/alerts")
def family_alerts(device_id: str, limit: int = 30,
                  claims=Depends(require_family)):
    """查询绑定设备的告警(SOS/跌倒/光线不足)。"""
    _guard_bound_device(device_id, claims)
    alerts = db.list_alerts(device_id, limit=limit)
    return {"ok": True, "device_id": device_id,
            "count": len(alerts), "alerts": alerts}


@router.get("/user")
def family_user_get(device_id: str, claims=Depends(require_family)):
    """读取老人画像(称谓/病史/偏好/忌口)。"""
    _guard_bound_device(device_id, claims)
    u = db.get_user(device_id)
    return {"ok": True, "device_id": device_id,
            "found": u is not None, "user": u}


class UserSetBody(BaseModel):
    device_id: str
    nickname: str | None = None
    profile: dict | None = None


@router.post("/user")
def family_user_set(body: UserSetBody, claims=Depends(require_family)):
    """更新老人画像(称谓/病史/偏好/忌口)。"""
    _guard_bound_device(body.device_id, claims)
    u = db.upsert_user(body.device_id, nickname=body.nickname,
                       profile=body.profile)
    return {"ok": True, "user": u}


# ---------------- 告警处理 ----------------
class AlertHandleBody(BaseModel):
    alert_id: int
    remark: str = ""


@router.post("/alerts/handle")
def family_alert_handle(body: AlertHandleBody, claims=Depends(require_family)):
    """家属标记告警已处理(可附备注, 如"已电话联系确认安全")。

    校验: 告警须属于家人绑定的设备。
    """
    alert = db.get_alert(body.alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    _guard_bound_device(alert["device_id"], claims)
    updated = db.mark_alert_handled(body.alert_id, body.remark)
    db.log_operation(claims["sub"], "alert_handle", str(body.alert_id),
                     {"remark": body.remark, "device_id": alert["device_id"]})
    return {"ok": True, "alert_id": body.alert_id,
            "handled": (updated or {}).get("handled", 1),
            "remark": body.remark}


# ---------------- 历史数据 ----------------
def _parse_ts(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"bad ts: {v}")


@router.get("/position/history")
def family_position_history(device_id: str, start_ts: str | None = None,
                            end_ts: str | None = None, limit: int = 1000,
                            claims=Depends(require_family)):
    """历史轨迹: 从传感历史中提取时序 GPS/LBS 点位(地图轨迹回放数据源)。"""
    _guard_bound_device(device_id, claims)
    rows = db.sensor_history(device_id, limit=limit,
                             start_ts=_parse_ts(start_ts),
                             end_ts=_parse_ts(end_ts))
    points = []
    for r in rows:
        d = r.get("data") or {}
        pos = d.get("position") or d.get("lbs")
        if pos and (pos.get("lat") or pos.get("lng") or pos.get("lac")):
            points.append({"ts": r["ts"], **pos})
    return {"ok": True, "device_id": device_id, "count": len(points),
            "points": points}


@router.get("/sensors/history")
def family_sensors_history(device_id: str, start_ts: str | None = None,
                           end_ts: str | None = None, limit: int = 200,
                           claims=Depends(require_family)):
    """传感历史时序(温湿度/光照趋势, AI 环境分析数据源), 时间升序。"""
    _guard_bound_device(device_id, claims)
    rows = db.sensor_history(device_id, limit=limit,
                             start_ts=_parse_ts(start_ts),
                             end_ts=_parse_ts(end_ts))
    return {"ok": True, "device_id": device_id, "count": len(rows),
            "records": [{"ts": r["ts"], "data": r["data"]} for r in rows]}


# ---------------- 今日动态(业务事件) ----------------
@router.get("/events")
def family_events(device_id: str, limit: int = 50,
                  start_ts: str | None = None,
                  claims=Depends(require_family)):
    """业务事件列表(障碍物预警/积水提醒/位置更新等, 首页「今日动态」)。"""
    _guard_bound_device(device_id, claims)
    events = db.list_events(device_id, limit=limit,
                            start_ts=_parse_ts(start_ts))
    return {"ok": True, "device_id": device_id, "count": len(events),
            "events": events}


# ---------------- 设备配置下发 ----------------
class DeviceConfigBody(BaseModel):
    device_id: str
    config: dict


@router.post("/device/config")
def family_device_config(body: DeviceConfigBody, claims=Depends(require_family)):
    """家人远程下发拐杖配置(障碍阈值/紧急联系人/倒计时等)。

    内部转为 config 类型 push 消息投递设备(MQTT 优先, poll 队列兜底)。
    """
    if claims["role"] != "admin" and not db.is_bound(claims["sub"], body.device_id):
        raise HTTPException(status_code=403, detail="device not bound")
    require_scope(claims, SCOPE_PUSH_OWN)
    msg = {"type": "config", "source": "family", "from": claims["sub"],
           "payload": body.config}
    ok = publish_to_device(body.device_id, msg)
    db.log_operation(claims["sub"], "family_device_config", body.device_id,
                     {"config": body.config, "published": bool(ok)})
    return {"ok": ok, "device_id": body.device_id, "config": body.config}