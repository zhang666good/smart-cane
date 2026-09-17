"""轻量音频转码 HTTP 服务(基于 linuxserver/ffmpeg 镜像内二进制).

独立容器部署, 供后端(FastAPI)上传家人语音时调用: WAV -> AMR-NB(8kHz 单声道),
使设备端下载/播放更快. 仅用 Python 标准库, 无需 pip 依赖.

接口:
  GET  /health      -> {"ok": true}
  POST /transcode   body JSON: {"in_format":"amr","out_format":"wav","audio_b64":"<base64>"}
                     out_format 也可用旧字段 "format"(兼容); in_format 可选用于临时文件后缀。
                     成功 -> {"ok":true,"format":"<out_format>","size":N,"audio_b64":"<base64>"}
                     失败返回 4xx/5xx + {"ok":false,"msg":...}
"""
import base64
import json
import os
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("TRANSCODE_PORT", "8001"))
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
# 编码器: AMR-NB 需外部库, 校验失败时回退列表见 _build_cmd
AMR_ENCODER = os.environ.get("AMR_ENCODER", "libopencore_amrnb")


def _build_cmd(out_fmt, in_path, out_path):
    out_fmt = (out_fmt or "").lower()
    if out_fmt == "amr":
        return [FFMPEG, "-y", "-i", in_path, "-vn",
                "-ac", "1", "-ar", "8000", "-ab", "12.2k",
                "-c:a", AMR_ENCODER, out_path]
    if out_fmt == "wav":   # ASR 常用 16k/mono/PCM16
        return [FFMPEG, "-y", "-i", in_path, "-vn",
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out_path]
    return None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/health"):
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False, "msg": "not found"})

    def do_POST(self):
        if not self.path.startswith("/transcode"):
            self._json(404, {"ok": False, "msg": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(n)
            req = json.loads(body.decode("utf-8"))
            out_fmt = (req.get("out_format") or req.get("format") or "amr").lower()
            in_fmt = (req.get("in_format") or "").lower()
            raw = base64.b64decode(req.get("audio_b64", ""))
            if not raw:
                self._json(400, {"ok": False, "msg": "empty audio_b64"})
                return
        except Exception as e:
            self._json(400, {"ok": False, "msg": "bad request: %s" % e})
            return

        in_path = None
        out_path = None
        try:
            in_suffix = "." + in_fmt if in_fmt else ".in"
            fd, in_path = tempfile.mkstemp(suffix=in_suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            out_path = in_path + ("." + out_fmt)
            cmd = _build_cmd(out_fmt, in_path, out_path)
            if cmd is None:
                self._json(400, {"ok": False, "msg": "unsupported format: %s" % out_fmt})
                return
            p = subprocess.run(cmd, capture_output=True, timeout=90)
            if p.returncode != 0 or not os.path.exists(out_path):
                err = (p.stderr or b"").decode("utf-8", errors="replace")[-600:]
                self._json(500, {"ok": False, "msg": "ffmpeg failed: %s" % err})
                return
            with open(out_path, "rb") as f:
                out_bytes = f.read()
            self._json(200, {
                "ok": True, "format": out_fmt, "size": len(out_bytes),
                "audio_b64": base64.b64encode(out_bytes).decode("ascii"),
            })
        except Exception as e:
            self._json(500, {"ok": False, "msg": str(e)})
        finally:
            for path in (in_path, out_path):
                try:
                    os.unlink(path)
                except Exception:
                    pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("transcode server listening on 0.0.0.0:%d (ffmpeg=%s)" % (PORT, FFMPEG))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()