"""Local filesystem connector."""

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .base import BaseConnector, FileInfo, FileMetadata


class LocalConnector(BaseConnector):
    """Connector for local filesystem documents."""

    def __init__(
        self,
        name: str,
        root_path: str | Path,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ):
        """Initialize local connector.

        Args:
            name: Unique name for this connector.
            root_path: Root directory path for documents.
            include_patterns: Glob patterns for files to include.
            exclude_patterns: Glob patterns for files to exclude.
        """
        super().__init__(name, include_patterns, exclude_patterns)
        self.root_path = Path(root_path).resolve()

        if not self.root_path.exists():
            raise ValueError(f"Root path does not exist: {self.root_path}")
        if not self.root_path.is_dir():
            raise ValueError(f"Root path is not a directory: {self.root_path}")

    def _resolve_path(self, path: str) -> Path:
        """Resolve a relative path to absolute path within root."""
        if not path:
            return self.root_path

        resolved = (self.root_path / path).resolve()

        # Security check: ensure path is within root
        if not str(resolved).startswith(str(self.root_path)):
            raise ValueError(f"Path {path} is outside root directory")

        return resolved

    def _get_relative_path(self, absolute_path: Path) -> str:
        """Get relative path from root."""
        return str(absolute_path.relative_to(self.root_path))

    def list_files(self, path: str = "") -> list[FileInfo]:
        """List files in a directory."""
        resolved = self._resolve_path(path)

        if not resolved.exists():
            return []

        files = []
        for entry in resolved.iterdir():
            try:
                stat = entry.stat()
                files.append(
                    FileInfo(
                        path=self._get_relative_path(entry),
                        name=entry.name,
                        size_bytes=stat.st_size if entry.is_file() else 0,
                        modified_at=datetime.fromtimestamp(stat.st_mtime),
                        is_directory=entry.is_dir(),
                    )
                )
            except (OSError, PermissionError):
                # Skip files we can't access
                continue

        return files

    def list_files_recursive(self, path: str = "") -> Iterator[FileInfo]:
        """Recursively list all files matching patterns."""
        resolved = self._resolve_path(path)

        if not resolved.exists():
            return

        for root, dirs, files in os.walk(resolved):
            root_path = Path(root)

            # Filter out excluded directories
            dirs[:] = [
                d for d in dirs
                if not any(
                    d.startswith(".") or d in self.exclude_patterns
                    for _ in [1]
                )
                and not any(
                    d == pattern.rstrip("/") for pattern in self.exclude_patterns
                )
            ]

            for file_name in files:
                file_path = root_path / file_name
                relative_path = self._get_relative_path(file_path)

                if not self.should_include(relative_path):
                    continue

                try:
                    stat = file_path.stat()
                    yield FileInfo(
                        path=relative_path,
                        name=file_name,
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime),
                        is_directory=False,
                    )
                except (OSError, PermissionError):
                    # Skip files we can't access
                    continue

    def read_file(self, path: str) -> bytes:
        """Read file contents."""
        resolved = self._resolve_path(path)

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not resolved.is_file():
            raise ValueError(f"Not a file: {path}")

        return resolved.read_bytes()

    def get_metadata(self, path: str) -> FileMetadata:
        """Get extended metadata for a file."""
        resolved = self._resolve_path(path)

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")

        stat = resolved.stat()

        # Calculate content hash
        content_hash = self._calculate_hash(resolved)

        return FileMetadata(
            path=self._get_relative_path(resolved),
            name=resolved.name,
            extension=resolved.suffix,
            size_bytes=stat.st_size,
            created_at=datetime.fromtimestamp(stat.st_ctime),
            modified_at=datetime.fromtimestamp(stat.st_mtime),
            content_hash=content_hash,
        )

    def _calculate_hash(self, file_path: Path, chunk_size: int = 8192) -> str:
        """Calculate SHA256 hash of file contents."""
        sha256 = hashlib.sha256()

        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                sha256.update(chunk)

        return sha256.hexdigest()

    def file_exists(self, path: str) -> bool:
        """Check if a file exists."""
        try:
            resolved = self._resolve_path(path)
            return resolved.exists() and resolved.is_file()
        except ValueError:
            return False

    def status(self) -> dict:
        """Get connector status information."""
        base_status = super().status()

        # Count files
        try:
            file_count = sum(1 for _ in self.list_files_recursive())
        except Exception:
            file_count = -1

        return {
            **base_status,
            "root_path": str(self.root_path),
            "exists": self.root_path.exists(),
            "file_count": file_count,
        }
