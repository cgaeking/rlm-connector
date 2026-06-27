"""HTTP-based MCP Server for RLM Knowledge Base.

This module provides an HTTP/SSE-based MCP server that can be accessed
by remote MCP clients like n8n's MCP Client Tool.

Usage:
    python -m src.mcp_http_server
    # or
    rlm-mcp-http (after pip install)

The server exposes:
    - POST /mcp - Streamable HTTP transport (recommended for n8n)
    - GET /mcp/sse - SSE endpoint for legacy clients
    - POST /mcp/message - Message endpoint for SSE transport
    - GET /health - Health check endpoint

Configuration:
    Uses the same config.yaml as the main application.
    Default port: 3000 (configurable via --port or MCP_HTTP_PORT env var)
"""

import argparse
import asyncio
import json
import logging
import os
import secrets
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import get_config, init_config
from src.database.repository import DocumentRepository
from src.connectors.local import LocalConnector
from src.indexer import Indexer, SyncManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("mcp_http_server")

# Security
security = HTTPBearer(auto_error=False)

# API Token - loaded from environment, file, or generated
API_TOKEN: str | None = None
TOKEN_FILE = Path(__file__).parent.parent / "data" / "api_token.txt"


def get_api_token() -> str:
    """Get or generate the API token.
    
    Priority:
    1. Environment variable MCP_API_TOKEN
    2. Token file (data/api_token.txt)
    3. Generate new token and save to file
    """
    global API_TOKEN
    if API_TOKEN is not None:
        return API_TOKEN
    
    # 1. Try environment variable (highest priority)
    API_TOKEN = os.environ.get("MCP_API_TOKEN")
    if API_TOKEN:
        logger.info("Using API token from environment variable")
        return API_TOKEN
    
    # 2. Try loading from file
    if TOKEN_FILE.exists():
        try:
            API_TOKEN = TOKEN_FILE.read_text().strip()
            if API_TOKEN:
                logger.info(f"Loaded API token from {TOKEN_FILE}")
                return API_TOKEN
        except Exception as e:
            logger.warning(f"Failed to read token file: {e}")
    
    # 3. Generate new token and save to file
    API_TOKEN = secrets.token_urlsafe(32)
    
    # Ensure data directory exists
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        TOKEN_FILE.write_text(API_TOKEN)
        logger.info("=" * 60)
        logger.info("Generated new API token and saved to file:")
        logger.info(f"  Token: {API_TOKEN}")
        logger.info(f"  File:  {TOKEN_FILE}")
        logger.info("This token will persist across restarts!")
        logger.info("=" * 60)
    except Exception as e:
        logger.warning(f"Failed to save token to file: {e}")
        logger.warning("Token will be regenerated on next restart!")
        logger.warning(f"Current token: {API_TOKEN}")
    
    return API_TOKEN


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Security(security),
) -> bool:
    """Verify the Bearer token."""
    token = get_api_token()

    # If no token is configured (empty string), allow all requests
    if not token:
        return True

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(credentials.credentials, token):
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True


# Tool definitions - n8n compatible format
TOOLS = [
    {
        "name": "search_documents",
        "description": "Schnelle Volltextsuche ueber alle Dokumente. Findet exakte Woerter und Praefixe. NUTZE DIES ZUERST.",
        "inputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suchbegriff(e)",
                },
                "file_type": {
                    "type": "string",
                    "description": "Filter nach Dateityp (pdf, docx, xlsx, txt)",
                },
                "limit": {
                    "type": "number",
                    "description": "Maximale Anzahl der Ergebnisse",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_fuzzy",
        "description": "Fuzzy-Suche mit Tippfehler-Toleranz. Nutze dies wenn search_documents keine Treffer liefert.",
        "inputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suchbegriff - auch mit Tippfehlern",
                },
                "file_type": {
                    "type": "string",
                    "description": "Filter nach Dateityp",
                },
                "min_similarity": {
                    "type": "number",
                    "description": "Minimale Aehnlichkeit 0.0-1.0",
                },
                "limit": {
                    "type": "number",
                    "description": "Maximale Anzahl der Ergebnisse",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_documents",
        "description": "Liste alle Dokumente mit Metadaten (ohne Inhalt).",
        "inputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "file_type": {
                    "type": "string",
                    "description": "Filter nach Dateityp",
                },
                "search_filename": {
                    "type": "string",
                    "description": "Suche im Dateinamen",
                },
                "limit": {
                    "type": "number",
                    "description": "Maximale Anzahl",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "read_document",
        "description": "Lese den Inhalt eines Dokuments anhand seiner ID.",
        "inputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Die ID des Dokuments",
                },
                "start_char": {
                    "type": "number",
                    "description": "Startposition",
                },
                "end_char": {
                    "type": "number",
                    "description": "Endposition (max 50000 Zeichen)",
                },
            },
            "required": ["doc_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_statistics",
        "description": "Zeige Statistiken ueber die Dokumentenbasis (Anzahl, Typen, Groesse).",
        "inputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


class MCPHttpServer:
    """HTTP-based MCP Server implementation."""

    def __init__(self):
        self.db: DocumentRepository | None = None
        self.server_info = {
            "name": "rlm-knowledge-base",
            "version": "0.1.0",
            "protocolVersion": "2024-11-05",
        }
        self._sse_sessions: dict[str, asyncio.Queue] = {}

    def initialize_database(self):
        """Initialize the database connection."""
        try:
            config_paths = [
                Path.cwd() / "config.yaml",
                Path(__file__).parent.parent / "config.yaml",
            ]

            config_path = None
            for path in config_paths:
                if path.exists():
                    config_path = path
                    break

            if config_path:
                config = init_config(config_path)
                logger.info(f"Loaded config from {config_path}")
            else:
                config = get_config()
                logger.info("Using default config")

            db_path = Path(config.database.path)
            if not db_path.is_absolute():
                if config_path:
                    db_path = config_path.parent / db_path
                else:
                    db_path = Path.cwd() / db_path

            logger.info(f"Connecting to database: {db_path}")
            self.db = DocumentRepository(db_path)

            stats = self.db.get_statistics()
            logger.info(
                f"Database ready: {stats['indexed_documents']} documents, "
                f"{stats['total_content_chars']:,} characters"
            )

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def handle_request(self, method: str, params: dict | None = None) -> dict:
        """Handle an MCP request and return the response."""
        if method == "initialize":
            return {
                "protocolVersion": self.server_info["protocolVersion"],
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": self.server_info["name"],
                    "version": self.server_info["version"],
                },
            }

        elif method == "initialized" or method == "notifications/initialized":
            # This is a notification, no response needed
            return {}

        elif method == "tools/list":
            return {"tools": TOOLS}

        elif method == "tools/call":
            if not params:
                raise ValueError("Missing params for tools/call")
            tool_name = params.get("name")
            # Support both formats: arguments nested or flat in params
            tool_args = params.get("arguments")
            if tool_args is None:
                # n8n might send args flat in params, extract them
                tool_args = {k: v for k, v in params.items() if k not in ("name", "tool", "toolCallId", "sessionId", "action", "chatInput")}
            logger.info(f"Tool call: {tool_name} with args: {tool_args}")
            result = self._execute_tool(tool_name, tool_args)
            return {
                "content": [{"type": "text", "text": result}],
                "isError": False,
            }

        elif method == "ping":
            return {}

        else:
            raise ValueError(f"Unknown method: {method}")

    def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool and return the result."""
        if self.db is None:
            return "Error: Database not initialized"

        try:
            if tool_name == "search_documents":
                return self._tool_search_documents(
                    query=tool_input["query"],
                    file_type=tool_input.get("file_type"),
                    limit=tool_input.get("limit", 10),
                )
            elif tool_name == "search_fuzzy":
                return self._tool_search_fuzzy(
                    query=tool_input["query"],
                    file_type=tool_input.get("file_type"),
                    min_similarity=tool_input.get("min_similarity", 0.3),
                    limit=tool_input.get("limit", 10),
                )
            elif tool_name == "list_documents":
                return self._tool_list_documents(
                    file_type=tool_input.get("file_type"),
                    search_filename=tool_input.get("search_filename"),
                    limit=tool_input.get("limit", 50),
                )
            elif tool_name == "read_document":
                return self._tool_read_document(
                    doc_id=tool_input["doc_id"],
                    start_char=tool_input.get("start_char"),
                    end_char=tool_input.get("end_char"),
                )
            elif tool_name == "get_statistics":
                return self._tool_get_statistics()
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(f"Tool execution error ({tool_name}): {e}")
            return f"Error: {str(e)}"

    def _tool_search_documents(self, query: str, file_type: str | None, limit: int) -> str:
        results = self.db.search_fulltext(query=query, limit=limit, file_type=file_type)
        if not results:
            return f"Keine Treffer fuer '{query}'"

        output = f"Gefunden: {len(results)} Treffer fuer '{query}'\n\n"
        for i, r in enumerate(results, 1):
            output += f"""--- Treffer {i} ---
Dokument: {r['file_name']}
ID: {r['doc_id']}
Typ: {r['file_type']}
Groesse: {r['content_length'] or 0:,} Zeichen
Snippet: {r['snippet']}
---

"""
        return output

    def _tool_search_fuzzy(self, query: str, file_type: str | None, min_similarity: float, limit: int) -> str:
        results = self.db.search_fuzzy(query=query, limit=limit, file_type=file_type, min_similarity=min_similarity)
        if not results:
            return f"Keine Fuzzy-Treffer fuer '{query}'"

        output = f"Fuzzy-Suche: {len(results)} Treffer fuer '{query}'\n\n"
        for i, r in enumerate(results, 1):
            output += f"""--- Treffer {i} (Aehnlichkeit: {r['similarity']}) ---
Dokument: {r['file_name']}
ID: {r['doc_id']}
Typ: {r['file_type']}
Groesse: {r['content_length'] or 0:,} Zeichen
Snippet: {r['snippet']}
---

"""
        return output

    def _tool_list_documents(self, file_type: str | None, search_filename: str | None, limit: int) -> str:
        docs = self.db.list_documents(file_type=file_type, search_filename=search_filename, limit=limit)
        if not docs:
            return "Keine Dokumente gefunden."

        output = f"Dokumente: {len(docs)} gefunden\n\n"
        for doc in docs:
            size_kb = (doc["size_bytes"] or 0) / 1024
            content_k = (doc["content_length"] or 0) / 1000
            output += f"- {doc['file_name']} ({doc['file_type']}, {size_kb:.1f} KB, {content_k:.0f}k Zeichen) [ID: {doc['doc_id']}]\n"
        return output

    def _tool_read_document(self, doc_id: str, start_char: int | None, end_char: int | None) -> str:
        max_read = 50000

        if start_char is None and end_char is None:
            doc = self.db.get_document(doc_id)
            if not doc:
                return f"Dokument nicht gefunden: {doc_id}"

            total_length = doc.content_length or 0
            if total_length > max_read:
                result = self.db.get_document_content(doc_id, 0, max_read)
                return f"""Dokument: {result['file_name']}
Typ: {result['file_type']}
Gesamtgroesse: {total_length:,} Zeichen
HINWEIS: Dokument ist gross. Zeige erste {max_read:,} Zeichen.

INHALT (Zeichen 0-{max_read}):
{result['content']}

[... Nutze read_document mit start_char={max_read} fuer mehr.]"""

        if end_char is not None and start_char is not None:
            if end_char - start_char > max_read:
                end_char = start_char + max_read

        result = self.db.get_document_content(doc_id, start_char, end_char)
        if not result:
            return f"Dokument nicht gefunden: {doc_id}"

        range_info = ""
        if start_char is not None or end_char is not None:
            range_info = f"\nBereich: Zeichen {start_char or 0} - {end_char or result['content_length']}"

        return f"""Dokument: {result['file_name']}
Typ: {result['file_type']}
Gesamtgroesse: {result['content_length']:,} Zeichen{range_info}

INHALT:
{result['content']}"""

    def _tool_get_statistics(self) -> str:
        stats = self.db.get_statistics()
        output = f"""Dokumentenbasis-Statistiken:

Dokumente gesamt: {stats['total_documents']:,}
Davon indiziert: {stats['indexed_documents']:,}
Mit Fehlern: {stats['error_documents']:,}

Gesamtgroesse (Dateien): {stats['total_size_bytes'] / 1024 / 1024:.1f} MB
Gesamter Textinhalt: {stats['total_content_chars']:,} Zeichen

Nach Dateityp:
"""
        for ft, count in sorted(stats["file_types"].items(), key=lambda x: -x[1]):
            output += f"  - {ft}: {count:,}\n"
        return output


# Global server instance
mcp_server = MCPHttpServer()

# Global scheduler instance
scheduler: AsyncIOScheduler | None = None
sync_manager: SyncManager | None = None


def create_sync_manager() -> SyncManager:
    """Create and configure the sync manager."""
    config = get_config()
    
    if config.database.type == "postgresql" and config.database.url:
        db = DocumentRepository(config.database.url)
    else:
        db = DocumentRepository(config.database.path)
    
    # Create connectors
    connectors = {}
    for conn_config in config.connectors:
        if conn_config.type == "local":
            connectors[conn_config.name] = LocalConnector(
                name=conn_config.name,
                root_path=conn_config.path,
                include_patterns=conn_config.include
                if conn_config.include is not None
                else config.indexer.include,
                exclude_patterns=conn_config.exclude
                if conn_config.exclude is not None
                else config.indexer.exclude,
            )
    
    indexer = Indexer(db, connectors, config)
    return SyncManager(db, connectors, config, indexer)


async def scheduled_sync():
    """Run scheduled incremental sync."""
    global sync_manager
    if sync_manager is None:
        logger.warning("SyncManager not initialized, skipping scheduled sync")
        return
    
    if sync_manager.is_running:
        logger.info("Sync already in progress, skipping scheduled run")
        return
    
    logger.info("Starting scheduled incremental sync...")
    try:
        result = await sync_manager.incremental_sync()
        total_indexed = sum(r.get("indexed", 0) for r in result.get("results", {}).values())
        total_removed = sum(r.get("removed", 0) for r in result.get("results", {}).values())
        logger.info(f"Scheduled sync completed: {total_indexed} indexed, {total_removed} removed in {result.get('duration_seconds', 0):.1f}s")
    except Exception as e:
        logger.error(f"Scheduled sync failed: {e}")


def parse_cron_schedule(cron_expr: str) -> dict:
    """Parse a cron expression into APScheduler CronTrigger arguments."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {cron_expr}")
    
    minute, hour, day, month, day_of_week = parts
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup and start scheduler."""
    global scheduler, sync_manager
    
    mcp_server.initialize_database()
    
    # Initialize sync manager and scheduler
    config = get_config()
    if config.indexer.sync_schedule:
        try:
            sync_manager = create_sync_manager()
            
            # Create scheduler
            scheduler = AsyncIOScheduler()
            
            # Parse cron expression and add job
            cron_args = parse_cron_schedule(config.indexer.sync_schedule)
            scheduler.add_job(
                scheduled_sync,
                CronTrigger(**cron_args),
                id="auto_sync",
                name="Automatic document indexing",
                replace_existing=True,
            )
            
            scheduler.start()
            logger.info(f"Scheduler started with schedule: {config.indexer.sync_schedule}")
            
            # Run initial sync on startup (incremental)
            logger.info("Running initial sync on startup...")
            asyncio.create_task(scheduled_sync())
            
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")
    else:
        logger.info("No sync_schedule configured, automatic indexing disabled")
    
    yield
    
    # Shutdown scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


# Create FastAPI app
app = FastAPI(
    title="RLM Knowledge Base MCP Server",
    description="HTTP-based MCP Server for document search and retrieval",
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


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "server": mcp_server.server_info}


@app.post("/mcp")
async def mcp_streamable_http(request: Request, authenticated: bool = Depends(verify_token)):
    """
    Streamable HTTP transport endpoint (recommended for n8n).

    Handles JSON-RPC requests and returns responses.
    Supports both single requests and batches.
    Requires Bearer token authentication.
    """
    try:
        body = await request.json()
        logger.info(f"Received MCP request: {body.get('method', 'unknown')}")

        # Handle JSON-RPC request
        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params")

        try:
            result = mcp_server.handle_request(method, params)
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        except Exception as e:
            logger.error(f"Error handling request: {e}")
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": str(e),
                },
            }

        return Response(
            content=json.dumps(response),
            media_type="application/json",
        )

    except Exception as e:
        logger.error(f"Request parsing error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/mcp/sse")
async def mcp_sse_endpoint(request: Request, authenticated: bool = Depends(verify_token)):
    """
    SSE endpoint for legacy MCP clients.

    Creates a session and returns events via Server-Sent Events.
    Requires Bearer token authentication.
    """
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    mcp_server._sse_sessions[session_id] = queue

    logger.info(f"SSE session created: {session_id}")

    async def event_generator():
        # Send endpoint event first
        endpoint_url = str(request.url).replace("/sse", "/message")
        yield f"event: endpoint\ndata: {endpoint_url}?sessionId={session_id}\n\n"

        try:
            while True:
                try:
                    # Wait for messages with timeout
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"event: message\ndata: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            mcp_server._sse_sessions.pop(session_id, None)
            logger.info(f"SSE session closed: {session_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/mcp/message")
async def mcp_sse_message(request: Request, sessionId: str, authenticated: bool = Depends(verify_token)):
    """
    Message endpoint for SSE transport.

    Receives messages and sends responses via the SSE connection.
    Requires Bearer token authentication.
    """
    queue = mcp_server._sse_sessions.get(sessionId)
    if not queue:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        body = await request.json()
        logger.info(f"SSE message received: {body.get('method', 'unknown')}")

        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params")

        try:
            result = mcp_server.handle_request(method, params)
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        except Exception as e:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": str(e)},
            }

        # Put response in queue for SSE delivery
        await queue.put(response)

        return Response(status_code=202)

    except Exception as e:
        logger.error(f"Message handling error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="RLM Knowledge Base HTTP MCP Server")
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HTTP_HOST", "0.0.0.0"),
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_HTTP_PORT", "3000")),
        help="Port to bind to (default: 3000)",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default="config.yaml",
        help="Path to configuration file",
    )

    args = parser.parse_args()

    # Change to config directory if specified
    if args.config != "config.yaml":
        config_path = Path(args.config)
        if config_path.exists():
            os.chdir(config_path.parent)

    # Initialize token early to display it
    token = get_api_token()

    print("=" * 60)
    print("RLM Knowledge Base MCP HTTP Server")
    print("=" * 60)
    print(f"  Endpoint:     http://{args.host}:{args.port}/mcp")
    print(f"  Health Check: http://{args.host}:{args.port}/health")
    print()
    print("  Authentication: Bearer Token")
    print(f"  Token: {token}")
    print()
    print("  For n8n: Use 'Bearer Auth' credential with this token")
    print("=" * 60)

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
