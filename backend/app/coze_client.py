"""Coze 开放接口客户端(占位/预留)。

设备音频/文本对话在此转为对 Coze 对话流或智能体 API 的调用：
  POST {coze_base_url}/v3/chat  (bot_id + user_id + stream)
接入后返回文案/指令；TTS 可在 Coze 侧或网关完成。
"""
import httpx

from .config import get_settings


class CozeClient:
    def __init__(self, settings=None) -> None:
        s = settings or get_settings()
        self.base_url, self.token, self.bot_id = (
            s.coze_base_url, s.coze_token, s.coze_bot_id)

    def chat(self, device_id: str, text: str, history: list[dict] | None = None):
        """预留：调用 Coze。尚未配置 token 时返回占位回复。"""
        if not self.token:
            return {
                "ok": True,
                "mock": True,
                "text": "（Coze 尚未配置）你好，" + (text or "我听到你了"),
            }
        # TODO: 组装 messages + bot_id 调用 /v3/chat，解析 response
        return {"ok": False, "mock": False, "error": "not implemented"}


_coze: CozeClient | None = None


def get_coze() -> CozeClient:
    global _coze
    if _coze is None:
        _coze = CozeClient()
    return _coze