"""MCP Server for RLM Knowledge Base.

This module provides an MCP (Model Context Protocol) server that exposes
the document search and retrieval functionality to MCP-compatible clients
like Claude Desktop, Claude.ai, or other AI assistants.

Usage:
    python -m src.mcp_server

Configuration:
    Uses the same config.yaml as the main application.
    Database path and connector settings are read from config.

Claude Desktop Integration:
    Add to ~/AppData/Roaming/Claude/claude_desktop_config.json:
    {
        "mcpServers": {
            "rlm-knowledge-base": {
                "command": "python",
                "args": ["-m", "src.mcp_server"],
                "cwd": "C:\\path\\to\\rlm-connector"
            }
        }
    }
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

# MCP SDK imports
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import get_config, init_config
from src.database.repository import DocumentRepository

# Configure logging - write to stderr so stdout stays clean for MCP protocol
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_server")


# Tool definitions matching the existing engine tools
TOOLS = [
    Tool(
        name="search_documents",
        description=(
            "Schnelle Volltextsuche (FTS5) über alle Dokumente. "
            "Findet exakte Wörter und Präfixe. NUTZE DIES ZUERST - es ist schnell und günstig. "
            "Gibt Fundstellen mit Textausschnitten zurück."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suchbegriff(e) - findet exakte Wörter und Präfixe (z.B. 'Rechnung' findet 'Rechnungen')",
                },
                "file_type": {
                    "type": "string",
                    "description": "Optional: Filter nach Dateityp (pdf, docx, xlsx, txt)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximale Anzahl der Ergebnisse (default: 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="search_fuzzy",
        description=(
            "Trigram-basierte Fuzzy-Suche mit Tippfehler-Toleranz. "
            "NUTZE DIES NUR wenn search_documents keine Treffer liefert oder "
            "der Suchbegriff unsicher/fehlerhaft sein könnte. "
            "Findet auch bei Tippfehlern (z.B. 'Rechnug' findet 'Rechnung')."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suchbegriff - auch mit Tippfehlern. Die Suche findet ähnliche Wörter.",
                },
                "file_type": {
                    "type": "string",
                    "description": "Optional: Filter nach Dateityp (pdf, docx, xlsx, txt)",
                },
                "min_similarity": {
                    "type": "number",
                    "description": "Minimale Ähnlichkeit 0.0-1.0 (default: 0.3). Höher = weniger aber genauere Treffer.",
                    "default": 0.3,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximale Anzahl der Ergebnisse (default: 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="list_documents",
        description=(
            "Liste alle Dokumente mit Metadaten (ohne Inhalt). "
            "Nutze dies für einen Überblick über verfügbare Dateien."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_type": {
                    "type": "string",
                    "description": "Optional: Filter nach Dateityp (pdf, docx, xlsx, txt)",
                },
                "search_filename": {
                    "type": "string",
                    "description": "Optional: Suche im Dateinamen",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximale Anzahl (default: 50)",
                    "default": 50,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="read_document",
        description=(
            "Lese den Inhalt eines Dokuments. "
            "Bei großen Dokumenten kannst du einen Bereich angeben."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Die ID des Dokuments (aus search_documents oder list_documents)",
                },
                "start_char": {
                    "type": "integer",
                    "description": "Optional: Startposition (Zeichennummer). Nützlich für große Dokumente.",
                },
                "end_char": {
                    "type": "integer",
                    "description": "Optional: Endposition (Zeichennummer). Max 50000 Zeichen pro Aufruf.",
                },
            },
            "required": ["doc_id"],
        },
    ),
    Tool(
        name="get_statistics",
        description=(
            "Zeige Statistiken über die Dokumentenbasis "
            "(Anzahl Dokumente, Dateitypen, Gesamtgröße)."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
]


class RLMConnectorMCPServer:
    """MCP Server implementation for RLM Knowledge Base."""

    def __init__(self):
        """Initialize the MCP server."""
        self.server = Server("rlm-knowledge-base")
        self.db: DocumentRepository | None = None
        self._setup_handlers()

    def _setup_handlers(self):
        """Set up MCP request handlers."""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """Return list of available tools."""
            return TOOLS

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            """Handle tool calls."""
            if self.db is None:
                return [TextContent(type="text", text="Error: Database not initialized")]

            try:
                result = self._execute_tool(name, arguments)
                return [TextContent(type="text", text=result)]
            except Exception as e:
                logger.error(f"Tool execution error ({name}): {e}")
                return [TextContent(type="text", text=f"Error: {str(e)}")]

    def _initialize_database(self):
        """Initialize the database connection."""
        try:
            # Try to find config.yaml in common locations
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

            # Initialize database
            db_path = Path(config.database.path)
            if not db_path.is_absolute():
                # Make relative path absolute from config file location or cwd
                if config_path:
                    db_path = config_path.parent / db_path
                else:
                    db_path = Path.cwd() / db_path

            logger.info(f"Connecting to database: {db_path}")
            self.db = DocumentRepository(db_path)

            # Log statistics
            stats = self.db.get_statistics()
            logger.info(
                f"Database ready: {stats['indexed_documents']} documents, "
                f"{stats['total_content_chars']:,} characters"
            )

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool and return the result as string."""
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

    def _tool_search_documents(
        self,
        query: str,
        file_type: str | None = None,
        limit: int = 10,
    ) -> str:
        """Full-text search tool implementation."""
        results = self.db.search_fulltext(query=query, limit=limit, file_type=file_type)

        if not results:
            return f"Keine Treffer für '{query}'"

        output = f"Gefunden: {len(results)} Treffer für '{query}'\n\n"

        for i, r in enumerate(results, 1):
            output += f"""--- Treffer {i} ---
Dokument: {r['file_name']}
ID: {r['doc_id']}
Typ: {r['file_type']}
Größe: {r['content_length'] or 0:,} Zeichen
Snippet: {r['snippet']}
---

"""
        return output

    def _tool_search_fuzzy(
        self,
        query: str,
        file_type: str | None = None,
        min_similarity: float = 0.3,
        limit: int = 10,
    ) -> str:
        """Fuzzy search tool implementation using trigram matching."""
        results = self.db.search_fuzzy(
            query=query,
            limit=limit,
            file_type=file_type,
            min_similarity=min_similarity,
        )

        if not results:
            return f"Keine Fuzzy-Treffer für '{query}' (Ähnlichkeit >= {min_similarity*100:.0f}%)"

        output = f"Fuzzy-Suche: {len(results)} Treffer für '{query}'\n\n"

        for i, r in enumerate(results, 1):
            output += f"""--- Treffer {i} (Ähnlichkeit: {r['similarity']}) ---
Dokument: {r['file_name']}
ID: {r['doc_id']}
Typ: {r['file_type']}
Größe: {r['content_length'] or 0:,} Zeichen
Snippet: {r['snippet']}
---

"""
        return output

    def _tool_list_documents(
        self,
        file_type: str | None = None,
        search_filename: str | None = None,
        limit: int = 50,
    ) -> str:
        """List documents tool implementation."""
        docs = self.db.list_documents(
            file_type=file_type,
            search_filename=search_filename,
            limit=limit,
        )

        if not docs:
            return "Keine Dokumente gefunden."

        output = f"Dokumente: {len(docs)} gefunden\n\n"

        for doc in docs:
            size_kb = (doc["size_bytes"] or 0) / 1024
            content_k = (doc["content_length"] or 0) / 1000
            output += f"- {doc['file_name']} ({doc['file_type']}, {size_kb:.1f} KB, {content_k:.0f}k Zeichen) [ID: {doc['doc_id']}]\n"

        return output

    def _tool_read_document(
        self,
        doc_id: str,
        start_char: int | None = None,
        end_char: int | None = None,
    ) -> str:
        """Read document content tool implementation."""
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
Gesamtgröße: {total_length:,} Zeichen
HINWEIS: Dokument ist groß. Zeige erste {max_read:,} Zeichen.
Nutze start_char/end_char um weitere Teile zu lesen.

INHALT (Zeichen 0-{max_read}):
{result['content']}

[... Dokument fortgesetzt. Nutze read_document mit start_char={max_read} für mehr.]"""

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
Gesamtgröße: {result['content_length']:,} Zeichen{range_info}

INHALT:
{result['content']}"""

    def _tool_get_statistics(self) -> str:
        """Get database statistics tool implementation."""
        stats = self.db.get_statistics()

        output = f"""Dokumentenbasis-Statistiken:

Dokumente gesamt: {stats['total_documents']:,}
Davon indiziert: {stats['indexed_documents']:,}
Mit Fehlern: {stats['error_documents']:,}

Gesamtgröße (Dateien): {stats['total_size_bytes'] / 1024 / 1024:.1f} MB
Gesamter Textinhalt: {stats['total_content_chars']:,} Zeichen

Nach Dateityp:
"""
        for ft, count in sorted(stats["file_types"].items(), key=lambda x: -x[1]):
            output += f"  - {ft}: {count:,}\n"

        return output

    async def run(self):
        """Run the MCP server."""
        logger.info("Starting RLM Knowledge Base MCP Server...")

        # Initialize database
        self._initialize_database()

        # Run the server using stdio transport
        async with stdio_server() as (read_stream, write_stream):
            logger.info("MCP Server ready, listening on stdio")
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )


def main():
    """Main entry point."""
    server = RLMConnectorMCPServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
