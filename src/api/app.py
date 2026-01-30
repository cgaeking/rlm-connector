"""FastAPI application factory."""

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
                    include_patterns=connector_config.include,
                    exclude_patterns=connector_config.exclude,
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

    # Setup scheduler if configured
    app_state.sync_manager.setup_scheduler()

    print(f"RLM Knowledge Base started with {len(app_state.connectors)} connector(s)")

    yield

    # Shutdown
    app_state.sync_manager.shutdown_scheduler()
    print("RLM Knowledge Base shutdown")


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
