"""后端接口服务层.

所有与后端的 HTTP 交互集中在此处统一管理, 页面层不直接发起网络请求.
每个方法包含: 接口地址、业务场景、参数、返回值注释.
真实接口契约与云端 FastAPI 网关对齐(基地址见 config.API_CONFIG).
"""
import os
from typing import Any, Dict, List, Optional

import requests

from config import API_CONFIG, CONSTANTS
from models.device_models import AlertItem, ChatMessage, SensorData


class ApiError(Exception):
    """后端请求异常(携带可读错误信息)."""


class ApiService:
    def __init__(self):
        self.base_url = API_CONFIG["base_url"].rstrip("/")
        self.timeout = API_CONFIG["timeout"]
        self.token: Optional[str] = None   # 登录后保存的 JWT
        self.user_id = ""
        self.device_id = CONSTANTS["default_device_id"]

    # ---------------- 基础 ----------------
    def _hd(self) -> Dict[str, str]:
        """统一注入 Bearer 鉴权头."""
        if not self.token:
            raise ApiError("未登录")
        return {"Authorization": f"Bearer {self.token}"}

    def _check(self, resp) -> Dict[str, Any]:
        if resp.status_code != 200:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise ApiError(str(detail))
        return resp.json()

    # ============== 1. 认证登录接口 ==============
    def login(self, user_id: str, password: str) -> Dict[str, Any]:
        """家属端登录获取 Token.

        接口: POST /api/auth/token
        业务场景: APP 启动登录, 获取 JWT 凭证与绑定设备, 后续所有接口自动携带.
        """
        resp = requests.post(
            f"{self.base_url}/api/auth/token",
            json={"user_id": user_id, "password": password},
            timeout=self.timeout,
        )
        data = self._check(resp)
        self.token = data["token"]
        self.user_id = data.get("user_id", user_id)
        self.device_id = data.get("device_id") or self.device_id
        return data

    # ============== 2. 设备传感器数据接口 ==============
    def get_sensor_data(self) -> SensorData:
        """获取设备最新一帧传感器 + 定位数据.

        接口: GET /api/family/sensors?device_id=xxx
        业务场景: 首页实时展示温湿度、光照、定位与在线状态.
        返回: {sensors:{data:{temperature,humidity,light,position:{lat,lng,lac,cid}}, ts}, online, last_report_ts}
        """
        resp = requests.get(
            f"{self.base_url}/api/family/sensors",
            params={"device_id": self.device_id},
            headers=self._hd(),
            timeout=self.timeout,
        )
        body = self._check(resp)
        s = (body.get("sensors") or {}).get("data") or {}
        pos = s.get("position") or {}
        return SensorData(
            temperature=s.get("temperature") or 0.0,
            humidity=s.get("humidity") or 0.0,
            light=s.get("light") or 0,
            lat=pos.get("lat"),
            lng=pos.get("lng"),
            lac=str(pos.get("lac", "")) or None,
            cid=str(pos.get("cid", "")) or None,
            battery=s.get("battery"),
            online=bool(body.get("online", True)),
        )

    # ============== 3. 告警相关接口 ==============
    def get_alert_list(self, limit: int = 30) -> List[AlertItem]:
        """获取告警事件列表.

        接口: GET /api/family/alerts?device_id=&limit=
        业务场景: 消息页展示跌倒(SOS)、障碍物、积水等告警.
        """
        resp = requests.get(
            f"{self.base_url}/api/family/alerts",
            params={"device_id": self.device_id, "limit": limit},
            headers=self._hd(),
            timeout=self.timeout,
        )
        alerts = self._check(resp).get("alerts", [])
        return [
            AlertItem(id=a["id"], type=a.get("type", ""),
                      message=a.get("message", ""), handled=a.get("handled", 0),
                      ts=a.get("ts", 0))
            for a in alerts
        ]

    def mark_alert_handled(self, alert_id: int, remark: str = "") -> bool:
        """标记告警已处理.

        接口: POST /api/family/alerts/handle  body: {alert_id, remark}
        业务场景: 家属确认老人安全后, 关闭告警(告警闭环).
        """
        resp = requests.post(
            f"{self.base_url}/api/family/alerts/handle",
            json={"alert_id": alert_id, "remark": remark},
            headers=self._hd(),
            timeout=self.timeout,
        )
        return bool(self._check(resp).get("ok"))

    # ============== 4. 文字消息下发接口 ==============
    def send_text_to_device(self, text: str) -> bool:
        """家属发送文字消息给拐杖, 设备 TTS 语音朗读.

        接口: POST /api/family/message  body: {device_id, message, type:"speak"}
        业务场景: 聊天页发送文字, 拐杖自动读给老人听.
        """
        resp = requests.post(
            f"{self.base_url}/api/family/message",
            json={"device_id": self.device_id, "message": text, "type": "speak"},
            headers=self._hd(),
            timeout=self.timeout,
        )
        return bool(self._check(resp).get("published"))

    # ============== 5. 语音消息接口 ==============
    def send_voice_to_device(self, audio_file_path: str) -> Dict[str, Any]:
        """家属发送语音留言给拐杖设备.

        接口: POST /api/voice/send (multipart/form-data)
        业务场景: 聊天页录音发送, 拐杖收到后播放, 播完回执.
        """
        with open(audio_file_path, "rb") as f:
            files = {"audio": (os.path.basename(audio_file_path), f, "application/octet-stream")}
            data = {
                "device_id": self.device_id,
                "from_role": "family",
                "sender_id": self.user_id,
                "sender_role": "family",
            }
            resp = requests.post(
                f"{self.base_url}/api/voice/send",
                files=files, data=data, headers=self._hd(),
                timeout=15,
            )
        return self._check(resp)

    def get_voice_messages(self, unread_only: bool = False) -> List[ChatMessage]:
        """获取语音/文字聊天历史记录.

        接口: GET /api/voice/messages?device_id=&unread_only=
        业务场景: 进入聊天页加载历史消息, 轮询获取新消息.
        """
        resp = requests.get(
            f"{self.base_url}/api/voice/messages",
            params={"device_id": self.device_id, "unread_only": unread_only},
            headers=self._hd(),
            timeout=self.timeout,
        )
        messages = self._check(resp).get("messages", [])
        result = []
        for m in messages:
            is_from_user = m.get("direction") == "to-device" or m.get("sender_role") == "family"
            result.append(ChatMessage(
                id=m["id"],
                content=m.get("text") or "[语音留言]",
                is_from_user=is_from_user,
                time_str="",
                msg_type="voice" if m.get("audio_url") else "text",
                audio_url=m.get("audio_url"),
                read=m.get("read", 0),
                ts=m.get("ts", 0),
            ))
        return result

    def mark_voice_read(self, msg_id: int):
        """标记语音消息已读. 接口: POST /api/voice/read?msg_id="""
        requests.post(
            f"{self.base_url}/api/voice/read",
            params={"msg_id": msg_id},
            headers=self._hd(),
            timeout=self.timeout,
        )

    # ============== 6. AI 对话接口 ==============
    def ai_chat(self, text: str, session_id: str = "") -> str:
        """家属端 AI 助手对话.

        接口: POST /api/ai/chat  body: {text, session_id, device_id}
        业务场景: AI 助手页查询设备状态、告警分析、健康咨询.
        """
        resp = requests.post(
            f"{self.base_url}/api/ai/chat",
            json={"text": text, "session_id": session_id, "device_id": self.device_id},
            headers=self._hd(),
            timeout=60,
        )
        return self._check(resp).get("text", "")

    # ============== 7. GPS 历史轨迹接口 ==============
    def get_position_history(self, start_ts: int, end_ts: int, limit: int = 1000) -> List[dict]:
        """查询时间段内的 GPS 历史轨迹点.

        接口: GET /api/family/position/history?device_id=&start_ts=&end_ts=
        业务场景: 地图页轨迹回放, 绘制行走路线.
        """
        resp = requests.get(
            f"{self.base_url}/api/family/position/history",
            params={"device_id": self.device_id, "start_ts": start_ts,
                    "end_ts": end_ts, "limit": limit},
            headers=self._hd(),
            timeout=self.timeout,
        )
        return self._check(resp).get("points", [])

    # ============== 8. 今日动态事件接口 ==============
    def get_events(self, limit: int = 50) -> List[dict]:
        """获取普通业务事件(障碍物/积水等), 用于首页今日动态.

        接口: GET /api/family/events?device_id=&limit=
        """
        resp = requests.get(
            f"{self.base_url}/api/family/events",
            params={"device_id": self.device_id, "limit": limit},
            headers=self._hd(),
            timeout=self.timeout,
        )
        return self._check(resp).get("events", [])

    # ============== 9. 老人档案接口 ==============
    def get_user_profile(self) -> Dict[str, Any]:
        """读取老人档案: {device_id, nickname, profile:{medical,preference,taboo}}."""
        resp = requests.get(
            f"{self.base_url}/api/family/user",
            params={"device_id": self.device_id},
            headers=self._hd(),
            timeout=self.timeout,
        )
        return self._check(resp).get("user") or {}

    def set_user_profile(self, nickname: Optional[str] = None,
                         profile: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """写入老人档案(昵称/病史/偏好/忌口)."""
        body: Dict[str, Any] = {}
        if nickname is not None:
            body["nickname"] = nickname
        if profile is not None:
            body["profile"] = profile
        resp = requests.post(
            f"{self.base_url}/api/family/user",
            params={"device_id": self.device_id},
            json=body, headers=self._hd(),
            timeout=self.timeout,
        )
        return self._check(resp)

    # ============== 10. 设备绑定接口 ==============
    def get_devices(self) -> List[str]:
        """查询已绑定拐杖设备. 接口: GET /api/family/devices"""
        resp = requests.get(
            f"{self.base_url}/api/family/devices",
            headers=self._hd(), timeout=self.timeout,
        )
        return self._check(resp).get("devices", [])

    def bind_device(self, device_id: str) -> Dict[str, Any]:
        """绑定拐杖设备. 接口: POST /api/family/bind"""
        resp = requests.post(
            f"{self.base_url}/api/family/bind",
            json={"device_id": device_id},
            headers=self._hd(), timeout=self.timeout,
        )
        data = self._check(resp)
        self.device_id = device_id
        return data

    # ============== 11. 音频下载 ==============
    def download_audio(self, audio_url: str) -> str:
        """下载语音留言到本地缓存, 返回本地路径(用于播放)."""
        url = audio_url if audio_url.startswith("http") else f"{self.base_url}{audio_url}"
        name = os.path.basename(audio_url.split("?")[0]) or "voice.amr"
        cache_dir = CONSTANTS["audio_cache_dir"]
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, name)
        if not os.path.exists(path):
            r = requests.get(url, headers=self._hd(), timeout=15)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
        return path


# 全局单例实例
api = ApiService()
