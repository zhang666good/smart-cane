"""家人语音留言双向异步链路.

- 家人 -> 设备: POST 上传语音(存储+入库) -> 经 dialog_service 高优先级下发 -> 设备播放
- 设备 -> 家人: 老人回复录音上传 -> 存为 is_reply -> 家人 App 拉取
"""
import os
import struct
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import db, dialog_service
from ..config import get_settings
from ..security import (require_role, require_scope,
                        SCOPE_PUSH_OWN, SCOPE_SENSORS_FULL)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# 上传目录(正式应接对象存储 / 音频服务)
MEDIA_DIR = get_settings().audio_dir
PUBLIC_AUDIO_BASE = get_settings().public_audio_base


def _audio_ext(upload: UploadFile) -> str:
    """依据上传文件名/Content-Type 推断音频扩展名, 默认 .wav(本系统统一格式)."""
    ct = (upload.content_type or "").lower()
    # 上传文件名带扩展名优先(如 XXX.wav / XXX.amr)
    name = (getattr(upload, "filename", "") or "")
    name_ext = os.path.splitext(name)[1].lower()
    if name_ext in (".wav", ".amr", ".mp3", ".aac", ".m4a", ".opus", ".pcm"):
        return name_ext
    # 兜底按 Content-Type
    if "amr" in ct:
        return ".amr"
    if "wav" in ct or "x-wav" in ct:
        return ".wav"
    if "mpeg" in ct or "mp3" in ct:
        return ".mp3"
    if "aac" in ct or "mp4" in ct and "mp4a" in ct:
        return ".aac"
    return ".wav"


def _normalize_wav(fpath: str) -> str:
    """把 WAV 头规范化为标准 RIFF, 去掉 JUNK 等非标准块.

    EC200U Audio.play 只识别干净 RIFF(fmt+data 直接连续, offset 44), 遇 JUNK 块即 ret -1.
    App 端编码器(flet-audio-recorder/ffmpeg)常写入 JUNK 占位, 导致设备播放失败.
    这里用纯 Python struct 按 data 块重写头, PCM 载荷原样复制; 失败则保留原文件.
    仅对 .wav 生效, 其它格式(amr/mp3)直接返回原路径.
    """
    if not fpath.lower().endswith(".wav"):
        return fpath
    try:
        with open(fpath, "rb") as f:
            raw = f.read()
        if raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
            return fpath  # 非标准 WAV, 不动

        # 遍历 RIFF 子块, 定位 fmt 与 data (跳过 JUNK 等)
        dpos = 12
        fmt = None
        while dpos + 8 <= len(raw):
            ck = raw[dpos:dpos+4]
            (sz,) = struct.unpack("<I", raw[dpos+4:dpos+8])
            if ck == b"fmt ":
                fmt = raw[dpos+8:dpos+8+sz]
            elif ck == b"data":
                data = raw[dpos+8:dpos+8+sz]
                break
            dpos += 8 + sz + (sz & 1)
        if fmt is None or len(fmt) < 16:
            return fpath

        # 读取声道/采样率/位深(前 16 字节标准 fmt), 音频格式强制为 PCM(1)
        (_afmt, ch, rate, _bv, _bal, bits) = struct.unpack("<HHIIHH", fmt[:16])
        bytes_per_sec = rate * ch * (bits // 8)
        block_align = ch * (bits // 8)
        std_fmt = struct.pack("<HHIIHH", 1, ch, rate, bytes_per_sec, block_align, bits)
        riff_size = 4 + (8 + len(std_fmt)) + (8 + len(data))
        header = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" \\
            + b"fmt " + struct.pack("<I", len(std_fmt)) + std_fmt \\
            + b"data" + struct.pack("<I", len(data)) + data
        with open(fpath, "wb") as f:
            f.write(header)
        print(f"[voice] WAV normalized: {os.path.basename(fpath)} "
              f"ch={ch} rate={rate} bits={bits} data={len(data)}")
        return fpath
    except Exception as e:
        print(f"[voice] WAV normalize skip: {e}")
        return fpath


def _save_audio(upload: UploadFile) -> tuple[str, str]:
    """保存上传音频, 原样下发; 对 WAV 做头规范化(去 JUNK 块), 不做采样率/编解码转码.

    真机验证: EC200U 只对干净标准 WAV(无 JUNK 块, 如 44.1k/16bit 立体声) 播放正常.
    App 端编码器常写入 JUNK 块导致设备 ret -1, 故服务端用 _normalize_wav 规范头,
    但保持采样率/位深/声道/载荷不变, 不引入 ffmpeg 依赖, 无质量损失.
    返回 (path, url).
    """
    os.makedirs(MEDIA_DIR, exist_ok=True)
    ext = _audio_ext(upload)
    fname = f"{uuid.uuid4().hex}{ext}"
    fpath = os.path.join(MEDIA_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(upload.file.read())
    _normalize_wav(fpath)
    url = f"{PUBLIC_AUDIO_BASE}/media/{os.path.basename(fpath)}"
    return fpath, url


def _audio_ext_from_name(filename: str, content_type: str) -> str:
    """从 filename(扩展名优先)/content_type 推断扩展名, 默认 .amr(设备录音)."""
    name = (filename or "")
    name_ext = os.path.splitext(name)[1].lower()
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


def _save_audio_b64(data_b64: str, filename: str = "", content_type: str = "") \\
        -> tuple[str, str]:
    """保存 base64 JSON 上传的音频(EC200U 录音). 返回 (path, url).

    设备端用 Record.start 直接录标准 WAV(8kHz)上传, 电脑/家人端通用可播,
    无需服务端转码; 仅对 wav 做头规范化兜底, 其它格式原样保留.
    """
    import base64
    os.makedirs(MEDIA_DIR, exist_ok=True)
    raw = base64.b64decode(data_b64)
    ext = _audio_ext_from_name(filename, content_type)
    fname = f"{uuid.uuid4().hex}{ext}"
    fpath = os.path.join(MEDIA_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(raw)
    _normalize_wav(fpath)
    url = f"{PUBLIC_AUDIO_BASE}/media/{os.path.basename(fpath)}"
    return fpath, url


@router.post("/send")
def voice_to_device(
    device_id: str = Form(...),
    from_role: str = Form(...),
    sender_id: str = Form(...),
    sender_role: str = Form(...),
    audio: UploadFile = File(...),
    claims=Depends(require_role("family", "admin")),
):
    """家人/管理 上传语音 -> 存入 -> 高优先级下发到设备。"""
    require_scope(claims, SCOPE_PUSH_OWN)
    path, url = _save_audio(audio)
    mid = db.add_voice_message(device_id=device_id, sender_id=sender_id,
                               sender_role=sender_role, audio_path=path,
                               audio_url=url, direction="to-device", is_reply=False)
    # 经对话模式调度: family_voice 高优先级打断 AI
    result = dialog_service.request_interrupt(
        device_id=device_id, msg_type="family_voice",
        sender_id=sender_id, sender_role=sender_role, audio_url=url,
    )
    return {"ok": True, "message_id": mid, "url": url, "dispatch": result}


@router.post("/reply")
async def elder_reply(
    request: Request,
    claims=Depends(require_role("elder", "admin")),
):
    """老人回复录音上传 -> 存为留言 -> 家人可拉取。

    兼容两种上传方式:
      (a) multipart: device_id/reply_to + audio 文件(家人端/旧客户端)
      (b) JSON:      device_id/reply_to/audio_b64/filename/content_type(EC200U,
                     QuecPython request 不支持 multipart bytes, 改走 base64 JSON)
    按 Content-Type 自动识别.
    """
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype == "application/json":
        body = await request.json()
        device_id = body.get("device_id", "")
        reply_to = body.get("reply_to", "") or ""
        audio_b64 = body.get("audio_b64", "") or ""
        if not device_id:
            raise HTTPException(status_code=400, detail="missing device_id")
        if not audio_b64:
            raise HTTPException(status_code=400, detail="no audio_b64")
        path, url = _save_audio_b64(audio_b64,
                                    body.get("filename", ""),
                                    body.get("content_type", ""))
    else:
        form = await request.form()
        device_id = form.get("device_id", "")
        audio = form.get("audio")
        if not device_id:
            raise HTTPException(status_code=400, detail="missing device_id")
        if audio is None or getattr(audio, "filename", None) is None or \\
                getattr(audio, "file", None) is None:
            raise HTTPException(status_code=400, detail="missing audio file")
        path, url = _save_audio(audio)
    mid = db.add_voice_message(device_id=device_id, sender_id=claims["sub"],
                               sender_role="elder", audio_path=path,
                               audio_url=url, direction="to-sender", is_reply=True)
    return {"ok": True, "message_id": mid, "url": url,
            "sender_id": claims["sub"], "device_id": device_id}


@router.get("/messages")
def incoming_replies(
    device_id: str,
    unread_only: bool = False,
    limit: int = 20,
    claims=Depends(require_role("family", "admin")),
):
    """家人拉取(sender 收到的)老人回复留言。"""
    msgs = db.list_voice_messages(device_id, limit=limit, unread_only=unread_only)
    return {"ok": True, "count": len(msgs), "messages": msgs}


@router.post("/read")
def mark_read(msg_id: int, claims=Depends(require_role("family", "admin"))):
    db.mark_voice_read(msg_id)
    return {"ok": True, "message_id": msg_id}
