"""设备接入路由: 传感上报 / SOS / 音频对话(预留) / 长轮询兜底."""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import db, dialog_service
from ..config import get_settings
from ..models import AudioChatRequest, PushTarget, SensorReport, SosReport
from ..mqtt_publisher import publish_to_device

router = APIRouter(prefix="/api/device", tags=["device"])


def verify_device(x_device_token: str = Header(default="")) -> None:
    """极简设备鉴权(demo)。正式可替换为签名/时效 token。"""
    if x_device_token != get_settings().device_token:
        raise HTTPException(status_code=401, detail="bad device token")


@router.post("/sensor/report")
def sensor_report(body: SensorReport, _=Depends(verify_device)):
    """接收传感数据并落库；按需对跌倒等做即时触发(Coze 侧由 MCP 再判定)。"""
    db.upsert_user(body.device_id)          # 确保用户存在
    db.add_sensor_record(body.device_id, body.data)
    # 预留: 触发 Coze 评估(可异步) —— 在此调用 coze_client.evaluate(body)
    return {"ok": True, "device_id": body.device_id}


@router.post("/sos")
def sos(body: SosReport, _=Depends(verify_device)):
    """一键求救：落库告警 + 触发下行确认/通知(可对接短信)。"""
    db.add_alert(body.device_id, "sos", body.message)
    publish_to_device(body.device_id, {"type": "sos", "text": body.message})
    return {"ok": True, "alert_id": db.list_alerts(body.device_id, 1)[0]["id"]}


@router.post("/audio/chat")
def audio_chat(body: AudioChatRequest, _=Depends(verify_device)):
    """预留：音频→文字→Coze→TTS→返回音频。此处先落库会话并回 ack。"""
    mid = db.add_message(body.device_id, "user", body.message or "(audio)")
    return {"ok": True, "message_id": mid, "reply": None, "audio_url": None}


class AckBody(BaseModel):
    device_id: str
    finished_type: str = Field(default="speak")


@router.post("/ack")
def device_ack(body: AckBody, _=Depends(verify_device)):
    """设备播完当前一条后回调: 弹出队列头部, 继续下一条或恢复 AI."""
    res = dialog_service.ack_finished(body.device_id, body.finished_type)
    return {"ok": True, **res}


@router.get("/poll")
def poll(device_id: str, _=Depends(verify_device)):
    """HTTP 长轮询兜底(MQTT 异常时): 返回待下发任务.

    仅分发"已入队但从未发布"的排队项(这些消息未被 MQTT 下发过, 不会重复播放).
    """
    nxt = db.pop_next_queue(device_id)
    pending = []
    if nxt and not nxt.get("resume"):
        pending.append(nxt)
        print_frame(nxt)
    elif nxt and nxt.get("resume"):
        # 队列头是恢复标记: 直接恢复原模式, 不回传
        db.set_conversation_mode(device_id, nxt.get("type", "ai"), "device")
    return {"ok": True, "device_id": device_id, "pending": pending}


def print_frame(msg):
    print("poll dispatching:", msg)


@router.post("/push")
def api_push(body: PushTarget, _=Depends(verify_device)):
    """网关直达设备主动下发(便于先用 HTTP 测试，不必先配 MQTT)。"""
    msg = {
        "type": body.type,
        "text": body.text,
        "audio_url": body.audio_url,
        **({"payload": body.payload} if body.payload else {}),
    }
    ok = publish_to_device(body.device_id, msg)
    return {"ok": ok, "published": msg}
