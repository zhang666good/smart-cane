"""地图页面.

按《app开发文档》4.5.4: 顶部日期选择 + 中部地图区域(演示占位) + 底部操作栏.
轨迹点来自真实接口 GET /api/family/position/history, 以列表/文本形式展示回放.
生产环境可接入高德/百度地图 Flet 插件按点位绘制路线.
"""
import asyncio
import datetime
import time

import flet as ft

from config import COLORS
from services.api_service import api, ApiError
from widgets.neu_widgets import NeuButton, NeuButtonPrimary, NeuCard, NeuInput


class MapPage(ft.Column):
    def __init__(self, page: ft.Page, on_goto_tab=None):
        super().__init__(spacing=16, expand=True, scroll=ft.ScrollMode.AUTO)
        self.pg = page
        self.on_goto_tab = on_goto_tab
        self.points = []          # 轨迹点 [{ts,lat,lng,lac,cid}]
        self._day_offset = 0      # 0=今日 -1=昨日
        self._replay_idx = 0
        self._replaying = False
        self._build_ui()

    def _build_ui(self):
        # ===== 日期选择 =====
        self.day_text = ft.Text("今日", size=14, weight=ft.FontWeight.W_600, color="#333")

        date_card = NeuCard(
            ft.Row([
                NeuButton("前一天", icon=ft.Icons.CHEVRON_LEFT, on_click=lambda e: self._shift_day(-1)),
                ft.Container(self.day_text, expand=True, alignment=ft.Alignment(0, 0)),
                NeuButton("后一天", icon=ft.Icons.CHEVRON_RIGHT, on_click=lambda e: self._shift_day(1)),
            ], spacing=12),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        )

        # ===== 地图占位区域(演示版, 生产接入地图插件) =====
        self.map_area = ft.Container(
            height=260,
            border_radius=24,
            gradient=ft.LinearGradient(
                begin=ft.Alignment(0, -1), end=ft.Alignment(0, 1),
                colors=["#e8f0f8", "#d5e2ee"],
            ),
            alignment=ft.Alignment(0, 0),
            content=ft.Column([
                ft.Icon(ft.Icons.MAP_OUTLINED, size=48, color=COLORS["primary"]),
                ft.Text("地图区域(演示占位)", size=13, color=COLORS["neutral"]),
                ft.Text("生产环境接入高德/百度地图插件绘制轨迹",
                        size=10, color=COLORS["neutral"]),
            ], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        )

        # ===== 轨迹信息卡 =====
        self.track_summary = ft.Text("尚未加载轨迹", size=13, color="#333")
        self.track_list = ft.Column(spacing=8)
        track_card = NeuCard(
            ft.Column([
                ft.Text("轨迹点回放", size=14, weight=ft.FontWeight.W_600, color="#333"),
                ft.Divider(height=12, color=ft.Colors.TRANSPARENT),
                self.track_summary,
                self.track_list,
            ])
        )

        # ===== 底部操作栏 =====
        action_bar = ft.Row([
            NeuButtonPrimary("加载并回放轨迹", expand=True, icon=ft.Icons.REPLAY,
                             on_click=self.on_replay),
            NeuButton("AI询问位置", expand=True, icon=ft.Icons.SMART_TOY,
                      on_click=lambda e: self._goto_ai()),
        ], spacing=12)

        self.controls = [
            ft.Container(padding=16, content=ft.Column(
                [date_card, self.map_area, track_card, action_bar, ft.Container(height=20)],
                spacing=16))
        ]

    # ---------------- 日期切换 ----------------
    def _shift_day(self, delta: int):
        self._day_offset = max(-30, min(0, self._day_offset + delta))
        day = datetime.date.today() + datetime.timedelta(days=self._day_offset)
        self.day_text.value = "今日" if self._day_offset == 0 else (
            "昨日" if self._day_offset == -1 else day.strftime("%m-%d"))
        try:
            self.day_text.update()
        except Exception:
            pass
        asyncio.ensure_future(self.load_track())

    def _goto_ai(self):
        if self.on_goto_tab:
            self.on_goto_tab(2)

    # ---------------- 轨迹加载与回放 ----------------
    async def load_track(self):
        day = datetime.date.today() + datetime.timedelta(days=self._day_offset)
        start_ts = int(time.mktime(day.timetuple()))
        end_ts = start_ts + 86400
        self.track_summary.value = "加载中..."
        self.track_list.controls = []
        self._safe_update()
        try:
            self.points = await asyncio.to_thread(api.get_position_history, start_ts, end_ts)
        except ApiError as ex:
            self.points = []
            self.track_summary.value = f"轨迹加载失败: {ex}"
            self._safe_update()
            return
        if not self.points:
            self.track_summary.value = f"{self.day_text.value}暂无轨迹数据"
        else:
            self.track_summary.value = f"{self.day_text.value}共 {len(self.points)} 个轨迹点"
            self._render_points(highlight=-1)
        self._safe_update()

    def _render_points(self, highlight: int = -1):
        """渲染轨迹点列表; highlight 高亮回放当前位置."""
        self.track_list.controls = []
        for i, p in enumerate(self.points[:30]):  # 最多展示30个点
            ts = p.get("ts") or 0
            tstr = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "--"
            loc = ""
            if p.get("lat") is not None and p.get("lng") is not None:
                loc = f"({p['lat']:.4f}, {p['lng']:.4f})"
            elif p.get("lac") or p.get("cid"):
                loc = f"LAC {p.get('lac', '--')} / CID {p.get('cid', '--')}"
            color = COLORS["primary"] if i == highlight else COLORS["neutral"]
            weight = ft.FontWeight.BOLD if i == highlight else ft.FontWeight.W_400
            self.track_list.controls.append(
                NeuInput(content=ft.Row([
                    ft.Container(ft.Text(tstr, size=11, color=COLORS["neutral"]), width=80),
                    ft.Text(f"点{i + 1} {loc}", size=12, color="#333", weight=weight),
                    ft.Icon(ft.Icons.PERSON_PIN_CIRCLE, size=14, color=color)
                    if i == highlight else ft.Container(width=0, height=0),
                ], spacing=8))
            )

    async def on_replay(self, e=None):
        """轨迹回放: 依次高亮每个轨迹点, 模拟位置移动."""
        if not self.points:
            await self.load_track()
        if not self.points:
            return
        if self._replaying:
            self._replaying = False  # 再次点击停止
            return
        self._replaying = True
        for i in range(len(self.points)):
            if not self._replaying:
                break
            self._render_points(highlight=i)
            self.track_summary.value = (
                f"回放中... 点{i + 1}/{len(self.points)}")
            self._safe_update()
            await asyncio.sleep(1.0)
        self._replaying = False
        self.track_summary.value = f"回放完成, 共 {len(self.points)} 个点"
        self._render_points(highlight=len(self.points) - 1)
        self._safe_update()

    def _safe_update(self):
        try:
            self.update()
        except Exception:
            pass
