"""我的页面.

按《app开发文档》4.5.5: 账号信息、设备绑定管理、老人档案维护、登出.
"""
import asyncio

import flet as ft

from config import COLORS
from services.api_service import api, ApiError
from widgets.neu_widgets import NeuButton, NeuButtonPrimary, NeuCard, NeuInput


class ProfilePage(ft.Column):
    def __init__(self, page: ft.Page, on_logout=None):
        super().__init__(spacing=16, expand=True, scroll=ft.ScrollMode.AUTO)
        self.pg = page
        self.on_logout = on_logout

        # ===== 1. 账号信息 =====
        self.account_text = ft.Text("--", size=14, weight=ft.FontWeight.W_500)
        self.device_text = ft.Text("--", size=12, color=COLORS["neutral"])
        account_card = NeuCard(ft.Column([
            ft.Text("账号信息", size=14, weight=ft.FontWeight.W_600, color="#333"),
            self.account_text,
            self.device_text,
        ], spacing=8))

        # ===== 2. 老人档案维护 =====
        self.nick_input = NeuInput(hint_text="老人称谓(如: 张爷爷)")
        self.medical_input = NeuInput(hint_text="病史(如: 高血压)", multiline=True)
        self.pref_input = NeuInput(hint_text="偏好(如: 喜欢散步、听戏曲)", multiline=True)
        self.taboo_input = NeuInput(hint_text="忌口(如: 不吃辛辣)", multiline=True)
        profile_card = NeuCard(ft.Column([
            ft.Text("老人档案", size=14, weight=ft.FontWeight.W_600, color="#333"),
            self.nick_input, self.medical_input, self.pref_input, self.taboo_input,
            NeuButtonPrimary("保存档案", icon=ft.Icons.SAVE,
                             on_click=lambda e: asyncio.ensure_future(self._save_profile())),
        ], spacing=10))

        # ===== 3. 设备绑定 =====
        self.bind_input = NeuInput(hint_text="输入设备号(如 dev-001)")
        self.devices_text = ft.Text("已绑定: --", size=12, color=COLORS["neutral"])
        bind_card = NeuCard(ft.Column([
            ft.Text("设备绑定", size=14, weight=ft.FontWeight.W_600, color="#333"),
            self.bind_input,
            self.devices_text,
            ft.Row([
                NeuButton("刷新列表", icon=ft.Icons.REFRESH,
                          on_click=lambda e: asyncio.ensure_future(self._load_devices())),
                NeuButtonPrimary("绑定设备", icon=ft.Icons.LINK,
                                 on_click=lambda e: asyncio.ensure_future(self._bind())),
            ], spacing=12),
        ], spacing=10))

        # ===== 4. 登出 =====
        logout_card = NeuCard(ft.Column([
            NeuButton("退出登录", icon=ft.Icons.LOGOUT, on_click=lambda e: self._logout()),
        ]))

        self.controls = [ft.Container(padding=16, content=ft.Column(
            [account_card, profile_card, bind_card, logout_card, ft.Container(height=20)],
            spacing=16))]

        asyncio.ensure_future(self._init_data())

    async def _init_data(self):
        self.account_text.value = api.user_id or "--"
        self.device_text.value = f"当前设备: {api.device_id}"
        await self._load_profile()
        await self._load_devices()
        self._safe_update()

    async def _load_profile(self):
        try:
            user = await asyncio.to_thread(api.get_user_profile)
        except ApiError:
            return
        profile = user.get("profile") or {}
        if user.get("nickname"):
            self.nick_input.value = user["nickname"]
        self.medical_input.value = profile.get("medical") or ""
        self.pref_input.value = profile.get("preference") or ""
        self.taboo_input.value = profile.get("taboo") or ""
        self._safe_update()

    async def _save_profile(self):
        profile = {
            "medical": self.medical_input.value or "",
            "preference": self.pref_input.value or "",
            "taboo": self.taboo_input.value or "",
        }
        try:
            await asyncio.to_thread(api.set_user_profile,
                                    self.nick_input.value or None, profile)
            self._snack("档案已保存")
        except ApiError as ex:
            self._snack(f"保存失败: {ex}")

    async def _load_devices(self):
        try:
            devices = await asyncio.to_thread(api.get_devices)
            self.devices_text.value = "已绑定: " + (", ".join(devices) if devices else "无")
        except ApiError:
            self.devices_text.value = "已绑定: --"
        self._safe_update()

    async def _bind(self):
        dev = (self.bind_input.value or "").strip()
        if not dev:
            self._snack("请输入设备号")
            return
        try:
            await asyncio.to_thread(api.bind_device, dev)
            self.device_text.value = f"当前设备: {api.device_id}"
            await self._load_devices()
            self._snack(f"设备 {dev} 绑定成功")
        except ApiError as ex:
            self._snack(f"绑定失败: {ex}")

    def _logout(self):
        if self.on_logout:
            self.on_logout()

    def _snack(self, msg: str):
        try:
            self.pg.snack_bar = ft.SnackBar(ft.Text(msg), open=True)
            self.pg.update()
        except Exception:
            pass

    def _safe_update(self):
        try:
            self.update()
        except Exception:
            pass
