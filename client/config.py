"""设备端(EC200U / QuecPython)集中配置.

部署时按实际环境修改本文件.
backend 侧对应: backend/app/config.py 的 mqtt_host / mqtt_topic_prefix / public_audio_base / device_token
"""

CFG = {
    # ---- 设备身份 ----
    "device_id": "dev-001",           # 与 gateway 分配的 device_id 一致
    "device_token": "demo-device-token",   # 上行 REST 鉴权, 对应 backend device_token

    # ---- 网络 ----
    "apn": "",
    "apn_user": "",
    "apn_pwd": "",
    "network_wait_s": 60,

    # ---- 老人账号(换取 elder JWT, 用于语音留言上传) ----
    "elder_user": "dev-001",       # 与 backend DEMO_ACCOUNTS 一致
    "elder_password": "123456",
    # ---- 录音(AMR, 单次时长秒) ----
    "record_duration_s": 10,
    # 录音采集通道: 0=听筒 1=耳机 2=喇叭; 该固件不支持0, 实测用2
    "rec_device": 2,
    # 按键松开后延迟多久才开始录音(秒, 防抖/给反应时间, 0=立即)
    "record_start_delay_s": 1.0,

    # ---- 云端 REST(上行) ----
    "http_base": "http://123.207.72.88:8000",  # FastAPI 网关(云端服务器)
    "http_timeout_s": 10,

    # ---- MQTT(下行订阅) ----
    "mqtt_server": "123.207.72.88",
    "mqtt_port": 1883,
    "mqtt_user": "",
    "mqtt_password": "",
    "mqtt_topic_prefix": "cane",        # 与 backend mqtt_topic_prefix 一致
    "mqtt_keepalive": 60,
    "mqtt_qos": 1,

    # ---- 传感采集周期(秒) ----
    "sensor_interval_s": 10,
    # ---- 音频 ----
    "audio_volume": 9,            # 0~9, 9=最大(真机验证 Audio.play 默认静音需显式设)
    # ---- SOS 连续上报周期 ----
    "sos_interval_s": 2,
    # ---- 跌倒检测阈值(合成加速度 g) ----
    "fall_detect_threshold_g": 1.6,
    "fall_detect_recover_g": 1.0,
    "fall_detect_samples": 3,
    # ---- 下行兜底轮询周期 ----
    "poll_interval_s": 30,
}


def get(key, default=None):
    # uos.getenv 并非所有固件都提供, 做兜底; 优先读环境变量
    try:
        from uos import getenv as _ge
        env_val = _ge(key)
        if env_val is not None:
            return env_val
    except Exception:
        pass
    return CFG.get(key, default)
