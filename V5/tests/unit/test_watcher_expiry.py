"""Unit tests for dynamic watcher expiry (plan v3 D8)."""

from scanner.signal_watcher import watcher_expiry_sec


class TestWatcherExpiry:
    def test_no_min_confirm_uses_base(self):
        assert watcher_expiry_sec(None, "BSC") >= 1
        # Falls back to the configured static expiry.
        from config.settings import settings
        assert watcher_expiry_sec(None, "BSC") == settings.WATCHER_EXPIRY_SEC
        assert watcher_expiry_sec(0, "BSC") == settings.WATCHER_EXPIRY_SEC

    def test_aidoge_like_arbitrum(self):
        """min_confirm=5000 on Arbitrum (0.25s block) → ~23 min, not 5."""
        expiry = watcher_expiry_sec(5000, "ARBITRUM")
        assert expiry == 5000 * 0.25 + 120  # 1370s
        assert expiry > 300

    def test_small_min_confirm_keeps_base(self):
        """A short deposit ETA never shrinks the expiry below the base."""
        from config.settings import settings
        expiry = watcher_expiry_sec(10, "BSC")  # 10*3+120 = 150 < base 300
        assert expiry == settings.WATCHER_EXPIRY_SEC

    def test_unknown_network_uses_default_block_time(self):
        expiry = watcher_expiry_sec(1000, "SOLANA")
        assert expiry == 1000 * 3.0 + 120
