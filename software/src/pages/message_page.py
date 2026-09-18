"""消息页面.

按《app开发文档》4.5.2 双视图切换:
1. 消息列表视图: 告警分类标签 + 老人聊天入口 + 告警卡片(可标记已处理)
2. 聊天会话视图: 仿微信, 文字下发(/api/family/message) + 语音留言(/api/voice/send),
   历史与轮询来自 /api/voice/messages, 语音点击下载播放.

进入聊天视图隐藏底部导航, 返回箭头退出.
"""
import asyncio
import datetime
import os
import subprocess
import sys
import tempfile
import time

import flet as ft

from config import COLORS
from models.device_models import ChatMessage
from services.api_service import api, ApiError
from widgets.neu_widgets import ChatBubble, NeuButton, NeuCard, NeuCircleButton, NeuInput

ALERT_LABELS = {
    "sos": "跌倒/SOS求救",
    "fall": "跌倒告警",
    "obstacle": "障碍物预警",
    "water": "地面积水提醒",
    "light": "光线不足提醒",
}
ALERT_COLORS = {
    "sos": "danger", "fall": "danger",
    "obstacle": "warning", "water": "warning", "light": "warning",
}


class MessagePage(ft.Column):
    def __init__(self, page: ft.Page, recorder, on_goto_tab=None):
        super().__init__(expand=True, spacing=0)
        self.pg = page
        self.recorder = recorder           # AudioRecorder(由 main 注入, 已挂 page.services)
        self.on_goto_tab = on_goto_tab
        self.is_chat_view = False
        self.alerts = []
        self._filter = "全部"
        self._loaded_ids = set()           # 已渲染的消息 id(去重)
        self._rec_active = False           # 录音状态(跨点击持久, 不能用局部变量)
        self._poll_task = None
        self._sending = False

        self._build_msg_list_view()
        self._build_chat_view()
        self.controls = [self.msg_list_container, self.chat_container]
        self._switch_view()

    # ================= 1. 消息列表视图 =================
    def _build_msg_list_view(self):
        self.tabs_row = ft.Row(spacing=10, scroll=ft.ScrollMode.AUTO)
        self._render_tabs()

        # 聊天会话入口
        self.unread_text = ft.Text("点击进入", size=10, color=COLORS["neutral"])
        chat_entry = NeuCard(
            ft.Row([
                ft.Container(
                    width=48, height=48, border_radius=999,
                    gradient=ft.LinearGradient(colors=["#ffffff", "#e6ecf3"]),
                    alignment=ft.Alignment(0, 0),
                    content=ft.Icon(ft.Icons.PERSON, color=COLORS["primary"], size=20),
                ),
                ft.Column([
                    ft.Text("老人（拐杖端）", size=14, weight=ft.FontWeight.W_500),
                    ft.Text("发送文字/语音，拐杖将语音朗读", size=11, color=COLORS["neutral"]),
                ], expand=True, spacing=4),
                self.unread_text,
            ], spacing=16),
            on_click=lambda e: asyncio.ensure_future(self.open_chat()),
        )

        self.alert_list = ft.Column(spacing=12)
        self.msg_list_container = ft.Container(
            ft.Column([self.tabs_row, chat_entry, self.alert_list, ft.Container(height=20)],
                      spacing=16, scroll=ft.ScrollMode.AUTO),
            padding=16, expand=True,
        )

    def _render_tabs(self):
        defs = [("全部", None), ("跌倒", "sos"), ("障碍物", "obstacle"), ("积水", "water")]
        self.tabs_row.controls = []
        for label, ftype in defs:
            selected = (self._filter == label)
            btn = (NeuButton if not selected else type("B", (NeuButton,), {}))
            b = self._tab_button(label, ftype, selected)
            self.tabs_row.controls.append(b)

    def _tab_button(self, label, ftype, selected):
        if selected:
            b = NeuButton(label, on_click=lambda e, l=label, t=ftype: self._set_filter(l, t))
            b.gradient = ft.LinearGradient(
                begin=ft.Alignment(-1, -1), end=ft.Alignment(1, 1),
                colors=["#5aa3d6", COLORS["primary"]])
            b.content.controls[1].color = ft.Colors.WHITE
            return b
        return NeuButton(label, on_click=lambda e, l=label, t=ftype: self._set_filter(l, t))

    def _set_filter(self, label, ftype):
        self._filter = label
        self._render_tabs()
        self._render_alerts()
        self._safe_update()

    async def refresh_alerts(self):
        """拉取告警列表与未读数(消息列表视图)."""
        try:
            self.alerts = await asyncio.to_thread(api.get_alert_list, 30)
        except ApiError:
            self.alerts = []
        try:
            unread = await asyncio.to_thread(api.get_voice_messages, True)
            n = len(unread)
            self.unread_text.value = f"{n} 条未读" if n else "点击进入"
        except Exception:
            pass
        self._render_alerts()
        self._safe_update()

    def _render_alerts(self):
        self.alert_list.controls = []
        for a in self.alerts:
            if self._filter != "全部" and a.type != self._filter:
                continue
            tstr = datetime.datetime.fromtimestamp(a.ts).strftime("%m-%d %H:%M") if a.ts else "--"
            label = ALERT_LABELS.get(a.type, a.type or "告警")
            color = COLORS[ALERT_COLORS.get(a.type, "warning")]
            handled_mark = (ft.Container(
                ft.Text("已处理", size=11, color=COLORS["success"]), padding=4,
                border_radius=8, bgcolor=ft.Colors.with_opacity(0.1, COLORS["success"])))
            if not a.handled:
                handled_mark = NeuButton("标记已处理", on_click=(
                    lambda e, aid=a.id: asyncio.ensure_future(self._handle_alert(aid))))
            self.alert_list.controls.append(NeuCard(
                ft.Column([
                    ft.Row([
                        ft.Row([ft.Icon(ft.Icons.WARNING_AMBER, size=16, color=color),
                                ft.Text(label, weight=ft.FontWeight.W_500)], spacing=6),
                        ft.Text(tstr, size=10, color=COLORS["neutral"]),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Text(a.message or "—", size=12, color=COLORS["neutral"]),
                    ft.Row([handled_mark], alignment=ft.MainAxisAlignment.END),
                ], spacing=8),
                padding=16,
                border=ft.Border.only(left=ft.BorderSide(4, color=color)),
            ))
        if not self.alert_list.controls:
            self.alert_list.controls.append(
                ft.Text("暂无告警消息", size=12, color=COLORS["neutral"]))

    async def _handle_alert(self, alert_id: int):
        try:
            ok = await asyncio.to_thread(api.mark_alert_handled, alert_id, "家属已确认")
            if ok:
                await self.refresh_alerts()
        except ApiError:
            pass

    # ================= 2. 聊天会话视图 =================
    def _build_chat_view(self):
        self.chat_title = ft.Text("老人（拐杖端）", weight=ft.FontWeight.W_600)
        self.chat_header = ft.Container(
            ft.Row([
                ft.IconButton(ft.Icons.ARROW_BACK,
                              on_click=lambda e: self.close_chat()),
                self.chat_title,
            ], spacing=12),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            gradient=ft.LinearGradient(
                begin=ft.Alignment(0, -1), end=ft.Alignment(0, 1),
                colors=["#eef4fb", "#e1e9f4"]),
            border_radius=ft.BorderRadius(0, 0, 22, 22),
            shadow=ft.BoxShadow(offset=ft.Offset(0, 10), blur_radius=24,
                                color=ft.Colors.with_opacity(0.25, "#7896b4")),
        )

        self.chat_list = ft.ListView(expand=True, spacing=16, padding=12)

        self.chat_input = NeuInput(hint_text="输入消息发送给老人", multiline=True, expand=True)
        self.mic_button = NeuCircleButton(ft.Icons.MIC, on_click=(
            lambda e: asyncio.ensure_future(self._toggle_record())))
        input_bar = ft.Container(
            ft.Row([
                self.mic_button,
                self.chat_input,
                NeuCircleButton(ft.Icons.SEND, primary=True,
                                on_click=lambda e: asyncio.ensure_future(self.send_msg())),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.END),
            padding=12, bgcolor=ft.Colors.WHITE,
            border=ft.Border.only(top=ft.BorderSide(1, color="#eee")),
        )

        self.chat_container = ft.Container(
            ft.Column([self.chat_header,
                       ft.Container(self.chat_list, expand=True,
                                    bgcolor=COLORS["chat_bg"], border_radius=22),
                       input_bar]),
            expand=True, visible=False,
        )

    # ================= 视图切换 =================
    def _switch_view(self):
        self.msg_list_container.visible = not self.is_chat_view
        self.chat_container.visible = self.is_chat_view
        # 隐藏/显示底部导航
        if self.pg.navigation_bar is not None:
            self.pg.navigation_bar.visible = not self.is_chat_view
        self._safe_update()

    async def open_chat(self):
        self.is_chat_view = True
        self._switch_view()
        await self._load_history()
        self._start_poll()

    def close_chat(self):
        self.is_chat_view = False
        self._switch_view()
        self._stop_poll()
        asyncio.ensure_future(self.refresh_alerts())

    # ================= 历史与轮询 =================
    async def _load_history(self):
        try:
            msgs = await asyncio.to_thread(api.get_voice_messages, False)
        except ApiError:
            msgs = []
        self.chat_list.controls = []
        self._loaded_ids = set()
        for m in msgs:
            self._append_message(m)
        self._scroll_end()

    def _start_poll(self):
        if self._poll_task and not self._poll_task.done():
            return

        async def _loop():
            while self.is_chat_view:
                await asyncio.sleep(5)
                if not self.is_chat_view:
                    break
                try:
                    msgs = await asyncio.to_thread(api.get_voice_messages, False)
                    new = [m for m in msgs if m.id not in self._loaded_ids]
                    for m in new:
                        self._append_message(m)
                    if new:
                        self._scroll_end()
                except Exception:
                    pass

        self._poll_task = asyncio.ensure_future(_loop())

    def _stop_poll(self):
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()

    def _append_message(self, m: ChatMessage):
        self._loaded_ids.add(m.id)
        tstr = datetime.datetime.fromtimestamp(m.ts).strftime("%H:%M") if m.ts else ""
        if m.is_from_user:
            row = ft.Row([
                ft.Container(ChatBubble(m.content, tstr, is_right=True,
                                        is_voice=(m.msg_type == "voice"))),
                self._chat_avatar(),
            ], spacing=10, alignment=ft.MainAxisAlignment.END)
        else:
            bubble = ChatBubble(m.content, tstr, is_right=False,
                                is_voice=(m.msg_type == "voice"))
            if m.audio_url:
                bubble.on_click = (lambda e, u=m.audio_url:
                                   asyncio.ensure_future(self._play_voice(u)))
            row = ft.Row([
                self._elder_avatar(),
                ft.Container(bubble),
            ], spacing=10)
        self.chat_list.controls.append(row)
        if not m.is_from_user and not m.read and m.id:
            asyncio.ensure_future(self._mark_read(m.id))

    @staticmethod
    def _chat_avatar():
        return ft.Container(width=36, height=36, border_radius=999, bgcolor="#ddd",
                            alignment=ft.Alignment(0, 0),
                            content=ft.Icon(ft.Icons.PERSON, size=16, color="#666"))

    @staticmethod
    def _elder_avatar():
        return ft.Container(
            width=36, height=36, border_radius=999,
            gradient=ft.LinearGradient(colors=["#ffffff", "#e6ecf3"]),
            alignment=ft.Alignment(0, 0),
            content=ft.Icon(ft.Icons.PERSON, color=COLORS["primary"], size=16))

    async def _mark_read(self, msg_id: int):
        try:
            await asyncio.to_thread(api.mark_voice_read, msg_id)
        except Exception:
            pass

    async def _play_voice(self, audio_url: str):
        """下载并播放老人语音留言."""
        try:
            path = await asyncio.to_thread(api.download_audio, audio_url)
            if sys.platform.startswith("win"):
                os.startfile(path)          # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def _scroll_end(self):
        try:
            self.chat_list.update()
            self.chat_list.scroll_to(offset=-1, duration=300)
        except Exception:
            pass

    # ================= 发送逻辑 =================
    async def send_msg(self, e=None):
        """发送文字消息: 右侧气泡 + 下发设备 TTS 朗读."""
        text = (self.chat_input.value or "").strip()
        if not text or self._sending:
            return
        self._sending = True
        self.chat_input.value = ""
        now = time.strftime("%H:%M")
        self.chat_list.controls.append(ft.Row([
            ft.Container(ChatBubble(text, now, is_right=True)),
            self._chat_avatar(),
        ], spacing=10, alignment=ft.MainAxisAlignment.END))
        self._scroll_end()
        try:
            await asyncio.to_thread(api.send_text_to_device, text)
        except ApiError:
            pass
        self._sending = False

    async def _toggle_record(self):
        """语音留言: 第一次点击开始录音, 第二次点击停止并上传."""
        if not self._rec_active:
            # 清理残留未停止的录音(否则 recorder 锁死)
            try:
                if await self.recorder.is_recording_async():
                    await self.recorder.cancel_recording_async()
            except Exception:
                pass
            self._rec_path = os.path.join(tempfile.gettempdir(),
                                          f"cane_voice_{int(time.time())}.wav")
            try:
                await self.recorder.start_recording(output_path=self._rec_path)
            except Exception:
                return
            self._rec_active = True
            self.mic_button.content = ft.Icon(ft.Icons.STOP_CIRCLE_OUTLINED,
                                              color=COLORS["danger"], size=18)
            self._safe_update()
        else:
            try:
                await self.recorder.stop_recording()
            except Exception:
                pass
            self._rec_active = False
            self.mic_button.content = ft.Icon(ft.Icons.MIC,
                                              color=COLORS["neutral"], size=18)
            self._safe_update()
            path = getattr(self, "_rec_path", None)
            if not path or not os.path.exists(path) or os.path.getsize(path) < 100:
                return
            # 上传语音留言
            now = time.strftime("%H:%M")
            try:
                await asyncio.to_thread(api.send_voice_to_device, path)
                self.chat_list.controls.append(ft.Row([
                    ft.Container(ChatBubble("[语音留言]", now, is_right=True, is_voice=True)),
                    self._chat_avatar(),
                ], spacing=10, alignment=ft.MainAxisAlignment.END))
                self._scroll_end()
            except ApiError:
                pass

    def _safe_update(self):
        try:
            self.update()
            self.pg.update()
        except Exception:
            pass
