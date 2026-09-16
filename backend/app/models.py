"""设备接入网关的 Pydantic 请求/响应模型。"""
from typing import Any

from pydantic import BaseModel, Field


class SensorReport(BaseModel):
    """设备上传的传感数据(EC200U 板载 + 可选 STM32 项)))。

    code:
    {
      "device_id": "...",
      "data": {
          "accel": {"x":..,"y":..,"z":..},
          "light": 123, "temperature": 26.1, "humidity": 55.0,
          "position": {"lng":..,"lat":..},
          "optional": {"ultrasonic": 0.35, "water": 0}   # STM32 可选
      }
    }
    """

    device_id: str
    data: dict[str, Any] = Field(default_factory=dict)


class SosReport(BaseModel):
    device_id: str
    confirmed: bool = True
    message: str = "一键求救"
    position: dict[str, Any] | None = None


class AudioChatRequest(BaseModel):
    """预留：音频对话(或多用途请求)元数据。"""
    device_id: str
    message: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class PushTarget(BaseModel):
    """主动下发目标与内容。"""
    device_id: str
    type: str = "speak"          # speak | oled | beep | config | sos
    text: str | None = None
    audio_url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
