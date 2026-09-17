"""AI 对话层(核心): LangChain 接入大模型(OpenAI 兼容接口) + 三角色提示词.

- CHAT_PROMPTS: elder / family / admin 三套系统提示词(内容暂为占位, 后续补齐)
- chat(role, ...): 组装 SystemMessage(按角色) + 历史 + 用户文本, 调大模型;
  模型已 bind 该角色可用的 MCP 工具函数(本轮先绑定+日志占位, 真执行后续接)
- text_reply(role, ...): AI 回复文本 -> 百度TTS(wav) -> transcode 转 AMR -> 存盘返公网URL

未配置 llm_base_url/api_key 或未安装 langchain 时返回 mock 占位, 保持现网可跑。
"""
import base64
import json
import logging
import os
import uuid
from dataclasses import dataclass, field

from .config import get_settings
from . import db

log = logging.getLogger("cane.backend.ai")

# 三角色系统提示词(占位; 内容稍后由需求方补齐)
CHAT_PROMPTS: dict[str, str] = {
    "elder": "你是智能拐杖的AI陪伴助手，负责与老人进行日常语音对话，语气亲切耐心，关心老人健康与安全，回答简洁口语化（短语音输出）。",
    "family": "你是智能拐杖的家人端助理，协助家人了解老人状态、留言下发与日常照护，回答面向家人、注重信息清晰与行动建议。",
    "admin": "你是智能拐杖的管理员助手，协助平台运营与设备运维，回答面向管理员、注重数据与管控口径。",
}


@dataclass
class ChatResult:
    text: str = ""
    action: str = "speak_only"      # speak_only / forward_family / alert_admin / sos
    mock: bool = False
    tool_calls: int = 0
    error: str = ""


# ---- LangChain 惰性导入(未安装/未配置时走 mock) ----
def _langchain_ok() -> bool:
    try:
        import langchain_core  # noqa: F401
        import langchain_openai  # noqa: F401
        return True
    except Exception:
        return False


def _llm_configured() -> bool:
    s = get_settings()
    return bool(s.llm_base_url and s.llm_api_key and s.llm_model)


def _build_llm():
    """按 settings 构建 OpenAI 兼容 ChatOpenAI。"""
    from langchain_openai import ChatOpenAI
    s = get_settings()
    return ChatOpenAI(
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        model=s.llm_model,
        temperature=s.llm_temperature,
        timeout=60,
    )


def _llm_tools(role: str):
    """把该角色可用的 MCP 工具函数转成 LangChain Runnable 工具供模型 bind.

    mcp_servers._tools_for(role) 已按角色过滤; 这里取底层函数转 langchain tool.
    本轮只做绑定与调用方日志占位, 真实 tool 循环执行后续接入(见 chat) 。
    """
    from langchain_core.tools import tool as lc_tool
    from .mcp_servers import _tools_for
    bound = []
    for t in _tools_for(role):
        fn = getattr(t, "fn", None)
        if fn is None or not callable(fn):
            continue
        try:
            bound.append(lc_tool(fn))
        except Exception as e:
            log.warning("[ai] 工具 %s 绑定失败: %s", getattr(t, "name", "?"), e)
    return bound


def chat(role: str, text: str, device_id: str,
         history: list[dict] | None = None) -> ChatResult:
    """AI 对话: text -> 大模型回复文本。未配置/未安装时返回 mock 占位。"""
    if _llm_configured() and _langchain_ok():
        try:
            return _chat_llm(role, text, device_id, history)
        except Exception as e:
            log.exception("[ai] LLM 调用异常, 回退 mock: %s", e)
            return ChatResult(text="（服务繁忙，请稍后再试）", error=str(e))
    # mock 占位: 结构可跑, 待配置 llm 后走真实
    reply = "（AI 未配置）你好，" + text
    _persist(device_id, role, text, reply)
    return ChatResult(text=reply, mock=True)


def _chat_llm(role: str, text: str, device_id: str,
              history: list[dict] | None) -> ChatResult:
    """真实 LLM 调用(带工具绑定)。"""
    from langchain_core.messages import HumanMessage, SystemMessage
    llm = _build_llm()
    tools = _llm_tools(role)
    if tools:
        llm = llm.bind_tools(tools)

    msgs: list = [SystemMessage(content=CHAT_PROMPTS.get(role, CHAT_PROMPTS["elder"]))]
    for h in (history if history is not None else db.recent_messages(device_id, 20)):
        r = (h or {}).get("role", "")
        c = (h or {}).get("content", "")
        if r == "assistant" and c:
            msgs.append(SystemMessage(content=str(c)))
        elif r == "user" and c:
            msgs.append(HumanMessage(content=str(c)))
    msgs.append(HumanMessage(content=text))

    resp = llm.invoke(msgs)
    reply = (getattr(resp, "content", "") or "").strip() or "（无回复）"
    _persist(device_id, role, text, reply)
    return ChatResult(text=reply, tool_calls=len(resp.tool_calls or []) if tools else 0)


def _persist(device_id: str, role: str, user_text: str, assistant_text: str) -> None:
    """写会话历史(user/assistant)。"""
    try:
        db.add_message(device_id, "user", user_text)
        db.add_message(device_id, "assistant", assistant_text)
    except Exception as e:
        log.warning("会话持久化失败: %s", e)


# ---------------------------------------------------------------- 回复音频
def _to_amr(wav_bytes: bytes) -> bytes | None:
    """WAV -> AMR(8k 单声道), 复用独立 ffmpeg 转码容器; 失败返回 None。"""
    import urllib.request
    s = get_settings()
    payload = json.dumps({
        "in_format": "wav", "out_format": "amr",
        "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
    }).encode("utf-8")
    req = urllib.request.Request(
        s.transcode_url + "/transcode", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        if res.get("ok") and res.get("audio_b64"):
            log.info("[ai] WAV->AMR 转换成功")
            return base64.b64decode(res["audio_b64"])
        log.warning("[ai] transcode not ok: %s", res.get("msg"))
        return None
    except Exception as e:
        log.warning("[ai] transcode except: %s", e)
        return None


def text_reply(role: str, device_id: str, text: str) -> str | None:
    """AI 回复文本 -> 百度TTS(wav) -> 转AMR -> 存盘返公网URL; 失败返回 None。"""
    from .tts_client import baidu_tts
    wav = baidu_tts(text, aue=3)   # 3=wav
    if not wav:
        log.warning("[ai] TTS 失败, 无法生成回复音频")
        return None
    amr = _to_amr(wav)
    data = amr if amr else wav
    ext = ".amr" if amr else ".wav"
    s = get_settings()
    os.makedirs(s.audio_dir, exist_ok=True)
    fname = uuid.uuid4().hex + ext
    fpath = s.audio_dir / fname
    with open(fpath, "wb") as f:
        f.write(data)
    url = f"{s.public_audio_base}/media/{fname}"
    log.info("[ai] reply audio 已生成: %s (%dB)", fname, len(data))
    return url