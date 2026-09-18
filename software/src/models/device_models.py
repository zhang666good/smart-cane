"""数据模型层.

与后端接口字段一一对应, 用于结构化解析接口返回数据.
(严格按《app开发文档》第 2 节实现)
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class SensorData:
    """传感器数据模型: 对应 GET /api/family/sensors 接口返回."""
    temperature: float = 0.0      # 温度 ℃
    humidity: float = 0.0         # 湿度 %RH
    light: float = 0              # 光照强度 Lux
    lat: Optional[float] = None   # 纬度
    lng: Optional[float] = None   # 经度
    lac: Optional[str] = None     # 基站 LAC
    cid: Optional[str] = None     # 基站 CID
    address: str = ""             # 详细地址(LBS 基站定位时为占位描述)
    battery: Optional[int] = None # 电量 %(设备未上报时为 None)
    online: bool = True           # 设备在线状态
    update_ts: str = ""           # 数据更新时间


@dataclass
class AlertItem:
    """告警事件模型: 对应 GET /api/family/alerts 接口返回."""
    id: int
    type: str        # sos / obstacle / water ...
    message: str
    handled: int     # 0未处理 1已处理
    ts: int          # 时间戳


@dataclass
class ChatMessage:
    """聊天消息模型: 语音/文字消息通用, 对应 GET /api/voice/messages."""
    id: int
    content: str
    is_from_user: bool       # True=家属发送 False=拐杖/老人端
    time_str: str
    msg_type: str = "text"   # text / voice
    audio_url: Optional[str] = None
    read: int = 0
    ts: int = 0
