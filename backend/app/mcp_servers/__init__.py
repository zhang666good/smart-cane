"""向 Coze 暴露的 MCP 服务器(基于 mcp SDK 2.x 的 MCPServer)。

多智能体隔离:三角色(elder/family/admin)各持一个 MCPServer,只注册该角色
允许的工具子集,挂载到不同端点。Coze 三个 Bot "添加 MCP 服务" 时分别接入:
  - /mcp/elder   (老人 Bot, 本人陪护/求助/基础对话)
  - /mcp/family  (家人 Bot, 观测传感/关怀留言/画像维护)
  - /mcp/admin   (管理员 Bot, 全量 + 远程运维/运营管控 + 留痕)

与架构文档 6.2(e) 权限矩阵对应(以"server 端点隔离"实现工具 subset 放行)。
"""
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.tools import Tool

from .. import db
from ..mqtt_publisher import publish_to_device
from .. import dialog_service


# ---------------- 共享工具实现 ----------------
def get_sensors(device_id: str) -> dict:
    """读取指定设备的最近一条实时传感数据(三轴/光敏/温湿/定位/可选超声水位)。"""
    latest = db.latest_sensors(device_id)
    if latest is None:
        return {"device_id": device_id, "found": False, "data": None}
    return {"device_id": device_id, "found": True, **latest}


def get_history(device_id: str, limit: int = 50) -> dict:
    """读取设备的历史传感数据(用于趋势判断,如跌倒、长时间无交互)。"""
    rows = db.sensor_history(device_id, limit=limit)
    return {"device_id": device_id, "count": len(rows), "samples": rows}


def user_get(device_id: str) -> dict:
    """读取用户画像(称谓/病史/偏好等)。"""
    u = db.get_user(device_id)
    return {"device_id": device_id, "found": u is not None, "user": u}


def user_set(device_id: str, nickname: str | None = None,
             profile: dict | None = None) -> dict:
    """新建/更新用户画像(称谓、健康信息、偏好)。"""
    u = db.upsert_user(device_id, nickname=nickname, profile=profile)
    return {"ok": True, "user": u}


def record_add(device_id: str, role: str, content: str) -> dict:
    """写入一条会话/记忆记录(role: user|assistant)。"""
    mid = db.add_message(device_id, role, content)
    return {"ok": True, "message_id": mid, "device_id": device_id}


def record_query(device_id: str, limit: int = 20) -> dict:
    """查询设备最近会话/记忆记录(用于多轮对话上下文)。"""
    msgs = db.recent_messages(device_id, limit=limit)
    return {"device_id": device_id, "count": len(msgs), "messages": msgs}


def alert_insert(device_id: str, type: str, message: str) -> dict:
    """写入一条安全告警(如跌倒/求救/光线不足)。"""
    aid = db.add_alert(device_id, type, message)
    return {"ok": True, "alert_id": aid, "device_id": device_id, "type": type}


def alert_query(device_id: str | None = None, limit: int = 30) -> dict:
    """查询告警(家人可看绑定设备;管理员可全量)。"""
    alerts = db.list_alerts(device_id, limit=limit)
    return {"ok": True, "count": len(alerts), "alerts": alerts}


def alert_mark_handled(alert_id: int) -> dict:
    """将告警标记为已处理(管理台)。"""
    return {"ok": True, "alert_id": alert_id, "note": "mark handled (placeholder)"}


def push_to_device(device_id: str, text: str = "", audio_url: str = "",
                   type: str = "speak", sender_role: str = "admin",
                   payload: dict | None = None) -> dict:
    """主动向设备下发指令。

    高优先级类型(family_voice / admin_tts / family_tts)走对话模式调度:
    可打断当前 AI 对话并置顶,或加入插播队列。普通类型直接经 MQTT 发布。
    """
    if type in ("family_voice", "admin_tts", "family_tts"):
        res = dialog_service.request_interrupt(
            device_id=device_id,
            msg_type=type,
            sender_id="aibot",
            sender_role=sender_role,
            audio_url=audio_url,
            text=text or "",
        )
        return {"ok": True, "device_id": device_id, "message": res["message"],
                "action": res["action"], "dialog": True}
    msg = {
        "type": type,
        "text": text,
        "audio_url": audio_url,
        **({"payload": payload} if payload else {}),
    }
    ok = publish_to_device(device_id, msg)
    return {"ok": ok, "device_id": device_id, "message": msg, "mqtt_published": ok}


def device_control(device_id: str, action: str, message: str | None = None,
                   payload: dict | None = None) -> dict:
    """远程控制设备(运维/升级/运营播报/改设置),并写入操作留痕。仅管理员。"""
    msg = {"type": "config", "action": action}
    if message:
        msg["text"] = message
    if payload:
        msg["payload"] = payload
    ok = publish_to_device(device_id, msg)
    db.log_operation(operator="admin-aibot", action=f"device_control:{action}",
                     target=device_id, detail={"ok": ok})
    return {"ok": ok, "action": action, "device_id": device_id}


def device_list() -> dict:
    """返回设备账号概览(管理员)。"""
    return {"ok": True, "note": "device list (placeholder)"}


def get_conversation_mode(device_id: str) -> dict:
    """查询设备当前的对话模式与插播队列(老人正在与 ai / family / admin 对话)。"""
    m = dialog_service._ensure_mode(device_id)
    return {"device_id": device_id, "curr_mode": m["curr_mode"],
            "queue": m["queue"], "is_busy": bool(m["queue"])}


def switch_conversation_mode(device_id: str, mode: str) -> dict:
    """切换对话模式为 ai / family / admin。

    遵循"最新操作生效",广播 MQTT mode_switch 到设备端,其余端收到提示。
    """
    if mode not in ("ai", "family", "admin"):
        return {"ok": False, "error": f"invalid mode: {mode}, must be ai|family|admin"}
    dialog_service._broadcast_mode(device_id, mode, changed_by="aibot",
                                   reason="平台切换")
    return {"ok": True, "device_id": device_id, "curr_mode": mode}


def get_voice_messages(device_id: str, unread_only: bool = False,
                       limit: int = 20) -> dict:
    """拉取老人的语音留言(家人可查绑定设备;管理员可全量)。"""
    msgs = db.list_voice_messages(device_id, limit=limit, unread_only=unread_only)
    return {"ok": True, "count": len(msgs), "messages": msgs}


def send_family_voice(device_id: str, audio_url: str,
                      text: str = "", sender_role: str = "family") -> dict:
    """给老人设备下发家人语音留言,经对话模式高优先级打断/插播。"""
    res = dialog_service.request_interrupt(
        device_id=device_id, msg_type="family_voice", sender_id="aibot",
        sender_role=sender_role, audio_url=audio_url, text=text or "",
    )
    return {"ok": True, "device_id": device_id, "message": res["message"],
            "action": res["action"], "dialog": True}



# ---------------- 工具注册表: name -> (func, 允许角色) ----------------
_ALL = {
    "get_sensors": (get_sensors, ("family", "admin")),
    "get_history": (get_history, ("family", "admin")),
    "user_get": (user_get, ("elder", "family", "admin")),
    "user_set": (user_set, ("elder", "family", "admin")),
    "record_add": (record_add, ("elder", "family", "admin")),
    "record_query": (record_query, ("elder", "family", "admin")),
    "alert_insert": (alert_insert, ("elder", "admin")),
    "alert_query": (alert_query, ("family", "admin")),
    "alert_mark_handled": (alert_mark_handled, ("admin",)),
    "push_to_device": (push_to_device, ("elder", "family", "admin")),
    "device_control": (device_control, ("admin",)),
    "device_list": (device_list, ("admin",)),
    "get_conversation_mode": (get_conversation_mode, ("elder", "family", "admin")),
    "switch_conversation_mode": (switch_conversation_mode, ("family", "admin")),
    "get_voice_messages": (get_voice_messages, ("family", "admin")),
    "send_family_voice": (send_family_voice, ("family", "admin")),
}


def _tools_for(role: str) -> list:
    tools = []
    for name, (fn, roles) in _ALL.items():
        if role in roles:
            tools.append(Tool.from_function(fn))
    return tools


def _build(role: str) -> MCPServer:
    return MCPServer(
        name=f"cane-{role}",
        title=f"智能拐杖云端后端({role})",
        version="0.2.0",
        tools=_tools_for(role),
    )


elder_server = _build("elder")
family_server = _build("family")
admin_server = _build("admin")

# 兼容旧引用(全量 demo server 等同 admin)
server = admin_server

__all__ = ["elder_server", "family_server", "admin_server", "server"]