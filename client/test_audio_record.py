"""EC200U 录音自检 —— 采用 bf 真机版 stream_start 逻辑(非 Record.start).

流程: 挂载SD -> 清理音频占用 + TTS缓冲 -> Record(2).stream_start(4,8000,5)
      -> 阻塞等回调结束 -> stream_stop -> 补AMR头写SD文件 -> 打印结果.
用法: QPYcom Run 本文件; 录音时长由 RECD_TIME 控制.
"""
import audio
import utime
import uos
import gc

RECD_TIME = 5            # 录音时长(秒)
VOLUME_LEVEL = 9
SAVE_PATH = "/sd/key_record_test.amr"


def _mount_sd():
    try:
        uos.listdir("/sd")
    except Exception:
        try:
            from uos import VfsSd, mount
            mount(VfsSd("sd_fs"), "/sd")
            print("[TEST] SD mounted")
        except Exception as e:
            print("[TEST] SD err:", e)


class TestRecorder:
    def __init__(self):
        self._rec = None
        self._buf = bytearray()
        self._recording = False
        self._save_path = SAVE_PATH

    def _cb(self, para):
        # para=[op, len, type]; type1=有流数据, type3=结束
        if para[0] == "stream":
            if para[2] == 1 and self._rec:
                n = para[1]
                tmp = bytearray(n)
                got = self._rec.stream_read(tmp, n)
                if got > 0:
                    self._buf.extend(tmp[:got])
            elif para[2] == 3:
                self._recording = False
                print("[CALLBACK] 录音结束信号")

    def record(self):
        """流式录音 RECD_TIME 秒, 返回 AMR 文件路径, 失败 None."""
        print("\n[TEST] 启动 %ds 录音..." % RECD_TIME)
        self._buf = bytearray()
        self._recording = True
        gc.collect()
        try:
            # 清理可能残留的音频占用
            try:
                ta = audio.Audio(2); ta.stop(); ta.stopPlayStream()
            except Exception:
                pass
            # 提示音缓冲(仿真机, 确保音频链路稳定)
            try:
                tts = audio.TTS(2); tts.setVolume(VOLUME_LEVEL)
                tts.play(4, 1, 2, "开始录音")
                utime.sleep(2)
            except Exception:
                pass

            self._rec = audio.Record(2)
            self._rec.end_callback(self._cb)
            ret = self._rec.stream_start(4, 8000, RECD_TIME)
            print("[TEST] stream_start ret:", ret)
            if ret != 0:
                utime.sleep(1)
                ret = self._rec.stream_start(4, 8000, RECD_TIME)
                if ret != 0:
                    print("[TEST] stream_start 重试仍失败")
                    return None
            start = utime.ticks_ms()
            timeout = (RECD_TIME + 2) * 1000
            while self._recording:
                if utime.ticks_diff(utime.ticks_ms(), start) > timeout:
                    break
                utime.sleep_ms(200)
        except Exception as e:
            print("[TEST] record err:", e)
            return None
        finally:
            if self._rec:
                try:
                    self._rec.stream_stop()
                except Exception:
                    pass

        print("[TEST] captured bytes:", len(self._buf))
        if len(self._buf) < 100:
            print("[TEST] 载荷过小")
            return None
        # 补 AMR 头写盘
        try:
            amr = b"\x23\x21\x41\x4d\x52\x0a"  # "#!AMR\n"
            with open(self._save_path, "wb") as f:
                if self._buf[:6] != amr:
                    f.write(amr)
                f.write(self._buf)
        except Exception as e:
            print("[TEST] file err:", e)
            return None
        print("[TEST] RESULT: RECORD_OK size=%d -> %s" % (len(self._buf), self._save_path))
        return self._save_path

    def play_back(self):
        """回放录制的文件, 验证喇叭输出链路."""
        print("[TEST] 回放:", self._save_path)
        try:
            aud = audio.Audio(2)
            aud.setVolume(VOLUME_LEVEL)
            ret = aud.play(4, 1, self._save_path)
            print("[TEST] play ret:", ret)
            utime.sleep(RECD_TIME + 1)   # 等播放完成
        except Exception as e:
            print("[TEST] play err:", e)


if __name__ == "__main__":
    _mount_sd()
    r = TestRecorder()
    ok = r.record()
    print("=== RESULT:", "RECORD_OK" if ok else "RECORD_FAIL", "===")
    if ok:
        r.play_back()