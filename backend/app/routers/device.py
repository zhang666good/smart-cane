"""设备接入路由: 传感上报 / SOS / 音频对话 / 长轮询兜底."""
import base64
import os

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import db, dialog_service
from .. import ai_service
from ..asr_client import baidu_asr
from ..config import get_settings
from ..models import AudioChatRequest, PushTarget, SensorReport, SosReport
from ..mqtt_publisher import publish_to_device
from ..tts_client import baidu_tts

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


def _audio_ext(filename: str, content_type: str) -> str:
    """从文件名/Content-Type 推断扩展名, 默认 .amr(设备录音)."""
    name_ext = os.path.splitext(filename or "")[1].lower()
    if name_ext in (".wav", ".amr", ".mp3", ".aac", ".m4a", ".opus", ".pcm"):
        return name_ext
    ct = (content_type or "").lower()
    if "amr" in ct:
        return ".amr"
    if "wav" in ct or "x-wav" in ct:
        return ".wav"
    if "mpeg" in ct or "mp3" in ct:
        return ".mp3"
    return ".amr"


def _chat_asr_blocking(audio_bytes: bytes, ext: str) -> str | None:
    """(线程池内执行) 直接送百度短语音识别: AMR(8k) 其余(wav 等 16k)."""
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio")
    in_fmt = (ext or ".amr").lstrip(".")
    rate = get_settings().baidu_rate   # 默认 16000(与官方样板一致; 设备录音实为8k如需精确可在 .env 调 BAIDU_RATE=8000)
    return baidu_asr(audio_bytes, speech_format=in_fmt, rate=rate, cuid="cane",
                     timeout_s=30)   # 失败返回 None


async def _chat_from_audio(audio_bytes: bytes, ext: str) -> str | None:
    """await-able 封装: ASR 轮询放到线程池, 避免阻塞事件循环."""
    return await run_in_threadpool(_chat_asr_blocking, audio_bytes, ext)


def _ai_reply_blocking(device_id: str, text: str) -> dict:
    """(线程池内执行) AI 回复链路: 大模型回答 -> TTS(wav)->AMR -> 存音频。

    返回 {reply, audio_url, dispatch}; text_reply/TTS 任一失败对应字段为 None。
    注意: 不调用 dialog_service.request_interrupt 推送 MQTT —— 由设备直接播放
    本接口响应携带的 audio_url 自播, 避免排队下发与本地播放重复。
    """
    result = ai_service.chat("elder", text, device_id)
    audio_url = ai_service.text_reply("elder", device_id, result.text) \
        if result.text else None
    return {"reply": result.text, "audio_url": audio_url, "dispatch": None,
            "mock": getattr(result, "mock", False)}


@router.post("/audio/chat")
async def audio_chat(request: Request, _=Depends(verify_device)):
    """音频对话: 接收设备录音(AMR, 8kHz) -> 百度短语音识别转文本(预留 Coze/TTS).

    兼容两种上传方式(与 voice.elder_reply 保持一致):
      (a) JSON:      {"device_id","audio_b64","filename","content_type"}
      (b) multipart: device_id(Form) + audio(File)
    JSON 仅含 message(旧客户端)仍回退文本占位.
    """
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype == "application/json":
        body = await request.json()
        device_id = body.get("device_id", "")
        audio_b64 = body.get("audio_b64", "") or ""
        if audio_b64:
            raw = base64.b64decode(audio_b64)
            ext = _audio_ext(body.get("filename", ""), body.get("content_type", ""))
            text = await _chat_from_audio(raw, ext)
        else:                                   # 旧占位: 纯文本消息
            mid = db.add_message(device_id, "user", body.get("message") or "(audio)")
            return {"ok": True, "message_id": mid, "text": None}
    else:                                       # multipart
        form = await request.form()
        device_id = form.get("device_id", "")
        upload = form.get("audio")
        if upload is None or getattr(upload, "filename", None) is None or \
                getattr(upload, "file", None) is None:
            raise HTTPException(status_code=400, detail="missing audio file")
        raw = upload.file.read()
        ext = _audio_ext(upload.filename or "", upload.content_type or "")
        text = await _chat_from_audio(raw, ext)

    # 接续 AI 回复: 大模型回答 -> TTS(wav)->AMR -> 存音频 -> 高优先级下发设备播放
    ai_reply = None
    if text:
        ai_reply = await run_in_threadpool(_ai_reply_blocking, device_id, text)
    return {
        "ok": text is not None,
        "text": text,
        "reply": (ai_reply or {}).get("reply"),
        "mock": (ai_reply or {}).get("mock"),
        "audio_url": (ai_reply or {}).get("audio_url"),
        "dispatch": (ai_reply or {}).get("dispatch"),
        "device_id": device_id,
    }


class TtsBody(BaseModel):
    text: str = Field(..., description="要合成的文本")


@router.post("/audio/tts")
async def audio_tts(body: TtsBody, _=Depends(verify_device)):
    """文本转语音: text -> 百度TTS audio bytes(默认 mp3). 成功返回音频流.

    供 AI 应答下发言语链路: Coze/AI 生成回复文本后调本接口合成语音,
    得到 mp3 可再经 transcode 转 AMR(8kHz) 下发设备播放.
    失败(鉴权缺失/百度err_no!=0)返回 502.
    """
    from fastapi.responses import Response
    audio = await run_in_threadpool(baidu_tts, body.text)
    if audio is None:
        raise HTTPException(status_code=502, detail="baidu tts failed")
    ext = "mp3" if get_settings().baidu_tts_aue == 6 else "wav"
    return Response(
        content=audio,
        media_type=f"audio/{ext}",
        headers={"X-Audio-Format": ext},
    )


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
