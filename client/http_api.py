"""HTTP 上行: 传感上报 / SOS / 音频上传 / poll 兜底.

使用 QuecPython 官方 request 模块(模块名 request, 单数).
权威 API 见官方文档(网络应用/request):
  - request.get(url) / request.post(url, data=..., headers=...)
      data 为字符串/bytes(JSON 需用 ujson.dumps), 无 json= 参数
  - 响应对象: status_code / headers / text(生成器) / content(生成器) / json() / close()
请求头统一携带 x-device-token 供网关鉴权.
"""
import ujson
import request
import utime as time

from usr.libs.config import get as cfg_get

import _thread

# QuecPython 的 request 线程不安全: 多线程并发 HTTP(sensor 上报 / 录音上传 / ack)
# 会互相抢占基站 socket 导致挂死。用全局锁串行化所有 HTTP 请求, 并加超时兜底。
_http_lock = _thread.allocate_lock()
HTTP_TIMEOUT_S = 8          # 单次 HTTP 超时(秒), 防止网络卡死钉住线程

_elder_token = ""   # elder 角色 JWT, /api/voice/* 上传用 Bearer 携带


def _base():
    return cfg_get("http_base", "http://127.0.0.1:8000")


def _headers(content_type="application/json"):
    return {
        "Content-Type": content_type,
        "x-device-token": cfg_get("device_token", ""),
    }


def _read_resp(resp):
    """尽力解析响应为 dict; 失败回退 {"status": code}."""
    code = resp.status_code
    try:
        return resp.json()
    except Exception:
        return {"status": code}
    finally:
        try:
            resp.close()
        except Exception:
            pass


def post(path, payload, content_type="application/json", timeout_s=None,
         bearer=None):
    """POST JSON(或二进制), 返回响应 dict; 失败返回 None.

    bearer: 可选 elder/family JWT, 传则追加 Authorization: Bearer 头.
    全局锁串行化 + 显式超时, 避免多线程并发 request 导致 socket 挂死.
    """
    _http_lock.acquire()
    try:
        data = payload if isinstance(payload, (bytes, bytearray)) else ujson.dumps(payload)
        headers = _headers(content_type)
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        r = request.post(_base() + path, data=data, headers=headers,
                         timeout=timeout_s or HTTP_TIMEOUT_S)
        code = r.status_code
        if code == 401:
            print("HTTP 401: 设备token错误")
            _read_resp(r)
            return None
        return _read_resp(r)
    except Exception as e:
        print("HTTP POST %s failed: %s" % (path, e))
        return None
    finally:
        _http_lock.release()


def get(path, params=None, timeout_s=None):
    _http_lock.acquire()
    try:
        url = _base() + path
        if params:
            url = url + "?" + "&".join("%s=%s" % (k, v) for k, v in params.items())
        r = request.get(url, headers=_headers(), timeout=timeout_s or HTTP_TIMEOUT_S)
        return _read_resp(r)
    except Exception as e:
        print("HTTP GET %s failed: %s" % (path, e))
        return None
    finally:
        _http_lock.release()


def report_sensor(data):
    """上报传感数据. data: dict 见 backend SensorReport.data."""
    return post("/api/device/sensor/report", {"device_id": cfg_get("device_id"), "data": data})


def report_sos(message="一键求救"):
    return post("/api/device/sos", {"device_id": cfg_get("device_id"), "message": message})


def poll(device_id):
    """MQTT 断连时兜底:查询是否有待下发; 返回 pending 下指令列表(可能为空)."""
    resp = get("/api/device/poll", params={"device_id": device_id})
    if resp:
        return resp.get("pending") or []
    return []


def ack_finished(finished_type="speak"):
    """播放完成回调: 通知云端弹出队列, 继续下一条或恢复AI."""
    return post("/api/device/ack", {
        "device_id": cfg_get("device_id"),
        "finished_type": finished_type,
    })


def upload_chat(message):
    """预留: 音频对话(或文本)上传."""
    return post("/api/device/audio/chat", {"device_id": cfg_get("device_id"), "message": message})


# ==================== elder 角色 JWT + 语音文件上传 ====================

def login_elder():
    """用老人账号换取 elder JWT(供 /api/voice/reply 上传鉴权). 成功 True."""
    global _elder_token
    try:
        _http_lock.acquire()
        try:
            r = request.post(
                _base() + "/api/auth/token",
                data=ujson.dumps({
                    "user_id": cfg_get("elder_user", "dev-001"),
                    "password": cfg_get("elder_password", "123456"),
                    "device_id": cfg_get("device_id"),
                }),
                headers=_headers("application/json"),
                timeout=HTTP_TIMEOUT_S,
            )
            tok = r.json().get("token")
        finally:
            _http_lock.release()
        if tok:
            _elder_token = tok
            print("login_elder ok")
            try:
                r.close()
            except Exception:
                pass
            return True
    except Exception as e:
        print("login_elder fail:", e)
    try:
        r.close()
    except Exception:
        pass
    return False


def elder_post(path, payload):
    """以 elder JWT 身份 POST JSON(自动 login_elder). 返回响应 dict; 失败 None."""
    global _elder_token
    if not _elder_token:
        login_elder()
    if not _elder_token:
        print("elder_post: no elder token")
        return None
    return post(path, payload, bearer=_elder_token)


def multipart_upload(path, fields, file_field, filepath, filename=None,
                     content_type="application/octet-stream"):
    """以 multipart/form-data 上传音频文件(带 elder Bearer). 返回响应 dict; 失败 None.

    后端 /api/voice/reply 契约: device_id/reply_to 为 Form 字段, audio 为 File 字段.
    QuecPython request 无内置 form-data, 手工拼接 body.
    """
    global _elder_token
    if not _elder_token:
        login_elder()
    if not _elder_token:
        print("multipart_upload: no elder token")
        return None
    filename = filename or filepath.split("/")[-1]
    boundary = "----caneBnd%d" % int(time.time())
    buf = bytearray()
    for k, v in fields.items():
        buf += b"--" + boundary.encode() + b"\r\n"
        buf += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode()
        buf += str(v).encode() + b"\r\n"
    try:
        with open(filepath, "rb") as f:
            filedata = f.read()
    except Exception as e:
        print("multipart_upload: read fail:", e)
        return None
    buf += b"--" + boundary.encode() + b"\r\n"
    buf += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (file_field, filename)).encode()
    buf += ("Content-Type: %s\r\n\r\n" % content_type).encode()
    buf += filedata + b"\r\n"
    buf += b"--" + boundary.encode() + b"--\r\n"
    try:
        r = request.post(
            _base() + path, data=bytes(buf),
            headers={
                "Content-Type": "multipart/form-data; boundary=" + boundary,
                "Authorization": "Bearer " + _elder_token,
            },
        )
        return _read_resp(r)
    except Exception as e:
        print("multipart_upload post fail:", path, e)
        return None
