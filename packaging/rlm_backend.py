"""Standalone entry point for the RLM backend API server.

PyInstaller bundles this into a single executable (see ``rlm-backend.spec``)
that the desktop app launches as a sidecar. With no arguments it serves the
REST API on ``127.0.0.1:8000``. ``RLM_HOME`` (set by the desktop) decides where
``config.yaml`` and the SQLite database live; ``RLM_HOST`` / ``RLM_PORT`` can
override the bind address.
"""

import multiprocessing
import os
import sys

from src.main import main


def run() -> None:
    """Run the API server, defaulting to 127.0.0.1:8000 when no args are given."""
    if len(sys.argv) == 1:
        host = os.environ.get("RLM_HOST", "127.0.0.1")
        port = os.environ.get("RLM_PORT", "8000")
        sys.argv += ["api", "--host", host, "--port", port]
    main()


if __name__ == "__main__":
    # Required so a frozen exe does not re-spawn the whole app in child processes.
    multiprocessing.freeze_support()
    run()
