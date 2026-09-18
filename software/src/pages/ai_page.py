"""AI 助手页面.

按《app开发文档》4.5.3: 纯文字对话(无语音按钮), 查询设备状态/告警分析/健康咨询.
快捷提问按钮 + 历史气泡 + 真实调用 POST /api/ai/chat.
"""
import asyncio

import flet as ft

from config import COLORS
from services.api_service import api, ApiError
from widgets.neu_widgets import ChatBubble, NeuButton, NeuCard, NeuCircleButton, NeuInput

WELCOME = ("您好！我是AI陪护助手，可以帮您查询拐杖设备状态、环境数据、"
           "告警记录、老人位置轨迹，请输入您的问题。")
QUICK_ASKS = ["查询设备状态", "查询今日告警", "查询行动轨迹"]


class AiPage(ft.Column):
    def __init__(self, page: ft.Page):
        super().__init__(expand=True)
        self.pg = page
        self.session_id = ""
        self._busy = False

        # 头部
        header = NeuCard(
            ft.Row([
                ft.Row([
                    ft.Icon(ft.Icons.SMART_TOY, color=COLORS["primary"], size=18),
                    ft.Text("AI陪护助手", weight=ft.FontWeight.W_600),
                ], spacing=8),
                NeuButton("新建会话", icon=ft.Icons.ADD, on_click=lambda e: self._new_session()),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        )

        # 快捷提问
        quick = ft.Row(
            [NeuButton(q, on_click=lambda e, q=q: asyncio.ensure_future(self.send_msg_text(q)))
             for q in QUICK_ASKS],
            spacing=10, scroll=ft.ScrollMode.AUTO,
        )

        # 聊天列表
        self.chat_list = ft.ListView(expand=True, spacing=16, padding=12)
        self._append_bubble(WELCOME, is_right=False)

        # 输入栏(仅文字)
        self.input_field = NeuInput(hint_text="输入问题，向AI查询设备信息",
                                    multiline=True, expand=True,
                                    on_submit=lambda e: asyncio.ensure_future(self.send_msg()))
        input_bar = ft.Container(
            ft.Row([
                self.input_field,
                NeuCircleButton(ft.Icons.SEND, primary=True,
                                on_click=lambda e: asyncio.ensure_future(self.send_msg())),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.END),
            padding=12,
            bgcolor=ft.Colors.WHITE,
            border=ft.Border.only(top=ft.BorderSide(1, color="#eee")),
        )

        self.controls = [
            header,
            ft.Container(quick, padding=ft.Padding.symmetric(horizontal=16)),
            ft.Container(self.chat_list, expand=True),
            input_bar,
        ]

    # ---------------- 消息渲染 ----------------
    def _append_bubble(self, text: str, is_right: bool):
        if is_right:
            row = ft.Row([
                ft.Container(ChatBubble(text, is_right=True)),
                self._avatar("#ddd", ft.Icons.PERSON, "#666"),
            ], spacing=10, alignment=ft.MainAxisAlignment.END)
        else:
            row = ft.Row([
                self._avatar(None, ft.Icons.SMART_TOY, COLORS["primary"]),
                ft.Container(ChatBubble(text, is_right=False), expand=True),
            ], spacing=10)
        self.chat_list.controls.append(row)
        self._scroll_end()

    @staticmethod
    def _avatar(bgcolor, icon, icon_color):
        if bgcolor:
            return ft.Container(width=36, height=36, border_radius=999, bgcolor=bgcolor,
                                alignment=ft.Alignment(0, 0),
                                content=ft.Icon(icon, size=16, color=icon_color))
        return ft.Container(
            width=36, height=36, border_radius=999,
            gradient=ft.LinearGradient(colors=["#ffffff", "#e6ecf3"]),
            alignment=ft.Alignment(0, 0),
            content=ft.Icon(icon, color=icon_color, size=16))

    def _scroll_end(self):
        try:
            self.chat_list.update()
            self.chat_list.scroll_to(offset=-1, duration=300)
        except Exception:
            pass

    def _new_session(self):
        self.session_id = ""
        self.chat_list.controls = []
        self._append_bubble(WELCOME, is_right=False)

    # ---------------- 发送逻辑 ----------------
    async def send_msg(self, e=None):
        text = (self.input_field.value or "").strip()
        if not text:
            return
        self.input_field.value = ""
        await self.send_msg_text(text)

    async def send_msg_text(self, text: str):
        if self._busy or not text:
            return
        self._busy = True
        self._append_bubble(text, is_right=True)
        try:
            reply = await asyncio.to_thread(api.ai_chat, text, self.session_id)
        except ApiError as ex:
            reply = f"AI 暂时无法回复: {ex}"
        except Exception:
            reply = "网络异常，请稍后重试"
        self._append_bubble(reply, is_right=False)
        self._busy = False
