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

    # 独立 ffmpeg 转码容器(compose 内网可达)
    transcode_url: str = "http://transcode:8001"

    # 百度短语音识别标准版(ASR)配置
    baidu_api_key: str = ""                 # 百度智能云 API Key (client_id)
    baidu_secret_key: str = ""              # 百度智能云 密钥 (client_secret)
    baidu_dev_pid: int = 1537               # 1537=普通话; 1536=普通话含简单英文等
    baidu_channel: int = 1                  # 声道数(单声道)
    baidu_cuid: str = "cane"                # 客户端唯一标识(建议设备id)
    baidu_rate: int = 8000                 # 设备 AMR 录音采样率为 8000(明确), 故默认 8000

    # 百度语音合成(TTS)配置(文本转语音, tsn.baidu.com/text2audio)
    baidu_tts_per: int = 4194              # 发音人(默认按已验证样板 4194)
    baidu_tts_spd: int = 5                 # 语速 0~15
    baidu_tts_pit: int = 5                 # 音调 0~15
    baidu_tts_vol: int = 5                 # 音量 0~15
    baidu_tts_aue: int = 6                 # 音频格式: 3=wav, 6=mp3(默认, 后续可转amr下发)

    coze_base_url: str = "https://api.coze.cn"
    coze_token: str = ""
    coze_bot_id: str = ""

    # LLM(LangChain, OpenAI 兼容接口; base_url/key/model 可配, 兼容 DeepSeek/Qwen/GLM/vLLM)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.3

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