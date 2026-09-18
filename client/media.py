"""媒体处理: 下载云端音频 + 本地播放 + 录音(AMR)上传.

依赖 EC200U 平台 audio API (QuecPython 固件内置).
格式约定:
  - 播放: audio.Audio.play() 支持 mp3/amr/wav; 下发音频沿用云端原始格式
  - 录音上传: audio.Record(2).stream 产 AMR-NB, 补 "#!AMR\\n" 头, 存 .amr, multipart 上传
请在固件/真机上按实际 API 签名微调.

注: 本模块命名为 media 而非 audio, 以避免与 QuecPython 固件内置 audio 库同名冲突.
"""
import uos
import utime as time
import request
from audio import Audio

from usr.libs import http_api
from usr.libs.config import get as cfg_get


_SD_AUDIO_DIR = "/sd/cane_audio"       # SD 卡上的临时音频目录
_FLASH_AUDIO_DIR = "/usr/cane_audio"   # 未插 SD 卡时兜底内置文件系统
_sd_ok = False
_dir = None   # 实际生效的音频目录


def _mount_sd():
    """尝试挂载 SD 卡到 /sd (SDIO, 官方 VfsSd('sd_fs') + mount)."""
    try:
        from uos import VfsSd, mount
        device = VfsSd("sd_fs")
        mount(device, "/sd")
        return True
    except Exception as e:
        print("sd mount fail:", e)
        return False


def audio_dir():
    """返回实际可用的临时音频目录: 优先 SD 卡, 未插卡时兜底内置文件系统."""
    global _dir
    if _dir:
        return _dir
    _mount_sd()
    # uos 无 exists, 用 listdir 探测 /sd 是否可用
    try:
        uos.listdir("/sd")
        _dir = _SD_AUDIO_DIR
    except Exception:
        _dir = _FLASH_AUDIO_DIR          # 无 SD -> 兜底内置 flash
    try:
        uos.mkdir(_dir)
    except Exception:
        pass
    print("audio dir:", _dir)
    return _dir


def download_audio(audio_url, timeout_s=None):
    """下载音频到本地临时目录(优先 SD 卡, 无卡兜底内置 Flash), 返回本地路径; 失败 None.

    使用 QuecPython 官方 request.get(url, decode=False) + response.content 生成器逐块写入.
    """
    d = audio_dir()
    if d is None:
        return None
    filename = audio_url.split("/")[-1] or "tmp.wav"
    local_path = "%s/%s" % (d, filename)
    if not audio_url.startswith("http"):
        print("download skipped (non-http url):", audio_url)
        return None
    resp = None
    try:
        http_api._http_lock.acquire()
        try:
            resp = request.get(audio_url, decode=False,
                               timeout=cfg_get("http_timeout_s", 15))
            if resp.status_code != 200:
                print("download failed, status:", resp.status_code)
                return None
            # 尝试取 Content-Length 以便打印百分比进度
            total = None
            headers = getattr(resp, "headers", None)
            if isinstance(headers, dict):
                cl = headers.get("Content-Length") or headers.get("content-length")
            else:
                cl = None
            try:
                total = int(cl) if cl else None
            except Exception:
                total = None
            with open(local_path, "wb") as f:
                size = 0
                bucket = -1
                for chunk in resp.content:
                    if not chunk:
                        break
                    # QuecPython 下 content 生成器元素可能为 bytes/int
                    d_ = bytes([chunk]) if isinstance(chunk, int) else chunk
                    f.write(d_)
                    size += len(d_)
                    if total:
                        pct = size * 100 // total
                        b = pct // 25
                        if b != bucket:
                            bucket = b
                            print("download progress: %d%% (%d/%d)" % (pct, size, total))
                    else:
                        if size >= (bucket + 1) * 20000:
                            bucket += 1
                            print("download progress: %d bytes" % size)
            print("download complete:", local_path, "size:", size,
                  ("total: " + str(total) if total else ""))
            return local_path
        finally:
            http_api._http_lock.release()
    except Exception as e:
        print("download except:", e)
        return None
    finally:
        if resp:
            resp.close()


def _wav_duration_s(path):
    """解析标准 WAV 头得到时长秒数; 失败返回 None.

    标准 RIFF: 采样率@24(4B le), 声道@22(2B le), 位深@34(2B le);
    data 大小取 data 子块. 兼容 App 上传的 44.1kHz/16bit/立体声 与 8k 单声道.
    """
    try:
        with open(path, "rb") as f:
            hdr = f.read(80)
        if hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE":
            return None
        import ustruct
        num_ch = ustruct.unpack("<H", hdr[22:24])[0]
        sample_rate = ustruct.unpack("<I", hdr[24:28])[0]
        bits = ustruct.unpack("<H", hdr[34:36])[0]
        # 找 data 子块大小
        data_size = 0
        i = 12
        while i + 8 <= len(hdr):
            ck, sz = hdr[i:i+4], ustruct.unpack("<I", hdr[i+4:i+8])[0]
            if ck == b"data":
                data_size = sz
                break
            i += 8 + sz + (sz & 1)
        if sample_rate <= 0 or num_ch <= 0 or bits <= 0:
            return None
        bps = sample_rate * num_ch * (bits // 8)
        if bps <= 0:
            return None
        return data_size / bps
    except Exception:
        return None


def play_file(local_path):
    """Play 音频文件 via audio.Audio. 阻塞直到播放完成.

    EC200U 仅支持喇叭(通道 2); 按真机验证成功的流程:
      aud = audio.Audio(2); aud.setVolume(8); aud.play(4,1,filepath); 等时长
    本实现: 尽力用 callback(event==7) 提前返回; 不触发则按文件真实时长兜底等待, 不卡死.
    """
    vol = cfg_get("audio_volume", 9)
    try:
        # 兜底等待秒数: 优先解析 WAV 头真实时长(兼容 44.1k 立体声与 8k 单声道),
        # 解析失败才退化为按 16KB/s 粗估再加余量 2s.
        try:
            dur = _wav_duration_s(local_path)
            est_s = max(3, int(dur) + 2) if dur is not None else \
                (uos.stat(local_path)[6] // 16000) + 2
        except Exception:
            est_s = 5
        a = Audio(2)                     # EC200U 仅喇叭
        try:
            # 复位可能被录音(Record(2))占用的喇叭通道, 否则 play 会 ret 非0 而无声音
            a.stop()
            a.stopPlayStream()
            print("[AUDIO] play_file 前已复位 Audio(2)")
        except Exception as e:
            print("[AUDIO] play_file 复位 except:", e)
        a.setVolume(vol)                 # 不设音量默认静音
        done = [False]
        cb_ok = [True]
        def _cb(event):
            if event == 7:               # 播放完成回调
                done[0] = True
        try:
            a.setCallback(_cb)
        except Exception:
            cb_ok[0] = False             # 固件不支持回调, 走固定时长等待
        ret = a.play(4, 1, local_path)   # priority=4(高), breakin=1(可打断)
        print("Audio.play ret:", ret, "est_s:", est_s)
        if cb_ok[0]:
            deadline = time.time() + est_s
            while not done[0] and time.time() < deadline:
                time.sleep_ms(200)
        else:
            time.sleep(est_s)            # 无回调: 等足估算时长
        return True
    except Exception as e:
        print("play error:", local_path, e)
        return False


def play_url(audio_url):
    """下载并播放音频."""
    path = download_audio(audio_url)
    if path is None:
        return False
    return play_file(path)


def record_to_file(duration_s, output_path=None):
    """录音 duration_s 秒, 输出 AMR-NB 文件(EC200U 硬件编码); 成功返回 output_path, 失败 None.

    使用 stream 模式: Record(2).stream_start(priority, 8000, seconds), 回调逐段累积 AMR 帧,
    写盘时补上 "#!AMR\\n" 文件头(Raw AMR 容器). 路径由 audio_dir() 决定(优先 SD, 无卡兜底 Flash).
    """
    from audio import Record
    d = audio_dir()
    if d is None:
        return None
    output_path = output_path or ("%s/reply_%s.amr" % (d, int(time.time())))
    rec = None
    voice = bytearray()
    stop = [False]

    def _cb(para):
        if para[0] == "stream":
            if para[2] == 1:
                n = para[1]
                tmp = bytearray(n)
                got = rec.stream_read(tmp, n)
                if got > 0:
                    voice.extend(tmp[:got])
            elif para[2] == 3:      # 录音结束信号
                stop[0] = True

    try:
        rec = Record(2)   # 显式喇叭通道2(测试脚本验证可采音)
        rec.end_callback(_cb)
        if rec.stream_start(4, 8000, int(duration_s)) != 0:
            rec.stream_start(4, 8000, int(duration_s))
        t0 = time.ticks_ms()
        timeout = (duration_s + 2) * 1000
        while not stop[0] and time.ticks_diff(time.ticks_ms(), t0) < timeout:
            time.sleep_ms(200)
        rec.stream_stop()
    except Exception as e:
        print("record error:", e)
        return None

    if len(voice) < 100:
        print("record: payload too small")
        return None
    amr_head = b"\x23\x21\x41\x4d\x52\x0a"   # "#!AMR\n"
    with open(output_path, "wb") as f:
        if voice[:6] != amr_head:
            f.write(amr_head)
        f.write(voice)
    print("record amr done:", output_path, len(voice))
    return output_path


def upload_voice_reply(local_path, reply_to=""):
    """上传老人语音回复(AMR)到云端. QuecPython request 不支持 bytes data,
    故转 base64 JSON 走 /api/voice/reply (elder JWT, 后端支持 audio_b64 字段)."""
    import ubinascii
    try:
        with open(local_path, "rb") as f:
            raw = f.read()
    except Exception as e:
        print("upload read fail:", e)
        return None
    b64str = ubinascii.b2a_base64(raw).decode()
    return http_api.elder_post("/api/voice/reply", {
        "device_id": cfg_get("device_id"),
        "reply_to": reply_to,
        "audio_b64": b64str,
        "filename": local_path.split("/")[-1],
        "content_type": "audio/amr",
    })


def upload_voice_ai(local_path):
    """上传老人语音到 AI 对话接口(/api/device/audio/chat, x-device-token).

    走长按 AI 对话流程, base64 JSON 提交. 后端: 百度ASR -> DeepSeek -> TTS(AMR)
    -> MQTT 下发设备播放, 故本函数只负责上报并返回 {ok, reply, audio_url, ...}.
    """
    import ubinascii
    try:
        with open(local_path, "rb") as f:
            raw = f.read()
    except Exception as e:
        print("upload read fail:", e)
        return None
    b64str = ubinascii.b2a_base64(raw).decode()
    return http_api.post("/api/device/audio/chat", {
        "device_id": cfg_get("device_id"),
        "audio_b64": b64str,
        "filename": local_path.split("/")[-1],
        "content_type": "audio/amr",
    })


class AmrStreamRecorder:
    """手动开关的 AMR 流录音(验证成功的 bf 真机方案).

    设备端无法直接录 WAV/MP3(Record.start 返回 -5, stream_start 用 WAV 报参错),
    故仍用 AMR: Record(2) + stream_start(4,8000,600) + 回调攒流, 补 "#!AMR\\n" 头.
    典型交互(电源键单击开关):
      - start(): 开始录音(不限时长), AMR 帧逐段攒入内存 buf
      - stop() : 结束录音, 补 AMR 头一次性写盘, 返回路径(过短则删除返回 None)
    """
    def __init__(self):
        self._rec = None
        self._path = None
        self._t0 = 0
        self._buf = bytearray()

    def start(self, output_path=None):
        from audio import Record, Audio
        d = audio_dir()
        if d is None:
            return False
        self._path = output_path or ("%s/reply_%s.amr" % (d, int(time.time())))
        self._buf = bytearray()
        self._rec = None

        def _cb(para):
            # para=[op, len, type]; type1=有流数据读入 _buf
            if para[0] == "stream" and para[2] == 1 and self._rec:
                n = para[1]
                tmp = bytearray(n)
                got = self._rec.stream_read(tmp, n)
                if got > 0:
                    self._buf.extend(tmp[:got])

        try:
            # 起录前清理残留音频占用(照测试脚本)
            try:
                ta = Audio(2)
                ta.stop()
                ta.stopPlayStream()
            except Exception:
                pass
            self._rec = Record(2)         # 显式喇叭通道2(测试脚本验证可采音)
            self._rec.end_callback(_cb)
            ret = self._rec.stream_start(4, 8000, 600)   # AMRNB; 大时长由 stop 提前结束
            if ret != 0:
                ret = self._rec.stream_start(4, 8000, 600)
            if ret != 0:
                print("stream_start fail:", ret)
                self._rec = None
                return False
        except Exception as e:
            print("rec start fail:", e)
            return False
        print("recording started:", self._path)
        self._t0 = time.ticks_ms()
        return True

    def stop(self):
        """停止录音; 返回 AMR 路径, 过短/异常返回 None."""
        print("[AUDIO] rec.stop: 尝试 stream_stop 释放录音资源...")
        try:
            if self._rec:
                r_ret = self._rec.stream_stop()
                print("[AUDIO] rec.stop -> stream_stop ret:", r_ret)
        except Exception as e:
            print("[AUDIO] rec.stop -> stream_stop except:", e)
        self._rec = None
        path = self._path
        self._path = None
        # 释放录音后复位喇叭通道, 避免占用导致后续播放/TTS 无声
        try:
            from audio import Audio as _A
            _a = _A(2)
            _a.stop()
            _a.stopPlayStream()
            print("[AUDIO] rec.stop -> Audio(2) 已复位(stop+stopPlayStream)")
        except Exception as e:
            print("[AUDIO] rec.stop -> Audio(2) 复位 except:", e)
        # 测试脚本同款: 内存 _buf 补 AMR 头一次性写盘
        size = len(self._buf)
        if size < 100:
            print("recording too short, discarded; dur=%ds size=%dB" %
                  (time.ticks_diff(time.ticks_ms(), self._t0) // 1000, size))
            try:
                uos.remove(path)
            except Exception:
                pass
            return None
        try:
            amr = b"\x23\x21\x41\x4d\x52\x0a"   # "#!AMR\n"
            with open(path, "wb") as f:
                if self._buf[:6] != amr:
                    f.write(amr)
                f.write(self._buf)
        except Exception as e:
            print("rec save err:", e)
            return None
        print("recording done:", path, size)
        return path

    def _cleanup(self, delete=False):
        try:
            if self._rec:
                self._rec.stream_stop()
        except Exception:
            pass
        if delete and self._path:
            try:
                uos.remove(self._path)
            except Exception:
                pass
        self._rec = None
        self._path = None