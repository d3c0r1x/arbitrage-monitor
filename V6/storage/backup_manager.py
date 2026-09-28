"""
Backup manager for SQLite database.

Creates backups before atomic replace and enforces retention policy.
"""

import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class BackupManager:
    """Manages database backups with retention policy."""

    def __init__(
        self,
        active_db_path: str,
        backup_dir: str,
        max_backups: int = 5,
    ):
        self._active_db_path = Path(active_db_path)
        self._backup_dir = Path(backup_dir)
        self._max_backups = max_backups

        self._backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self) -> Path | None:
        """Create a timestamped backup of the active DB."""
        if not self._active_db_path.exists():
            logger.warning("active_db_not_found_for_backup")
            return None

        timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        backup_path = self._backup_dir / f"active_{timestamp}.sqlite3"

        shutil.copy2(str(self._active_db_path), str(backup_path))

        logger.info("backup_created: %s", backup_path)

        self._cleanup_old_backups()

        return backup_path

    def _cleanup_old_backups(self) -> None:
        """Remove backups exceeding the retention limit."""
        backups = sorted(
            self._backup_dir.glob("active_*.sqlite3"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

        for old_backup in backups[self._max_backups:]:
            try:
                old_backup.unlink(missing_ok=True)
                logger.info("old_backup_removed: %s", old_backup)
            except OSError as exc:
                logger.warning(
                    "failed_to_remove_backup %s: %s",
                    old_backup,
                    exc,
                )
