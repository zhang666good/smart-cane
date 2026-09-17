# 智能拐杖云端网关 - 生产镜像
# 无需 ffmpeg: 设备端直接用 Record.start 录标准 WAV 上传, 服务端不转码.
FROM docker.1ms.run/library/python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 系统依赖: 仅 tini(轻量), apt 走国内源加速
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com/debian-security|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com/debian-security|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
        tini \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖层(利用 Docker 缓存)；使用清华 PyPI 镜像加速下载
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 拷贝应用代码 (app/ 与 data/、storage/ 分开; app_code 只含代码)
COPY app /app/app

# 数据与音频运行期挂卷
RUN mkdir -p /app/data /app/storage/audio /app/logs

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]