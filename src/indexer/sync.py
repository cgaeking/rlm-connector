"""Sync manager for scheduled and manual index synchronization."""

import asyncio
import logging
from datetime import datetime

from ..config import AppConfig
from ..connectors.base import BaseConnector
from ..database.repository import DocumentRepository
from .indexer import Indexer

logger = logging.getLogger(__name__)


class SyncManager:
    """Manages index synchronization schedules and operations."""

    def __init__(
        self,
        db: DocumentRepository,
        connectors: dict[str, BaseConnector],
        config: AppConfig,
        indexer: Indexer | None = None,
    ):
        """Initialize the sync manager.

        Args:
            db: Document repository.
            connectors: Dictionary of connectors.
            config: Application configuration.
            indexer: Optional indexer instance (created if not provided).
        """
        self.db = db
        self.connectors = connectors
        self.config = config
        self.indexer = indexer or Indexer(db, connectors, config)
        self._is_running = False
        self._scheduler = None

    @property
    def is_running(self) -> bool:
        """Check if a sync is currently in progress."""
        return self._is_running

    async def full_sync(self, connector_name: str | None = None) -> dict:
        """Perform a full synchronization.

        Args:
            connector_name: Optional specific connector to sync.

        Returns:
            Sync results dictionary.
        """
        if self._is_running:
            raise RuntimeError("Sync already in progress")

        self._is_running = True
        start_time = datetime.now()

        try:
            if connector_name:
                # Sync specific connector
                results = {
                    connector_name: await self.indexer.index_connector(
                        connector_name, force=True
                    )
                }
            else:
                # Sync all connectors
                results = await self.indexer.index_all(force=True)

            # Cleanup deleted files
            for name in (results.keys() if not connector_name else [connector_name]):
                try:
                    removed = self.indexer.cleanup_deleted_files(name)
                    results[name]["removed"] = removed
                except Exception as e:
                    logger.error(f"Error cleaning up {name}: {e}")

            # Update full sync timestamp
            for name in results:
                if "error" not in results[name]:
                    self.db.update_sync_status(name, last_full_sync_at=datetime.now())

            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"Full sync completed in {duration:.2f}s")

            return {
                "type": "full",
                "duration_seconds": duration,
                "results": results,
            }

        finally:
            self._is_running = False

    async def incremental_sync(self, connector_name: str | None = None) -> dict:
        """Perform an incremental synchronization.

        Only indexes files modified since the last sync.

        Args:
            connector_name: Optional specific connector to sync.

        Returns:
            Sync results dictionary.
        """
        if self._is_running:
            raise RuntimeError("Sync already in progress")

        self._is_running = True
        start_time = datetime.now()

        try:
            if connector_name:
                results = {
                    connector_name: await self.indexer.index_connector(
                        connector_name, force=False
                    )
                }
            else:
                results = await self.indexer.index_all(force=False)

            # Cleanup deleted files
            for name in results:
                try:
                    removed = self.indexer.cleanup_deleted_files(name)
                    results[name]["removed"] = removed
                except Exception as e:
                    logger.error(f"Error cleaning up {name}: {e}")

            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"Incremental sync completed in {duration:.2f}s")

            return {
                "type": "incremental",
                "duration_seconds": duration,
                "results": results,
            }

        finally:
            self._is_running = False

    def get_status(self) -> dict:
        """Get current sync status for all connectors.

        Returns:
            Status dictionary with connector info and counts.
        """
        statuses = self.db.get_all_sync_statuses()

        connector_statuses = {}
        for status in statuses:
            connector_statuses[status.connector_name] = status.to_dict()

        # Add connectors without status
        for name in self.connectors:
            if name not in connector_statuses:
                connector_statuses[name] = {
                    "connector_name": name,
                    "last_sync_at": None,
                    "last_full_sync_at": None,
                    "documents_total": 0,
                    "documents_indexed": 0,
                    "documents_error": 0,
                    "is_syncing": False,
                    "error_message": None,
                }

        return {
            "is_running": self._is_running,
            "connectors": connector_statuses,
            "total_documents": self.db.count_documents(),
            "indexed_documents": self.db.count_documents(status="indexed"),
            "error_documents": self.db.count_documents(status="error"),
        }

    def setup_scheduler(self) -> None:
        """Setup APScheduler for automatic sync.

        Uses cron schedule from config.
        """
        schedule = self.config.indexer.sync_schedule
        if not schedule:
            logger.info("No sync schedule configured, skipping scheduler setup")
            return

        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger

            self._scheduler = AsyncIOScheduler()

            # Parse cron expression (minute hour day month day_of_week)
            parts = schedule.split()
            if len(parts) == 5:
                trigger = CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4],
                )

                self._scheduler.add_job(
                    self._scheduled_sync,
                    trigger,
                    id="scheduled_sync",
                    name="Scheduled Index Sync",
                )

                self._scheduler.start()
                logger.info(f"Scheduler started with schedule: {schedule}")
            else:
                logger.error(f"Invalid cron schedule: {schedule}")

        except ImportError:
            logger.warning("APScheduler not installed, scheduled sync disabled")
        except Exception as e:
            logger.error(f"Error setting up scheduler: {e}")

    async def _scheduled_sync(self) -> None:
        """Run scheduled sync job."""
        logger.info("Starting scheduled sync")
        try:
            await self.incremental_sync()
        except Exception as e:
            logger.error(f"Scheduled sync failed: {e}")

    def shutdown_scheduler(self) -> None:
        """Shutdown the scheduler if running."""
        if self._scheduler:
            self._scheduler.shutdown()
            self._scheduler = None
            logger.info("Scheduler shutdown")
