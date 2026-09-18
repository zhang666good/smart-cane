"""首页页面.

功能模块(按《app开发文档》4.5.1):
1. 设备基础状态卡片(老人昵称/在线/电量/今日外出/告警次数/快捷按钮)
2. 环境传感数据(温湿度/光照) — 定时自动刷新 + 闪烁动画
3. GPS 定位信息(经纬度/基站/更新时间)
4. 今日动态列表(events 接口)

数据全部来自真实后端接口(services.api_service.api).
"""
import asyncio
import datetime
import time

import flet as ft

from config import COLORS, CONSTANTS
from models.device_models import SensorData
from services.api_service import api, ApiError
from widgets.neu_widgets import NeuButton, NeuButtonPrimary, NeuCard, NeuInput

EVENT_LABELS = {
    "obstacle": "障碍物预警",
    "water": "地面积水提醒",
    "position": "位置更新",
}


class HomePage(ft.Column):
    def __init__(self, page: ft.Page, on_goto_tab=None):
        super().__init__(scroll=ft.ScrollMode.AUTO, spacing=16, expand=True)
        self.pg = page
        self.on_goto_tab = on_goto_tab   # 跳转其他 Tab 的回调(首页→地图/AI)
        self.sensor_data = SensorData(online=False)
        self.nickname = "老人"
        self._refreshing = False
        self._loop_started = False

        self._build_ui()
        self.start_auto_refresh()

    # ================ UI 构建 ================
    def _build_ui(self):
        # ===== 1. 设备状态卡片 =====
        self.battery_text = ft.Text("电量 --", size=12, color=COLORS["neutral"])
        self.status_dot = ft.Container(
            width=10, height=10, border_radius=999,
            bgcolor=COLORS["neutral"],
            shadow=ft.BoxShadow(spread_radius=3, blur_radius=0,
                                color=ft.Colors.with_opacity(0.25, COLORS["success"])),
        )
        self.online_text = ft.Text("离线", size=11, color=COLORS["neutral"])
        self.name_text = ft.Text(f"{self.nickname} · 智能拐杖",
                                 size=18, weight=ft.FontWeight.BOLD, color="#333")
        self.out_time_text = ft.Text("--", weight=ft.FontWeight.BOLD)
        self.alarm_cnt_text = ft.Text("--", weight=ft.FontWeight.BOLD, color=COLORS["warning"])

        device_card = NeuCard(
            ft.Column([
                ft.Row([
                    ft.Column([
                        self.name_text,
                        ft.Row([self.status_dot, self.online_text, self.battery_text], spacing=8),
                    ], expand=True, spacing=6),
                    ft.Container(
                        width=56, height=56, border_radius=999,
                        gradient=ft.LinearGradient(
                            begin=ft.Alignment(-1, -1), end=ft.Alignment(1, 1),
                            colors=["#ffffff", "#e6ecf3"]),
                        alignment=ft.Alignment(0, 0),
                        content=ft.Icon(ft.Icons.ELDERLY, color=COLORS["primary"], size=22),
                    ),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                ft.Row([
                    NeuInput(expand=True, content=ft.Column([
                        ft.Text("今日外出", size=11, color=COLORS["neutral"]),
                        self.out_time_text,
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER)),
                    NeuInput(expand=True, content=ft.Column([
                        ft.Text("告警次数", size=11, color=COLORS["neutral"]),
                        self.alarm_cnt_text,
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER)),
                ], spacing=12),
                ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                ft.Row([
                    NeuButtonPrimary("查看位置", expand=True, icon=ft.Icons.LOCATION_ON,
                                     on_click=lambda e: self._goto(1)),
                    NeuButton("AI助手", expand=True, icon=ft.Icons.SMART_TOY,
                              on_click=lambda e: self._goto(2)),
                ], spacing=12),
            ])
        )

        # ===== 2. 环境传感数据 =====
        self.temp_text = ft.Text("-- ℃", size=18, weight=ft.FontWeight.BOLD)
        self.hum_text = ft.Text("-- %RH", size=18, weight=ft.FontWeight.BOLD)
        self.lux_text = ft.Text("-- Lux", size=18, weight=ft.FontWeight.BOLD)
        self.temp_wrap, self.hum_wrap, self.lux_wrap = ft.Container(), ft.Container(), ft.Container()

        env_card = NeuCard(
            ft.Column([
                ft.Row([
                    ft.Text("环境传感数据", size=14, weight=ft.FontWeight.W_600, color="#333"),
                    ft.Text(f"(每{CONSTANTS['refresh_interval']}s自动刷新)",
                            size=10, color=COLORS["neutral"]),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Divider(height=12, color=ft.Colors.TRANSPARENT),
                ft.Row([
                    self._wrap_sensor_item(self.temp_wrap, ft.Column([
                        ft.Row([ft.Icon(ft.Icons.THERMOSTAT, size=12, color=COLORS["neutral"]),
                                ft.Text("环境温度", size=11, color=COLORS["neutral"])], spacing=4),
                        self.temp_text,
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER), expand=True),
                    self._wrap_sensor_item(self.hum_wrap, ft.Column([
                        ft.Row([ft.Icon(ft.Icons.WATER_DROP, size=12, color=COLORS["neutral"]),
                                ft.Text("环境湿度", size=11, color=COLORS["neutral"])], spacing=4),
                        self.hum_text,
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER), expand=True),
                ], spacing=12),
                ft.Divider(height=12, color=ft.Colors.TRANSPARENT),
                self._wrap_sensor_item(self.lux_wrap, ft.Column([
                    ft.Row([ft.Icon(ft.Icons.LIGHT_MODE, size=12, color=COLORS["neutral"]),
                            ft.Text("环境光照强度", size=11, color=COLORS["neutral"])], spacing=4),
                    self.lux_text,
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER)),
            ])
        )

        # ===== 3. GPS 定位信息 =====
        self.ll_text = ft.Text("经纬度：--", size=13, weight=ft.FontWeight.W_500, color="#333")
        self.addr_text = ft.Text("--", size=13, weight=ft.FontWeight.W_500, color="#333")
        self.gps_time_text = ft.Text("定位更新时间：--", size=10, color=COLORS["neutral"])
        self.ll_wrap, self.addr_wrap = ft.Container(), ft.Container()

        gps_card = NeuCard(
            ft.Column([
                ft.Text("GPS定位信息", size=14, weight=ft.FontWeight.W_600, color="#333"),
                ft.Divider(height=12, color=ft.Colors.TRANSPARENT),
                self._wrap_sensor_item(self.ll_wrap, ft.Column([
                    ft.Text("经纬度 / 基站", size=11, color=COLORS["neutral"]),
                    self.ll_text,
                ])),
                ft.Divider(height=12, color=ft.Colors.TRANSPARENT),
                self._wrap_sensor_item(self.addr_wrap, ft.Column([
                    ft.Text("详细地理位置", size=11, color=COLORS["neutral"]),
                    self.addr_text,
                ])),
                ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
                self.gps_time_text,
            ])
        )

        # ===== 4. 今日动态 =====
        self.dynamic_list = ft.Column(spacing=10)
        dynamic_card = NeuCard(
            ft.Column([
                ft.Text("今日动态", size=14, weight=ft.FontWeight.W_600, color="#333"),
                ft.Divider(height=12, color=ft.Colors.TRANSPARENT),
                self.dynamic_list,
            ])
        )

        self.controls = [device_card, env_card, gps_card, dynamic_card,
                         ft.Container(height=20)]

    def _wrap_sensor_item(self, wrap: ft.Container, content: ft.Control, expand=False):
        """包裹传感器项, 用于刷新闪烁动画."""
        wrap.content = NeuInput(content=content, padding=12, expand=expand)
        return wrap

    def _dynamic_item(self, time_str: str, text: str, warning: bool = False):
        color = COLORS["warning"] if warning else "#333"
        return NeuInput(
            content=ft.Row([
                ft.Container(ft.Text(time_str, size=11, color=COLORS["neutral"]), width=60),
                ft.Text(text, size=12, color=color),
            ], spacing=12),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        )

    def _goto(self, idx: int):
        if self.on_goto_tab:
            self.on_goto_tab(idx)

    # ================ 数据刷新逻辑 ================
    def start_auto_refresh(self):
        """启动定时自动刷新(真实调用 /api/family/sensors)."""
        if self._loop_started:
            return
        self._loop_started = True

        async def refresh_loop():
            await self.refresh_data()
            while True:
                await asyncio.sleep(CONSTANTS["refresh_interval"])
                try:
                    # 聊天视图打开或页面不可见时跳过(聊天页会隐藏导航)
                    if self.pg.navigation_bar is not None and not self.pg.navigation_bar.visible:
                        continue
                    await self.refresh_data()
                except Exception:
                    pass

        asyncio.ensure_future(refresh_loop())

    async def refresh_data(self):
        """拉取传感器/告警/事件数据并更新 UI."""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            s, alerts, events, user = await asyncio.gather(
                self._safe(lambda: api.get_sensor_data()),
                self._safe(lambda: api.get_alert_list(limit=30)),
                self._safe(lambda: api.get_events(limit=20)),
                self._safe(lambda: api.get_user_profile()),
                return_exceptions=False,
            )
            if s:
                self.sensor_data = s
            today = datetime.date.today()
            if alerts is not None:
                cnt = sum(1 for a in alerts
                          if a.ts and datetime.date.fromtimestamp(a.ts) == today)
                self.alarm_cnt_text.value = f"{cnt}次"
            if events is not None:
                self._render_events(events)
            if user and user.get("nickname"):
                self.nickname = user["nickname"]
                self.name_text.value = f"{self.nickname} · 智能拐杖"
            await self._update_ui_data()
        finally:
            self._refreshing = False

    @staticmethod
    async def _safe(fn):
        try:
            return await asyncio.to_thread(fn)
        except (ApiError, Exception):
            return None

    def _render_events(self, events):
        self.dynamic_list.controls = []
        for ev in events[:10]:
            ts = ev.get("ts") or 0
            tstr = datetime.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "--"
            label = EVENT_LABELS.get(ev.get("type"), ev.get("message", "事件"))
            warning = ev.get("type") in ("water", "obstacle")
            self.dynamic_list.controls.append(self._dynamic_item(tstr, label, warning))
        if not self.dynamic_list.controls:
            self.dynamic_list.controls.append(
                ft.Text("今日暂无动态", size=12, color=COLORS["neutral"]))

    async def _update_ui_data(self):
        """更新 UI 数据 + 闪烁动画."""
        d = self.sensor_data
        self.temp_text.value = f"{d.temperature:.1f} ℃"
        self.hum_text.value = f"{d.humidity:.0f} %RH"
        self.lux_text.value = f"{d.light:.0f} Lux"
        self.battery_text.value = f"电量 {d.battery}%" if d.battery is not None else "电量 --"
        # 在线状态
        self.status_dot.bgcolor = COLORS["success"] if d.online else COLORS["neutral"]
        self.online_text.value = "在线" if d.online else "离线"
        self.online_text.color = COLORS["success"] if d.online else COLORS["neutral"]
        # 定位
        if d.lat is not None and d.lng is not None:
            self.ll_text.value = f"北纬 {d.lat:.4f}°，东经 {d.lng:.4f}°"
        elif d.lac or d.cid:
            self.ll_text.value = f"基站定位 LAC {d.lac or '--'} / CID {d.cid or '--'}"
        else:
            self.ll_text.value = "暂无定位数据"
        self.addr_text.value = d.address or "暂无详细地址(以经纬度/基站为准)"
        now = time.strftime("%H:%M:%S")
        self.gps_time_text.value = f"定位更新时间：{now}"
        try:
            self.update()
        except Exception:
            pass
        # 闪烁动画
        for w in [self.temp_wrap, self.hum_wrap, self.lux_wrap, self.ll_wrap]:
            w.bgcolor = ft.Colors.with_opacity(0.15, COLORS["primary"])
            try:
                w.update()
            except Exception:
                pass
        await asyncio.sleep(0.6)
        for w in [self.temp_wrap, self.hum_wrap, self.lux_wrap, self.ll_wrap]:
            w.bgcolor = ft.Colors.TRANSPARENT
            try:
                w.update()
            except Exception:
                pass
