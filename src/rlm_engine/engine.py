"""Knowledge Base Engine with Tool-Use (RLM) for querying documents.

This engine uses FTS5 full-text search and intelligent document access
instead of pre-computed summaries/embeddings. The LLM decides what to
read based on search results and document metadata.
"""

import json
import logging
import re
from typing import Any

from ..config import AppConfig
from ..connectors.base import BaseConnector
from ..database.repository import DocumentRepository

logger = logging.getLogger(__name__)


# Tool definitions for Claude
TOOLS = [
    {
        "name": "search_documents",
        "description": "Schnelle Volltextsuche (FTS5) über alle Dokumente. Findet exakte Wörter und Präfixe. NUTZE DIES ZUERST - es ist schnell und günstig. Gibt Fundstellen mit Textausschnitten zurück.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suchbegriff(e) - findet exakte Wörter und Präfixe (z.B. 'Rechnung' findet 'Rechnungen')"
                },
                "file_type": {
                    "type": "string",
                    "description": "Optional: Filter nach Dateityp (pdf, docx, xlsx, txt)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximale Anzahl der Ergebnisse (default: 10)",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "search_fuzzy",
        "description": "Trigram-basierte Fuzzy-Suche mit Tippfehler-Toleranz. NUTZE DIES NUR wenn search_documents keine Treffer liefert oder der Suchbegriff unsicher/fehlerhaft sein könnte. Findet auch bei Tippfehlern (z.B. 'Rechnug' findet 'Rechnung').",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suchbegriff - auch mit Tippfehlern. Die Suche findet ähnliche Wörter."
                },
                "file_type": {
                    "type": "string",
                    "description": "Optional: Filter nach Dateityp (pdf, docx, xlsx, txt)"
                },
                "min_similarity": {
                    "type": "number",
                    "description": "Minimale Ähnlichkeit 0.0-1.0 (default: 0.3). Höher = weniger aber genauere Treffer.",
                    "default": 0.3
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximale Anzahl der Ergebnisse (default: 10)",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "list_documents",
        "description": "Liste alle Dokumente mit Metadaten (ohne Inhalt). Nutze dies für einen Überblick über verfügbare Dateien.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_type": {
                    "type": "string",
                    "description": "Optional: Filter nach Dateityp (pdf, docx, xlsx, txt)"
                },
                "search_filename": {
                    "type": "string",
                    "description": "Optional: Suche im Dateinamen"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximale Anzahl (default: 50)",
                    "default": 50
                }
            },
            "required": []
        }
    },
    {
        "name": "read_document",
        "description": "Lese den Inhalt eines Dokuments. Bei großen Dokumenten kannst du einen Bereich angeben.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Die ID des Dokuments (aus search_documents oder list_documents)"
                },
                "start_char": {
                    "type": "integer",
                    "description": "Optional: Startposition (Zeichennummer). Nützlich für große Dokumente."
                },
                "end_char": {
                    "type": "integer",
                    "description": "Optional: Endposition (Zeichennummer). Max 50000 Zeichen pro Aufruf."
                }
            },
            "required": ["doc_id"]
        }
    },
    {
        "name": "get_statistics",
        "description": "Zeige Statistiken über die Dokumentenbasis (Anzahl Dokumente, Dateitypen, Gesamtgröße).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

SYSTEM_PROMPT = """Du bist ein intelligenter Assistent mit Zugriff auf eine Unternehmens-Dokumentenbasis.

Du hast folgende Tools zur Verfügung:
1. search_documents - Schnelle exakte Volltextsuche (NUTZE DIES ZUERST!)
2. search_fuzzy - Fuzzy-Suche mit Tippfehler-Toleranz (nur wenn search_documents versagt)
3. list_documents - Übersicht aller Dokumente
4. read_document - Lese Dokumentinhalt (ganz oder Bereich)
5. get_statistics - Statistiken zur Dokumentenbasis

VORGEHENSWEISE:
1. Nutze ZUERST search_documents - es ist schnell und effizient
2. NUR wenn search_documents keine Treffer liefert: Nutze search_fuzzy für Tippfehler-tolerante Suche
3. Die Suchergebnisse zeigen dir Snippets mit den Fundstellen
4. Wenn du mehr Kontext brauchst, lies das Dokument mit read_document
5. Bei großen Dokumenten (>50000 Zeichen) lies in Abschnitten

WICHTIGE REGELN:
- Gib IMMER an, aus welchem Dokument eine Information stammt (Dateiname)
- Zitiere relevante Textstellen wenn möglich
- Wenn du etwas nicht findest, sage das ehrlich
- Antworte auf Deutsch
- Zeige dein Vorgehen transparent"""


class KnowledgeBaseEngine:
    """Engine for querying the knowledge base with Tool-Use (RLM)."""

    def __init__(
        self,
        db: DocumentRepository,
        connectors: dict[str, BaseConnector],
        config: AppConfig,
    ):
        """Initialize the knowledge base engine.

        Args:
            db: Document repository.
            connectors: Dictionary of connectors.
            config: Application configuration.
        """
        self.db = db
        self.connectors = connectors
        self.config = config
        self._client = None

    def _get_client(self):
        """Get or create the LLM client."""
        if self._client is not None:
            return self._client

        provider = self.config.llm.provider

        if provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.config.llm.api_key)
        elif provider == "openai":
            import openai
            self._client = openai.OpenAI(api_key=self.config.llm.api_key)
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

        return self._client

    def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool and return the result as string.

        Args:
            tool_name: Name of the tool to execute.
            tool_input: Input parameters for the tool.

        Returns:
            Tool result as formatted string.
        """
        try:
            if tool_name == "search_documents":
                return self._tool_search_documents(
                    query=tool_input["query"],
                    file_type=tool_input.get("file_type"),
                    limit=tool_input.get("limit", 10)
                )
            elif tool_name == "search_fuzzy":
                return self._tool_search_fuzzy(
                    query=tool_input["query"],
                    file_type=tool_input.get("file_type"),
                    min_similarity=tool_input.get("min_similarity", 0.3),
                    limit=tool_input.get("limit", 10)
                )
            elif tool_name == "list_documents":
                return self._tool_list_documents(
                    file_type=tool_input.get("file_type"),
                    search_filename=tool_input.get("search_filename"),
                    limit=tool_input.get("limit", 50)
                )
            elif tool_name == "read_document":
                return self._tool_read_document(
                    doc_id=tool_input["doc_id"],
                    start_char=tool_input.get("start_char"),
                    end_char=tool_input.get("end_char")
                )
            elif tool_name == "get_statistics":
                return self._tool_get_statistics()
            else:
                return f"Unbekanntes Tool: {tool_name}"
        except Exception as e:
            logger.error(f"Tool execution error ({tool_name}): {e}")
            return f"Fehler bei Tool-Ausführung: {str(e)}"

    def _tool_search_documents(
        self,
        query: str,
        file_type: str | None = None,
        limit: int = 10
    ) -> str:
        """Full-text search tool implementation."""
        results = self.db.search_fulltext(
            query=query,
            limit=limit,
            file_type=file_type
        )

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
        limit: int = 10
    ) -> str:
        """Fuzzy search tool implementation using trigram matching."""
        results = self.db.search_fuzzy(
            query=query,
            limit=limit,
            file_type=file_type,
            min_similarity=min_similarity
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
        limit: int = 50
    ) -> str:
        """List documents tool implementation."""
        docs = self.db.list_documents(
            file_type=file_type,
            search_filename=search_filename,
            limit=limit
        )

        if not docs:
            return "Keine Dokumente gefunden."

        output = f"Dokumente: {len(docs)} gefunden\n\n"

        for doc in docs:
            size_kb = (doc['size_bytes'] or 0) / 1024
            content_k = (doc['content_length'] or 0) / 1000
            output += f"- {doc['file_name']} ({doc['file_type']}, {size_kb:.1f} KB, {content_k:.0f}k Zeichen) [ID: {doc['doc_id']}]\n"

        return output

    def _tool_read_document(
        self,
        doc_id: str,
        start_char: int | None = None,
        end_char: int | None = None
    ) -> str:
        """Read document content tool implementation."""
        # Limit max read size to prevent context overflow
        max_read = 50000

        if start_char is None and end_char is None:
            # First, check document size
            doc = self.db.get_document(doc_id)
            if not doc:
                return f"Dokument nicht gefunden: {doc_id}"

            total_length = doc.content_length or 0

            if total_length > max_read:
                # Read only first part, inform about size
                result = self.db.get_document_content(doc_id, 0, max_read)
                return f"""Dokument: {result['file_name']}
Typ: {result['file_type']}
Gesamtgröße: {total_length:,} Zeichen
HINWEIS: Dokument ist groß. Zeige erste {max_read:,} Zeichen.
Nutze start_char/end_char um weitere Teile zu lesen.

INHALT (Zeichen 0-{max_read}):
{result['content']}

[... Dokument fortgesetzt. Nutze read_document mit start_char={max_read} für mehr.]"""

        # Apply limit to range
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
        for ft, count in sorted(stats['file_types'].items(), key=lambda x: -x[1]):
            output += f"  - {ft}: {count:,}\n"

        return output

    async def query(
        self,
        question: str,
        max_iterations: int = 10,
    ) -> dict[str, Any]:
        """Query the knowledge base using agentic tool-use loop.

        Args:
            question: The user's question.
            max_iterations: Maximum number of tool-use iterations.

        Returns:
            Dictionary with answer, sources, and token usage.
        """
        client = self._get_client()
        provider = self.config.llm.provider
        model = self.config.llm.model

        messages = [{"role": "user", "content": question}]
        total_tokens = 0
        tool_calls_made = []

        for iteration in range(max_iterations):
            logger.info(f"RLM iteration {iteration + 1}")

            if provider == "anthropic":
                response = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=messages,
                )

                total_tokens += response.usage.input_tokens + response.usage.output_tokens

                # Check if we have a final response (no tool use)
                if response.stop_reason == "end_turn":
                    # Extract text response
                    answer = ""
                    for block in response.content:
                        if hasattr(block, "text"):
                            answer += block.text

                    sources = self._extract_sources(tool_calls_made)
                    return {
                        "answer": answer,
                        "sources": sources,
                        "tokens_used": total_tokens,
                        "tool_calls": tool_calls_made,
                    }

                # Process tool uses
                tool_results = []
                assistant_content = []

                for block in response.content:
                    if block.type == "text":
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input
                        })

                        logger.info(f"Tool call: {block.name}({json.dumps(block.input, ensure_ascii=False)})")
                        tool_calls_made.append({
                            "tool": block.name,
                            "input": block.input
                        })

                        # Execute tool
                        result = self._execute_tool(block.name, block.input)

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result
                        })

                # Add assistant message and tool results to conversation
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_results})

            else:
                # OpenAI implementation would go here
                raise NotImplementedError("OpenAI tool-use not yet implemented")

        # Max iterations reached
        return {
            "answer": "Die Anfrage konnte nicht vollständig bearbeitet werden (maximale Iterationen erreicht).",
            "sources": [],
            "tokens_used": total_tokens,
            "tool_calls": tool_calls_made,
        }

    def _extract_sources(self, tool_calls: list[dict]) -> list[dict[str, Any]]:
        """Extract document references from tool calls."""
        sources = []
        seen_ids = set()

        for call in tool_calls:
            if call["tool"] == "read_document":
                doc_id = call["input"].get("doc_id")
                if doc_id and doc_id not in seen_ids:
                    doc = self.db.get_document(doc_id)
                    if doc:
                        sources.append({
                            "id": doc.id,
                            "file_name": doc.file_name,
                            "file_path": doc.file_path,
                        })
                        seen_ids.add(doc_id)
            elif call["tool"] == "search_documents":
                # Could also track searched documents
                pass

        return sources

    # Legacy methods for backwards compatibility
    def read_document(self, doc_id: str) -> str:
        """Read the full content of a document."""
        return self._tool_read_document(doc_id)

    def search_documents(self, query: str, limit: int = 10) -> list[dict]:
        """Search for documents."""
        return self.db.search_fulltext(query, limit=limit)
