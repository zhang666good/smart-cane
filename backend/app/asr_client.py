"""百度短语音识别标准版(ASR)客户端.

设备端录音(AMR, 8kHz)直接 base64 JSON 上送百度语音识别, 拿到文本.
不再需要转 wav / 公开 URL / 火山引擎.
鉴权: 用 百度APIKey(baidu_api_key)+密钥(baidu_secret_key) 换 access_token,
token 有效期为 2592000s(30天), 模块级做简单缓存.

调用流程: get_token() -> POST http://vop.baidu.com/server_api (JSON)
使用 requests(同步); FastAPI 侧建议用 run_in_threadpool 包裹避免阻塞事件循环.
"""
import base64
import json
import logging
import time

import requests

from .config import get_settings

log = logging.getLogger("cane.backend.asr")

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
ASR_URL = "https://vop.baidu.com/server_api"   # HTTPS(用户核验可用的样板即用此地址)

# 模块级 token 缓存: {"access_token": str, "expire_at": float}
_token_cache: dict = {"access_token": None, "expire_at": 0.0}


def get_token(force: bool = False) -> str | None:
    """获取百度 access_token; 命中有效缓存直接返回, 否则换取.

    失败(未配置/网络/响应异常)打印告警并返回 None.
    """
    s = get_settings()
    ak, sk = s.baidu_api_key, s.baidu_secret_key
    if not ak or not sk:
        log.warning("百度语音 API Key/密钥未配置 (baidu_api_key/baidu_secret_key)")
        return None

    if not force and _token_cache.get("access_token"):
        # 预留少量余量, 提前 60s 视为过期
        if _token_cache.get("expire_at", 0) > time.time() + 60:
            return _token_cache["access_token"]

    try:
        r = requests.post(   # 与官方样板一致用 POST
            TOKEN_URL,
            params={
                "grant_type": "client_credentials",
                "client_id": ak,
                "client_secret": sk,
            },
            timeout=15,
        )
        data = r.json()
    except Exception as e:
        log.warning("百度 token 换取 except: %s", e)
        return None

    token = data.get("access_token")
    if not token:
        log.warning("百度 token 换取失败: %s", data)
        return None
    expires_in = int(data.get("expires_in") or 2592000)
    _token_cache["access_token"] = token
    _token_cache["expire_at"] = time.time() + expires_in
    return token


def baidu_asr(audio_bytes: bytes, speech_format: str = "amr", rate: int = 8000,
              cuid: str = "cane", timeout_s: float = 30.0) -> str | None:
    """百度短语音识别: 字节音频 -> 文本.

    speech_format: pcm/wav/amr/m4a; rate: 8000 或 16000(默认 8000=设备 AMR 真值);
    cuid: 客户端标识(建议设备id).
    失败(鉴权缺失/网络/err_no!=0)打印告警并返回 None; 成功返回 result[0].
    """
    if not audio_bytes:
        log.warning("百度 ASR: 空音频")
        return None
    token = get_token()
    if not token:
        return None

    s = get_settings()
    body = {
        "format": (speech_format or "amr").lstrip("."),
        "rate": rate or s.baidu_rate,
        "dev_pid": s.baidu_dev_pid,
        "channel": s.baidu_channel,
        "token": token,
        "cuid": cuid or s.baidu_cuid,
        "len": len(audio_bytes),
        "speech": base64.b64encode(audio_bytes).decode("ascii"),
    }
    try:
        r = requests.post(
            ASR_URL,
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
            timeout=timeout_s,
        )
        data = r.json()
    except Exception as e:
        log.warning("百度 ASR 调用 except: %s", e)
        return None

    err_no = data.get("err_no")
    if err_no != 0:
        log.warning("百度 ASR 失败: err_no=%s err_msg=%s", err_no, data.get("err_msg"))
        return None
    result = data.get("result") or []
    if not result:
        log.warning("百度 ASR: result 为空 (%s)", data.get("err_msg"))
        return None
    return str(result[0]).strip()