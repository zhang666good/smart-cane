"""拟物风格通用组件库.

1:1 还原《app开发文档》中的 NeuCard / NeuButton / NeuInput / NeuCircleButton /
ChatBubble 视觉效果: Container + 多层 BoxShadow + LinearGradient 实现立体拟物质感.
适配 Flet 1.0 语法(ft.Colors / 异步事件回调).
"""
import asyncio
import threading

import flet as ft

from config import COLORS


def _op(alpha: float, color: str) -> str:
    return ft.Colors.with_opacity(alpha, color)


# ====================== 拟物卡片 ======================
class NeuCard(ft.Container):
    """拟物凸起卡片: 白色渐变 + 外阴影 + 内高光, 用于所有页面内容区块."""

    def __init__(self, content: ft.Control, padding=20, **kwargs):
        super().__init__(
            content=content,
            padding=padding,
            border_radius=24,
            gradient=ft.LinearGradient(
                begin=ft.Alignment(0, -1),
                end=ft.Alignment(0, 1),
                colors=[COLORS["card_bg_top"], COLORS["card_bg_bottom"]],
            ),
            shadow=[
                # 内顶部高光
                ft.BoxShadow(offset=ft.Offset(0, 1), blur_radius=1,
                             color=_op(0.9, ft.Colors.WHITE)),
                # 内底部暗边
                ft.BoxShadow(offset=ft.Offset(0, -1), blur_radius=1,
                             color=_op(0.6, ft.Colors.WHITE)),
                # 外右下阴影
                ft.BoxShadow(offset=ft.Offset(8, 12), blur_radius=24,
                             color=_op(0.35, "#a0afbf")),
                # 外左上高光
                ft.BoxShadow(offset=ft.Offset(-6, -8), blur_radius=18,
                             color=_op(0.8, ft.Colors.WHITE)),
            ],
            **kwargs,
        )


# ====================== 拟物凸起按钮 ======================
class NeuButton(ft.Container):
    """拟物凸起按钮(浅色): 点击时自动切换凹陷效果(按压回弹反馈).

    on_click 既支持同步函数也支持 async 协程函数.
    """

    def __init__(self, text: str, on_click=None, icon=None, expand=False,
                 padding=None, **kwargs):
        self.text = text
        self.icon = icon
        self.click_handler = on_click
        if padding is None:
            padding = ft.Padding.symmetric(vertical=10, horizontal=12)
        super().__init__(
            on_click=self._handle_click,
            expand=expand,
            padding=padding,
            border_radius=16,
            gradient=ft.LinearGradient(
                begin=ft.Alignment(-1, -1),
                end=ft.Alignment(1, 1),
                colors=["#ffffff", "#e6ecf3"],
            ),
            shadow=self._get_shadow(pressed=False),
            alignment=ft.Alignment(0, 0),
            **kwargs,
        )
        self.content = ft.Row(
            [
                ft.Icon(icon, color=COLORS["primary"], size=14) if icon else ft.Container(width=0, height=0),
                ft.Text(text, size=13, color="#333", weight=ft.FontWeight.W_500),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=4,
        )

    def _get_shadow(self, pressed: bool):
        if pressed:
            # 按压凹陷效果
            return [
                ft.BoxShadow(offset=ft.Offset(4, 4), blur_radius=10,
                             color=_op(0.25, "#788ca5")),
                ft.BoxShadow(offset=ft.Offset(-4, -4), blur_radius=10,
                             color=_op(0.9, ft.Colors.WHITE)),
            ]
        # 凸起效果
        return [
            ft.BoxShadow(offset=ft.Offset(0, 1), blur_radius=1,
                         color=_op(0.9, ft.Colors.WHITE)),
            ft.BoxShadow(offset=ft.Offset(6, 8), blur_radius=16,
                         color=_op(0.35, "#96a5b9")),
            ft.BoxShadow(offset=ft.Offset(-4, -6), blur_radius=12,
                         color=_op(0.9, ft.Colors.WHITE)),
        ]

    def _handle_click(self, e):
        # 按压动画: 切换阴影后回弹
        self.shadow = self._get_shadow(pressed=True)
        try:
            self.update()
        except Exception:
            pass
        threading.Timer(0.15, self._release).start()
        if self.click_handler:
            result = self.click_handler(e)
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result)  # 异步回调安全调度

    def _release(self):
        self.shadow = self._get_shadow(pressed=False)
        try:
            self.update()
        except Exception:
            pass


class NeuButtonPrimary(NeuButton):
    """拟物主色按钮: 蓝色渐变立体."""

    def __init__(self, text: str, on_click=None, icon=None, expand=False, **kwargs):
        super().__init__(text, on_click, icon, expand, **kwargs)
        self.gradient = ft.LinearGradient(
            begin=ft.Alignment(-1, -1),
            end=ft.Alignment(1, 1),
            colors=["#5aa3d6", COLORS["primary"]],
        )
        self.content.controls[1].color = ft.Colors.WHITE
        if self.icon and self.content.controls:
            self.content.controls[0].color = ft.Colors.WHITE


# ====================== 拟物凹陷输入框 ======================
class NeuInput(ft.Container):
    """拟物凹陷输入框: 内阴影嵌入感.

    - 传 hint_text: 作为文本输入框使用(.value 读写)
    - 传 content:   作为数据展示槽使用(凹陷容器)
    """

    def __init__(self, hint_text: str = "", multiline: bool = False,
                 on_submit=None, content: ft.Control = None, padding=None,
                 on_change=None, expand=False, **kwargs):
        if padding is None:
            padding = ft.Padding.symmetric(horizontal=12, vertical=8)
        super().__init__(
            bgcolor=COLORS["input_bg"],
            border_radius=18,
            padding=padding,
            expand=expand,
            shadow=[
                ft.BoxShadow(offset=ft.Offset(4, 4), blur_radius=10,
                             color=_op(0.35, "#96a5b9")),
                ft.BoxShadow(offset=ft.Offset(-4, -4), blur_radius=10,
                             color=_op(0.9, ft.Colors.WHITE)),
            ],
            **kwargs,
        )
        if content is not None:
            # 数据展示槽模式
            self.text_field = None
            self.content = content
        else:
            self.text_field = ft.TextField(
                hint_text=hint_text,
                multiline=multiline,
                border=ft.NoInputBorder(),
                filled=True,
                bgcolor=ft.Colors.TRANSPARENT,
                text_size=13,
                on_submit=on_submit,
                max_lines=3 if multiline else 1,
            )
            self.content = self.text_field

    @property
    def value(self):
        return self.text_field.value if self.text_field else None

    @value.setter
    def value(self, v):
        if self.text_field:
            self.text_field.value = v
            try:
                self.update()
            except Exception:
                pass


# ====================== 拟物圆形按钮 ======================
class NeuCircleButton(ft.Container):
    """拟物圆形图标按钮: 用于发送、麦克风等操作."""

    def __init__(self, icon, on_click=None, primary: bool = False, **kwargs):
        super().__init__(
            width=44,
            height=44,
            border_radius=999,
            on_click=on_click,
            alignment=ft.Alignment(0, 0),
            **kwargs,
        )
        if primary:
            self.gradient = ft.LinearGradient(
                begin=ft.Alignment(-1, -1),
                end=ft.Alignment(1, 1),
                colors=["#5aa3d6", COLORS["primary"]],
            )
            self.content = ft.Icon(icon, color=ft.Colors.WHITE, size=18)
        else:
            self.gradient = ft.LinearGradient(
                begin=ft.Alignment(-1, -1),
                end=ft.Alignment(1, 1),
                colors=["#ffffff", "#e6ecf3"],
            )
            self.content = ft.Icon(icon, color=COLORS["neutral"], size=18)

        self.shadow = [
            ft.BoxShadow(offset=ft.Offset(0, 1), blur_radius=1,
                         color=_op(0.9, ft.Colors.WHITE)),
            ft.BoxShadow(offset=ft.Offset(5, 6), blur_radius=14,
                         color=_op(0.35, "#96a5b9")),
            ft.BoxShadow(offset=ft.Offset(-4, -5), blur_radius=12,
                         color=_op(0.9, ft.Colors.WHITE)),
        ]


# ====================== 聊天气泡 ======================
class ChatBubble(ft.Container):
    """拟物聊天气泡.

    is_right: True=右侧(家属发送, 微信绿) False=左侧(设备/老人, 白色)
    is_voice: True 时显示语音图标与"点击播放"提示
    """

    def __init__(self, text: str, time_str: str = "", is_right: bool = False,
                 is_voice: bool = False, on_click=None, **kwargs):
        bg_color = COLORS["wechat_green"] if is_right else ft.Colors.WHITE
        super().__init__(
            padding=12,
            border_radius=22,
            bgcolor=bg_color,
            on_click=on_click,
            **kwargs,
        )
        body = ft.Row(spacing=6, tight=True) if is_voice else None
        if is_voice:
            body.controls.append(ft.Icon(ft.Icons.PLAY_CIRCLE_OUTLINED,
                                         size=16, color=COLORS["neutral"]))
            body.controls.append(ft.Text(text, size=13, color="#333"))
            main = body
        else:
            main = ft.Text(text, size=13, color="#333")
        self.content = ft.Column(
            [main]
            + ([ft.Text(time_str, size=10, color=COLORS["neutral"],
                        text_align=ft.TextAlign.RIGHT)] if time_str else []),
            spacing=2,
            horizontal_alignment=ft.CrossAxisAlignment.END,
            tight=True,
        )
