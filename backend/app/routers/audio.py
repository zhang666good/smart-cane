"""音频文件存储与静态下发(供设备 4G 拉取)。

设备下行信令携带 audio_url，设备经 HTTP GET 此处拉取 TTS 音频。
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import get_settings

router = APIRouter(prefix="/audio", tags=["audio"])


@router.post("/upload")
async def upload_audio(file: UploadFile) -> dict:
    """存入一段音频(可由 TTS 或人工制作)，返回可用于下行下发的公网 URL。"""
    s = get_settings()
    ext = Path(file.filename or "a.mp3").suffix or ".mp3"
    fid = uuid.uuid4().hex
    dest: Path = s.audio_dir / f"{fid}{ext}"
    dest.write_bytes(await file.read())
    url = f"{s.public_audio_base}/audio/{dest.name}"
    return {"ok": True, "id": fid, "filename": dest.name, "url": url}


@router.get("/{audio_id}")
async def get_audio(audio_id: str) -> FileResponse:
    """向设备提供音频文件(整段下载后播放)。防目录穿越：仅取文件名。"""
    name = Path(audio_id).name
    path: Path = get_settings().audio_dir / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="audio not found")
    return FileResponse(path)
