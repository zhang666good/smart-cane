"""家人端 App 配置(与 backend 网关契约对齐)."""
import dataclasses, os

@dataclasses.dataclass
class Settings:
    # FastAPI 网关地址(云端服务器)
    http_base: str = os.environ.get("CANE_API_BASE", "http://123.207.72.88:8000")
    # 家人演示账号(backend DEMO_ACCOUNTS)
    demo_user: str = "family-demo"
    demo_password: str = "family123"
    # 音频临时目录(拉取老人语音)
    audio_cache_dir: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

settings = Settings()
