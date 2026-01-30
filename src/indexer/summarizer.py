"""Document summarizer using LLM."""

import json
import logging
from dataclasses import dataclass
from typing import Any

from ..config import SummaryConfig
from ..connectors.base import FileMetadata
from .llm_client import BaseLLMClient

logger = logging.getLogger(__name__)


@dataclass
class SummaryResult:
    """Result of document summarization."""

    summary: str | None = None
    keywords: list[str] | None = None
    document_type: str | None = None
    entities: dict[str, Any] | None = None
    structure: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "keywords": self.keywords,
            "document_type": self.document_type,
            "entities": self.entities,
            "structure": self.structure,
        }


class DocumentSummarizer:
    """Generates summaries and metadata for documents using LLM."""

    SYSTEM_PROMPT = """Du bist ein Dokumenten-Analyst. Deine Aufgabe ist es, Dokumente zu analysieren
und strukturierte Metadaten zu extrahieren. Antworte immer nur mit validem JSON, keine Erklärungen."""

    def __init__(self, llm_client: BaseLLMClient, config: SummaryConfig):
        """Initialize the summarizer.

        Args:
            llm_client: LLM client for generating summaries.
            config: Summary configuration.
        """
        self.llm = llm_client
        self.config = config

    async def summarize(
        self,
        content: str,
        metadata: FileMetadata,
    ) -> SummaryResult:
        """Generate a summary for a document.

        Args:
            content: Extracted text content of the document.
            metadata: File metadata.

        Returns:
            SummaryResult with summary, keywords, etc.
        """
        if not content.strip():
            return SummaryResult(error="Kein Textinhalt vorhanden")

        # Truncate content if too long
        max_length = self.config.max_content_length
        truncated = content[:max_length] if len(content) > max_length else content
        was_truncated = len(content) > max_length

        language = self.config.language.upper()

        prompt = f"""Analysiere folgendes Dokument und erstelle einen strukturierten Steckbrief.

**Datei-Informationen:**
- Dateiname: {metadata.name}
- Dateityp: {metadata.file_type}
- Größe: {metadata.size_bytes} Bytes
- Erstellt: {metadata.created_at or 'unbekannt'}
- Geändert: {metadata.modified_at}

**Dokumentinhalt{' (gekürzt)' if was_truncated else ''}:**
{truncated}

---

Erstelle einen JSON-Steckbrief mit genau diesen Feldern:

{{
  "summary": "2-3 prägnante Sätze auf {language}, die den Kerninhalt zusammenfassen",
  "keywords": ["keyword1", "keyword2", ...],  // 5-10 relevante Schlagwörter auf {language}
  "document_type": "Kategorie",  // z.B. Rechnung, Vertrag, Angebot, Protokoll, E-Mail, Bericht, Tabelle, Präsentation, Notiz, etc.
  "entities": {{
    "personen": ["Name1", "Name2"],  // erkannte Personennamen
    "firmen": ["Firma1", "Firma2"],  // erkannte Firmennamen
    "betraege": ["100€", "1.500,00 EUR"],  // erkannte Geldbeträge
    "daten": ["01.01.2024", "Q3 2024"]  // erkannte Daten/Zeiträume
  }},
  "structure": "Kurze Beschreibung der Dokumentstruktur / Hauptabschnitte"
}}

Antworte NUR mit dem JSON-Objekt, keine zusätzlichen Erklärungen oder Markdown-Formatierung."""

        try:
            response = await self.llm.complete(prompt, self.SYSTEM_PROMPT)

            # Clean up response - remove potential markdown code blocks
            json_str = response.strip()
            if json_str.startswith("```"):
                lines = json_str.split("\n")
                # Remove first and last lines if they're code block markers
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                json_str = "\n".join(lines)

            # Parse JSON
            data = json.loads(json_str)

            return SummaryResult(
                summary=data.get("summary"),
                keywords=data.get("keywords", []),
                document_type=data.get("document_type"),
                entities=data.get("entities", {}),
                structure=data.get("structure"),
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"Response was: {response[:500] if 'response' in dir() else 'N/A'}")
            return SummaryResult(error=f"JSON-Parsing fehlgeschlagen: {e}")

        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return SummaryResult(error=str(e))

    async def summarize_batch(
        self,
        documents: list[tuple[str, FileMetadata]],
    ) -> list[SummaryResult]:
        """Summarize multiple documents.

        Args:
            documents: List of (content, metadata) tuples.

        Returns:
            List of SummaryResult for each document.
        """
        results = []
        for content, metadata in documents:
            result = await self.summarize(content, metadata)
            results.append(result)
        return results
