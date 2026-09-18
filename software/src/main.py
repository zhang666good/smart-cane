"""暖杖随行 · 家人守护 App 主入口.

按《app开发文档》实现: 登录页 + 底部 5 Tab 导航(首页/地图/AI助手/消息/我的).
适配 Flet 1.0 语法(ft.run / NavigationBarDestination / AudioRecorder 挂 page.services).
运行: cd software && uv run flet run
"""
import asyncio

import flet as ft
from flet_audio_recorder import AudioEncoder, AudioRecorder, AudioRecorderConfiguration

from config import API_CONFIG, COLORS, CONSTANTS, get_theme
from pages.ai_page import AiPage
from pages.home_page import HomePage
from pages.map_page import MapPage
from pages.message_page import MessagePage
from pages.profile_page import ProfilePage
from services.api_service import api, ApiError


def make_recorder():
    """录音服务控件: WAV 44.1kHz 双声道(与 EC200U 已验证可播格式一致)."""
    return AudioRecorder(
        configuration=AudioRecorderConfiguration(
            encoder=AudioEncoder.WAV,
            sample_rate=44100,
            channels=2,
        )
    )


def build_login(page: ft.Page, on_success):
    """登录页: 获取 JWT 后进入主界面."""
    page.navigation_bar = None
    page.scroll = ft.ScrollMode.AUTO

    user = ft.TextField(label="账号", value=CONSTANTS["demo_user"])
    pwd = ft.TextField(label="密码", value=CONSTANTS["demo_password"], password=True)
    status = ft.Text("", color=COLORS["danger"], size=12)

    async def do_login(e):
        try:
            await asyncio.to_thread(api.login, user.value.strip(), pwd.value.strip())
        except ApiError as ex:
            status.value = str(ex)
            page.update()
            return
        except Exception:
            status.value = "网络异常，请稍后重试"
            page.update()
            return
        on_success()

    return ft.Container(
        padding=32, expand=True, alignment=ft.Alignment(0, 0),
        content=ft.Column([
            ft.Container(height=60),
            ft.Icon(ft.Icons.ELDERLY, size=56, color=COLORS["primary"]),
            ft.Text("暖杖随行 · 家人守护", size=24, weight=ft.FontWeight.BOLD, color="#333"),
            ft.Text("关爱不缺席", size=13, color=COLORS["neutral"]),
            ft.Container(height=30),
            user, pwd,
            ft.FilledButton(content=ft.Text("登录"), on_click=do_login),
            status,
            ft.Text(f"服务地址: {API_CONFIG['base_url']}", size=10, color=COLORS["neutral"]),
        ], spacing=16, width=340, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
    )


class AppShell:
    """主界面: 5 页面 + 底部导航."""

    def __init__(self, page: ft.Page):
        self.page = page
        page.scroll = None
        page.bgcolor = COLORS["page_bg"]
        page.padding = 0

        # 录音服务(挂 page.services, Flet 1.0 要求)
        self.recorder = make_recorder()
        page.services.clear()
        page.services.append(self.recorder)

        self.pages = [
            HomePage(page, on_goto_tab=self.goto_tab),
            MapPage(page, on_goto_tab=self.goto_tab),
            AiPage(page),
            MessagePage(page, self.recorder, on_goto_tab=self.goto_tab),
            ProfilePage(page, on_logout=self.logout),
        ]

        self.nav = ft.NavigationBar(
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.HOME_OUTLINED, label="首页"),
                ft.NavigationBarDestination(icon=ft.Icons.MAP_OUTLINED, label="地图"),
                ft.NavigationBarDestination(icon=ft.Icons.CHAT_OUTLINED, label="AI助手"),
                ft.NavigationBarDestination(icon=ft.Icons.NOTIFICATIONS_OUTLINED, label="消息"),
                ft.NavigationBarDestination(icon=ft.Icons.PERSON_OUTLINED, label="我的"),
            ],
            selected_index=0,
            on_change=self._on_change,
            bgcolor=ft.Colors.with_opacity(0.8, ft.Colors.WHITE),
            indicator_color=ft.Colors.with_opacity(0.1, COLORS["primary"]),
        )

        self.stack = ft.Stack(self.pages, expand=True)
        for i, p in enumerate(self.pages):
            p.visible = (i == 0)
        page.controls = [self.stack]
        page.navigation_bar = self.nav
        page.update()
        # 首页/消息页初始加载
        asyncio.ensure_future(self.pages[3].refresh_alerts())

    def _on_change(self, e):
        idx = getattr(e, "selected_index", None)
        if idx is None:
            idx = getattr(e, "index", None)
        self.goto_tab(idx if idx is not None else self.nav.selected_index)

    def goto_tab(self, idx: int):
        idx = max(0, min(len(self.pages) - 1, int(idx)))
        self.nav.selected_index = idx
        for i, p in enumerate(self.pages):
            p.visible = (i == idx)
        self.page.update()
        if idx == 3:  # 进入消息页刷新告警
            asyncio.ensure_future(self.pages[3].refresh_alerts())

    def logout(self):
        api.token = None
        self.page.controls = [build_login(self.page, lambda: AppShell(self.page))]
        self.page.navigation_bar = None
        self.page.update()


def main(page: ft.Page):
    page.title = "暖杖随行 · 家人守护"
    page.theme = get_theme()
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = COLORS["page_bg"]

    page.controls = [build_login(page, lambda: AppShell(page))]
    page.update()


if __name__ == "__main__":
    ft.run(main)
