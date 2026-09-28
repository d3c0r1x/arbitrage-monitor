"""Unit tests for security.token_address_registry (plan v3 D4)."""

from security.token_address_registry import verify_token


class TestVerifyToken:
    def test_canonical_address_verified(self):
        ok, reason = verify_token(
            "BSC", "CAKE", "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"
        )
        assert ok and reason == "verified"

    def test_case_insensitive(self):
        ok, reason = verify_token(
            "bsc", "cake", "0x0E09FABB73BD3ADE0A17ECC321FD13A19E81CE82"
        )
        assert ok and reason == "verified"

    def test_impostor_rejected(self):
        ok, reason = verify_token("BSC", "CAKE", "0x" + "d" * 40)
        assert not ok and reason.startswith("FAKE:")

    def test_unknown_symbol_passes(self):
        ok, reason = verify_token("BSC", "NEWTOKEN2026", "0x" + "a" * 40)
        assert ok and reason == "not_in_whitelist"

    def test_canonical_eth_bat_not_rejected(self):
        """Plan v3: 0x0d8775...2887ef IS the real BAT — must not be banned."""
        ok, reason = verify_token(
            "ETHEREUM", "BAT", "0x0d8775f648430679a709e98d2b0cb6250d2887ef"
        )
        assert ok and reason == "verified"

    def test_empty_symbol_passes(self):
        ok, _ = verify_token("BSC", "", "0x" + "a" * 40)
        assert ok
