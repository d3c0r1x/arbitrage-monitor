"""
Logging configuration for the project.

Never logs secrets, API keys, or private keys.
API key URLs are masked.
Only secret presence flags are logged.
"""

import logging
import re
import sys

_API_KEY_URL_RE = re.compile(
    r"("
    r"/v2/"
    r"|/v3/"
    r"|lb\.drpc\.live/[A-Za-z0-9\-]+/"
    r")[^\s'\"\\]+",
    re.IGNORECASE,
)


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

    # Silence noisy HTTP libraries: httpx/httpcore log full request URLs
    # at INFO/DEBUG which leaks Alchemy API keys into log files (D12/D18).
    for noisy in ("httpcore", "httpx", "web3", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def mask_url(url: str) -> str:
    """Mask API keys in URLs for safe logging.

    Examples:
        https://eth-mainnet.g.alchemy.com/v2/abcd1234
        -> https://eth-mainnet.g.alchemy.com/v2/***MASKED***
        https://mainnet.infura.io/v3/abcd
        -> https://mainnet.infura.io/v3/***MASKED***
        https://lb.drpc.live/ethereum/abcd
        -> https://lb.drpc.live/ethereum/***MASKED***
    """
    if not url:
        return url
    if "/v2/" in url:
        parts = url.split("/v2/", 1)
        if len(parts) == 2 and parts[1]:
            return parts[0] + "/v2/***MASKED***"
    if "/v3/" in url:
        parts = url.split("/v3/", 1)
        if len(parts) == 2 and parts[1]:
            return parts[0] + "/v3/***MASKED***"
    lower = url.lower()
    marker = "lb.drpc.live/"
    idx = lower.find(marker)
    if idx >= 0:
        # https://lb.drpc.live/{network}/{key}
        rest = url[idx + len(marker) :]
        slash = rest.find("/")
        if slash >= 0 and rest[slash + 1 :]:
            return url[: idx + len(marker)] + rest[: slash + 1] + "***MASKED***"
    return url


def mask_secrets(text: str) -> str:
    """Mask API keys embedded anywhere in arbitrary text (e.g. exception
    messages containing full request URLs like ``.../v2/<key>``).

    Unlike ``mask_url``, safe to apply to whole log messages.
    """
    if not text:
        return text
    return _API_KEY_URL_RE.sub(r"\1***MASKED***", text)
