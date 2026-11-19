from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    citation_max_references: int = Field(default=1000, ge=1, le=100_000)
    citation_max_bytes: int = Field(default=1_000_000, ge=1, le=100_000_000)
    request_timeout_seconds: float = Field(default=120, gt=0, le=300)
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="gpt-5.4-mini", min_length=1, pattern=r"^\S+$")
    openai_timeout_seconds: float = Field(default=30, gt=0, le=120)
    openai_max_output_tokens: int = Field(default=2000, ge=256, le=8000)
    ctgov_timeout_seconds: float = Field(default=20, gt=0, le=120)
    ctgov_page_size: int = Field(default=100, ge=1, le=1000)
    ctgov_max_pages: int = Field(default=50, ge=1, le=1000)
    ctgov_max_studies: int = Field(default=5000, ge=1, le=100_000)
    ctgov_max_retries: int = Field(default=2, ge=0, le=5)
    ctgov_backoff_seconds: float = Field(default=0.5, ge=0, le=5)
    ctgov_max_retry_delay_seconds: float = Field(default=5, ge=0, le=30)
