"""家人端 backend API 客户端.

均带 Bearer token 鉴权; 语音上传用 multipart, 与 backend /api/voice/* 契约一致.
"""
import os
import requests

from config import settings
from my_logger import get_logger

logger = get_logger("cane.app.api")


class BackendError(Exception):
    pass


class BackendClient:
    def __init__(self, base=None):
        self.base = (base or settings.http_base).rstrip("/")
        self.token = None

    # ---------- 认证 ----------
    def login(self, user_id, password):
        logger.info("login attempt user=%s -> %s/api/auth/token", user_id, self.base)
        r = requests.post(
            self.base + "/api/auth/token",
            json={"user_id": user_id, "password": password},
            timeout=8,
        )
        data = r.json()
        if r.status_code != 200:
            logger.warning("login FAILED user=%s status=%s detail=%s",
                           user_id, r.status_code, data.get("detail"))
            raise BackendError(data.get("detail", "登录失败"))
        self.token = data["token"]
        logger.info("login OK user=%s role=%s token_len=%s",
                    user_id, data.get("role"), len(data["token"]))
        return data

    def _hd(self):
        if not self.token:
            raise BackendError("未登录")
        return {"Authorization": "Bearer " + self.token}

    # ---------- 家人 ----------
    def my_devices(self):
        logger.debug("GET %s/api/family/devices", self.base)
        r = requests.get(self.base + "/api/family/devices", headers=self._hd(), timeout=8)
        if r.status_code != 200:
            logger.error("my_devices FAIL status=%s body=%s", r.status_code, r.text)
            raise BackendError(r.json().get("detail", r.text))
        raw = r.json().get("devices", [])
        logger.info("my_devices OK count=%s", len(raw))
        return [{"device_id": x} if isinstance(x, str) else x for x in raw]

    def bind_device(self, device_id):
        logger.info("bind_device device=%s -> %s/api/family/bind", device_id, self.base)
        r = requests.post(
            self.base + "/api/family/bind", headers=self._hd(),
            json={"device_id": device_id}, timeout=8,
        )
        if r.status_code != 200:
            logger.error("bind_device FAIL device=%s status=%s body=%s",
                         device_id, r.status_code, r.text)
            raise BackendError(r.json().get("detail", r.text))
        body = r.json()
        logger.info("bind_device OK device=%s created=%s already=%s",
                    body.get("device_id"), body.get("created"), body.get("already_bound"))
        return body

    def sensors(self, device_id):
        logger.debug("GET %s/api/family/sensors device_id=%s", self.base, device_id)
        r = requests.get(
            self.base + "/api/family/sensors", headers=self._hd(),
            params={"device_id": device_id}, timeout=8,
        )
        if r.status_code != 200:
            logger.error("sensors FAIL device=%s status=%s body=%s",
                         device_id, r.status_code, r.text)
            raise BackendError(r.json().get("detail", r.text))
        body = r.json()
        s = body.get("sensors")
        # 后端 latest_sensors 形如 {'data': {数值字段}, 'ts': ...}，解包一层供上层直接用
        if isinstance(s, dict) and "data" in s and isinstance(s["data"], dict):
            s = s["data"]
        logger.info("sensors OK device=%s data=%s", device_id, s)
        return s

    def send_message(self, device_id, message, mtype="speak"):
        logger.info("send_message device=%s type=%s text=%r", device_id, mtype, message)
        r = requests.post(
            self.base + "/api/family/message", headers=self._hd(),
            json={"device_id": device_id, "message": message, "type": mtype},
            timeout=8,
        )
        if r.status_code != 200:
            logger.error("send_message FAIL device=%s status=%s body=%s",
                         device_id, r.status_code, r.text)
            raise BackendError(r.json().get("detail", r.text))
        ok = r.json().get("ok", False)
        logger.info("send_message OK device=%s type=%s ok=%s", device_id, mtype, ok)
        return ok

    # ---------- 语音留言 ----------
    def send_voice(self, device_id, sender_id, audio_path):
        logger.info("send_voice device=%s sender=%s file=%s", device_id, sender_id, audio_path)
        with open(audio_path, "rb") as f:
            files = {"audio": (os.path.basename(audio_path), f, "application/octet-stream")}
            data = {
                "device_id": device_id,
                "from_role": "family",
                "sender_id": sender_id,
                "sender_role": "family",
            }
            r = requests.post(
                self.base + "/api/voice/send", headers=self._hd(),
                files=files, data=data, timeout=15,
            )
        if r.status_code != 200:
            logger.error("send_voice FAIL device=%s status=%s body=%s",
                         device_id, r.status_code, r.text)
            raise BackendError(r.json().get("detail", r.text))
        logger.info("send_voice OK device=%s body=%s", device_id, r.json())
        return r.json()

    def voice_messages(self, device_id, unread_only=False):
        logger.debug("GET %s/api/voice/messages device=%s unread=%s",
                     self.base, device_id, unread_only)
        r = requests.get(
            self.base + "/api/voice/messages", headers=self._hd(),
            params={"device_id": device_id, "unread_only": unread_only}, timeout=8,
        )
        if r.status_code != 200:
            logger.error("voice_messages FAIL status=%s body=%s", r.status_code, r.text)
            raise BackendError(r.json().get("detail", r.text))
        msgs = r.json().get("messages", [])
        logger.info("voice_messages OK device=%s count=%s", device_id, len(msgs))
        return msgs

    def mark_read(self, msg_id):
        logger.debug("mark_read msg_id=%s", msg_id)
        requests.post(
            self.base + "/api/voice/read", headers=self._hd(),
            params={"msg_id": msg_id}, timeout=8,
        )

    # ---------- 音频 ----------
    def download_audio(self, url: str, cache_dir: str, filename: str | None = None):
        """下载老人音频到本地缓存, 返回本地路径; 失败 raise BackendError."""
        from pathlib import Path
        os.makedirs(cache_dir, exist_ok=True)
        # audio_url 可能是相对 /media/xx 或完整 URL
        if url.startswith("/"):
            url = self.base.rstrip("/") + url
        fname = filename or os.path.basename(url.split("?")[0]) or "voice.bin"
        dest = str(Path(cache_dir) / fname)
        logger.info("download_audio -> %s", dest)
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            logger.error("download_audio FAIL status=%s url=%s", r.status_code, url)
            raise BackendError(f"音频拉取失败 status={r.status_code}")
        with open(dest, "wb") as f:
            f.write(r.content)
        logger.info("download_audio OK file=%s bytes=%s", dest, len(r.content))
        return dest
