"""
JSONL file rotator.

Rotates JSONL files by size, keeping N most recent files.
Designed for signals.jsonl and performance.jsonl append-only logs.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class JsonlRotator:
    """Rotates JSONL files by size, keeping N most recent backups.

    Defaults: max_size_mb=50, keep=5 backups.
    """

    def __init__(self, filepath: str, max_size_mb: int = 50, keep: int = 5):
        self._path = Path(filepath)
        self._max_size_bytes = max_size_mb * 1024 * 1024
        self._keep = keep

    def write_line(self, line: str) -> None:
        """Append a JSON line, rotating the file if it exceeds max_size."""
        if self._path.exists() and self._path.stat().st_size > self._max_size_bytes:
            self._rotate()
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _rotate(self) -> None:
        """Shift .N files up and rename current file to .1."""
        if not self._path.exists():
            return

        stem = self._path.stem
        parent = self._path.parent

        # Shift existing backups: .2 → .3, .1 → .2, ...
        for i in range(self._keep - 1, 0, -1):
            old = parent / f"{stem}.{i}.jsonl"
            newer = parent / f"{stem}.{i + 1}.jsonl"
            if old.exists():
                os.replace(str(old), str(newer))

        # Rename current file → .1
        first = parent / f"{stem}.1.jsonl"
        try:
            os.replace(str(self._path), str(first))
        except OSError as exc:
            logger.warning("jsonl_rotate_failed: %s", exc)
            return

        # Delete files beyond retention limit
        for f in sorted(parent.glob(f"{stem}.*.jsonl")):
            parts = f.name.split(".")
            # Expected format: stem.N.jsonl where N is a digit
            if len(parts) >= 3 and parts[-2].isdigit():
                num = int(parts[-2])
                if num > self._keep:
                    try:
                        f.unlink()
                    except OSError:
                        pass
