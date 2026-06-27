"""Configuration management for RLM Knowledge Base."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


def app_home() -> Path:
    """Base directory for ``config.yaml`` and the ``data/`` folder.

    When ``RLM_HOME`` is set (e.g. by the desktop app, which points it at a
    per-user app-data folder), config and database live there so an installed
    app can write outside its read-only program folder. When unset, paths stay
    relative to the current working directory, preserving the standalone/server
    behavior.
    """
    home = os.environ.get("RLM_HOME")
    if home:
        p = Path(home).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path.cwd()


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
    # None -> inherit the global indexer.include / indexer.exclude patterns.
    include: list[str] | None = None
    exclude: list[str] | None = None


class SummaryConfig(BaseModel):
    """Summary generation configuration."""

    max_content_length: int = 10000
    language: str = "de"


class IndexerConfig(BaseModel):
    """Indexer configuration."""

    sync_schedule: str | None = "0 3 * * *"
    # Automatic re-index interval in hours (default: once a day). Takes precedence
    # over sync_schedule when > 0.
    sync_interval_hours: int = 24
    max_file_size_mb: int = 50
    max_concurrent: int = 5
    # Global file patterns applied to every indexed folder.
    include: list[str] = Field(default_factory=lambda: ["*.pdf", "*.docx", "*.xlsx", "*.md", "*.txt"])
    exclude: list[str] = Field(default_factory=lambda: [".*", "~$*", "node_modules", "__pycache__"])
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
    # Resolve a relative config path under the app home (RLM_HOME) so loading and
    # saving always hit the same file, even when a default name like "config.yaml"
    # is passed in explicitly.
    path = Path(config_path or settings.config_path)
    if not path.is_absolute():
        path = app_home() / path

    data = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    config = AppConfig.model_validate(data)

    # Resolve a relative SQLite path under the app home so the database lives
    # next to the config (matters when RLM_HOME points outside the program dir).
    if (
        config.database.type == "sqlite"
        and config.database.path
        and not Path(config.database.path).is_absolute()
    ):
        config.database.path = str(app_home() / config.database.path)

    # Migrate: seed the global include/exclude from existing connector patterns
    # when they are not set explicitly in the file (preserves prior file types).
    indexer_raw = data.get("indexer") or {}
    if "include" not in indexer_raw:
        seed = next((c.include for c in config.connectors if c.include), None)
        if seed:
            config.indexer.include = seed
    if "exclude" not in indexer_raw:
        seed = next((c.exclude for c in config.connectors if c.exclude), None)
        if seed:
            config.indexer.exclude = seed

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
    if not path.is_absolute():
        path = app_home() / path
    path.parent.mkdir(parents=True, exist_ok=True)

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
        new_conns = []
        for c in data["connectors"]:
            entry = {"name": c["name"], "type": c.get("type", "local"), "path": c.get("path")}
            if c.get("include") is not None:
                entry["include"] = c["include"]
            if c.get("exclude") is not None:
                entry["exclude"] = c["exclude"]
            new_conns.append(entry)
        raw["connectors"] = new_conns

    if "indexer" in data and data["indexer"] is not None:
        raw.setdefault("indexer", {})
        if "sync_schedule" in data["indexer"]:
            raw["indexer"]["sync_schedule"] = data["indexer"]["sync_schedule"]
        if data["indexer"].get("sync_interval_hours") is not None:
            raw["indexer"]["sync_interval_hours"] = data["indexer"]["sync_interval_hours"]
        if data["indexer"].get("include") is not None:
            raw["indexer"]["include"] = data["indexer"]["include"]
        if data["indexer"].get("exclude") is not None:
            raw["indexer"]["exclude"] = data["indexer"]["exclude"]

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
