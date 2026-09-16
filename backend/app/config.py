"""应用配置(env 驱动)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """集中式配置，字段名对应 .env。"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "0.0.0.0"
    port: int = 8000

    device_token: str = "demo-device-token"

    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    mqtt_topic_prefix: str = "cane"

    public_audio_base: str = "http://127.0.0.1:8000"

    coze_base_url: str = "https://api.coze.cn"
    coze_token: str = ""
    coze_bot_id: str = ""

    # 数据/音频落盘目录
    data_dir: Path = BASE_DIR / "data"
    audio_dir: Path = BASE_DIR / "storage" / "audio"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
