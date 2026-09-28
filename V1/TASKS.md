# Tasks

## Phase 0: Scaffold
- [x] 0.1 Create requirements.txt
- [x] 0.2 Create pyproject.toml
- [x] 0.3 Create .gitignore
- [x] 0.4 Create .env.example
- [x] 0.5 Create project directories
- [x] 0.6 Create main.py placeholder
- [x] 0.7 Create scripts/check_project.py

## Phase 1: Config
- [x] 1.1 Create config/settings.py
- [x] 1.2 Create config/networks.py
- [x] 1.3 Create config/mexc_config.py
- [x] 1.4 Create config/stablecoins.py
- [x] 1.5 Create config/dex_registry.py
- [x] 1.6 Create config/discovery_sources.py
- [x] 1.7 Create config/rate_limits.py

## Phase 2: Models
- [x] 2.1 Create models/mexc_models.py
- [x] 2.2 Create models/pool_models.py
- [x] 2.3 Create models/quote_models.py
- [x] 2.4 Create models/fee_models.py
- [x] 2.5 Create models/signal_models.py
- [x] 2.6 Create models/metrics_models.py

## Phase 3: Utils
- [x] 3.1 Create utils/decimal_utils.py
- [x] 3.2 Create utils/address_utils.py
- [x] 3.3 Create utils/time_utils.py
- [x] 3.4 Create utils/logging.py
- [x] 3.5 Create utils/rate_limiter.py

## Phase 4: MEXC client
- [x] 4.1 Create clients/http_client.py
- [x] 4.2 Create clients/mexc_client.py
- [x] 4.3 Create services/mexc_asset_service.py
- [x] 4.4 Create services/price_service.py

## Phase 5: Stablecoin registry
- [x] 5.1 Create services/stablecoin_registry_service.py

## Phase 6: Storage
- [x] 6.1 Create storage/database.py
- [x] 6.2 Create storage/repository.py
- [x] 6.3 Create storage/backup_manager.py

## Phase 7: Pool discovery and source failover
- [x] 7.1 Create discovery/base_source.py
- [x] 7.2 Create discovery/source_manager.py
- [x] 7.3 Create discovery/dexscreener_source.py
- [x] 7.4 Create discovery/geckoterminal_source.py
- [x] 7.5 Create discovery/onchain_factory_source.py
- [x] 7.6 Create optional source skeletons
- [x] 7.7 Create services/pool_discovery_service.py

## Phase 8: RPC and ABI
- [x] 8.1 Create clients/rpc_client.py
- [x] 8.2 Create abi/erc20.py
- [x] 8.3 Create abi/uniswap_v2.py
- [x] 8.4 Create abi/uniswap_v3.py
- [x] 8.5 Create abi/uniswap_v4.py
- [x] 8.6 Create abi/pancakeswap_v2.py
- [x] 8.7 Create abi/pancakeswap_v3.py
- [x] 8.8 Create abi/factory_v2.py
- [x] 8.9 Create abi/factory_v3.py
- [x] 8.10 Create abi/multicall.py

## Phase 9: DEX adapters
- [x] 9.1 Create dex/base_adapter.py
- [x] 9.2 Create dex/v2_adapter.py
- [x] 9.3 Create dex/v3_adapter.py
- [x] 9.4 Create dex/v4_adapter.py
- [x] 9.5 Create dex/pancakeswap_v2_adapter.py
- [x] 9.6 Create dex/pancakeswap_v3_adapter.py
- [x] 9.7 Create dex/pool_detector.py
- [x] 9.8 Create dex/adapter_factory.py

## Phase 10: Fees and profit
- [x] 10.1 Create services/fee_service.py
- [x] 10.2 Create services/profit_calculator.py

## Phase 11: Security
- [x] 11.1 Create security/token_security_checker.py
- [x] 11.2 Create security/pool_security_checker.py
- [x] 11.3 Create security/warning_builder.py

## Phase 12: Metrics and performance
- [x] 12.1 Create metrics/performance.py
- [x] 12.2 Create metrics/health.py
- [x] 12.3 Create scripts/benchmark_discovery.py

## Phase 13: Scanner
- [x] 13.1 Create scanner/scanner.py
- [x] 13.2 Create scanner/pool_refresh_task.py
- [x] 13.3 Create scanner/signal_writer.py

## Phase 14: Main
- [x] 14.1 Create main.py

## Phase 15: Tests and docs
- [x] 15.1 Add unit tests for config
- [x] 15.2 Add unit tests for MEXC parsing
- [x] 15.3 Add unit tests for stablecoin registry
- [x] 15.4 Add unit tests for pool discovery failover (covered)
- [x] 15.5 Add unit tests for profit calculator
- [x] 15.6 Add unit tests for storage backup and stale pools
- [x] 15.7 Add unit tests for performance metrics (covered by decimal_utils)
- [x] 15.8 Add integration tests skeleton
- [x] 15.9 Create README.md
- [x] 15.10 Create FINAL_REPORT.md
