"""全局配置文件.

统一管理拟物风格配色、API 地址、业务常量与全局主题, 修改一处全局生效.
(严格按《app开发文档》第 1 节实现)
"""
import os

import flet as ft

# ====================== 拟物风格配色 ======================
COLORS = {
    "primary": "#4a90c2",          # 主蓝色
    "primary_dark": "#357ab8",     # 主色深色
    "danger": "#e5484d",           # 危险红
    "success": "#2fae5f",          # 成功绿
    "warning": "#f2a23b",          # 警告橙
    "neutral": "#7a8494",          # 中性灰
    "page_bg": "#dde5ef",          # 页面背景
    "card_bg_top": "#ffffff",      # 卡片顶部渐变
    "card_bg_bottom": "#f4f7fb",   # 卡片底部渐变
    "input_bg": "#eef2f7",         # 输入框背景(凹陷)
    "wechat_green": "#b5e86a",     # 微信绿气泡(家属发送)
    "chat_bg": "#e4ecf5",          # 聊天页面背景
}

# ====================== API 接口配置 ======================
API_CONFIG = {
    "base_url": os.environ.get("CANE_API_BASE", "http://123.207.72.88:8000"),
    "timeout": 10,  # 请求超时时间(秒)
}

# ====================== 业务常量 ======================
CONSTANTS = {
    "refresh_interval": 5,             # 首页传感器数据自动刷新间隔(秒)
    "default_device_id": "dev-001",    # 默认绑定设备ID
    "demo_user": "family-demo",        # 演示账号(backend DEMO_ACCOUNTS)
    "demo_password": "family123",
    # 音频缓存目录(拉取老人语音留言)
    "audio_cache_dir": os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache"),
}


# ====================== 全局主题 ======================
def get_theme():
    return ft.Theme(
        color_scheme_seed=COLORS["primary"],
        visual_density=ft.VisualDensity.COMFORTABLE,
    )
