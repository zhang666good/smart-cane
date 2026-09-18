"""设备端主应用: 编排 传感上报 / MQTT 下行 / 模式切换 / SOS / 跌倒检测 / poll 兜底.

运行: 本项目扁平拷贝到 EC200U /usr/libs/, 使用 QPYcom 执行 app_main.py.
(与 usr/d.py 一致: 依赖 /usr 在 sys.path, usr.libs.* 包式导入)
"""
import utime as time
import _thread

from usr.libs.config import get as cfg_get
from usr.libs.sensors import (AccelSensor, TempHumSensor, LightSensor, LbsLocator)
from usr.libs.mqtt_down import MqttDown
from usr.libs.media import play_url, AmrStreamRecorder, upload_voice_reply, upload_voice_ai
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

# LBS 定位缓存: {ts, data}. 由后台线程 lbs_loop 刷新, collect_data 只读缓存.
_lbs_cache = {"ts": 0, "data": None}

# 三轴缓存: {ts,x,y,z}. 由后台线程 accel_loop 刷新, 上报与跌倒检测只读缓存.
# 原因: 录音后音频 codec 会让 accel 的 I2C 读阻塞且不可中断, 内联读取会钉死上报线程.
_accel_cache = {"ts": 0, "x": 0.0, "y": 0.0, "z": 0.0}


def now_str():
    return str(time.localtime())


def collect_data(include_position=True):
    """采集板载传感, 组装上报 data dict(结构与 backend SensorReport.data 对应).
    accel 由后台线程 accel_loop 刷新缓存(录音后 I2C 会挂, 内联读会钉死本线程);
    th/light 仍内联, 逐段打点便于后续定位."""
    data = {}
    try:
        a = _accel_cache
        if a["ts"] > 0:
            data["accel"] = {"x": round(a["x"], 3), "y": round(a["y"], 3), "z": round(a["z"], 3)}
    except Exception as e:
        print("accel cache err:", e)
    try:
        print("[sensor]   th读前")
        h, t = th.read()
        print("[sensor]   th读后")
        data["humidity"] = round(h, 2)
        data["temperature"] = round(t, 2)
    except Exception as e:
        print("th err:", e)
    try:
        print("[sensor]   light读前")
        resis, lux = light.read()
        print("[sensor]   light读后")
        if lux:
            data["light"] = lux
    except Exception as e:
        print("light err:", e)
    if include_position:
        # LBS 定位由后台线程刷新缓存(lbs_loop), 这里只读缓存, 瞬时返回.
        # 原因: net.getCellInfo() 在录音/上传占用网络时会挂起, 内联读取会把上报线程钉死.
        try:
            pos = _lbs_cache.get("data")
            if pos:
                data["position"] = pos
        except Exception as e:
            print("lbs cache err:", e)
    return data


# ---------- 跌倒检测(简化: 合成加速度超阈值连续若干次) ----------
_fall_count = 0

def check_fall_trigger():
    global _fall_count
    try:
        a = _accel_cache
        if a["ts"] <= 0:
            return None
        mag = (a["x"]**2 + a["y"]**2 + a["z"]**2) ** 0.5
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
        from audio import Audio, TTS
        # 复位被录音(Record(2))占用的喇叭通道, 否则 TTS 无声
        try:
            ta = Audio(2)
            ta.stop()
            ta.stopPlayStream()
            print("[AUDIO] local_tts 前已复位 Audio(2)")
        except Exception:
            pass
        tts = TTS(2)                 # EC200U 仅支持喇叭(通道2)
        t_ret = tts.play(4, 1, 2, text)      # priority=4(高), breakin=1(可打断), mode=2(UTF-8)
        print("[AUDIO] TTS play ret:", t_ret, "text:", text)
    except Exception as e:
        print("tts error:", e, text)


# ---------- 主线程: 传感采集 + SOS ----------
_rep_count = [0]

def sensor_loop():
    """传感上报循环. 整体 try/except: 单次上报异常/超时不得让线程死亡, 否则上报永久停止.
    逐段打点: 便于定位是卡在『传感器读取』还是『网络上报』.
    """
    global sos_active
    print("[sensor] sensor_loop 启动")
    while True:
        try:
            interval = cfg_get("sos_interval_s" if sos_active else "sensor_interval_s", 10)
            _rep_count[0] += 1
            t0 = time.ticks_ms()
            print("[sensor] #%d 采集前" % _rep_count[0])
            data = collect_data()
            print("[sensor] #%d 采集后, 用时 %dms" % (_rep_count[0],
                                                     time.ticks_diff(time.ticks_ms(), t0)))
            print("report data:", data)
            resp = http_api.report_sensor(data)
            print("[sensor] #%d 上报耗时 %dms resp=%s" % (
                _rep_count[0], time.ticks_diff(time.ticks_ms(), t0), resp))
            check_fall_trigger()
        except Exception as e:
            print("[sensor] 上报异常(已捕获,继续循环):", e)
        time.sleep(interval)


def lbs_loop():
    """后台 LBS 定位: 独立线程刷新 _lbs_cache, 与上报热路径解耦.

    net.getCellInfo() 速度慢且在录音/上传抢网时会挂起; 原内联读取会把
    sensor_loop 钉死。这里单独线程刷新缓存, 即使挂死也只影响本线程,
    上报线程始终读上一帧缓存(瞬时)。
    """
    print("[lbs] lbs_loop 启动")
    while True:
        interval = cfg_get("lbs_interval_s", 30)
        try:
            pos = lbs.get_lbs_data()
            if pos:
                _lbs_cache["ts"] = time.ticks_ms()
                _lbs_cache["data"] = pos
                print("[lbs] 刷新成功:", pos.get("lac"), pos.get("cid"))
        except Exception as e:
            print("[lbs] 刷新异常:", e)
        time.sleep(interval)


def accel_loop():
    """后台三轴读取: 独立线程刷新 _accel_cache, 与上报/跌倒检测解耦.

    accel 的 I2C 读在录音(audio codec)后会阻塞且无超时, 内联读取会把
    sensor_loop 钉死。这里单独线程刷新缓存; 即使某次读挂死, 也只影响本线程,
    上报与跌倒检测始终读上一帧缓存, 线程不会因此死亡。
    """
    print("[accel] accel_loop 启动")
    while True:
        interval = cfg_get("accel_interval_s", 1)
        try:
            x, y, z = accel.read()
            _accel_cache["ts"] = time.ticks_ms()
            _accel_cache["x"] = x
            _accel_cache["y"] = y
            _accel_cache["z"] = z
            print("[accel] 刷新: %.3f %.3f %.3f" % (x, y, z))
        except Exception as e:
            print("[accel] 读取异常(缓存沿用上帧):", e)
        time.sleep(interval)


# ---------- 按键监听(电源键: 单击=留言开关, 双击=AI对话) ----------
_recoder = [None]       # AmrStreamRecorder or None
_recording = [False]    # 是否正在录音
_rec_target = [None]    # 当前录音去向: 'family'(留言) / 'ai'(AI对话)
_started = [False]      # 独立线程是否已拉起(避免重复启动)
_tap_q = [0]            # 当前 tap 分组累计次数(静默窗口结束后结算)
_tap_last = [0]         # 最近一次 tap 的时刻(ms)
_flow_busy = [False]    # 录音/上传流程是否正在执行

# _lock 用于保护上述列表量(QuecPython 下 _thread 共享全局无需锁, 此处仅防回调/线程竞争)


def _power_key_cb(status):
    """电源键回调(瞬时返回, 不阻塞): 仅按「单击」登记 tap 次数.
    具体动作由 _rec_worker 在静默窗口结束后按 tap 次数结算:
      1下=留言录音开关; 2下=进入AI对话录音; AI录音中1下=提交.
    """
    if status == 1:                          # 按下: 无动作
        print("[按键] 电源键按下")
    elif status == 0:                        # 松开 = 一次 tap
        if _flow_busy[0]:
            print("[按键] 正忙, 本次 tap 忽略")
            return
        _tap_q[0] += 1
        _tap_last[0] = time.ticks_ms()
        print("[按键] tap #%d (静默窗口结算)" % _tap_q[0])


def _rec_worker():
    """独立录音线程: 静默窗口结束后按 tap 次数结算动作."""
    _started[0] = True
    double_ms = int(cfg_get("double_tap_ms", 300))
    try:
        while True:
            now = time.ticks_ms()
            # 静默窗口(double_tap_ms)内无新 tap -> 结算本组
            if _tap_q[0] > 0 and time.ticks_diff(now, _tap_last[0]) >= double_ms:
                n = _tap_q[0]
                _tap_q[0] = 0
                print("[按键] 结算 tap 组, 次数=%d" % n)
                _flow_busy[0] = True
                try:
                    if _recording[0] and _rec_target[0] == "ai":
                        # AI 录音中: 单击即停止并提交
                        _stop_and_upload("ai")
                    elif n >= 2:
                        # 双击: 进入 AI 对话录音
                        _begin_record("ai", with_delay=False)
                    else:
                        # 单击: 家人留言录音开关
                        _toggle_family_record()
                finally:
                    _flow_busy[0] = False
            time.sleep_ms(50)
    except Exception as e:
        print("[录音] worker 异常:", e)
        _started[0] = False


def _begin_record(target, with_delay):
    """开始录音(target: 'family'/'ai'); with_delay 表示是否预留按键防抖延时."""
    if _recording[0]:
        return
    if with_delay:
        delay = float(cfg_get("record_start_delay_s", 1.0))
        if delay > 0:
            print("[录音] %s 秒后开始录音..." % delay)
            time.sleep_ms(int(delay * 1000))
    print("[录音] 开始录音...")
    _recoder[0] = AmrStreamRecorder()
    if _recoder[0].start():
        _recording[0] = True
        _rec_target[0] = target
        print("[录音] 录音已开始 target:", target)
        local_tts("开始录音")
    else:
        _recoder[0] = None
        print("[录音] 启动失败")
        local_tts("录音失败")


def _stop_and_upload(target):
    """停止当前录音并按 target 提交: 'family'->留言, 'ai'->AI对话. 返回是否成功."""
    _recording[0] = False
    rec = _recoder[0]
    _recoder[0] = None
    path = rec.stop() if rec else None
    if not path:
        print("[录音] 未生成文件")
        local_tts("录音失败")
        return False
    print("[录音] 录音文件:", path)
    local_tts("正在发送")
    ok = False
    reply_text = None
    try:
        print("[录音] 上传中...")
        if target == "ai":
            resp = upload_voice_ai(path)
            reply_text = (resp or {}).get("reply")
        else:
            resp = upload_voice_reply(path)
        ok = bool(resp and resp.get("ok"))
        print("[录音] 上传应答:", resp)
    except Exception as e:
        print("[录音] 上传错误:", e)
    print("[录音] 上传", "成功" if ok else "失败")
    if ok:
        au = (resp or {}).get("audio_url")
        if target == "ai" and au:
            # AI 回复走 MQTT 排队下发不可靠(当时在播本地TTS被 queued), 直接播返回的音频
            if reply_text:
                print("[AI] 回复:", reply_text)
            print("[AI] 播放回复音频:", au)
            play_url(au)
        else:
            if reply_text is not None:
                print("[AI] 回复:", reply_text)
            local_tts("发送成功")
    else:
        local_tts("发送失败")
    return ok


def _toggle_family_record():
    """家人留言录音开关(短按): 未录音->开始; 录音中->停止并上传到家人留言."""
    if not _recording[0] or _rec_target[0] != "family":
        if _recording[0]:
            print("[录音] 已在录音(target=%s), 取消本次切换" % _rec_target[0])
            return
        _begin_record("family", with_delay=True)
        return
    _stop_and_upload("family")


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

    # 2. 启动传感上报线程 + 录音/上传线程 + 按键监听 + LBS定位/三轴后台线程
    _thread.start_new_thread(sensor_loop, ())
    _thread.start_new_thread(lbs_loop, ())
    _thread.start_new_thread(accel_loop, ())
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
