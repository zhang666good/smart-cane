"""智能拐杖 · 老人家人端 App (Flet 1.0+).

功能: 登录 -> 观测绑定设备传感 / 切换对话模式 / 发送文字关怀 / 语音留言双向 / 查看老人回复.
运行:  cd software && uv run flet run
"""
import os
import subprocess
import sys
import datetime
import flet as ft

from api_client import BackendClient, BackendError
from config import settings
from my_logger import configure_logging, get_logger
from flet_audio_recorder import AudioEncoder, AudioRecorder, AudioRecorderConfiguration

configure_logging(level="DEBUG")       # 家人端启动即打印详细日志到控制台+logs/app.log
log = get_logger("cane.app.ui")


# ---------------------------------------------------------------- App 状态
class AppState:
    def __init__(self):
        self.api = BackendClient()
        self.login_info = None   # {token, role, user_id, name, device_id}
        self.devices = []
        self.current_device = None   # 当前选中操作的设备 id; 通过"选择绑定设备"设置

STATE = AppState()


# ---------------------------------------------------------------- 通用控件
def make_card(content, title=None, color=ft.Colors.BLUE_50):
    rows = []
    if title:
        rows.append(ft.Text(title, style=ft.TextStyle(size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.BLACK_87)))
    rows.append(content)
    return ft.Container(
        content=ft.Column(rows, spacing=8),
        bgcolor=color, border_radius=ft.BorderRadius.all(12),
        padding=16, margin=ft.Margin(top=8, bottom=8),
    )


def snack(page, msg):
    page.snack_bar = ft.SnackBar(ft.Text(msg), open=True)
    page.update()


def play_local_audio(path):
    """用系统默认播放器打开本地音频(跨平台)."""
    log.info("播放音频: %s", path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)          # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        log.error("播放音频失败: %s path=%s", exc, path)


async def run_blocking(page, fn, ok_msg="完成"):
    """在后台线程执行阻塞 IO, 完成回主线程更新."""
    page.run_thread(fn)
    # 简单起见此处不阻塞等待; 具体调用处通过回调展示结果


# ---------------------------------------------------------------- 登录页
def build_login(page):
    user = ft.TextField(label="账号", value=settings.demo_user)
    pwd = ft.TextField(label="密码", value=settings.demo_password, password=True)
    status = ft.Text("", color=ft.Colors.RED)

    async def do_login(e):
        try:
            log.info("UI 登录点击: user=%s", user.value.strip())
            info = STATE.api.login(user.value.strip(), pwd.value.strip())
            STATE.login_info = info
            STATE.api.token = info["token"]
            status.value = "登录成功: " + info.get("name", "")
            status.color = ft.Colors.GREEN
            page.update()
            log.info("UI 登录成功: name=%s role=%s device=%s",
                     info.get("name"), info.get("role"), info.get("device_id"))
            build_home(page)
        except BackendError as ex:
            log.warning("UI 登录失败: %s", ex)
            status.value = str(ex)
            status.color = ft.Colors.RED
            page.update()

    return ft.Column([
        ft.Text("智能拐杖 · 家人端", size=24, weight=ft.FontWeight.BOLD),
        ft.Text("关爱不缺席", color=ft.Colors.GREY),
        user, pwd,
        ft.FilledButton("登录", on_click=do_login),
        status,
    ], spacing=16, width=360, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)


# ---------------------------------------------------------------- 主界面
def clear_content(page):
    page.controls.clear()
    page.update()


def add_back_button(page):
    def back(e):
        build_home(page)
    page.appbar = ft.AppBar(
        leading=ft.IconButton(ft.Icons.ARROW_BACK, on_click=back),
        title=ft.Text("返回"), bgcolor=ft.Colors.BLUE,
    )


def add_top_bar(page, title, subtitle=""):
    page.appbar = ft.AppBar(
        title=ft.Column([ft.Text(title, size=20, color=ft.Colors.WHITE), ft.Text(subtitle, size=12, color=ft.Colors.WHITE_70)]),
        bgcolor=ft.Colors.BLUE, center_title=False,
    )
    page.navigation_bar = None
    page.update()


async def load_sensors(device_id, disp, page):
    log.info("加载传感: device=%s", device_id)
    try:
        data = STATE.api.sensors(device_id)
    except BackendError as ex:
        log.error("加载传感失败: device=%s err=%s", device_id, ex)
        disp.value = "获取失败: " + str(ex)
        page.update(); return
    if not data:
        disp.value = "暂无传感数据(设备可能未上报)"; page.update(); return
    accel = data.get("accel") or {}
    lines = [
        f" 温度: {data.get('temperature')} °C",
        f" 湿度: {data.get('humidity')} %",
        f" 光照: {data.get('light')} lux",
        f" 三轴: x={accel.get('x')} y={accel.get('y')} z={accel.get('z')}",
    ]
    pos = data.get("position")
    if pos:
        lines.append(f" LBS: lac={pos.get('lac')} cid={pos.get('cid')}")
    disp.value = "\n".join(lines)
    page.update()


def build_home(page):
    info = STATE.login_info
    page.navigation_bar = None
    page.appbar = ft.AppBar(
        title=ft.Text(f"欢迎, {info.get('name','')}", color=ft.Colors.WHITE),
        bgcolor=ft.Colors.BLUE,
    )

    # 载入设备
    try:
        STATE.devices = STATE.api.my_devices()
    except BackendError as ex:
        log.error("加载设备列表失败: %s", ex)
        page.controls = [make_card(ft.Text("设备加载失败: " + str(ex)), title="我的设备")]
        page.update(); return
    log.info("加载设备列表 OK: count=%s", len(STATE.devices))

    dids = [(d.get("device_id") or d.get("id")) for d in STATE.devices]
    dids = [x for x in dids if x]

    # 绑定/切换入口: 始终可见, 无论是否有设备
    top = ft.Row([
        ft.FilledButton(
            f"绑定设备: {STATE.current_device or '(未绑定)'}",
            icon=ft.Icons.SWAP_HORIZ,
            on_click=lambda e: build_device_select(page),
        ),
        ft.IconButton(ft.Icons.REFRESH, tooltip="刷新设备列表",
                      on_click=lambda e: build_home(page)),
    ], spacing=8)

    if not dids:
        page.controls = [
            make_card(ft.Column([
                ft.Text("暂无绑定设备, 请先绑定一台设备"),
                ft.FilledButton("+ 去绑定新设备",
                                on_click=lambda e: build_device_select(page)),
            ]), title="我的设备"),
            make_card(top, title="切换绑定设备"),
        ]
        page.update(); return

    # 默认拉当前用户预置设备; 未选择则默认第一个(保证可立即操作)
    if STATE.current_device not in dids:
        if STATE.login_info and STATE.login_info.get("device_id") in dids:
            STATE.current_device = STATE.login_info.get("device_id")
        else:
            STATE.current_device = dids[0]

    d = STATE.current_device
    page.controls = [
        make_card(
            ft.Column([
                ft.Text(f"设备: {d}", weight=ft.FontWeight.BOLD),
                ft.Row([
                    ft.TextButton("观测状态", on_click=lambda e, x=d: build_device(page, x)),
                    ft.TextButton("发消息", on_click=lambda e, x=d: build_message(page, x)),
                    ft.TextButton("语音留言", on_click=lambda e, x=d: build_voice(page, x)),
                    ft.TextButton("模式切换", on_click=lambda e, x=d: build_mode(page, x)),
                ], spacing=4, wrap=True),
            ]),
            title="当前绑定设备"
        ),
        make_card(top, title="切换绑定设备"),
    ]
    page.update()


def build_device_select(page):
    """选择绑定设备: 列出所有已绑定设备 + 手动绑定新设备输入框."""
    add_back_button(page)
    rows = ft.Column([], spacing=8)
    for d in STATE.devices:
        did = d.get("device_id") or d.get("id") or "?"
        mark = "  ✔ 当前" if did == STATE.current_device else ""
        rows.controls.append(ft.FilledButton(
            f"设备 {did}{mark}",
            on_click=lambda e, x=did: _pick_device(page, x),
        ))

    bind_id = ft.TextField(label="新设备ID", hint_text="如 dev-001", multiline=False)
    bind_status = ft.Text("", size=12, color=ft.Colors.GREY)

    async def do_bind(e):
        did = bind_id.value.strip()
        if not did:
            bind_status.value = "请输入设备ID"; bind_status.color = ft.Colors.RED
            page.update(); return
        try:
            log.info("UI 手动绑定设备: %s", did)
            body = STATE.api.bind_device(did)
            bind_status.value = f"绑定成功: {did}" if body.get("created") else f"已在绑定列表"
            bind_status.color = ft.Colors.GREEN
            bind_id.value = ""
            # 刷新设备列表并回到主页选中新设备
            STATE.devices = STATE.api.my_devices()
            STATE.current_device = did
            log.info("UI 绑定后刷新设备列表: %s", did)
            page.update()
            build_home(page)
        except BackendError as ex:
            log.error("UI 绑定设备失败: device=%s err=%s", did, ex)
            bind_status.value = str(ex); bind_status.color = ft.Colors.RED
            page.update()

    page.controls = [
        make_card(rows, title="已绑定设备"),
        make_card(
            ft.Column([
                bind_id,
                ft.FilledButton("+ 绑定新设备", on_click=do_bind),
                bind_status,
            ]),
            title="手动绑定新设备",
        ),
    ]
    page.update()


def _pick_device(page, device_id):
    STATE.current_device = device_id
    log.info("UI 选择绑定设备: %s", device_id)
    snack(page, "已选择设备: " + device_id)
    build_home(page)


def build_device(page, device_id):
    disp = ft.Text("加载中...")
    add_back_button(page)
    page.controls = [make_card(disp, title=f"实时传感 · {device_id}")]
    page.update()
    page.run_task(load_sensors, device_id, disp, page)


def build_message(page, device_id):
    add_back_button(page)
    txt = ft.TextField(label="关怀内容", multiline=True, min_lines=3)
    async def send(e):
        try:
            log.info("UI 发送文字关怀: device=%s text=%r", device_id, txt.value.strip())
            ok = STATE.api.send_message(device_id, txt.value.strip())
            snack(page, "已发送" if ok else "发送失败")
        except BackendError as ex:
            log.error("发送文字关怀失败: device=%s err=%s", device_id, ex)
            snack(page, str(ex))
    page.controls = [make_card(
        ft.Column([txt, ft.FilledButton("发送给老人", on_click=send)]),
        title=f"发送文字关怀 · {device_id}",
    )]
    page.update()


def build_mode(page, device_id):
    add_back_button(page)
    async def sw(e, mode):
        try:
            log.info("UI 切换对话模式: device=%s mode=%s", device_id, mode)
            ok = STATE.api.send_message(device_id, f"切换到{mode}", "mode_switch")
            snack(page, "已请求切换: " + mode)
        except BackendError as ex:
            log.error("切换模式失败: device=%s mode=%s err=%s", device_id, mode, ex)
            snack(page, str(ex))
    def make_switch(label, mode, is_primary=False):
        # 返回 async 闭包作为 on_click, Flet 会正确 await(coroutine 不会被丢弃)
        async def on_click(e):
            await sw(e, mode)
        btn_cls = ft.FilledButton if is_primary else ft.OutlinedButton
        return btn_cls(label, on_click=on_click)

    page.controls = [make_card(
        ft.Column([
            ft.Text("选择对话模式(云端/设备协同)"),
            make_switch("AI 陪伴", "ai", is_primary=True),
            make_switch("切换到家人对话", "family"),
            make_switch("切换到管理端", "admin"),
        ]),
        title=f"对话模式切换 · {device_id}",
    )]
    page.update()


def build_voice(page, device_id):
    add_back_button(page)

    rec = ft.TextField(label="或输入文字转语音", multiline=False)
    msgs = ft.Column([], spacing=6, scroll=ft.ScrollMode.AUTO)

    # ---- 音频录制(经 flet-audio-recorder 服务控件; 服务需挂到 overlay) ----
    recorder = AudioRecorder(
        configuration=AudioRecorderConfiguration(
            encoder=AudioEncoder.WAV,           # 标准 WAV: 与 EC200U 已验证可播的 44.1k 一致
            sample_rate=44100,                  # 标准 44.1kHz
            channels=2,                         # 立体声
            # 位深: flet-audio-recorder WAV 默认 16bit
        )
    )
    page.services.append(recorder)
    rec_state = ft.Text("", size=12, color=ft.Colors.GREY)

    async def do_record(e):
        try:
            import uuid as _uuid
            has_perm = await recorder.has_permission()
            if not has_perm:
                snack(page, "无麦克风权限, 请授权后重试"); return
            os.makedirs(settings.audio_cache_dir, exist_ok=True)
            out = os.path.join(settings.audio_cache_dir, f"fam_{_uuid.uuid4().hex}.wav")
            log.info("UI 开始录音 -> %s", out)
            ok = await recorder.start_recording(output_path=out)
            rec_state.value = "录音中... (点停止并上传结束)" if ok else "启动失败"
            page.update()
        except Exception as ex:
            log.error("录音启动失败: %s", ex)
            snack(page, "录音失败: " + str(ex))

    async def do_stop_upload(e):
        try:
            log.info("UI 停止录音并上传")
            path = await recorder.stop_recording()  # 返回保存路径
            if not path:
                snack(page, "未获取到录音文件"); page.update(); return
            log.info("录音已保存: %s -> 上传", path)
            rec_state.value = "上传中..."
            page.update()
            sender = STATE.login_info.get("user_id", "family")
            r = STATE.api.send_voice(device_id, sender, path)
            snack(page, "语音已发送给老人")
            rec_state.value = ""
            page.update()
        except Exception as ex:
            log.error("上传录音失败: %s", ex)
            snack(page, "上传失败: " + str(ex))
            rec_state.value = ""

    # ---- 老人留言列表(含播放) ----
    async def refresh(e):
        try:
            log.info("UI 刷新语音留言: device=%s", device_id)
            data = STATE.api.voice_messages(device_id) or []
            msgs.controls.clear()
            if not data:
                msgs.controls.append(ft.Text("(暂无老人留言)", color=ft.Colors.GREY))
            for m in data:
                who = "老人" if m.get("direction") == "to-sender" else "家人/管理"
                text = m.get("text") or "[语音]"
                t = m.get("created_at", "")
                row = [ft.Text(f"{t} {who}: {text}", size=13, expand=True)]
                if m.get("audio_url"):
                    mid = int(m.get("id", 0))
                    row.append(ft.IconButton(
                        ft.Icons.PLAY_CIRCLE_OUTLINE, tooltip="播放",
                        on_click=lambda e, u=m.get("audio_url"): play_audio(page, u)))
                    row.append(ft.IconButton(
                        ft.Icons.DONE_OUTLINE, tooltip="标记已读",
                        on_click=lambda e, x=mid: mark_read(page, x)))
                msgs.controls.append(ft.Row(row, spacing=4))
            log.info("UI 语音留言刷新 OK: device=%s count=%s", device_id, len(data))
            page.update()
        except BackendError as ex:
            log.error("刷新语音留言失败: device=%s err=%s", device_id, ex)
            msgs.controls.clear()
            msgs.controls.append(ft.Text("加载失败: " + str(ex), color=ft.Colors.RED))
            page.update()

    async def play_audio(page, url):
        try:
            log.info("UI 拉取并播放老人音频: %s", url)
            local = STATE.api.download_audio(
                url, settings.audio_cache_dir,
                filename=os.path.basename(url.split("?")[0]) or "voice.bin")
            play_local_audio(local)
            snack(page, "已下载并在系统播放器打开")
        except BackendError as ex:
            log.error("拉取音频失败: %s err=%s", url, ex)
            snack(page, "拉取音频失败: " + str(ex))

    async def mark_read(page, msg_id):
        try:
            STATE.api.mark_read(msg_id)
            log.info("UI 标记已读: msg_id=%s", msg_id)
            snack(page, "已标记已读")
            await refresh(None)
        except Exception as ex:
            log.error("标记已读失败: msg_id=%s err=%s", msg_id, ex)
            snack(page, "操作失败: " + str(ex))

    async def send_text(e):
        try:
            log.info("UI 文字TTS下发: device=%s text=%r", device_id, rec.value.strip())
            STATE.api.send_message(device_id, rec.value.strip())
            snack(page, "文字已下发(设备TTS播报)")
        except BackendError as ex:
            log.error("文字TTS下发失败: device=%s err=%s", device_id, ex)
            snack(page, str(ex))

    page.controls = [make_card(
        ft.Column([
            ft.Row([rec, ft.FilledButton("文字下发", on_click=send_text)]),
            ft.Row([
                ft.FilledButton("🎤 录音", on_click=do_record),
                ft.FilledButton("⏹ 停止并上传", on_click=do_stop_upload),
                ft.TextButton("刷新留言", on_click=refresh),
            ]),
            rec_state,
            ft.Divider(),
            ft.Text("老人回复留言:", weight=ft.FontWeight.BOLD),
            msgs,
        ]),
        title=f"语音留言 · {device_id}",
    )]
    page.update()


# ---------------------------------------------------------------- 入口
async def main(page: ft.Page):
    page.title = "智能拐杖 · 家人端"
    page.scroll = ft.ScrollMode.AUTO
    page.padding = 20
    page.theme_mode = ft.ThemeMode.LIGHT
    page.controls = [build_login(page)]
    page.update()


if __name__ == "__main__":
    ft.run(main)
