"""对话模式与优先级调度服务。

云端权威状态: 每个设备一个 conversation_mode(当前模式 + 插播队列)。
优先级(由高到低): admin(admin_tts/admin_voice) > family(family_voice/admin_tts) > ai。
规则: 信息下发 > 主动 AI 对话;高优先级打断并置顶,AI 对话入队恢复。
"""
from . import db
from .mqtt_publisher import publish_to_device

# 优先级数字越小越优先
PRIORITY = {
    "admin_voice": 1,
    "admin_tts": 1,
    "family_voice": 1,
    "family_tts": 2,
    "ai": 3,
}


def _ensure_mode(device_id: str) -> dict:
    m = db.get_conversation_mode(device_id)
    if m is None:
        db.set_conversation_mode(device_id, "ai", "system")
    return db.get_conversation_mode(device_id)


def _broadcast_mode(device_id: str, mode: str, changed_by: str, reason: str = "") -> None:
    """广播模式变更到设备端(经 MQTT),并附带提示。"""
    publish_to_device(device_id, {
        "type": "mode_switch",
        "mode": mode,
        "changed_by": changed_by,
        "text": f"对话已切换到{mode}模式" + (f"（{reason}）" if reason else ""),
    })


def request_interrupt(device_id: str, msg_type: str, sender_id: str,
                      sender_role: str, audio_url: str = "",
                      text: str = "") -> dict:
    """请求高优先级下发(家人语音/管理 TTS 等)。

    若目标优先级高于当前模式则打断并置顶;否则入队。
    返回: 该条消息是立即下发(interrupt)还是入队(queued)。
    """
    mode = _ensure_mode(device_id)
    curr = mode["curr_mode"]
    incoming_pri = PRIORITY.get(msg_type, 3)
    curr_pri = PRIORITY.get(curr, 3)

    entry = {
        "type": msg_type,
        "sender_id": sender_id,
        "sender_role": sender_role,
        "audio_url": audio_url,
        "text": text,
    }

    if incoming_pri < curr_pri:
        # 高优先级: 打断,置顶下发;原模式进入队列让其播完后恢复
        db.set_conversation_mode(device_id, msg_type, sender_id,
                                 append_queue={"type": curr, "resume": True})
        publish_to_device(device_id, entry)
        _broadcast_mode(device_id, msg_type, sender_id, reason="高优先级打断")
        return {"action": "interrupt", "message": entry, "prev_mode": curr}
    else:
        # 低于或等于当前: 排队
        db.set_conversation_mode(device_id, curr, sender_id, append_queue=entry)
        return {"action": "queued", "message": entry}


def notify_ai_start(device_id: str, user_id: str = "system") -> dict:
    """老人主动发起 AI 对话: 若无更高优先级插播在执行,则切到 ai。"""
    mode = _ensure_mode(device_id)
    curr_pri = PRIORITY.get(mode["curr_mode"], 3)
    if curr_pri < 3:
        # 当前在家人/管理插播中,AI 请求让位,入队排队
        db.set_conversation_mode(device_id, mode["curr_mode"], user_id,
                                 append_queue={"type": "ai", "resume": True})
        return {"action": "queued", "note": "high-prio playback in progress"}
    db.set_conversation_mode(device_id, "ai", user_id)
    _broadcast_mode(device_id, "ai", user_id)
    return {"action": "switch_to_ai", "ok": True}


def ack_finished(device_id: str, finished_type: str, ack_by: str = "device") -> dict:
    """设备播完当前一条后回调: 弹出队列头部,继续下一个或恢复 AI。"""
    nxt = db.pop_next_queue(device_id)
    if nxt and nxt.get("resume"):
        # 恢复原模式
        db.set_conversation_mode(device_id, nxt["type"], ack_by)
        return {"action": "resume", "mode": nxt["type"]}
    if nxt:
        publish_to_device(device_id, nxt)
        return {"action": "next", "message": nxt}
    # 队列空,回落到 idle/AI
    db.set_conversation_mode(device_id, "ai", ack_by)
    return {"action": "idle", "mode": "ai"}