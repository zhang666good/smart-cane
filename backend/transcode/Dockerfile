# 音频转码服务: 复用 linuxserver/ffmpeg 的 ffmpeg 二进制, 加一个极简 HTTP 封装
# 供后端上传家人语音时调用做 WAV -> AMR 转码(设备端下载/播放更快).
FROM docker.1ms.run/linuxserver/ffmpeg

USER root
# ubuntu 基础镜像不含 python3, 安装最小 python3 运行 stdlib 转码服务(无需 pip)
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

COPY transcode_server.py /transcode_server.py
ENV TRANSCODE_PORT=8001 FFMPEG_BIN=ffmpeg AMR_ENCODER=libopencore_amrnb
EXPOSE 8001
# 覆盖 linuxserver 默认 entrypoint(ffmpeg wrapper), 否则会把 CMD 当 ffmpeg 参数
ENTRYPOINT ["/usr/bin/python3", "/transcode_server.py"]