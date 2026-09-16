# Smart Cane - EC200U 设备端 (client)

基于 QuecPython 的智能拐杖设备端。通过与 backend(FastAPI 网关) 配合实现:
- 板载传感采集上报 (LIS2DH12 三轴 / Ath20 温湿 / gl5528 光敏 / LBS 定位)
- MQTT 下行订阅 云端主动下发 (speak / family_voice / admin_tts / mode_switch / sos 等)
- 跌倒检测 + 一键 SOS
- 对话模式切换 (ai / family / admin) —— 云端权威, 设备端接收 mode_switch 更新
- MQTT 断连时 HTTP poll 兜底

## API 契约(与 backend 对齐)
- MQTT 订阅主题: `{prefix}/{device_id}/cmd`   (prefix=cane)
- 上行: `POST /api/device/sensor/report` (请求头带 x-device-token)
- SOS:  `POST /api/device/sos`
- 兜底: `GET  /api/device/poll`
- 语音留言回复: `POST /api/voice/reply`

## 部署
1. 将本 client 目录整体复制到 EC200U 的 `/usr/client/` 下 (QPYcom 下载)。
2. 修改 `/usr/client/config.py` 的 mqtt_server / http_base / device_id / device_token。
3. QPYcom 运行 `/usr/client/boot.py` 或直接 `/usr/client/app_main.py`。
4. 若未下载 `usr/libs`(LIS2DH12/Ath20/gl5528), 需一并放入 `/usr/` , 因为 sensors.py 引用 `usr.libs`.

> 说明: audio.py / networker.py 涉及 EC200U 平台 audio/网络 API, 请以当前固件为准微调;
> 本骨架已按 Quectel QuecPython 官方 API 结构编写, 后续按真机验证结果修正。

## 下行指令 (type)
| type | 含义 |
|------|------|
| speak | 文本 TTS 播报 |
| family_voice / admin_tts / family_tts / ai | 语音/播报(按优先级) |
| mode_switch | 对话模式切换, 载荷 mode=ai/family/admin |
| sos / beep / oled / config | 其它 I/O 与配置 |
