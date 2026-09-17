"""百度语音合成(TTS)客户端: 文本 -> audio bytes(默认 mp3).

使用 tsn.baidu.com/text2audio, 与 ASR 共用 AK/SK 换取的 access_token.
与官方样板一致: POST 表单, tex(文本)+tok+cuid+ctp=1+lan=zh+spd/pit/vol/per+aue.
成功返回音频字节(缺省 mp3); 失败(未配置/网络/err_no!=0)打印告警返回 None.
调用建议: FastAPI 侧用 run_in_threadpool 包裹避免阻塞事件循环.
"""
import logging

import requests

from .asr_client import get_token
from .config import get_settings

log = logging.getLogger("cane.backend.tts")

TTS_URL = "https://tsn.baidu.com/text2audio"

# 百度用 Content-Type 区分错误(JSON)与音频; 音频的 media type 前缀
_AUDIO_CT = ("audio/", "application/octet-stream")


def baidu_tts(text: str, aue: int | None = None, timeout_s: float = 30.0) -> bytes | None:
    """文本 -> 音频字节; 失败返回 None. aue: 3=wav, 6=mp3, None=取配置默认."""
    if not text or not str(text).strip():
        log.warning("百度 TTS: 空文本")
        return None
    token = get_token()
    if not token:
        return None

    s = get_settings()
    params = {
        "tex": str(text),
        "tok": token,
        "cuid": s.baidu_cuid,
        "ctp": "1",
        "lan": "zh",
        "spd": s.baidu_tts_spd,
        "pit": s.baidu_tts_pit,
        "vol": s.baidu_tts_vol,
        "per": s.baidu_tts_per,
        "aue": aue if aue is not None else s.baidu_tts_aue,
    }
    try:
        r = requests.post(
            TTS_URL,
            data=params,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout_s,
        )
    except Exception as e:
        log.warning("百度 TTS 调用 except: %s", e)
        return None

    ctype = (r.headers.get("Content-Type") or "").lower()
    body = r.content
    if not body:
        log.warning("百度 TTS: 空响应")
        return None
    # 成功=音频 Content-Type; 失败=JSON(err_no) 或纯文本错误码
    try:
        j = r.json()
        if isinstance(j, dict) and j.get("err_no", 0) != 0:
            log.warning("百度 TTS 失败: err_no=%s err_msg=%s",
                        j.get("err_no"), j.get("err_msg"))
            return None
    except ValueError:
        pass  # 非 JSON -> 下面是音频或文本错误码
    if not any(ctype.startswith(t) for t in _AUDIO_CT):
        log.warning("百度 TTS: 响应非音频, Content-Type=%s body前200=%r",
                    ctype, body[:200])
        return None
    return body