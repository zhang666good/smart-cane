"""家人端 App 日志配置: 控制台 + 文件 logs/app.log, 详细级别可配.

用法:
    from my_logger import get_logger, configure_logging
    configure_logging(level="DEBUG")
    log = get_logger(__name__)
"""
import logging
import os
from pathlib import Path

_configured = False


def configure_logging(level: str = "DEBUG", log_dir: str | None = None) -> None:
    """初始化 App 日志(幂等). level: DEBUG(默认,详细) / INFO / WARNING / ERROR."""
    global _configured
    if _configured:
        return
    _configured = True

    base = Path(os.path.dirname(os.path.abspath(__file__)))
    target = Path(log_dir) if log_dir else base.parent / "logs"
    target.mkdir(parents=True, exist_ok=True)

    # 只重置本 App 使用的 logger 名字空间，避免破坏 flet 自身日志
    app_logger = logging.getLogger("cane.app")
    app_logger.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    if app_logger.handlers:
        app_logger.handlers.clear()

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    app_logger.addHandler(console)

    try:
        fh = logging.FileHandler(str(target / "app.log"), encoding="utf-8")
        fh.setFormatter(fmt)
        app_logger.addHandler(fh)
    except Exception as exc:
        app_logger.warning("file logging unavailable: %s", exc)


def get_logger(name: str = "cane.app") -> logging.Logger:
    return logging.getLogger(name)
