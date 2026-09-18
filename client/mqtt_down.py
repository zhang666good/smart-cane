"""MQTT 下行订阅: 云端主动下发处理.

订阅主题: {prefix}/{device_id}/cmd
下行指令 type (与后端 dialog_service / mcp push_to_device 一致):
  speak        -> 文本TTS 播报(本地合成或拉音频)
  family_voice -> 家人语音(拉 audio_url 播放)
  admin_tts    -> 管理端TTS 播报
  family_tts   -> 家人文字TTS 播报
  ai           -> AI对话播报(低优先级)
  mode_switch  -> 对话模式切换(ai / family / admin)
  beep / oled / config / sos -> 其它 I/O

收到后调用注册的 handler 处理; 播完通过 HTTP 回执 ack_finished.
"""
import ujson
import _thread
import utime as time

from umqtt import MQTTClient

from usr.libs.config import get as cfg_get


class MqttDown:
    """MQTT 下行订阅. 采用官方文档方案: 独立线程阻塞 wait_msg() 收消息.

    umqtt 回调在 wait_msg() 内触发, 由 MQTTClient 构造参数 reconn=True(默认) 处理自动重连.
    """
    def __init__(self, handler=None, worker_available=None):
        """
        handler: function(cmd_dict) -> None  处理具体下行指令(播放/切换/IO)
        worker_available: function() -> bool  是否可立即处理(播放空闲), 用于高优先级判断
        """
        self.handler = handler
        self.worker_available = worker_available
        self.client = None
        self._connected = False
        self._thread_on = False

    @property
    def topic(self):
        return "%s/%s/cmd" % (cfg_get("mqtt_topic_prefix"), cfg_get("device_id"))

    def _on_msg(self, topic, data):
        """MQTT 回调(topic, data bytes)."""
        try:
            msg = ujson.loads(data.decode())
        except Exception as e:
            print("bad mqtt payload:", e, data)
            return
        print("MQTT recv:", topic, msg.get("type"))
        if self.handler:
            try:
                self.handler(msg)
            except Exception as e:
                print("handler error:", e)

    def _recv_loop(self):
        """独立线程入口: 阻塞 wait_msg() 持续接收下行, 断开则重连续跑."""
        while self._thread_on:
            try:
                if not self.is_connected():
                    self._connect()
                self.client.wait_msg()      # 阻塞; 有消息时触发 _on_msg
            except Exception as e:
                print("mqtt recv error:", e)
                self._connected = False
                time.sleep(2)               # 断线退避后重连

    def _close_client(self, client):
        """尽力释放 MQTT 客户端占用的 socket, 兼容不同固件 API.

        重连必须释放旧连接, 否则连接失败反复重连会泄漏 socket,
        最终耗尽 QuecPython socket 池, 导致所有网络请求报 Errno 22 EINVAL.
        """
        if client is None:
            return
        for m in ("disconnect", "close"):
            try:
                getattr(client, m)()
            except Exception:
                pass
        try:
            client.sock.close()      # umqtt 底层 socket 兜底
        except Exception:
            pass

    def _connect(self):
        # 先释放上一次连接占用的 socket, 再新建, 防止重连泄漏
        self._close_client(self.client)
        self.client = MQTTClient(
            client_id=cfg_get("device_id"),
            server=cfg_get("mqtt_server"),
            port=cfg_get("mqtt_port"),
            user=cfg_get("mqtt_user"),
            password=cfg_get("mqtt_password"),
            keepalive=cfg_get("mqtt_keepalive"),
        )
        self.client.set_callback(self._on_msg)
        try:
            self.client.connect()
            self.client.subscribe(self.topic, qos=cfg_get("mqtt_qos", 1))
        except Exception:
            # 本次尝试失败: 回收其 socket, 交给 _recv_loop 退避后重建
            self._close_client(self.client)
            self.client = None
            self._connected = False
            raise
        self._connected = True
        print("MQTT connected & subscribed:", self.topic)

    def start(self):
        """建立连接并启动独立接收线程."""
        self._thread_on = True
        _thread.start_new_thread(self._recv_loop, ())

    def is_connected(self):
        return (self._connected and self.client is not None)

    def stop(self):
        self._thread_on = False
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
            self._connected = False

    def tick(self):
        """主循环兜底: 若接收线程因故退出, 主动触发重连."""
        if not self._thread_on:
            self.start()


# 便捷: 播完后回执
def ack_finished(cmd):
    """向网关回执已播完(触发对话框恢复). 走 HTTP 即可, 无需额外建队列."""
    # 网关当前 ack 通过后续 /poll 或 /message 体现; 此处预留上传确认:
    if cmd and cmd.get("type"):
        # 可选: http_api.post("/api/device/ack", {...})
        pass
