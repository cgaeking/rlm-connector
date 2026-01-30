"""Main entry point for RLM Knowledge Base."""

import argparse
import asyncio
import logging
import sys
import threading

import uvicorn

from .config import get_config, init_config


def setup_logging(debug: bool = False):
    """Configure logging."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def run_api(host: str, port: int):
    """Run the FastAPI server."""
    from .api.app import create_app

    app = create_app()
    uvicorn.run(app, host=host, port=port)


def run_ui(port: int):
    """Run the Gradio UI."""
    from .api.app import app_state, create_connectors
    from .config import get_config
    from .database import DocumentRepository
    from .indexer import Indexer, SyncManager
    from .rlm_engine import KnowledgeBaseEngine
    from .ui.chat import launch_ui

    config = get_config()

    # Initialize components
    if config.database.type == "postgresql" and config.database.url:
        db = DocumentRepository(config.database.url)
    else:
        db = DocumentRepository(config.database.path)

    connectors = create_connectors(config)
    indexer = Indexer(db, connectors, config)
    sync_manager = SyncManager(db, connectors, config, indexer)
    engine = KnowledgeBaseEngine(db, connectors, config)

    launch_ui(engine, db, sync_manager, port=port)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="RLM Knowledge Base - Make company documents accessible to AI agents"
    )

    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default="config.yaml",
        help="Path to configuration file",
    )

    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug logging",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # API server command
    api_parser = subparsers.add_parser("api", help="Run the API server")
    api_parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    api_parser.add_argument("--port", type=int, default=8000, help="Port to bind to")

    # UI command
    ui_parser = subparsers.add_parser("ui", help="Run the Chat UI")
    ui_parser.add_argument("--port", type=int, default=7860, help="Port for UI")

    # Both command
    both_parser = subparsers.add_parser("both", help="Run both API and UI")
    both_parser.add_argument("--api-port", type=int, default=8000, help="API port")
    both_parser.add_argument("--ui-port", type=int, default=7860, help="UI port")

    # Index command
    index_parser = subparsers.add_parser("index", help="Run indexing")
    index_parser.add_argument("--full", action="store_true", help="Full re-index")
    index_parser.add_argument("--connector", help="Index specific connector only")

    # Rebuild trigram index command
    trigram_parser = subparsers.add_parser("rebuild-trigrams", help="Rebuild trigram index for fuzzy search")

    args = parser.parse_args()

    # Setup
    setup_logging(args.debug)
    init_config(args.config)
    config = get_config()

    if args.command == "api":
        run_api(args.host, args.port)

    elif args.command == "ui":
        run_ui(args.port)

    elif args.command == "both":
        # Run API in background thread
        api_thread = threading.Thread(
            target=run_api,
            args=("0.0.0.0", args.api_port),
            daemon=True,
        )
        api_thread.start()

        # Run UI in main thread
        run_ui(args.ui_port)

    elif args.command == "index":
        asyncio.run(run_index(config, args.full, args.connector))

    elif args.command == "rebuild-trigrams":
        run_rebuild_trigrams(config)

    else:
        # Default: run both
        print("Starting RLM Knowledge Base...")
        print(f"API: http://0.0.0.0:{config.api.port}")
        print(f"UI:  http://0.0.0.0:{config.ui.port}")

        api_thread = threading.Thread(
            target=run_api,
            args=(config.api.host, config.api.port),
            daemon=True,
        )
        api_thread.start()

        if config.ui.enabled:
            run_ui(config.ui.port)
        else:
            # Keep API running
            try:
                api_thread.join()
            except KeyboardInterrupt:
                print("\nShutting down...")


def run_rebuild_trigrams(config):
    """Rebuild trigram index for fuzzy search."""
    from .database import DocumentRepository

    # Initialize DB
    if config.database.type == "postgresql" and config.database.url:
        db = DocumentRepository(config.database.url)
    else:
        db = DocumentRepository(config.database.path)

    stats = db.get_statistics()
    total_docs = stats["indexed_documents"]
    print(f"Rebuilding trigram index for {total_docs} documents...")

    def progress(current, total):
        pct = (current / total * 100) if total > 0 else 100
        bar = "=" * int(pct / 2) + "-" * (50 - int(pct / 2))
        print(f"\r[{bar}] {pct:.0f}% ({current}/{total})", end="", flush=True)

    count = db.rebuild_trigram_index(progress_callback=progress)
    print(f"\n\nDone! {count} documents indexed for fuzzy search.")


async def run_index(config, full: bool, connector_name: str | None):
    """Run indexing operation."""
    from .api.app import create_connectors
    from .database import DocumentRepository
    from .indexer import Indexer, SyncManager

    # Initialize components
    if config.database.type == "postgresql" and config.database.url:
        db = DocumentRepository(config.database.url)
    else:
        db = DocumentRepository(config.database.path)

    connectors = create_connectors(config)

    if not connectors:
        print("No connectors configured!")
        return

    indexer = Indexer(db, connectors, config)
    sync_manager = SyncManager(db, connectors, config, indexer)

    print(f"Starting {'full' if full else 'incremental'} index...")

    if full:
        results = await sync_manager.full_sync(connector_name)
    else:
        results = await sync_manager.incremental_sync(connector_name)

    print(f"\nIndexing completed in {results['duration_seconds']:.2f}s")
    for name, counts in results["results"].items():
        if isinstance(counts, dict) and "error" not in counts:
            print(f"  {name}: {counts['indexed']} indexed, {counts['skipped']} skipped, {counts.get('errors', 0)} errors")
        else:
            print(f"  {name}: Error - {counts}")


def run_rebuild_trigrams(config):
    """Rebuild trigram index for fuzzy search."""
    from .database import DocumentRepository

    # Initialize database
    if config.database.type == "postgresql" and config.database.url:
        db = DocumentRepository(config.database.url)
    else:
        db = DocumentRepository(config.database.path)

    print("Rebuilding trigram index for fuzzy search...")
    count = db.rebuild_trigram_index()
    print(f"Done! Processed {count} documents.")


if __name__ == "__main__":
    main()
