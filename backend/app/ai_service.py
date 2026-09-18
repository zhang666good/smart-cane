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


def _agent_tools(role: str, device_id: str):
    """为该角色可用的 MCP 工具生成 langchain 工具, 并预绑定 device_id.

    返回 (tools, name2exec): Tools 用于 bind_tools(模型 schema),
    name2exec[name] 是 langchain Runnable, 调用时底层函数已拿到 device_id,
    避免模型需要自己填 device_id(工具签名里 device_id 已被 partial 占位, 不出现在 schema)。
    """
    import functools
    import inspect
    from langchain_core.tools import tool as lc_tool
    from .mcp_servers import _ALL

    tools, name2exec = [], {}
    for name, (fn, roles) in _ALL.items():
        if role not in roles:
            continue
        bound = fn
        if "device_id" in inspect.signature(fn).parameters:
            # 包装成真函数注入 device_id: 显式 __signature__ 去掉 device_id,
            # 因此模型 schema 不再要求填 device_id(inspect 优先用 __signature__)
            params = [p for p in inspect.signature(fn).parameters.values()
                      if p.name != "device_id"]
            new_sig = inspect.signature(fn).replace(parameters=params)

            @functools.wraps(fn)
            def bound(*args, _fn=fn, device_id=device_id, **kwargs):
                return _fn(*args, device_id=device_id, **kwargs)

            bound.__signature__ = new_sig  # type: ignore[attr-defined]
        lc = lc_tool(bound)
        tools.append(lc)
        name2exec[getattr(lc, "name", None) or bound.__name__] = lc
    return tools, name2exec


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
    """真实 LLM 调用(带 ReAct 工具循环).

    模型请求调用工具时, 实际执行 MCP 工具函数取出真实数据(get_sensors/alert_query等),
    把工具结果(ToolMessage)回传给模型, 再由模型整合成自然语言回复。
    """
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    llm = _build_llm()
    tools, name2exec = _agent_tools(role, device_id)
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

    reply = ""
    tool_calls = 0
    # 多轮工具循环: 最多 MAX_ITERS 次 推理→工具 往返
    for _ in range(MAX_AGENT_ITERS):
        resp = llm.invoke(msgs)
        msgs.append(resp)
        tcs = getattr(resp, "tool_calls", None) or []
        if not tcs:
            reply = (getattr(resp, "content", "") or "").strip() or "（无回复）"
            break
        tool_calls += len(tcs)
        for tc in tcs:
            index, name, args = tc.get("index", 0), tc.get("name", ""), tc.get("args") or {}
            exec = name2exec.get(name)
            if exec is None:
                out = f"[工具错误] 未知工具: {name}"
            else:
                try:
                    out = exec.invoke(args)
                except Exception as ex:
                    log.warning("[ai] 工具 %s 执行失败: %s", name, ex)
                    out = f"[工具错误] {ex}"
            log.info("[ai] 工具调用 %s args=%s -> %s", name, args, str(out)[:200])
            msgs.append(ToolMessage(
                content=str(out),
                tool_call_id=tc.get("id", f"call-{index}"),
            ))
    else:
        reply = "（我暂时无法完成该操作，请再问一次）"

    _persist(device_id, role, text, reply)
    return ChatResult(text=reply, tool_calls=tool_calls)


MAX_AGENT_ITERS = 6


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