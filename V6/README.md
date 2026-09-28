# MEXC × DEX Arbitrage Monitor

Асинхронный монитор арбитража между **MEXC** (mid + order book) и on-chain DEX (Uniswap / PancakeSwap / Aerodrome и др.), плюс режим **DEX↔DEX** (same-pair cross-DEX и multi-hop chain).

По умолчанию это **только мониторинг**: сигналы, LIVE-радар, метрики и веб-дашборд. Исполнение сделок включается только при наличии `DEX_PRIVATE_KEY` (и даже тогда по умолчанию `EXECUTION_DRY_RUN=1`).

> Дефолты ниже — из `config/settings.py`. Файл `.env.example` может отличаться; при конфликте ориентируйтесь на settings и свой `.env`.

---

## Содержание

1. [Возможности](#возможности)
2. [Архитектура](#архитектура)
3. [Структура репозитория](#структура-репозитория)
4. [Требования и установка](#требования-и-установка)
5. [Запуск](#запуск)
6. [Режимы сканирования](#режимы-сканирования)
7. [Сети и DEX](#сети-и-dex)
8. [Конфигурация (env)](#конфигурация-env)
9. [Пайплайн расчёта PnL](#пайплайн-расчёта-pnl)
10. [Дашборд](#дашборд)
11. [Данные в `data/`](#данные-в-data)
12. [Тесты и утилиты](#тесты-и-утилиты)
13. [Исполнение (execution)](#исполнение-execution)
14. [Документация и планы](#документация-и-планы)
15. [Типичные проблемы](#типичные-проблемы)

---

## Возможности

| Направление | Описание |
|-------------|----------|
| **A · DEX_BUY_MEXC_SELL** | Купить токен на DEX → продать на MEXC |
| **B · MEXC_BUY_DEX_SELL** | Купить на MEXC → вывести → продать на DEX (+ closing hop WBNB/WETH → USDT) |
| **Cross-DEX** | Один pair на двух DEX одной сети |
| **Chain** | Multi-hop маршруты до `CHAIN_MAX_HOPS` |

Дополнительно:

- Hot-set + периодический full-scan
- V2 Multicall3 prefetch резервов → локальный `amountOut` (быстрее router RPC)
- Size ladder / orderbook maximize → окно **SIZE min–max** в LIVE
- Mid-headroom: дорогой orderbook только если mid ≥ `MIN_NET + ORDERBOOK_MID_HEADROOM_PCT`
- Liquid-quotes filter на full-scan (USDT/WBNB/…)
- SignalWatcher: переквот каждые `WATCHER_INTERVAL_SEC`
- FastAPI dashboard: LIVE, DEX↔DEX, OPS, Alt-CEX

---

## Архитектура

```
Discovery (DexScreener / GeckoTerminal / subgraph / onchain_factory)
        │
        ▼
PoolRefreshTask ──► data/pools_cache.json + data/state/active.sqlite3
        │
        ▼
Scanner (hot-set / full-scan)
  ├─ V2ReservesCache (Multicall3) + DEX adapters (v1–v4)
  ├─ FeeService + ProfitCalculator
  ├─ OrderbookService (MEXC depth, size curve)
  └─ SignalWriter → data/signals.jsonl
        │
        ▼
SignalWatcher → data/opportunities_live.json
        │
        ▼
Dashboard (FastAPI) ← читает data/*

Параллельно по режиму:
  mexc     → MEXC↔DEX scanner + (опц.) Alt-CEX probe
  dex_dex  → CrossDexEngine + ChainEngine + universe/hot
  always   → execution loop (inert без DEX_PRIVATE_KEY)
```

---

## Структура репозитория

| Путь | Назначение |
|------|------------|
| `main.py` | Entrypoint бота |
| `run_dashboard.py` | Веб-дашборд (uvicorn) |
| `run_gui.py` | Опциональный tkinter GUI |
| `config/` | Settings, networks, DEX registry, rate limits |
| `scanner/` | Scanner, watcher, chain/cross engines, signal writer |
| `services/` | Prices, fees, profit, orderbook, discovery helpers, PCS API, scan mode |
| `clients/` | HTTP, MEXC, RPC, multicall, web3 |
| `dex/` | AMM adapters V1–V4 + factory |
| `discovery/` | DexScreener, GeckoTerminal, subgraph, on-chain factory |
| `dashboard/` | FastAPI + `web/*.html` |
| `execution/` | Опциональный executor + safety |
| `security/` | Token/pool checkers |
| `storage/` | SQLite schema + backups |
| `models/` | Модели сигналов/fees |
| `metrics/` | Health + performance |
| `data/` | Runtime caches, signals, sqlite |
| `tests/` | unit / integration |
| `tools/` | Audit/bench, PCS sidecar |
| `docs/superpowers/` | Спеки и планы |

---

## Требования и установка

- Python **3.11+** (проверялось на 3.13)
- Windows / Linux
- Ключи: MEXC API + хотя бы один RPC (Alchemy / Infura / dRPC / прямые URL)

```bash
python -m pip install -r requirements.txt
copy .env.example .env
# Заполните MEXC_API_KEY, MEXC_API_SECRET, ALCHEMY_KEY_* / DRPC_KEY / RPC URL
```

Основные зависимости: `httpx`, `web3`, `aiosqlite`, `pydantic`, `fastapi`, `uvicorn`, `python-dotenv`.

---

## Запуск

### Бот (сканер + watcher)

```bash
python main.py
```

Остановка: `Ctrl+C` или завершение процесса `python … main.py`.

Полезные флаги через env:

- `SKIP_INITIAL_REFRESH=1` — не ждать refresh, взять существующий `data/pools_cache.json`
- `SCAN_MODE=mexc` или `dex_dex`

### Дашборд

```bash
python run_dashboard.py --host 127.0.0.1 --port 8000
```

Открыть: **http://127.0.0.1:8000**

### GUI (опционально)

```bash
python run_gui.py
```

---

## Режимы сканирования

Источник: `services/scan_mode.py`, `settings.SCAN_MODE`, runtime-файл `data/scan_mode.json` (переключатель в UI).

| | **mexc** | **dex_dex** |
|---|----------|-------------|
| Цикл | `Scanner.run_cycle()` | `Scanner.run_dex_dex_cycle()` |
| Pool refresh | каждые `POOL_REFRESH_INTERVAL_SEC` | пропускается |
| Фокус | MEXC↔DEX edges | Cross-DEX + chain |
| Сигналы | `data/signals.jsonl` | `data/dex_dex_signals.jsonl` |
| LIVE | `opportunities_live.json` | `dex_dex_opportunities_live.json` |

Валидные значения: только `mexc` | `dex_dex`.

---

## Сети и DEX

**ACTIVE_NETWORKS** (`config/networks.py`):

`BSC`, `ETHEREUM`, `ARBITRUM`, `BASE`, `POLYGON`

(ROBINHOOD есть в реестре, но не в active set.)

Примеры DEX (`config/dex_registry.py`):

- **BSC** — PancakeSwap v1/v2/v3, Thena, Uniswap v3, Sushi
- **ETH** — Uniswap v2/v3/v4, Sushi, PCS v3
- **BASE** — Aerodrome, Uniswap, BaseSwap, PCS
- **ARBITRUM** — Uniswap, Camelot, Ramses, PCS
- **POLYGON** — Uniswap, QuickSwap, Sushi, Dystopia

---

## Конфигурация (env)

Все переменные читаются из `.env` через `config/settings.py`.

### Credentials / RPC

| Переменная | Назначение |
|------------|------------|
| `MEXC_API_KEY` / `MEXC_API_SECRET` | MEXC API |
| `ALCHEMY_KEY` / `ALCHEMY_KEY_1` / `ALCHEMY_KEY_2` | Alchemy |
| `INFURA_KEY`, `DRPC_KEY` | Infura / dRPC |
| `ETH_RPC_URL`, `BSC_RPC_URL`, … | Прямые RPC (опционально) |
| `GRAPH_API`, `PCS_SUBGRAPH_*` | The Graph / PCS subgraph |
| `DEX_PRIVATE_KEY` | Ключ кошелька (пусто = execution OFF) |

### Пороги и комиссии

| Переменная | Default (settings) | Смысл |
|------------|-------------------|--------|
| `BASE_AMOUNT_USD` | `10` | Базовый размер клипа |
| `MIN_NET_PROFIT_PCT` | `1` | Порог hard-сигнала (строго `>`) |
| `MIN_NET_PROFIT_USD` | `0` | Мин. net $ |
| `ETH_MIN_NET_PROFIT_PCT` | `1` | Порог для Ethereum |
| `NEAR_MISS_MIN_PCT` | `0.35` | Near-miss / soft radar |
| `MEXC_TAKER_FEE_BPS` | `10` | Taker fee (0.10%) |
| `SLIPPAGE_BUFFER_BPS` | `30` | Mid-буфер до orderbook (0.30%) |
| `ORDERBOOK_MID_HEADROOM_PCT` | `0.25` | Book только если mid ≥ MIN+headroom |
| `WATCHER_MIN_PROFIT_PCT` | `0.5` | Снятие с LIVE |
| `CROSS_DEX_MIN_PROFIT_PCT` | `1` | Cross-DEX |
| `CHAIN_MIN_PROFIT_PCT` | `1` | Chain |
| `CHAIN_MAX_HOPS` | `3` | Макс. hops |

### Тайминги и concurrency

| Переменная | Default | Смысл |
|------------|---------|--------|
| `SCAN_INTERVAL_SEC` | `1` | Пауза между циклами |
| `POOL_REFRESH_INTERVAL_SEC` | `10800` | Refresh пулов (3 ч) |
| `WATCHER_INTERVAL_SEC` | `10` | Переквот LIVE |
| `FULL_SCAN_EVERY_N_CYCLES` | `6` | Full universe каждые N |
| `HOT_POOL_TTL_SEC` | `1800` | TTL hot-set |
| `FRESH_MAX_AGE_SEC` | `30` | «СЕЙЧАС» на радаре |
| `SCANNER_MAX_CONCURRENCY` | `50` | Параллель пулов |
| `RPC_MAX_CONCURRENCY` | `20` | Параллель RPC |
| `RPC_MULTICALL_BATCH_SIZE` | `100` | Размер Multicall batch |

### Скан / скорость quotes

| Переменная | Default | Смысл |
|------------|---------|--------|
| `SCAN_MODE` | `dex_dex`* | Режим (*runtime может переопределить через `data/scan_mode.json`) |
| `SCAN_LIQUID_QUOTES_ONLY` | `1` | На full-scan только USDT/WBNB/… |
| `V2_RESERVES_MULTICALL` | `1` | Prefetch V2 reserves |
| `SIZE_SWEEP_USD` | `10,25,50,100,250,500` | Лестница размеров |
| `SIZE_SWEEP_MAX_USD` | `500` | Кап лестницы |
| `PCS_PRICE_API_ENABLED` | `1` | Mark с PCS Price API (не executable) |
| `PCS_SMART_ROUTER_ENABLED` | `0` | Только bench sidecar, не hot-path |
| `ALT_CEX_PROBE_ENABLED` | `0` | Alt-CEX probe |
| `SKIP_INITIAL_REFRESH` | `0` | Skip первого refresh |
| `POOL_SOURCE_PRIORITY` | см. discovery | Порядок источников пулов |

### Execution

| Переменная | Default | Смысл |
|------------|---------|--------|
| `EXECUTION_DRY_RUN` | `1` | eth_call only |
| `EXECUTION_MIN_PROFIT_PCT` | `3` | Порог исполнения |
| `EXECUTION_MAX_GAS_GWEI` | `50` | Cap gas |
| `EXECUTION_SLIPPAGE_BPS` | `50` | Slippage |
| `DAILY_LOSS_LIMIT_USD` | `50` | Дневной лимит убытка |

---

## Пайплайн расчёта PnL

1. **Quote** — `adapter.quote_exact_input` (V2: сначала Multicall reserves + локальный CPMM с integer floor как Solidity; иначе router/Quoter).
2. **Dir B closing** — для WBNB/WETH второй hop в USDT через `config/closing_pools.py` (иначе WBNB@$1 даёт ~−100%).
3. **Withdraw** — fee в токенах вычитается **до** DEX-quote; в `fees.total()` для Dir B **не** дублируется (`mexc_withdraw_fee_usd=0`).
4. **Fees** — gas + MEXC taker + slip buffer + (Dir B) flat deposit network; **pool fee** только informational (уже в amountOut).
5. **Mid net** — `gross − fees.total()`.
6. **Headroom** — если mid < `MIN_NET + headroom` → soft LIVE, без orderbook.
7. **Orderbook** — VWAP maximize по стакану; `size_min` / `size_max` / `size_optimal` для UI.
8. **Hard write** — только если book net ≥ порога → `signals.jsonl` + promote.
9. **Watcher** — тот же пайплайн на requote; якорь размера (`anchor_base_usd`) не даёт разгонять клип.

Аудит на живых данных: `python tools/audit_pnl_real.py`.

---

## Дашборд

| URL | Страница |
|-----|----------|
| `/` | MEXC⇄DEX LIVE + summary |
| `/dex-dex` | DEX↔DEX радар + funnel |
| `/ops` | Архив, пулы, логи, БД |
| `/alt-cex` | Alt-CEX (если включён) |

Ключевые API:

- `GET /api/summary`, `/api/opportunities`, `/api/signals`
- `GET|POST /api/scan_mode`
- `GET /api/dex_dex/opportunities`, `/api/dex_dex/diagnostics`
- `GET /api/refresh_status`, `/api/performance`, `/api/health`

В колонке **SIZE** показывается диапазон `$min–$max` (из orderbook curve или size ladder), не один кап `$100`.

---

## Данные в `data/`

| Файл | Роль |
|------|------|
| `pools_cache.json` | Кэш пулов (режим mexc) |
| `dex_dex_pools_cache.json` | Universe DEX↔DEX |
| `scan_mode.json` | Runtime mode override |
| `signals.jsonl` | Hard-сигналы MEXC⇄DEX |
| `opportunities_live.json` | LIVE watcher |
| `opportunities.json` | Экспорт возможностей |
| `signals_archive.jsonl` | Архив снятых |
| `dex_dex_*.json(l)` | I/O режима dex_dex |
| `performance.jsonl` | Метрики циклов |
| `refresh_status.json` | Прогресс refresh для UI |
| `hot_pools.json` | Hot scores |
| `state/active.sqlite3` | SQLite state + backups |
| `KILL_SWITCH` | Файл-стоп для execution |

Не коммитьте секреты и крупные runtime-логи в git.

---

## Тесты и утилиты

```bash
# Unit (без integration / api_tests)
pytest -m "not integration" -q
# или
pytest tests/unit/ -q
```

Полезные tools:

| Команда | Назначение |
|---------|------------|
| `python tools/audit_pnl_real.py` | Live-аудит FEG/V2/book/fees |
| `python tools/bench_pcs_vs_bot.py` | Bench PCS Smart Router vs bot Quoter |
| `tools/pcs_sidecar/` | Node sidecar для PCS SDK (bench only) |

---

## Исполнение (execution)

1. Без `DEX_PRIVATE_KEY` → `executor_disabled`, только мониторинг.
2. С ключом + `EXECUTION_DRY_RUN=1` → симуляция (eth_call), без broadcast.
3. Safety: kill-switch файл, daily loss limit, gas cap, re-quote, slippage.
4. Не оставляйте ключ в `.env`, если не готовы к риску.

---

## Документация и планы

Спеки и планы: `docs/superpowers/`

| Документ | Тема |
|----------|------|
| `specs/2026-07-27-mexc-first-scan-design.md` | MEXC-first pipeline |
| `specs/2026-07-28-dex-dex-zero-opps-design.md` | DEX↔DEX diagnostics |
| `specs/2026-07-29-executable-opportunity-maximizer-design.md` | Size ladder / ≥1% |
| `plans/2026-07-29-watcher-pnl-parity.md` | Watcher = scanner PnL |
| `plans/2026-07-29-pcs-sdk-integration.md` | PCS SDK / Price API |
| `plans/2026-07-29-pcs-sdk-bench-results.md` | Результаты бенча |

История решений в корне: `STATE.md`, `DECISIONS.md`, `plan.md` (могут быть устаревшими относительно кода).

---

## Типичные проблемы

| Симптом | Что проверить |
|---------|----------------|
| Нет сигналов | `SCAN_MODE`, `pools_cache.json` не пустой, MEXC цены, `MIN_NET_PROFIT_PCT` |
| LIVE пустой / −100% на WBNB-парах | Closing hop (Dir B), watcher hops |
| SIZE всегда `$100` | Старый UI/бот; нужен рестарт + hard refresh (должен быть `$min–$max`) |
| RPC 429 | Снизить `SCANNER_MAX_CONCURRENCY` / `RPC_MAX_CONCURRENCY`, добавить ключи |
| Cycle hangs на ETH V2 prefetch | Prefetch timeout 12s; смотреть `v2_prefetch_timeout` в логе |
| Executor не стартует | Ожидаемо без `DEX_PRIVATE_KEY` |

---

## Лицензия / дисклеймер

Проект для исследования и мониторинга. Крипто-арбитраж несёт рыночный, операционный и смарт-контрактный риск. Автор не несёт ответственности за финансовые потери. Используйте на свой страх и риск.
