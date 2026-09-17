"""FastAPI 网关入口。

运行:  cd backend && .venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

挂载：
  - REST 设备接入路由   /api/device/*  /audio/*
  - MCP 服务器(面向 Coze)  /mcp  (Streamable HTTP)

说明：mcp 2.x 的 StreamableHTTPSessionManager 需要生命周期管理(task group)。
FastAPI 的 Mount 不会执行被挂载子应用的 lifespan，因此把 session_manager.run()
放进 FastAPI 自身的 lifespan 中初始化。
"""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from .config import BASE_DIR, get_settings
from .db import init_db
from .mcp_servers import (admin_server, elder_server, family_server, server as mcp_server)
from .routers import audio_router, auth_router, device_router, family_router, admin_router, voice_router, ai_router

logger = logging.getLogger("cane.backend")


def setup_logging(level: int = logging.INFO) -> None:
    """统一后端日志: 控制台 + 文件 backend/logs/backend.log."""
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    try:
        log_dir = BASE_DIR.parent / "logs"      # backend/logs, 非 app/logs
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(log_dir / "backend.log"), encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except Exception as exc:  # 文件日志失败不影响服务
        logger.warning("file logging unavailable: %s", exc)


def create_app() -> FastAPI:
    settings = get_settings()
    settings.ensure_dirs()
    setup_logging()               # 统一日志(控制台 + 文件)
    init_db()
    logger.info("backend starting: device_token=%s mqtt=%s:%s",
                settings.device_token, settings.mqtt_host, settings.mqtt_port)

    # 预构建三个角色 MCP Starlette 应用，并各自持有 session manager，lifespan 全部托管
    mcp_servers_by_role = {
        "elder": elder_server,
        "family": family_server,
        "admin": admin_server,
    }
    mcp_apps = {
        role: srv.streamable_http_app(streamable_http_path="/")
        for role, srv in mcp_servers_by_role.items()
    }
    session_managers = [
        srv._lowlevel_server._session_manager for srv in mcp_servers_by_role.values()
    ]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        import contextlib
        async with contextlib.AsyncExitStack() as stack:
            for mgr in session_managers:
                await stack.enter_async_context(mgr.run())
            yield

    application = FastAPI(
        title="智能拐杖云端网关",
        version="0.1.0",
        description="设备接入 + Coze 对话 + 传感/数据库 MCP + MQTT 下行",
        lifespan=lifespan,
    )
    application.include_router(audio_router)
    application.include_router(device_router)
    application.include_router(auth_router)

    @application.middleware("http")
    async def access_log(request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
            dur_ms = round((time.perf_counter() - start) * 1000)
            logger.info("%s %s -> %s  (%sms)", request.method, request.url.path,
                        response.status_code, dur_ms)
            return response
        except Exception:
            dur_ms = round((time.perf_counter() - start) * 1000)
            logger.exception("%s %s -> ERROR (%sms)", request.method, request.url.path, dur_ms)
            raise
    application.include_router(family_router)
    application.include_router(admin_router)
    application.include_router(voice_router)
    application.include_router(ai_router)

    # 挂载 MCP 服务器为 Streamable HTTP 传输 /mcp
    # Coze: "添加 MCP 服务" → 流式 HTTP URL: http://<host>:<port>/mcp
    for role in ("elder", "family", "admin"):
        application.mount(f"/mcp/{role}", mcp_apps[role])
    application.mount("/mcp", mcp_apps["admin"])  # 兼容: /mcp = 全量 admin

    # 静态音频目录(家人语音/老人回复下载)
    from fastapi.staticfiles import StaticFiles
    application.mount("/media", StaticFiles(directory=str(settings.audio_dir)))

    @application.get("/health")
    def health():
        return {"status": "ok", "service": "cane-backend"}

    return application


app = create_app()