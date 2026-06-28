"""FastAPI application factory."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import AppConfig, get_config
from ..connectors import LocalConnector
from ..database import DocumentRepository
from ..indexer import Indexer, SyncManager
from .routes import create_router


class AppState:
    """Application state container."""

    db: DocumentRepository
    connectors: dict
    indexer: Indexer
    sync_manager: SyncManager
    rlm_engine: any = None
    config: AppConfig


app_state = AppState()


def create_connectors(config: AppConfig) -> dict:
    """Create connector instances from configuration."""
    connectors = {}

    for connector_config in config.connectors:
        if connector_config.type == "local":
            try:
                connector = LocalConnector(
                    name=connector_config.name,
                    root_path=connector_config.path,
                    include_patterns=connector_config.include
                    if connector_config.include is not None
                    else config.indexer.include,
                    exclude_patterns=connector_config.exclude
                    if connector_config.exclude is not None
                    else config.indexer.exclude,
                )
                connectors[connector_config.name] = connector
            except Exception as e:
                print(f"Warning: Could not create connector {connector_config.name}: {e}")

        elif connector_config.type == "onedrive":
            # OneDrive connector not yet implemented
            print(f"Warning: OneDrive connector not yet implemented: {connector_config.name}")

    return connectors


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    config = get_config()
    app_state.config = config

    # Initialize database
    if config.database.type == "postgresql" and config.database.url:
        app_state.db = DocumentRepository(config.database.url)
    else:
        app_state.db = DocumentRepository(config.database.path)

    # Initialize connectors
    app_state.connectors = create_connectors(config)

    # Initialize indexer (no LLM needed - uses FTS5 for search)
    app_state.indexer = Indexer(
        db=app_state.db,
        connectors=app_state.connectors,
        config=config,
    )

    # Initialize sync manager
    app_state.sync_manager = SyncManager(
        db=app_state.db,
        connectors=app_state.connectors,
        config=config,
        indexer=app_state.indexer,
    )

    # Initialize RLM engine
    from ..rlm_engine.engine import KnowledgeBaseEngine
    app_state.rlm_engine = KnowledgeBaseEngine(
        db=app_state.db,
        connectors=app_state.connectors,
        config=config,
    )

    # Setup the background indexing scheduler, unless disabled. Set
    # RLM_DISABLE_SCHEDULER=1 to run this API process for serving only (e.g. a
    # REST API next to an MCP service that already owns indexing), so the same
    # files aren't indexed twice.
    if os.environ.get("RLM_DISABLE_SCHEDULER"):
        print("Scheduler disabled (RLM_DISABLE_SCHEDULER set) — serving only, no indexing")
    else:
        app_state.sync_manager.setup_scheduler()

    print(f"RLM Knowledge Base started with {len(app_state.connectors)} connector(s)")

    yield

    # Shutdown
    app_state.sync_manager.shutdown_scheduler()
    print("RLM Knowledge Base shutdown")


def reload_app_state() -> None:
    """Reload config from disk and rebuild config-dependent state in place.

    Lets configuration changes (LLM, connectors, schedule) take effect without
    restarting the process. Mutates the shared ``app_state`` object so the
    existing route handlers immediately see the new values.
    """
    from ..config import init_config
    from ..rlm_engine.engine import KnowledgeBaseEngine

    # Stop the running scheduler before swapping things out.
    try:
        if getattr(app_state, "sync_manager", None):
            app_state.sync_manager.shutdown_scheduler()
    except Exception as e:
        print(f"Warning: scheduler shutdown during reload failed: {e}")

    config = init_config()
    app_state.config = config

    if config.database.type == "postgresql" and config.database.url:
        app_state.db = DocumentRepository(config.database.url)
    else:
        app_state.db = DocumentRepository(config.database.path)

    app_state.connectors = create_connectors(config)
    app_state.indexer = Indexer(
        db=app_state.db, connectors=app_state.connectors, config=config
    )
    app_state.sync_manager = SyncManager(
        db=app_state.db,
        connectors=app_state.connectors,
        config=config,
        indexer=app_state.indexer,
    )
    app_state.rlm_engine = KnowledgeBaseEngine(
        db=app_state.db, connectors=app_state.connectors, config=config
    )

    app_state.sync_manager.setup_scheduler()
    print(f"RLM Knowledge Base config reloaded ({len(app_state.connectors)} connector(s))")


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Create FastAPI application instance.

    Args:
        config: Optional configuration override.

    Returns:
        Configured FastAPI application.
    """
    if config:
        from ..config import init_config
        init_config(config)

    app = FastAPI(
        title="RLM Knowledge Base",
        description="Make company documents accessible to AI agents",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routes
    router = create_router(app_state)
    app.include_router(router)

    return app
