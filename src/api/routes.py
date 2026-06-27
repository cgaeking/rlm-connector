"""API routes for RLM Knowledge Base."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import get_config, save_config


class QueryRequest(BaseModel):
    """Request body for knowledge base queries."""

    question: str = Field(..., description="The question to ask")


class QueryResponse(BaseModel):
    """Response for knowledge base queries."""

    answer: str
    sources: list[dict[str, Any]]
    tokens_used: int = 0
    tool_calls: list[dict[str, Any]] = []


class SearchRequest(BaseModel):
    """Request body for document search."""

    query: str = Field(..., description="Search query")
    file_type: str | None = Field(None, description="Filter by file type")
    limit: int = Field(20, description="Maximum results")


class SyncRequest(BaseModel):
    """Request body for sync operations."""

    full: bool = Field(False, description="Perform full sync instead of incremental")
    connector_name: str | None = Field(None, description="Sync specific connector only")


class StatusResponse(BaseModel):
    """System status response."""

    is_running: bool
    total_documents: int
    indexed_documents: int
    error_documents: int
    connectors: dict[str, Any]


class LlmConfigBody(BaseModel):
    """Editable LLM settings."""

    provider: str = Field("anthropic", description="anthropic | openai | ollama")
    model: str = "claude-sonnet-4-6"
    api_key: str | None = None
    base_url: str | None = None


class ConnectorBody(BaseModel):
    """Editable connector (indexed folder) settings."""

    name: str
    type: str = "local"
    path: str | None = None
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class IndexerBody(BaseModel):
    """Editable indexer settings."""

    sync_schedule: str | None = None


class ConfigBody(BaseModel):
    """Editable subset of the application config (for GET/POST /config)."""

    llm: LlmConfigBody
    connectors: list[ConnectorBody] = Field(default_factory=list)
    indexer: IndexerBody = Field(default_factory=IndexerBody)


def create_router(app_state: Any) -> APIRouter:
    """Create API router with all endpoints.

    Args:
        app_state: Application state container.

    Returns:
        Configured APIRouter.
    """
    router = APIRouter()

    @router.get("/", tags=["Health"])
    async def root():
        """Root endpoint - health check."""
        return {
            "name": "RLM Knowledge Base",
            "version": "0.2.0",
            "status": "running",
            "features": ["FTS5 full-text search", "RLM tool-use query"]
        }

    @router.get("/health", tags=["Health"])
    async def health():
        """Health check endpoint."""
        return {"status": "healthy"}

    @router.get("/status", response_model=StatusResponse, tags=["Status"])
    def get_status():
        """Get system status including sync status and document counts."""
        return app_state.sync_manager.get_status()

    # Document endpoints

    @router.get("/documents", tags=["Documents"])
    def list_documents(
        connector_name: str | None = Query(None, description="Filter by connector"),
        file_type: str | None = Query(None, description="Filter by file type"),
        search_filename: str | None = Query(None, description="Search in file names"),
        limit: int = Query(100, ge=1, le=1000, description="Maximum results"),
        offset: int = Query(0, ge=0, description="Offset for pagination"),
    ):
        """List indexed documents with optional filters."""
        docs = app_state.db.list_documents(
            connector_name=connector_name,
            file_type=file_type,
            search_filename=search_filename,
            limit=limit,
            offset=offset,
        )

        return {
            "documents": docs,
            "count": len(docs),
            "limit": limit,
            "offset": offset,
        }

    @router.get("/documents/{doc_id}", tags=["Documents"])
    def get_document(doc_id: str):
        """Get document details by ID."""
        doc = app_state.db.get_document(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return doc.to_dict()

    @router.get("/documents/{doc_id}/content", tags=["Documents"])
    def get_document_content(
        doc_id: str,
        start: int | None = Query(None, description="Start character position"),
        end: int | None = Query(None, description="End character position"),
    ):
        """Get text content of a document (optionally a range)."""
        result = app_state.db.get_document_content(doc_id, start, end)
        if not result:
            raise HTTPException(status_code=404, detail="Document not found")
        return result

    @router.get("/statistics", tags=["Documents"])
    def get_statistics():
        """Get database statistics."""
        return app_state.db.get_statistics()

    # Search endpoint (FTS5)

    @router.post("/search", tags=["Search"])
    def search_documents(request: SearchRequest):
        """Full-text search across all documents using FTS5."""
        results = app_state.db.search_fulltext(
            query=request.query,
            limit=request.limit,
            file_type=request.file_type,
        )

        return {
            "query": request.query,
            "results": results,
            "count": len(results),
        }

    @router.get("/search", tags=["Search"])
    def search_documents_get(
        q: str = Query(..., description="Search query"),
        file_type: str | None = Query(None, description="Filter by file type"),
        limit: int = Query(20, ge=1, le=100, description="Maximum results"),
    ):
        """Full-text search (GET variant)."""
        results = app_state.db.search_fulltext(
            query=q,
            limit=limit,
            file_type=file_type,
        )

        return {
            "query": q,
            "results": results,
            "count": len(results),
        }

    # Index/Sync endpoints

    @router.post("/index/refresh", tags=["Index"])
    async def refresh_index(request: SyncRequest | None = None):
        """Trigger index synchronization.

        Use full=true for complete re-indexing.
        """
        if request is None:
            request = SyncRequest()

        if app_state.sync_manager.is_running:
            raise HTTPException(
                status_code=409,
                detail="Sync already in progress",
            )

        # Run sync in background
        if request.full:
            asyncio.create_task(
                app_state.sync_manager.full_sync(request.connector_name)
            )
            return {
                "status": "full_sync_started",
                "connector": request.connector_name or "all",
            }
        else:
            asyncio.create_task(
                app_state.sync_manager.incremental_sync(request.connector_name)
            )
            return {
                "status": "incremental_sync_started",
                "connector": request.connector_name or "all",
            }

    @router.get("/index/status", tags=["Index"])
    def get_index_status():
        """Get current index/sync status."""
        return app_state.sync_manager.get_status()

    @router.get("/index/progress", tags=["Index"])
    async def get_index_progress():
        """Get current indexing progress (when running)."""
        return app_state.indexer.progress

    # Connector endpoints

    @router.get("/connectors", tags=["Connectors"])
    def list_connectors():
        """List all configured connectors."""
        connectors = []
        for name, connector in app_state.connectors.items():
            connectors.append(connector.status())
        return {"connectors": connectors}

    @router.get("/connectors/{connector_name}", tags=["Connectors"])
    def get_connector(connector_name: str):
        """Get connector details."""
        connector = app_state.connectors.get(connector_name)
        if not connector:
            raise HTTPException(status_code=404, detail="Connector not found")
        return connector.status()

    # Query endpoint (RLM)

    @router.post("/query", response_model=QueryResponse, tags=["Query"])
    async def query_knowledge_base(request: QueryRequest):
        """Query the knowledge base using RLM (Reasoning-based Language Model).

        The RLM agent will:
        1. Search documents using FTS5 full-text search
        2. Read relevant document sections
        3. Reason about the content
        4. Provide a comprehensive answer with sources
        """
        try:
            result = await app_state.rlm_engine.query(request.question)

            return QueryResponse(
                answer=result["answer"],
                sources=result["sources"],
                tokens_used=result["tokens_used"],
                tool_calls=result.get("tool_calls", []),
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Query failed: {str(e)}",
            )

    # Config endpoints (editable subset; changes require a restart)

    @router.get("/config", response_model=ConfigBody, tags=["Config"])
    async def get_config_endpoint():
        """Return the editable subset of the current configuration."""
        cfg = get_config()
        return ConfigBody(
            llm=LlmConfigBody(
                provider=cfg.llm.provider,
                model=cfg.llm.model,
                api_key=cfg.llm.api_key,
                base_url=cfg.llm.base_url,
            ),
            connectors=[
                ConnectorBody(
                    name=c.name,
                    type=c.type,
                    path=c.path,
                    include=c.include,
                    exclude=c.exclude,
                )
                for c in cfg.connectors
            ],
            indexer=IndexerBody(sync_schedule=cfg.indexer.sync_schedule),
        )

    @router.post("/config", tags=["Config"])
    def update_config_endpoint(body: ConfigBody):
        """Persist the editable config to config.yaml.

        Validates and writes the file (with a .bak backup). Does NOT hot-apply:
        the backend must be restarted for changes to take effect.
        """
        try:
            save_config(body.model_dump())
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid config: {str(e)}")
        return {"ok": True, "restart_required": True}

    return router
