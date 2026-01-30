"""Configuration management for RLM Knowledge Base."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    base_url: str | None = None
    api_key: str | None = None


class DatabaseConfig(BaseModel):
    """Database configuration."""

    type: str = "sqlite"
    path: str = "./data/index.db"
    url: str | None = None


class ConnectorConfig(BaseModel):
    """Individual connector configuration."""

    name: str
    type: str
    path: str | None = None
    mount_path: str | None = None
    use_api: bool = False
    include: list[str] = Field(default_factory=lambda: ["*.pdf", "*.docx", "*.xlsx", "*.md", "*.txt"])
    exclude: list[str] = Field(default_factory=lambda: [".*", "~$*", "node_modules", "__pycache__"])


class SummaryConfig(BaseModel):
    """Summary generation configuration."""

    max_content_length: int = 10000
    language: str = "de"


class IndexerConfig(BaseModel):
    """Indexer configuration."""

    sync_schedule: str | None = "0 3 * * *"
    max_file_size_mb: int = 50
    max_concurrent: int = 5
    summary: SummaryConfig = Field(default_factory=SummaryConfig)


class APIConfig(BaseModel):
    """API server configuration."""

    host: str = "0.0.0.0"
    port: int = 8000


class UIConfig(BaseModel):
    """Chat UI configuration."""

    enabled: bool = True
    port: int = 7860


class AppConfig(BaseModel):
    """Main application configuration."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    connectors: list[ConnectorConfig] = Field(default_factory=list)
    indexer: IndexerConfig = Field(default_factory=IndexerConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    ui: UIConfig = Field(default_factory=UIConfig)


class Settings(BaseSettings):
    """Environment-based settings."""

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    ollama_url: str | None = None
    config_path: str = "config.yaml"
    debug: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load configuration from YAML file.

    Args:
        config_path: Path to config file. If None, uses default or env var.

    Returns:
        Loaded AppConfig instance.
    """
    settings = Settings()
    path = Path(config_path or settings.config_path)

    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        config = AppConfig.model_validate(data)
    else:
        config = AppConfig()

    # Override LLM API key from environment if not in config
    if settings.anthropic_api_key and not config.llm.api_key:
        config.llm.api_key = settings.anthropic_api_key

    return config


def get_settings() -> Settings:
    """Get environment settings."""
    return Settings()


# Global config instance (lazy loaded)
_config: AppConfig | None = None
_settings: Settings | None = None


def get_config() -> AppConfig:
    """Get the global config instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def init_config(config_path: str | Path | None = None) -> AppConfig:
    """Initialize the global config from a specific path."""
    global _config
    _config = load_config(config_path)
    return _config
