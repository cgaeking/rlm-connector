"""Database repository for document operations with FTS5 support."""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, Document, SyncStatus


def generate_trigrams(text: str) -> set[str]:
    """Generate trigrams from text for fuzzy matching.

    Args:
        text: Input text to generate trigrams from.

    Returns:
        Set of trigrams (3-character sequences).
    """
    if not text or len(text) < 3:
        return set()

    # Normalize: lowercase, keep alphanumeric and umlauts
    text = text.lower()
    # Pad with spaces for edge trigrams
    text = f"  {text}  "

    trigrams = set()
    for i in range(len(text) - 2):
        trigram = text[i:i+3]
        # Skip trigrams that are all spaces
        if trigram.strip():
            trigrams.add(trigram)

    return trigrams


def trigram_similarity(text1: str, text2: str) -> float:
    """Calculate trigram similarity between two strings.

    Returns a value between 0.0 (no match) and 1.0 (identical).
    """
    trigrams1 = generate_trigrams(text1)
    trigrams2 = generate_trigrams(text2)

    if not trigrams1 or not trigrams2:
        return 0.0

    intersection = len(trigrams1 & trigrams2)
    union = len(trigrams1 | trigrams2)

    return intersection / union if union > 0 else 0.0


class DocumentRepository:
    """Repository for document database operations with FTS5 full-text search."""

    def __init__(self, db_path: str | Path = "./data/index.db"):
        """Initialize the repository.

        Args:
            db_path: Path to SQLite database file.
        """
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            # Enable WAL mode for better concurrent reads
            connect_args={"check_same_thread": False}
        )

        # Enable WAL mode and FTS5
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

        # Create FTS5 virtual table if not exists
        self._setup_fts5()

    def _setup_fts5(self):
        """Create FTS5 virtual table for full-text search."""
        with self.engine.connect() as conn:
            # Create FTS5 table (standalone, stores its own content)
            conn.execute(text("""
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    doc_id UNINDEXED,
                    file_name,
                    content,
                    tokenize='unicode61 remove_diacritics 0'
                )
            """))

            # Create trigram table for fuzzy search
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS document_trigrams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    trigram TEXT NOT NULL,
                    source TEXT NOT NULL,
                    UNIQUE(doc_id, trigram, source)
                )
            """))

            # Create index for fast trigram lookups
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_trigram ON document_trigrams(trigram)
            """))

            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_doc_id_trigrams ON document_trigrams(doc_id)
            """))

            conn.commit()

    def _get_session(self) -> Session:
        """Get a new database session."""
        return self.SessionLocal()

    @staticmethod
    def generate_doc_id(connector_name: str, file_path: str) -> str:
        """Generate a unique document ID from connector and path."""
        combined = f"{connector_name}:{file_path}"
        return hashlib.sha256(combined.encode()).hexdigest()[:32]

    def get_document(self, doc_id: str) -> Document | None:
        """Get a document by ID."""
        with self._get_session() as session:
            return session.get(Document, doc_id)

    def get_document_by_path(self, connector_name: str, file_path: str) -> Document | None:
        """Get a document by connector and path."""
        doc_id = self.generate_doc_id(connector_name, file_path)
        return self.get_document(doc_id)

    def upsert_document(
        self,
        connector_name: str,
        file_path: str,
        file_name: str,
        file_type: str,
        size_bytes: int | None = None,
        created_at: datetime | None = None,
        modified_at: datetime | None = None,
        content_hash: str | None = None,
        content_text: str | None = None,
        page_count: int | None = None,
        status: str = "indexed",
        error_message: str | None = None,
    ) -> Document:
        """Insert or update a document."""
        doc_id = self.generate_doc_id(connector_name, file_path)

        with self._get_session() as session:
            doc = session.get(Document, doc_id)
            is_new = doc is None

            if is_new:
                doc = Document(
                    id=doc_id,
                    connector_name=connector_name,
                    file_path=file_path,
                    file_name=file_name,
                    file_type=file_type,
                )
                session.add(doc)

            # Update fields
            doc.size_bytes = size_bytes
            doc.created_at = created_at
            doc.modified_at = modified_at
            doc.content_hash = content_hash
            doc.content_text = content_text
            doc.content_length = len(content_text) if content_text else None
            doc.page_count = page_count
            doc.status = status
            doc.error_message = error_message
            doc.indexed_at = datetime.now()

            session.commit()

            # Update FTS5 index
            if content_text and status == "indexed":
                self._update_fts_index(doc_id, file_name, content_text, is_new)

            session.refresh(doc)
            return doc

    def _update_fts_index(self, doc_id: str, file_name: str, content: str, is_new: bool):
        """Update the FTS5 index for a document."""
        with self.engine.connect() as conn:
            if not is_new:
                # Delete old entry
                conn.execute(
                    text("DELETE FROM documents_fts WHERE doc_id = :doc_id"),
                    {"doc_id": doc_id}
                )

            # Insert new entry
            conn.execute(
                text("INSERT INTO documents_fts (doc_id, file_name, content) VALUES (:doc_id, :file_name, :content)"),
                {"doc_id": doc_id, "file_name": file_name, "content": content}
            )
            conn.commit()

        # Update trigram index
        self._update_trigram_index(doc_id, file_name, content, is_new)

    def _update_trigram_index(self, doc_id: str, file_name: str, content: str, is_new: bool):
        """Update the trigram index for fuzzy search."""
        with self.engine.connect() as conn:
            if not is_new:
                # Delete old trigrams
                conn.execute(
                    text("DELETE FROM document_trigrams WHERE doc_id = :doc_id"),
                    {"doc_id": doc_id}
                )

            # Generate trigrams from filename
            filename_trigrams = generate_trigrams(file_name)

            # Generate trigrams from content (limit to first 50k chars for performance)
            content_sample = content[:50000] if len(content) > 50000 else content

            # Extract words (3+ chars) and generate trigrams - limit unique words
            words = set(re.findall(r'\b\w{3,}\b', content_sample.lower()))
            # Limit to 1000 unique words for performance
            if len(words) > 1000:
                words = set(list(words)[:1000])

            content_trigrams = set()
            for word in words:
                content_trigrams.update(generate_trigrams(word))

            # Batch insert all trigrams at once
            all_trigrams = []
            for trigram in filename_trigrams:
                all_trigrams.append({"doc_id": doc_id, "trigram": trigram, "source": "filename"})
            for trigram in content_trigrams:
                all_trigrams.append({"doc_id": doc_id, "trigram": trigram, "source": "content"})

            # Use executemany for batch insert
            if all_trigrams:
                conn.execute(
                    text("INSERT OR IGNORE INTO document_trigrams (doc_id, trigram, source) VALUES (:doc_id, :trigram, :source)"),
                    all_trigrams
                )

            conn.commit()

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document by ID."""
        with self._get_session() as session:
            doc = session.get(Document, doc_id)
            if doc:
                session.delete(doc)
                session.commit()

                # Delete from FTS
                with self.engine.connect() as conn:
                    conn.execute(
                        text("DELETE FROM documents_fts WHERE doc_id = :doc_id"),
                        {"doc_id": doc_id}
                    )
                    conn.commit()
                return True
            return False

    def delete_by_connector(self, connector_name: str) -> int:
        """Delete all documents for a connector."""
        with self._get_session() as session:
            docs = session.query(Document).filter(
                Document.connector_name == connector_name
            ).all()

            doc_ids = [d.id for d in docs]
            count = len(doc_ids)

            for doc in docs:
                session.delete(doc)
            session.commit()

            # Delete from FTS
            if doc_ids:
                with self.engine.connect() as conn:
                    for doc_id in doc_ids:
                        conn.execute(
                            text("DELETE FROM documents_fts WHERE doc_id = :doc_id"),
                            {"doc_id": doc_id}
                        )
                    conn.commit()

            return count

    def search_fulltext(
        self,
        query: str,
        limit: int = 20,
        connector_name: str | None = None,
        file_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search using FTS5 with multi-strategy approach.

        Uses multiple search strategies for better recall:
        1. Exact phrase match (highest weight)
        2. AND search with prefix matching
        3. OR search for partial matches
        4. Filename search as fallback

        Returns documents with matching snippets and highlights, sorted by relevance.
        """
        # Escape special FTS5 characters, keep umlauts
        safe_query = re.sub(r'[^\w\s\-äöüÄÖÜß]', ' ', query)
        search_terms = [t for t in safe_query.split() if len(t) >= 2]

        if not search_terms:
            return []

        # Collect results from multiple strategies with different weights
        all_results: dict[str, dict[str, Any]] = {}  # doc_id -> result

        def add_result(doc_id: str, snippet: str, base_score: float, weight: float):
            """Add or update result with weighted score."""
            doc = self.get_document(doc_id)
            if not doc or doc.status != "indexed":
                return
            if connector_name and doc.connector_name != connector_name:
                return
            if file_type and doc.file_type != file_type:
                return

            weighted_score = abs(base_score) * weight

            if doc_id in all_results:
                # Keep best score and snippet
                if weighted_score > all_results[doc_id]["score"]:
                    all_results[doc_id]["score"] = weighted_score
                    all_results[doc_id]["snippet"] = snippet.replace(">>>", "**").replace("<<<", "**")
            else:
                all_results[doc_id] = {
                    "doc_id": doc_id,
                    "file_name": doc.file_name,
                    "file_path": doc.file_path,
                    "file_type": doc.file_type,
                    "snippet": snippet.replace(">>>", "**").replace("<<<", "**"),
                    "score": weighted_score,
                    "content_length": doc.content_length,
                    "page_count": doc.page_count,
                }

        with self.engine.connect() as conn:
            # Strategy 1: Exact phrase match (highest weight)
            if len(search_terms) > 1:
                phrase_query = '"' + ' '.join(search_terms) + '"'
                try:
                    result = conn.execute(
                        text("""
                            SELECT doc_id, snippet(documents_fts, 2, '>>>', '<<<', '...', 64), bm25(documents_fts)
                            FROM documents_fts WHERE documents_fts MATCH :query
                            ORDER BY bm25(documents_fts) LIMIT :limit
                        """),
                        {"query": phrase_query, "limit": limit}
                    )
                    for row in result:
                        add_result(row[0], row[1], row[2], weight=3.0)
                except Exception:
                    pass  # Query might fail, continue with other strategies

            # Strategy 2: AND search with exact terms (high weight)
            and_query = " AND ".join(f'"{term}"' for term in search_terms)
            try:
                result = conn.execute(
                    text("""
                        SELECT doc_id, snippet(documents_fts, 2, '>>>', '<<<', '...', 64), bm25(documents_fts)
                        FROM documents_fts WHERE documents_fts MATCH :query
                        ORDER BY bm25(documents_fts) LIMIT :limit
                    """),
                    {"query": and_query, "limit": limit}
                )
                for row in result:
                    add_result(row[0], row[1], row[2], weight=2.5)
            except Exception:
                pass

            # Strategy 3: AND search with prefix matching (medium-high weight)
            prefix_and_query = " AND ".join(f'{term}*' for term in search_terms)
            try:
                result = conn.execute(
                    text("""
                        SELECT doc_id, snippet(documents_fts, 2, '>>>', '<<<', '...', 64), bm25(documents_fts)
                        FROM documents_fts WHERE documents_fts MATCH :query
                        ORDER BY bm25(documents_fts) LIMIT :limit
                    """),
                    {"query": prefix_and_query, "limit": limit}
                )
                for row in result:
                    add_result(row[0], row[1], row[2], weight=2.0)
            except Exception:
                pass

            # Strategy 4: OR search with prefix (medium weight) - catches partial matches
            prefix_or_query = " OR ".join(f'{term}*' for term in search_terms)
            try:
                result = conn.execute(
                    text("""
                        SELECT doc_id, snippet(documents_fts, 2, '>>>', '<<<', '...', 64), bm25(documents_fts)
                        FROM documents_fts WHERE documents_fts MATCH :query
                        ORDER BY bm25(documents_fts) LIMIT :limit
                    """),
                    {"query": prefix_or_query, "limit": limit}
                )
                for row in result:
                    add_result(row[0], row[1], row[2], weight=1.0)
            except Exception:
                pass

            # Strategy 5: Search in filename (fallback, low weight)
            for term in search_terms:
                try:
                    result = conn.execute(
                        text("""
                            SELECT doc_id, snippet(documents_fts, 1, '>>>', '<<<', '...', 64), bm25(documents_fts)
                            FROM documents_fts WHERE file_name MATCH :query
                            ORDER BY bm25(documents_fts) LIMIT :limit
                        """),
                        {"query": f'{term}*', "limit": limit}
                    )
                    for row in result:
                        add_result(row[0], row[1], row[2], weight=0.5)
                except Exception:
                    pass

        # Sort by score (descending) and return top results
        sorted_results = sorted(all_results.values(), key=lambda x: -x["score"])
        return sorted_results[:limit]

    def search_fuzzy(
        self,
        query: str,
        limit: int = 20,
        connector_name: str | None = None,
        file_type: str | None = None,
        min_similarity: float = 0.3,
    ) -> list[dict[str, Any]]:
        """Fuzzy search using trigram matching for typo tolerance.

        This is more expensive than FTS5 but handles:
        - Typos (Rechnung vs Rechnug)
        - Partial matches
        - Character transpositions

        Args:
            query: Search query (can contain typos).
            limit: Maximum results to return.
            connector_name: Optional filter by connector.
            file_type: Optional filter by file type.
            min_similarity: Minimum trigram similarity (0.0-1.0).

        Returns:
            List of matching documents with similarity scores.
        """
        # Generate trigrams from query
        query_trigrams = generate_trigrams(query)

        if not query_trigrams:
            return []

        # Find documents with matching trigrams
        with self.engine.connect() as conn:
            # Build query to find docs with matching trigrams
            placeholders = ", ".join([f":t{i}" for i in range(len(query_trigrams))])
            params = {f"t{i}": t for i, t in enumerate(query_trigrams)}

            result = conn.execute(
                text(f"""
                    SELECT doc_id, COUNT(*) as match_count, GROUP_CONCAT(DISTINCT trigram) as matched
                    FROM document_trigrams
                    WHERE trigram IN ({placeholders})
                    GROUP BY doc_id
                    ORDER BY match_count DESC
                    LIMIT :limit_mult
                """),
                {**params, "limit_mult": limit * 5}  # Get more candidates for filtering
            )

            candidates = []
            for row in result:
                doc_id = row[0]
                match_count = row[1]
                matched_trigrams = set(row[2].split(",")) if row[2] else set()

                # Calculate similarity
                similarity = len(matched_trigrams) / len(query_trigrams) if query_trigrams else 0

                if similarity >= min_similarity:
                    candidates.append({
                        "doc_id": doc_id,
                        "similarity": similarity,
                        "match_count": match_count,
                    })

        # Fetch document details and filter
        results = []
        for candidate in candidates:
            doc = self.get_document(candidate["doc_id"])
            if not doc or doc.status != "indexed":
                continue
            if connector_name and doc.connector_name != connector_name:
                continue
            if file_type and doc.file_type != file_type:
                continue

            # Get a snippet from content
            snippet = ""
            if doc.content_text:
                # Try to find query terms in content for snippet
                content_lower = doc.content_text.lower()
                query_lower = query.lower()

                # Find best position for snippet
                pos = content_lower.find(query_lower[:5]) if len(query_lower) >= 5 else -1
                if pos == -1:
                    # Try first word
                    first_word = query.split()[0].lower() if query.split() else ""
                    pos = content_lower.find(first_word) if first_word else -1

                if pos >= 0:
                    start = max(0, pos - 50)
                    end = min(len(doc.content_text), pos + 150)
                    snippet = "..." + doc.content_text[start:end] + "..."
                else:
                    snippet = doc.content_text[:200] + "..."

            results.append({
                "doc_id": doc.id,
                "file_name": doc.file_name,
                "file_path": doc.file_path,
                "file_type": doc.file_type,
                "snippet": snippet,
                "score": candidate["similarity"],
                "similarity": f"{candidate['similarity']*100:.0f}%",
                "content_length": doc.content_length,
                "page_count": doc.page_count,
            })

        # Sort by similarity and return top results
        results.sort(key=lambda x: -x["score"])
        return results[:limit]

    def get_document_content(
        self,
        doc_id: str,
        start_char: int | None = None,
        end_char: int | None = None,
    ) -> dict[str, Any] | None:
        """Get document content, optionally a specific range.

        For large documents, you can request a specific character range.
        """
        doc = self.get_document(doc_id)
        if not doc:
            return None

        content = doc.content_text or ""
        total_length = len(content)

        # Apply range if specified
        if start_char is not None or end_char is not None:
            start = start_char or 0
            end = end_char or total_length
            content = content[start:end]

        return {
            "doc_id": doc_id,
            "file_name": doc.file_name,
            "file_path": doc.file_path,
            "file_type": doc.file_type,
            "content": content,
            "content_length": total_length,
            "range_start": start_char,
            "range_end": end_char,
            "page_count": doc.page_count,
        }

    def get_all_documents(
        self,
        connector_name: str | None = None,
        status: str | None = None,
        file_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Document]:
        """Get all documents with optional filters."""
        with self._get_session() as session:
            query = select(Document)

            if connector_name:
                query = query.where(Document.connector_name == connector_name)
            if status:
                query = query.where(Document.status == status)
            if file_type:
                query = query.where(Document.file_type == file_type)

            query = query.order_by(Document.modified_at.desc())

            if offset:
                query = query.offset(offset)
            if limit:
                query = query.limit(limit)

            return list(session.scalars(query).all())

    def list_documents(
        self,
        connector_name: str | None = None,
        file_type: str | None = None,
        search_filename: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List documents with metadata (no content)."""
        with self._get_session() as session:
            query = select(Document).where(Document.status == "indexed")

            if connector_name:
                query = query.where(Document.connector_name == connector_name)
            if file_type:
                query = query.where(Document.file_type == file_type)
            if search_filename:
                query = query.where(Document.file_name.ilike(f"%{search_filename}%"))

            query = query.order_by(Document.modified_at.desc())
            query = query.offset(offset).limit(limit)

            docs = session.scalars(query).all()

            return [
                {
                    "doc_id": doc.id,
                    "file_name": doc.file_name,
                    "file_path": doc.file_path,
                    "file_type": doc.file_type,
                    "size_bytes": doc.size_bytes,
                    "content_length": doc.content_length,
                    "page_count": doc.page_count,
                    "modified_at": doc.modified_at.isoformat() if doc.modified_at else None,
                }
                for doc in docs
            ]

    def recent_documents(self, limit: int = 20) -> list[dict[str, Any]]:
        """List the most recently indexed documents (newest first)."""
        with self._get_session() as session:
            query = (
                select(Document)
                .where(Document.status == "indexed")
                .order_by(Document.indexed_at.desc())
                .limit(limit)
            )
            docs = session.scalars(query).all()
            return [
                {
                    "doc_id": doc.id,
                    "file_name": doc.file_name,
                    "file_path": doc.file_path,
                    "file_type": doc.file_type,
                    "size_bytes": doc.size_bytes,
                    "connector_name": doc.connector_name,
                    "indexed_at": doc.indexed_at.isoformat() if doc.indexed_at else None,
                }
                for doc in docs
            ]

    def count_documents(
        self,
        connector_name: str | None = None,
        status: str | None = None,
    ) -> int:
        """Count documents with optional filters."""
        with self._get_session() as session:
            query = select(func.count(Document.id))

            if connector_name:
                query = query.where(Document.connector_name == connector_name)
            if status:
                query = query.where(Document.status == status)

            return session.scalar(query) or 0

    def get_statistics(self) -> dict[str, Any]:
        """Get database statistics."""
        with self._get_session() as session:
            total = session.scalar(select(func.count(Document.id))) or 0
            indexed = session.scalar(
                select(func.count(Document.id)).where(Document.status == "indexed")
            ) or 0
            errors = session.scalar(
                select(func.count(Document.id)).where(Document.status == "error")
            ) or 0

            total_size = session.scalar(
                select(func.sum(Document.size_bytes)).where(Document.status == "indexed")
            ) or 0

            total_content = session.scalar(
                select(func.sum(Document.content_length)).where(Document.status == "indexed")
            ) or 0

            # File types breakdown
            file_types = session.execute(
                select(Document.file_type, func.count(Document.id))
                .where(Document.status == "indexed")
                .group_by(Document.file_type)
            ).all()

            return {
                "total_documents": total,
                "indexed_documents": indexed,
                "error_documents": errors,
                "total_size_bytes": total_size,
                "total_content_chars": total_content,
                "file_types": {ft: count for ft, count in file_types},
            }

    # Sync Status Methods

    def get_sync_status(self, connector_name: str) -> SyncStatus | None:
        """Get sync status for a connector."""
        with self._get_session() as session:
            return session.scalars(
                select(SyncStatus).where(SyncStatus.connector_name == connector_name)
            ).first()

    def update_sync_status(
        self,
        connector_name: str,
        last_sync_at: datetime | None = None,
        last_full_sync_at: datetime | None = None,
        documents_total: int | None = None,
        documents_indexed: int | None = None,
        documents_error: int | None = None,
        is_syncing: bool | None = None,
        error_message: str | None = None,
    ) -> SyncStatus:
        """Update sync status for a connector."""
        with self._get_session() as session:
            status = session.scalars(
                select(SyncStatus).where(SyncStatus.connector_name == connector_name)
            ).first()

            if status is None:
                status = SyncStatus(connector_name=connector_name)
                session.add(status)

            if last_sync_at is not None:
                status.last_sync_at = last_sync_at
            if last_full_sync_at is not None:
                status.last_full_sync_at = last_full_sync_at
            if documents_total is not None:
                status.documents_total = documents_total
            if documents_indexed is not None:
                status.documents_indexed = documents_indexed
            if documents_error is not None:
                status.documents_error = documents_error
            if is_syncing is not None:
                status.is_syncing = is_syncing
            if error_message is not None:
                status.error_message = error_message

            session.commit()
            session.refresh(status)
            return status

    def get_all_sync_statuses(self) -> list[SyncStatus]:
        """Get sync status for all connectors."""
        with self._get_session() as session:
            return list(session.scalars(select(SyncStatus)).all())

    def rebuild_trigram_index(self, progress_callback=None) -> int:
        """Rebuild trigram index for all documents.

        Call this after upgrading to add trigram support to existing documents.

        Args:
            progress_callback: Optional callback(current, total) for progress updates.

        Returns:
            Number of documents processed.
        """
        # Clear existing trigrams
        with self.engine.connect() as conn:
            conn.execute(text("DELETE FROM document_trigrams"))
            conn.commit()

        # Get all indexed documents
        docs = self.get_all_documents(status="indexed")
        total = len(docs)
        count = 0

        for i, doc in enumerate(docs):
            if doc.content_text:
                self._update_trigram_index(
                    doc_id=doc.id,
                    file_name=doc.file_name,
                    content=doc.content_text,
                    is_new=True
                )
                count += 1

            # Progress callback every 10 docs
            if progress_callback and (i + 1) % 10 == 0:
                progress_callback(i + 1, total)

        # Final progress update
        if progress_callback:
            progress_callback(total, total)

        return count
