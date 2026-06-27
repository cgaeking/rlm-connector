"""Configuration management for RLM Knowledge Base."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    base_url: str | None = None
    # api_keys is the canonical per-provider store; api_key mirrors the active
    # provider's key for backward compatibility (the engine reads api_key).
    api_key: str | None = None
    api_keys: dict[str, str] = Field(default_factory=dict)


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

    # Normalize per-provider keys. Seed from the legacy single api_key and from
    # environment variables, then expose the active provider's key as api_key.
    if config.llm.api_key and not config.llm.api_keys.get(config.llm.provider):
        config.llm.api_keys[config.llm.provider] = config.llm.api_key
    if settings.anthropic_api_key and not config.llm.api_keys.get("anthropic"):
        config.llm.api_keys["anthropic"] = settings.anthropic_api_key
    if settings.openai_api_key and not config.llm.api_keys.get("openai"):
        config.llm.api_keys["openai"] = settings.openai_api_key
    config.llm.api_key = config.llm.api_keys.get(config.llm.provider)

    return config


def save_config(data: dict, config_path: str | Path | None = None) -> AppConfig:
    """Persist editable config fields to the YAML file.

    Only known, user-editable sections are merged in (``llm``, ``connectors``,
    and ``indexer.sync_schedule``); all other sections in the file are kept as-is.
    The merged result is validated against ``AppConfig`` before anything is written,
    and a ``.bak`` backup of the previous file is created. Changes require a
    backend restart to take effect (config is loaded once at startup).

    Args:
        data: Editable config subset, e.g.
            ``{"llm": {...}, "connectors": [...], "indexer": {"sync_schedule": ...}}``.
        config_path: Target file. Defaults to the same path as ``load_config``.

    Returns:
        The validated :class:`AppConfig`.
    """
    settings = Settings()
    path = Path(config_path or settings.config_path)

    # Start from the existing file so untouched sections survive.
    if path.exists():
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = AppConfig().model_dump(mode="json")

    if "llm" in data and data["llm"] is not None:
        raw.setdefault("llm", {})
        for key in ("provider", "model", "base_url"):
            if key in data["llm"]:
                raw["llm"][key] = data["llm"][key]
        if data["llm"].get("api_keys") is not None:
            raw["llm"]["api_keys"] = {k: v for k, v in data["llm"]["api_keys"].items() if v}
        elif data["llm"].get("api_key"):
            prov0 = data["llm"].get("provider") or raw["llm"].get("provider", "anthropic")
            raw["llm"].setdefault("api_keys", {})[prov0] = data["llm"]["api_key"]
        # Keep the single api_key in sync with the active provider (engine compat).
        prov = data["llm"].get("provider") or raw["llm"].get("provider", "anthropic")
        raw["llm"]["api_key"] = (raw["llm"].get("api_keys") or {}).get(prov)

    if "connectors" in data and data["connectors"] is not None:
        raw["connectors"] = [
            {
                "name": c["name"],
                "type": c.get("type", "local"),
                "path": c.get("path"),
                "include": c.get("include", []),
                "exclude": c.get("exclude", []),
            }
            for c in data["connectors"]
        ]

    if "indexer" in data and data["indexer"] is not None and "sync_schedule" in data["indexer"]:
        raw.setdefault("indexer", {})
        raw["indexer"]["sync_schedule"] = data["indexer"]["sync_schedule"]

    # Validate the full merged config before touching disk.
    config = AppConfig.model_validate(raw)

    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

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
