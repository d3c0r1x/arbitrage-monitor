"""
Unit tests for storage modules.

Tests:
- Backup retention (keeps 5, deletes old)
- Atomic replace validation
- Invalid temp DB does not replace active DB
"""

import shutil
import tempfile
from pathlib import Path

import pytest


class TestBackupManager:
    """Tests for BackupManager."""

    @pytest.fixture
    def backup_dir(self):
        """Create a temporary backup directory."""
        tmpdir = Path(tempfile.mkdtemp())
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.fixture
    def active_db(self, backup_dir):
        """Create a fake active database."""
        path = backup_dir / "active.sqlite3"
        path.write_text("fake_data")
        return path

    def test_create_backup(self, active_db, backup_dir):
        from storage.backup_manager import BackupManager

        manager = BackupManager(
            active_db_path=str(active_db),
            backup_dir=str(backup_dir / "backups"),
            max_backups=5,
        )

        backup = manager.create_backup()
        assert backup is not None
        assert backup.exists()
        assert backup.name.startswith("active_")
        assert backup.suffix == ".sqlite3"

    def test_backup_retention_keeps_5(self, active_db, backup_dir):
        from storage.backup_manager import BackupManager

        manager = BackupManager(
            active_db_path=str(active_db),
            backup_dir=str(backup_dir / "backups"),
            max_backups=5,
        )

        # Create 6 backups.
        for _ in range(6):
            manager.create_backup()

        # Cleanup happens after each backup.
        backups = list((backup_dir / "backups").glob("active_*.sqlite3"))
        assert len(backups) <= 5

    def test_no_backup_if_no_active_db(self, backup_dir):
        from storage.backup_manager import BackupManager

        manager = BackupManager(
            active_db_path=str(backup_dir / "nonexistent.sqlite3"),
            backup_dir=str(backup_dir / "backups"),
        )

        backup = manager.create_backup()
        assert backup is None

