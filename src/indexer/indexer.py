"""Document indexer for processing and storing document content (no LLM required)."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from ..config import AppConfig
from ..connectors.base import BaseConnector, FileMetadata
from ..database.repository import DocumentRepository
from .parser import DocumentParser

logger = logging.getLogger(__name__)


class Indexer:
    """Indexes documents from connectors into the database.

    This indexer stores full document content without LLM processing.
    The RLM engine will handle intelligent querying via FTS5 and direct access.
    """

    def __init__(
        self,
        db: DocumentRepository,
        connectors: dict[str, BaseConnector],
        config: AppConfig,
    ):
        """Initialize the indexer.

        Args:
            db: Document repository for storing index data.
            connectors: Dictionary of connector name to connector instance.
            config: Application configuration.
        """
        self.db = db
        self.connectors = connectors
        self.config = config
        self.parser = DocumentParser()
        self._executor = ThreadPoolExecutor(max_workers=config.indexer.max_concurrent)

        # Progress tracking
        self._current_progress = {
            "total": 0,
            "processed": 0,
            "indexed": 0,
            "skipped": 0,
            "errors": 0,
            "current_file": "",
        }

    @property
    def progress(self) -> dict[str, Any]:
        """Get current indexing progress."""
        return self._current_progress.copy()

    async def index_file(
        self,
        connector: BaseConnector,
        file_path: str,
        force: bool = False,
    ) -> bool:
        """Index a single file.

        Args:
            connector: The connector to read the file from.
            file_path: Path to the file within the connector.
            force: If True, re-index even if hash matches.

        Returns:
            True if file was indexed, False if skipped or error.
        """
        try:
            self._current_progress["current_file"] = file_path

            # Get metadata
            metadata = connector.get_metadata(file_path)

            # Check if update is needed
            if not force:
                existing = self.db.get_document_by_path(connector.name, file_path)
                if existing and existing.content_hash == metadata.content_hash:
                    logger.debug(f"Skipping unchanged file: {file_path}")
                    return False

            # Check file size limit
            max_size = self.config.indexer.max_file_size_mb * 1024 * 1024
            if metadata.size_bytes > max_size:
                logger.warning(f"File too large, skipping: {file_path} ({metadata.size_bytes} bytes)")
                self.db.upsert_document(
                    connector_name=connector.name,
                    file_path=file_path,
                    file_name=metadata.name,
                    file_type=metadata.file_type,
                    size_bytes=metadata.size_bytes,
                    created_at=metadata.created_at,
                    modified_at=metadata.modified_at,
                    content_hash=metadata.content_hash,
                    status="skipped",
                    error_message=f"File too large: {metadata.size_bytes} bytes (max: {max_size})",
                )
                return False

            # Check if file type is supported
            if not DocumentParser.is_supported(file_path):
                logger.debug(f"Unsupported file type, skipping: {file_path}")
                return False

            # Read and parse content
            content_bytes = connector.read_file(file_path)
            content_text = DocumentParser.parse(content_bytes, file_path)

            if not content_text:
                logger.warning(f"Could not extract text from: {file_path}")
                self.db.upsert_document(
                    connector_name=connector.name,
                    file_path=file_path,
                    file_name=metadata.name,
                    file_type=metadata.file_type,
                    size_bytes=metadata.size_bytes,
                    created_at=metadata.created_at,
                    modified_at=metadata.modified_at,
                    content_hash=metadata.content_hash,
                    status="error",
                    error_message="Could not extract text content",
                )
                return False

            # Estimate page count (for PDFs it's calculated during parsing)
            page_count = self._estimate_page_count(content_text, metadata.file_type)

            # Store in database (full text, no LLM processing)
            self.db.upsert_document(
                connector_name=connector.name,
                file_path=file_path,
                file_name=metadata.name,
                file_type=metadata.file_type,
                size_bytes=metadata.size_bytes,
                created_at=metadata.created_at,
                modified_at=metadata.modified_at,
                content_hash=metadata.content_hash,
                content_text=content_text,
                page_count=page_count,
                status="indexed",
            )

            logger.info(f"Indexed: {file_path} ({len(content_text)} chars)")
            return True

        except FileNotFoundError:
            logger.warning(f"File not found: {file_path}")
            return False
        except Exception as e:
            logger.error(f"Error indexing {file_path}: {e}")
            self.db.upsert_document(
                connector_name=connector.name,
                file_path=file_path,
                file_name=file_path.split("/")[-1].split("\\")[-1],
                file_type=file_path.split(".")[-1] if "." in file_path else "unknown",
                status="error",
                error_message=str(e),
            )
            return False

    def _estimate_page_count(self, content: str, file_type: str) -> int | None:
        """Estimate page count from content length."""
        if not content:
            return None

        # Average characters per page (rough estimate)
        chars_per_page = 2000

        if file_type == "pdf":
            # PDFs usually have more content per page
            chars_per_page = 3000
        elif file_type in ("xlsx", "csv"):
            # Spreadsheets don't really have "pages"
            return None

        return max(1, len(content) // chars_per_page)

    async def index_connector(
        self,
        connector_name: str,
        force: bool = False,
        progress_callback: callable = None,
    ) -> dict[str, int]:
        """Index all files from a connector.

        Args:
            connector_name: Name of the connector to index.
            force: If True, re-index all files.
            progress_callback: Optional callback for progress updates.

        Returns:
            Dictionary with counts: total, indexed, skipped, errors.
        """
        connector = self.connectors.get(connector_name)
        if not connector:
            raise ValueError(f"Unknown connector: {connector_name}")

        # Update sync status
        self.db.update_sync_status(connector_name, is_syncing=True)

        # Reset progress
        self._current_progress = {
            "total": 0,
            "processed": 0,
            "indexed": 0,
            "skipped": 0,
            "errors": 0,
            "current_file": "",
        }

        counts = {"total": 0, "indexed": 0, "skipped": 0, "errors": 0}

        try:
            # List all files
            logger.info(f"Scanning files in connector: {connector_name}")
            files = list(connector.list_files_recursive())
            counts["total"] = len(files)
            self._current_progress["total"] = len(files)

            logger.info(f"Found {len(files)} files in connector: {connector_name}")

            # Index files with concurrency limit
            semaphore = asyncio.Semaphore(self.config.indexer.max_concurrent)

            async def index_with_semaphore(file_info):
                async with semaphore:
                    result = await self.index_file(connector, file_info.path, force=force)
                    self._current_progress["processed"] += 1
                    if progress_callback:
                        progress_callback(self._current_progress)
                    return result

            # Process files
            tasks = [index_with_semaphore(f) for f in files]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    counts["errors"] += 1
                    self._current_progress["errors"] += 1
                elif result:
                    counts["indexed"] += 1
                    self._current_progress["indexed"] += 1
                else:
                    counts["skipped"] += 1
                    self._current_progress["skipped"] += 1

            # Update sync status
            self.db.update_sync_status(
                connector_name,
                last_sync_at=datetime.now(),
                documents_total=counts["total"],
                documents_indexed=counts["indexed"],
                documents_error=counts["errors"],
                is_syncing=False,
            )

            logger.info(
                f"Indexed connector {connector_name}: "
                f"{counts['indexed']} indexed, {counts['skipped']} skipped, "
                f"{counts['errors']} errors"
            )

            return counts

        except Exception as e:
            logger.error(f"Error indexing connector {connector_name}: {e}")
            self.db.update_sync_status(
                connector_name,
                is_syncing=False,
                error_message=str(e),
            )
            raise

    async def index_all(self, force: bool = False) -> dict[str, dict[str, int]]:
        """Index all files from all connectors.

        Args:
            force: If True, re-index all files.

        Returns:
            Dictionary mapping connector names to their counts.
        """
        results = {}

        for connector_name in self.connectors:
            try:
                results[connector_name] = await self.index_connector(
                    connector_name, force=force
                )
            except Exception as e:
                logger.error(f"Error indexing connector {connector_name}: {e}")
                results[connector_name] = {"error": str(e)}

        return results

    def cleanup_deleted_files(self, connector_name: str) -> int:
        """Remove index entries for files that no longer exist.

        Args:
            connector_name: Name of the connector to check.

        Returns:
            Number of documents removed.
        """
        connector = self.connectors.get(connector_name)
        if not connector:
            raise ValueError(f"Unknown connector: {connector_name}")

        # Get all indexed documents for this connector
        indexed_docs = self.db.get_all_documents(connector_name=connector_name)

        removed = 0
        for doc in indexed_docs:
            if not connector.file_exists(doc.file_path):
                self.db.delete_document(doc.id)
                logger.info(f"Removed deleted file from index: {doc.file_path}")
                removed += 1

        return removed
