# 智能拐杖 · 家人端 App

基于 [Flet 1.0+](https://flet.dev) 的家人端移动/桌面 App，用于观测绑定老人设备、切换对话模式、发送文字关怀、语音留言双向收发。

> 架构背景参考 `docs/智能拐杖技术方案-架构文档.md`。设备端只管 I/O，大脑在云端。

## 功能

- 角色登录（家人账号，JWT 鉴权）
- 设备列表 & 实时传感观测（温湿度 / 光照 / 三轴 / LBS 定位）
- 对话模式切换（AI 陪伴 / 家人 / 管理端）
- 文字关怀下发（云端转 TTS 播报给设备）
- 语音留言双向（家人→设备下发播放 / 老人留言回传拉取）

## 环境要求

- Python 3.10+
- UV（依赖管理，`uv` 命令）

## 快速开始

```bash
cd software

# 1) 安装依赖（自动创建 .venv）
uv sync --dev

# 2) 运行 App（桌面窗口）
uv run flet run

# 或仅运行源码
uv run python src/main.py
```

后端网关地址默认 `http://127.0.0.1:8000`，可用环境变量覆盖：

```bash
CANE_API_BASE=https://your-gateway.example.com uv run flet run
```

演示家人账号：`family-demo` / `family123`（与 backend `DEMO_ACCOUNTS` 一致）。

## 运行测试

```bash
uv run flet test            # Flet 集成冒烟测试
uv run pytest tests/        # 依赖注入 / 逻辑测试
```

## 目录结构

```
software/
├── pyproject.toml          # 项目元数据 + 依赖(flet/requests) + [tool.flet] 配置
├── src/
│   ├── main.py             # App 入口: 登录→设备→传感/消息/语音/模式
│   ├── api_client.py       # backend REST 客户端(带 JWT)
│   ├── config.py           # 网关地址 / 演示账号 / 音频缓存目录
│   └── assets/icon.png
└── tests/
    └── test_main.py        # flet 冒烟测试
```