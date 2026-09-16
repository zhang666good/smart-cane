"""EC200U 音频播放硬件测试 (自包含, 不依赖项目其它模块).

本版流程:
  1. 挂载 SD 卡 (无卡则兜底内置 Flash)
  2. 用 request 下载 samplelib 公开 WAV 样本到 SD 卡
  3. 按真机验证成功的播放序列播放: Audio(2)+setVolume(9)+play(4,1,path)
  4. 打印每一步结果, 供判断硬件到底能不能播 WAV

用 QPYCom 直接 Run 本文件即可.
"""
import uos
import utime
import request
from audio import Audio

# 公开可用的 12s WAV 样本(samplelib 官方)
WAV_URL = "https://samplelib.com/wav/sample-12s.wav"
SD_MP = "/sd"
AUD_DIR = "/sd/cane_audio"
FALLBACK_DIR = "/usr/cane_audio"


def ensure_dir():
    """优先 /sd/cane_audio, 无 SD 卡兜底 /usr/cane_audio."""
    # 尝试挂载 SD 卡
    try:
        from uos import VfsSd, mount
        device = VfsSd("sd_fs")
        mount(device, SD_MP)
        print("[TEST] SD mounted ->", SD_MP)
    except Exception as e:
        print("[TEST] sd mount skipped:", e)

    for d in (AUD_DIR, FALLBACK_DIR):
        try:
            uos.listdir(d)
            return d
        except Exception:
            try:
                uos.mkdir(d)
                return d
            except Exception:
                continue
    return "/"


def download(url, path):
    """按 samplelib 返回的 content 逐块写入, 返回 (ok, size)."""
    print("[TEST] downloading", url)
    resp = None
    try:
        resp = request.get(url, decode=False)
        print("[TEST] status:", resp.status_code)
        if resp.status_code != 200:
            # https 失败时给一个 http 兜底尝试
            return download_http(url, path)
        ok, size = write_content(resp, path)
        return ok, size
    except Exception as e:
        print("[TEST] download except:", e)
        # 部分固件 https 需 ssl 参数, 换 http 重试
        return download_http(url.replace("https://", "http://"), path)
    finally:
        if resp:
            try:
                resp.close()
            except Exception:
                pass


def download_http(url, path):
    if url.startswith("http://") is False:
        return False, 0
    print("[TEST] retry http:", url)
    try:
        resp = request.get(url, decode=False)
        if resp.status_code != 200:
            return False, 0
        return write_content(resp, path)
    except Exception as e:
        print("[TEST] http also failed:", e)
        return False, 0


def write_content(resp, path):
    size = 0
    with open(path, "wb") as f:
        for chunk in resp.content:
            if not chunk:
                break
            d = bytes([chunk]) if isinstance(chunk, int) else chunk
            f.write(d)
            size += len(d)
    print("[TEST] saved", path, "size=", size)
    return (size > 0), size


def play_one(path, vol=9):
    """按真机成功序列播放, 返回 ret."""
    try:
        sz = uos.stat(path)[6]
    except Exception:
        sz = -1
    est_s = 2 if sz <= 0 else (sz // 16000) + 2
    if est_s > 30:
        est_s = 30
    print("  size=%d est_s=%d" % (sz, est_s))
    try:
        a = Audio(2)            # EC200U 仅喇叭
        a.setVolume(vol)        # 最大
        try:
            a.setCallback(lambda e: None if e != 7 else None)
        except Exception:
            pass
        ret = a.play(4, 1, path)   # priority=4高, breakin=1可打断
        print("  play(%s) -> ret=%d" % (path, ret))
        return ret, est_s
    except Exception as e:
        print("  play except:", e)
        return -1, est_s


def main():
    print("=== EC200U Audio WAV Play Test ===")
    d = ensure_dir()
    print("[TEST] audio dir ->", d)
    wav = "%s/sample-12s.wav" % d
    ok, size = download(WAV_URL, wav)
    if not ok:
        print("[TEST] download FAIL, abort")
        return
    print("--- now playing WAV (should HEAR ~12s sound):")
    ret, est_s = play_one(wav)
    if ret == 0:
        print("  waiting ~%ds for playback..." % est_s)
        utime.sleep(est_s)
    print("=== RESULT: WAV play ret =", ret, "(OK)" if ret == 0 else "(ERR -> 需转码/换格式)")


main()
