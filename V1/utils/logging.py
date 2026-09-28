"""
Logging configuration for the project.

Never logs secrets, API keys, or private keys.
API key URLs are masked.
Only secret presence flags are logged.
"""

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging for the project.

    Configures the ROOT logger so that ALL modules using
    ``logging.getLogger(__name__)`` produce visible output.
    Without this, logs from sub-modules (scanner, services, etc.)
    are silently discarded.

    Enables line-buffering so logs are visible in real-time even when
    stdout is redirected to a file or pipe.

    Args:
        level: Logging level string (DEBUG, INFO, WARNING, ERROR).
    """
    # Enable line-buffering to prevent log loss when stdout is redirected.
    # Python defaults to full buffering (8KB blocks) for non-TTY output,
    # which can swallow logs when the process runs indefinitely.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Configure ROOT logger so every module's logs are visible.
    # Previously only "mexc_dex_arb" was configured, which meant logs
    # from scanner.scanner, pool_refresh_task, etc. were silently discarded.
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Avoid duplicate handlers if setup_logging is called more than once.
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)


def mask_url(url: str) -> str:
    """Mask API keys in URLs for safe logging.

    Example:
        https://eth-mainnet.g.alchemy.com/v2/abcd1234
        -> https://eth-mainnet.g.alchemy.com/v2/***MASKED***
    """
    if not url:
        return url
    # Mask Alchemy-style keys.
    if "/v2/" in url:
        parts = url.split("/v2/")
        if len(parts) == 2 and parts[1]:
            return parts[0] + "/v2/***MASKED***"
    return url
