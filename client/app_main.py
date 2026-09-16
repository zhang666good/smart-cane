"""设备端主应用: 编排 传感上报 / MQTT 下行 / 模式切换 / SOS / 跌倒检测 / poll 兜底.

运行: 本项目扁平拷贝到 EC200U /usr/libs/, 使用 QPYcom 执行 app_main.py.
(与 usr/d.py 一致: 依赖 /usr 在 sys.path, usr.libs.* 包式导入)
"""
import utime as time
import _thread

from usr.libs.config import get as cfg_get
from usr.libs.sensors import (AccelSensor, TempHumSensor, LightSensor, LbsLocator)
from usr.libs.mqtt_down import MqttDown
from usr.libs.media import play_url, AmrStreamRecorder, upload_voice_reply
from usr.libs import http_api

# ---------- 硬件单例(按实际板载使能) ----------
accel = AccelSensor()      # 三轴(跌倒检测 + 上报)
th = TempHumSensor()       # 温湿
light = LightSensor()      # 光敏
lbs = LbsLocator()         # LBS定位

# ---------- 状态 ----------
# 对话模式: ai / family / admin ; 云端权威, 设备端接收 mode_switch 更新
current_mode = "ai"
# SOS 标志
sos_active = False
# 播放忙标志(用于高低优先级判断)
playing = False

# 锁(QuecPython/MicroPython 用 _thread.allocate_lock; 供跨线程保护状态)
_lock = _thread.allocate_lock()


def now_str():
    return str(time.localtime())


def collect_data(include_position=True):
    """采集板载传感, 组装上报 data dict(结构与 backend SensorReport.data 对应)."""
    data = {}
    try:
        x, y, z = accel.read()
        data["accel"] = {"x": round(x, 3), "y": round(y, 3), "z": round(z, 3)}
    except Exception as e:
        print("accel err:", e)
    try:
        h, t = th.read()
        data["humidity"] = round(h, 2)
        data["temperature"] = round(t, 2)
    except Exception as e:
        print("th err:", e)
    try:
        resis, lux = light.read()
        if lux:
            data["light"] = lux
    except Exception as e:
        print("light err:", e)
    if include_position:
        try:
            pos = lbs.get_lbs_data()
            if pos:
                data["position"] = pos
        except Exception as e:
            print("lbs err:", e)
    return data


# ---------- 跌倒检测(简化: 合成加速度超阈值连续若干次) ----------
_fall_count = 0

def check_fall_trigger():
    global _fall_count
    try:
        mag = accel.magnitude()
    except Exception:
        return None
    thr = cfg_get("fall_detect_threshold_g", 1.6)
    rec = cfg_get("fall_detect_recover_g", 1.0)
    samples = cfg_get("fall_detect_samples", 3)
    if mag >= thr:
        _fall_count += 1
        if _fall_count >= samples:
            _fall_count = 0
            print("FALL DETECTED! mag=%.2f" % mag)
            _trigger_sos("检测到跌倒, 请确认")
            return True
    else:
        # 回落到恢复阈值以下才清计数
        if mag < rec:
            _fall_count = 0
    return False


def _trigger_sos(msg):
    global sos_active
    sos_active = True
    http_api.report_sos(msg)


# ---------- 下行指令处理 ----------
def _handle_cmd(cmd):
    global current_mode, sos_active
    ctype = cmd.get("type")
    print("handle cmd:", ctype)
    if ctype == "speak":
        # 文本播报: 优先本地TTS, 有 audio_url 则拉音频
        u = cmd.get("audio_url")
        if u:
            play_url(u)
        else:
            local_tts(cmd.get("text", ""))
        http_api.ack_finished("speak")
    elif ctype in ("family_voice", "admin_tts", "family_tts", "ai"):
        u = cmd.get("audio_url")
        if u:
            play_url(u)
        else:
            local_tts(cmd.get("text", ""))
        http_api.ack_finished(ctype)
    elif ctype == "mode_switch":
        nm = cmd.get("mode") or cmd.get("text")
        if nm in ("ai", "family", "admin"):
            current_mode = nm
            print("mode switched to", nm)
            local_tts("已切换到%s对话" % nm)
    elif ctype == "sos":
        sos_active = True
        local_tts(cmd.get("text", "收到紧急通知"))
    elif ctype == "beep":
        print("beep")
    elif ctype == "config":
        print("config update:", cmd.get("payload"))


def local_tts(text):
    """文本转语音播报. EC200U 使用 audio.TTS (参考 usr/music.py 已验证用法):
    tts = TTS(2); tts.play(priority, breakin, mode, text)."""
    if not text:
        return
    try:
        from audio import TTS
        tts = TTS(2)                 # EC200U 仅支持喇叭(通道2)
        tts.play(4, 1, 2, text)      # priority=4(高), breakin=1(可打断), mode=2(UTF-8)
    except Exception as e:
        print("tts error:", e, text)


# ---------- 主线程: 传感采集 + SOS ----------
def sensor_loop():
    global sos_active
    while True:
        interval = cfg_get("sos_interval_s" if sos_active else "sensor_interval_s", 10)
        data = collect_data()
        print("report data:", data)
        http_api.report_sensor(data)
        check_fall_trigger()
        time.sleep(interval)


# ---------- 按键监听(电源键单击开关录音; 录音/上传在独立线程, 不阻塞回调) ----------
_pk_down = [0]          # 电源键按下的时刻(ms)
_recoder = [None]       # AmrStreamRecorder or None
_recording = [False]    # 是否正在录音
_started = [False]      # 独立线程是否已拉起(避免重复启动)
_pending = [False]      # 待处理的一次录音事件(开始 或 结束)
_flow_busy = [False]    # 录音/上传流程是否正在执行

# _lock 用于保护上述列表量(QuecPython 下 _thread 共享全局无需锁, 此处仅防回调/线程竞争)


def _power_key_cb(status):
    """电源键回调(瞬时返回, 不阻塞): 短按开关录音.
    非短按或播放中忽略; 实际录音/上传放到独立线程 _rec_worker.
    """
    if status == 1:                    # 按下
        _pk_down[0] = time.ticks_ms()
        print("[按键] 电源键按下")
    elif status == 0:                  # 松开
        print("[按键] 电源键松开")
        if _pk_down[0] > 0:
            dur = time.ticks_diff(time.ticks_ms(), _pk_down[0])
            print("[按键] 持续 %d 毫秒" % dur)
            # 松开即认定为单击: 按下->松开即执行录音开关(不再区分短按/长按)
            if not _flow_busy[0]:
                _pending[0] = True
            else:
                print("[录音] 正忙, 忽略本次按键")
            _pk_down[0] = 0


def _rec_worker():
    """独立录音线程: 消费 _pending 事件, 执行开始/结束+上传."""
    _started[0] = True
    try:
        while True:
            if _pending[0]:
                _pending[0] = False
                _flow_busy[0] = True
                try:
                    _toggle_record()
                finally:
                    _flow_busy[0] = False
            time.sleep_ms(100)
    except Exception as e:
        print("[录音] worker 异常:", e)
        _started[0] = False


def _toggle_record():
    """开始/结束录音: 结束时报 TTS 并自动上传(在独立线程执行)."""
    if not _recording[0]:
        # —— 开始录音 ——
        # 按键松开后延迟再开始(防抖/给反应时间), 由 record_start_delay_s 配置
        delay = float(cfg_get("record_start_delay_s", 1.0))
        if delay > 0:
            print("[录音] %s 秒后开始录音..." % delay)
            time.sleep_ms(int(delay * 1000))
        print("[录音] 开始录音...")
        _recoder[0] = AmrStreamRecorder()
        if _recoder[0].start():
            _recording[0] = True
            print("[录音] 录音已开始")
            local_tts("开始录音")
        else:
            _recoder[0] = None
            print("[录音] 启动失败")
            local_tts("录音失败")
        return
    # —— 停止并自动上传 ——
    print("[录音] 停止并上传...")
    _recording[0] = False
    path = _recoder[0].stop() if _recoder[0] else None
    _recoder[0] = None
    if not path:
        print("[录音] 未生成文件")
        local_tts("录音失败")
        return
    print("[录音] 录音文件:", path)
    local_tts("正在发送")
    ok = False
    try:
        print("[录音] 上传中...")
        resp = upload_voice_reply(path)
        ok = bool(resp and resp.get("ok"))
        print("[录音] 上传应答:", resp)
    except Exception as e:
        print("[录音] 上传错误:", e)
    print("[录音] 上传", "成功" if ok else "失败")
    local_tts("发送成功" if ok else "发送失败")


def key_loop():
    global sos_active
    # 注册物理按键(PowerKey), 失败则降级为轮询占位
    try:
        from misc import PowerKey
        pk = PowerKey()
        pk.powerKeyEventRegister(_power_key_cb, 1)
        print("PowerKey registered")
    except Exception as e:
        print("PowerKey fail:", e)
    while True:
        time.sleep(1)


# ---------- MQTT 空闲轮询 + 断连时 poll 兜底 ----------
def mqtt_worker(mqtt):
    while True:
        if mqtt.is_connected():
            mqtt.tick()
        else:
            # 断连时以 HTTP poll 兜底拉取下发
            try:
                http_api.poll(cfg_get("device_id"))
            except Exception as e:
                pass
        time.sleep(1)


def main():
    print("=== Smart Cane Device Start ===")

    # 0. Qth 平台初始化(注册物模型, 固件管理连接)
    try:
        from usr import Qth
        Qth.init()
        Qth.setProductInfo('p11z1z', 'T2NWT3M2eEhUMWlE')
        print("[QTH] init done")
    except Exception as e:
        print("[QTH] init fail:", e)

    # 1. 入网: Qth 已能连到公网(serUrl/DK 打印)即证明网络OK, 不再阻塞等待.
    #    (如需显式校验可用 usr.libs.networker.ensure_online(), 一般无需)

    # 2. 启动传感上报线程 + 录音/上传线程 + 按键监听
    _thread.start_new_thread(sensor_loop, ())
    if not _started[0]:
        _thread.start_new_thread(_rec_worker, ())
    _thread.start_new_thread(key_loop, ())

    # 3. 建立 MQTT 下行
    mqtt = MqttDown(handler=_handle_cmd)
    try:
        mqtt.start()
    except Exception as e:
        print("mqtt start error:", e)

    # 4. 主循环(非阻塞)
    mqtt_worker(mqtt)


if __name__ == "__main__":
    main()
