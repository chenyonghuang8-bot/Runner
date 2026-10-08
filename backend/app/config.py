from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore', hide_input_in_errors=True)
    app_env: Literal['development', 'test', 'production'] = 'development'
    weather_provider: Literal['disabled','open_meteo'] = 'disabled'
    app_timezone: str = 'Asia/Shanghai'
    app_public_url: str = 'http://localhost:5173'
    database_url: str = 'sqlite:///./data/runner.db'
    private_storage_dir: Path = ROOT / 'data/private'
    session_secret: str = ''
    ai_provider: Literal['mock', 'deepseek'] = 'mock'
    deepseek_api_key: str = ''
    deepseek_base_url: str = 'https://api.deepseek.com'
    ai_vision_model: str = 'deepseek-flash'
    ai_coach_model: str = 'deepseek-flash'
    ai_monthly_budget_cny: float = 20
    ai_coach_thinking: Literal['enabled','disabled'] = 'enabled'
    ai_coach_max_output_tokens: int = 8192
    ai_timeout_seconds: int = 90
    ai_max_retries: int = 2
    ai_vision_max_output_tokens: int = 4096
    ai_input_price_cny_per_million: float = 2
    ai_output_price_cny_per_million: float = 8
    import_max_file_mb: int = 20
    import_max_pixels: int = 40000000
    import_tile_height_px: int = 2000
    import_tile_overlap_px: int = 200
    import_original_retention_days: int = 30

    @field_validator('app_timezone')
    @classmethod
    def timezone_valid(cls, value: str):
        ZoneInfo(value)
        return value

    @model_validator(mode='after')
    def validate_settings(self):
        if self.ai_provider == 'deepseek' and not self.deepseek_api_key:
            raise ValueError('DeepSeek 模式需要 DEEPSEEK_API_KEY')
        if self.ai_monthly_budget_cny < 0:
            raise ValueError('AI 预算必须非负')
        if not 1 <= self.ai_timeout_seconds <= 300 or not 0 <= self.ai_max_retries <= 5:
            raise ValueError('AI 超时或重试配置无效')
        if not 256 <= self.ai_vision_max_output_tokens <= 8192:
            raise ValueError('识别输出上限应为 256–8192 tokens')
        if self.ai_input_price_cny_per_million < 2 or self.ai_output_price_cny_per_million < 8:
            raise ValueError('预算计价不得低于已核实的高峰价格 2/8 元每百万 tokens')
        if not 256 <= self.ai_coach_max_output_tokens <= 8192 or self.ai_coach_model!='deepseek-flash':
            raise ValueError('教练需要 deepseek-flash 和 256–8192 输出上限')
        if self.ai_vision_model != 'deepseek-flash':
            raise ValueError('截图识别目前仅支持 deepseek-flash')
        if self.import_max_file_mb <= 0 or self.import_max_pixels <= 0:
            raise ValueError('图片上限必须为正数')
        if not 0 <= self.import_tile_overlap_px < self.import_tile_height_px:
            raise ValueError('切片重叠必须小于切片高度')
        if not 200 <= self.import_tile_height_px <= 4000:
            raise ValueError('切片高度应为 200–4000 像素')
        if self.app_env == 'production':
            url=urlsplit(self.app_public_url)
            if len(self.session_secret)<32 or url.scheme!='https' or not url.hostname or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
                raise ValueError('生产环境需要至少32字符的会话配置及合法HTTPS同源地址（不含路径、认证信息或查询）')
            try:url.port
            except ValueError:raise ValueError('生产环境HTTPS端口无效') from None
        if not self.private_storage_dir.is_absolute():
            self.private_storage_dir = ROOT / self.private_storage_dir
        if self.database_url.startswith('sqlite:///./'):
            self.database_url = 'sqlite:///' + str(ROOT / self.database_url[len('sqlite:///./'):])
        return self
