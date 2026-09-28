"""Unit tests for services.pool_blacklist (plan v3, Phase 1.4)."""

from services.pool_blacklist import PoolBlacklist


class TestPoolBlacklist:
    def test_not_blocked_initially(self):
        bl = PoolBlacklist()
        assert not bl.is_blocked("BSC:0xpool")

    def test_blocks_after_max_fails(self):
        bl = PoolBlacklist(max_fails=3)
        key = "BSC:0xpool"
        bl.record_fail(key)
        bl.record_fail(key)
        assert not bl.is_blocked(key)
        bl.record_fail(key)
        assert bl.is_blocked(key)
        assert bl.blocked_count == 1

    def test_success_resets_fail_streak(self):
        bl = PoolBlacklist(max_fails=3)
        key = "BSC:0xpool"
        bl.record_fail(key)
        bl.record_fail(key)
        bl.record_success(key)
        bl.record_fail(key)
        bl.record_fail(key)
        assert not bl.is_blocked(key)

    def test_block_expires(self, monkeypatch):
        bl = PoolBlacklist(max_fails=1, base_ttl=100)
        key = "ARB:0xdead"
        bl.record_fail(key)
        assert bl.is_blocked(key)

        import services.pool_blacklist as mod
        real_time = mod.time.time()
        monkeypatch.setattr(mod.time, "time", lambda: real_time + 101)
        assert not bl.is_blocked(key)
        # After expiry the fail counter is reset too.
        assert bl.blocked_count == 0
