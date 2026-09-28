"""Unit tests for dRPC URL resolution and log masking."""

import os
from unittest.mock import patch

from utils.logging import mask_secrets, mask_url


def test_mask_url_drpc_and_infura():
    assert (
        mask_url("https://lb.drpc.live/ethereum/secretKEY123")
        == "https://lb.drpc.live/ethereum/***MASKED***"
    )
    assert (
        mask_url("https://mainnet.infura.io/v3/abcdef")
        == "https://mainnet.infura.io/v3/***MASKED***"
    )
    assert "secretKEY123" not in mask_secrets(
        "fail https://lb.drpc.live/bsc/secretKEY123 oops"
    )


@patch.dict(
    os.environ,
    {
        "DRPC_KEY": "test-drpc-key",
        "ALCHEMY_KEY_1": "",
        "ALCHEMY_KEY_2": "",
        "ALCHEMY_KEY": "",
        "INFURA_KEY": "",
        "ETH_RPC_URL": "",
        "BSC_RPC_URL": "",
        "ARBITRUM_RPC_URL": "",
    },
    clear=False,
)
def test_resolve_rpc_urls_includes_drpc():
    from config.settings import Settings
    from config import networks as networks_mod

    s = Settings()
    assert s.DRPC_KEY == "test-drpc-key"
    with patch.object(networks_mod, "settings", s):
        urls = networks_mod.resolve_rpc_urls("ETHEREUM")
    assert any(u.endswith("/ethereum/test-drpc-key") for u in urls), urls
    assert s.drpc_key_present is True
