"""MQTT 下行发布器.

设备端订阅 `cane/{device_id}/cmd`，云网关向该主题发布主动下发内容。
使用官方 paho-mqtt；token 优先使用当前线程连接，与 FastAPI 同进程共用。
"""
import logging
import threading
import time

import paho.mqtt.client as mqtt

from .config import get_settings

logger = logging.getLogger(__name__)


class MqttPublisher:
    """面向"主动下发"的轻量发布器(可被 MCP push_to_device 调用)。"""

    def __init__(self, settings=None) -> None:
        s = settings or get_settings()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if s.mqtt_user:
            self._client.username_pw_set(s.mqtt_user, s.mqtt_password)
        self._client.on_connect = self._on_connect
        self._host, self._port = s.mqtt_host, s.mqtt_port
        self._topic_prefix = s.mqtt_topic_prefix
        self._lock = threading.Lock()
        self._connected = False

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self._connected = True
        logger.info("MQTT connected, rc=%s", reason_code)

    def _wait_ready(self, timeout: float = 5.0) -> bool:
        """等待 MQTT 异步连接就绪(首次发布前置等待)。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._connected:
                return True
            time.sleep(0.05)
        return False

    def connect(self) -> None:
        try:
            self._client.connect_async(self._host, self._port, keepalive=60)
            self._client.loop_start()
            if self._wait_ready():
                logger.info("MQTT connected to %s:%s", self._host, self._port)
            else:
                logger.warning("MQTT connect timeout (will auto-retry on publish)")
        except Exception as exc:  # 不阻塞网关启动
            logger.warning("MQTT connect failed, will publish fail: %s", exc)

    def publish(self, device_id: str, message: dict) -> bool:
        """发布一条下行消息到 `cane/{device_id}/cmd`。"""
        import json

        if not self._wait_ready():
            logger.warning("MQTT not ready, drop publish")
            return False
        topic = f"{self._topic_prefix}/{device_id}/cmd"
        try:
            with self._lock:
                info = self._client.publish(topic, json.dumps(message, ensure_ascii=False), qos=1)
            info.wait_for_publish(timeout=5)
            logger.info("[MQTT→] %s :: %s", topic, message.get("type"))
            return True
        except Exception as exc:
            logger.warning("MQTT publish failed: %s", exc)
            return False

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


_publisher: MqttPublisher | None = None
_pub_lock = threading.Lock()


def get_publisher() -> MqttPublisher:
    global _publisher
    if _publisher is None:
        with _pub_lock:
            if _publisher is None:
                _publisher = MqttPublisher()
                _publisher.connect()
    return _publisher


def publish_to_device(device_id: str, message: dict) -> bool:
    return get_publisher().publish(device_id, message)