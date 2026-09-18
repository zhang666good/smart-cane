# 智能拐杖 · 云端后端 (FastAPI 网关 + MCP)

## 环境 (Python 3.11)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env   # 按需修改
```

> 注: 新版本 fastapi-cli 已移除 `fastapi new` 脚手架命令(仅剩 dev/run/login/deploy/cloud)。
> 本项目采用官方 FastAPI 标准结构 `app/` + `routers/` 手动组织。

## 本地 MQTT Broker (amqtt, 纯 Python)

```powershell
.\.venv\Scripts\python -m pip install amqtt
.\.venv\Scripts\amqtt.exe -c mqtt_broker.toml -d   # 监听 0.0.0.0:1883
```

快速自检(订阅端 + 发布端两个终端):
- 订阅端: `amqtt_sub.exe --url mqtt://127.0.0.1:1883 -t "cane/#" -d`
- 发布端: `amqtt_pub.exe --url mqtt://127.0.0.1:1883 -t "cane/dev-001/cmd" -m "hello"`

## 启动

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- REST 网关:      http://127.0.0.1:8000
- 接口文档:       http://127.0.0.1:8000/docs
- 健康检查:       http://127.0.0.1:8000/health
- MCP (Coze 用):  http://<host>:8000/mcp  (Streamable HTTP)

## 结构

```
app/
  main.py           FastAPI 入口(含 lifespan 托管 MCP session)
  config.py         配置(pydantic-settings, .env)
  db.py             SQLite DAO(用户/传感/会话/告警)
  models.py         Pydantic 请求模型
  mqtt_publisher.py MQTT 下行发布器(主动下发)
  coze_client.py    Coze 开放接口客户端(预留)
  mcp_servers/      MCP 服务器(8 个工具, 供 Coze 接入)
  routers/
    device.py       设备接入 REST(/api/device/*)
    audio.py        音频上传/下发(/audio/*)
mqtt_broker.toml    amqtt broker 配置
```

## MCP 工具(供 Coze "添加 MCP 服务")

| 类        | 工具 | 说明 |
|-----------|------|------|
| 传感信息  | get_sensors / get_history | 实时/历史传感 |
| 数据库    | user_get / user_set / record_add / record_query / alert_insert | 画像/记忆/告警 |
| 下行指令  | push_to_device | MQTT 主动下发 speak/oled/beep/sos |

## 已验证 (Python 3.11.6 / mcp 2.2 / paho-mqtt 2.1 / amqtt 0.12)

- [x] venv 创建 + 依赖安装
- [x] FastAPI 网关启动, /health 200
- [x] 传感上报 POST /api/device/sensor/report → SQLite 落库
- [x] MCP 端点握手 200, 8 工具可被官方客户端发现并调用
- [x] 本地 MQTT Broker(amqtt) 启动, pub/sub 双向打通
- [x] push_to_device 链路: 网关发布 → 设备端订阅 cane/{id}/cmd 收到 speak/sos
- [x] MCP 工具 push_to_device 端到端触发 MQTT 下行, 设备端收到