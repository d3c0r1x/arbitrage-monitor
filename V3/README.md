# MEXC × DEX Arbitrage Monitor

Мониторинг арбитража между **MEXC** (CEX) и DEX-пулами (PancakeSwap, Uniswap, Aerodrome и др.) с веб-дашбордом.

> По умолчанию это **read-only монитор**. Исполнение сделок активируется только при наличии `DEX_PRIVATE_KEY` (и обычно в `EXECUTION_DRY_RUN=1`).

---

## Что умеет бот

| Модуль | Описание |
|--------|----------|
| **MEXC ⇄ DEX** | Основной сканер: цена MEXC vs on-chain quote, fees/gas, финальный gate по **стакану MEXC** (оптимальный клип) |
| **Cross-DEX** | Same-pair арбитраж DEX↔DEX на одной сети (throttled) |
| **Chain multi-hop** | Циклические маршруты A→…→A (throttled) |
| **Alt-CEX probe** | Bitget / HTX / BingX (отдельный цикл и отдельный дашборд) |
| **Watcher** | Переквот «живых» возможностей для LIVE-панели |
| **Dashboard** | Браузерный UI: сводка, LIVE, история, пулы, логи |

Активные сети по умолчанию: **BSC + ETHEREUM + ARBITRUM** (`config/networks.py` → `ACTIVE_NETWORKS`).

---

## Быстрый старт

```bash
# 1) Зависимости
python -m pip install -r requirements.txt

# 2) Конфиг
copy .env.example .env   # Windows
# Отредактировать .env: MEXC_API_KEY/SECRET, ALCHEMY_KEY (или RPC URL)

# 3) Бот (мониторинг)
python main.py

# Быстрый старт без полного refresh пулов:
#   set SKIP_INITIAL_REFRESH=1
#   python main.py

# 4) Дашборд (отдельный процесс)
python run_dashboard.py --host 127.0.0.1 --port 8000
```

- Основной UI (MEXC⇄DEX): http://127.0.0.1:8000  
- Alt-CEX окно: http://127.0.0.1:8000/alt-cex  

---

## Архитектура

```
                    ┌─────────────────┐
                    │  MEXC public /  │
                    │  signed API     │
                    └────────┬────────┘
                             │ prices, capital config, depth
┌──────────────┐    ┌────────▼────────┐    ┌──────────────────┐
│ Pool refresh │───▶│ pools_cache.json│───▶│ Scanner cycle    │
│ discovery    │    │ + SQLite state  │    │ ProfitCalculator │
└──────────────┘    └─────────────────┘    │ Orderbook gate   │
                                           └────────┬─────────┘
                                                    │
                    ┌───────────────────────────────┼────────────────┐
                    ▼                               ▼                ▼
             signals.jsonl                   signal_watcher    alt_cex_signals.jsonl
             (MEXC_*)                        opportunities_*   (ALT_CEX_*)
                    │                               │                │
                    └───────────────┬───────────────┴────────────────┘
                                    ▼
                           Dashboard (FastAPI)
```

Ключевые каталоги:

| Путь | Назначение |
|------|------------|
| `main.py` | Точка входа, asyncio loops |
| `scanner/` | Сканер, watcher, chain/cross-dex engines, signal writer |
| `services/` | Цены, fees, orderbook, profit, alt-CEX probe |
| `clients/` | MEXC, RPC, HTTP, alt-CEX |
| `dex/` | Адаптеры V1–V4 |
| `discovery/` | DexScreener / GeckoTerminal / on-chain factory |
| `dashboard/` | FastAPI + `web/index.html` + `web/alt_cex.html` |
| `execution/` | Опциональный executor (без ключа — inert) |
| `data/` | Кэши, jsonl, sqlite |
| `.run/` | Логи запусков |

---

## Потоки данных и сигналы

### MEXC ⇄ DEX (основной)

Направления:

- `MEXC_BUY_DEX_SELL` — купить на MEXC, продать на DEX  
- `DEX_BUY_MEXC_SELL` — купить на DEX, продать на MEXC  

Пайплайн:

1. Загрузка пулов из `data/pools_cache.json`  
2. Цена MEXC (`PriceService`)  
3. On-chain `quote_exact_input`  
4. Fees / gas (`FeeService`)  
5. **Orderbook final gate** — обход стакана MEXC, выбор клипа с max net $  
6. Запись в `data/signals.jsonl` (+ stdout JSONL)  
7. На сигнале строится **`size_curve`** (математика по уже скачанному стакану ± V2 CPMM reserves) — без дополнительных RPC  

Дашборд:

- показывает **1 сигнал на (сеть, токен, направление)** — лучший по `net_profit_usd`  
- интерактивный **слайдер объёма** min→max по `size_curve`  

### Alt-CEX

Отдельный loop (`AltCexProbe`): Bitget/HTX/BingX, сравнение с MEXC и (редко) DEX.  
Пишет только в `data/alt_cex_signals.jsonl`, не портит статистику MEXC⇄DEX.

---

## Конфигурация (`.env`)

См. полный список в `.env.example`. Важное:

| Переменная | Смысл | Типично |
|------------|--------|---------|
| `MEXC_API_KEY` / `MEXC_API_SECRET` | Capital config / signed endpoints | нужно для ассетов |
| `ALCHEMY_KEY` или `*_RPC_URL` | RPC | нужно для quotes |
| `BASE_AMOUNT_USD` | Базовый нотионал до orderbook sizing | `10` |
| `MIN_NET_PROFIT_PCT` | Мин. net % | `0.1` |
| `MIN_NET_PROFIT_USD` | Мин. net $ (orderbook) | `0.1` |
| `SCAN_INTERVAL_SEC` | Пауза между циклами сканера | `1` |
| `POOL_REFRESH_INTERVAL_SEC` | Refresh пулов | `10800` |
| `SKIP_INITIAL_REFRESH` | `1` = взять существующий `pools_cache.json` | debug |
| `ALT_CEX_PROBE_ENABLED` | Вкл. alt-CEX probe | `1` |
| `ALT_CEX_ENABLE_OKX_DEX` | OKX DEX aggregator | `0` |
| `CROSS_DEX_*` / `CHAIN_*` | Throttle multi-DEX / multi-hop | см. settings |
| `DEX_PRIVATE_KEY` | Включает execution module | пусто = monitor-only |

CU / rate limits: `config/rate_limits.py`, round-robin Alchemy keys.

---

## Дашборд API

| Endpoint | Scope |
|----------|--------|
| `GET /api/summary` | Только `MEXC_*`, дедуп best-per-token |
| `GET /api/signals` | То же + `size_curve` если есть |
| `GET /api/opportunities` | LIVE watcher |
| `GET /api/alt_cex/summary` | Alt-CEX статистика |
| `GET /api/alt_cex/signals` | Alt-CEX история |
| `GET /api/pools` | Кэш пулов |
| `GET /api/logs` | Хвост логов бота |

---

## Запуск и логи

```bash
# Бот → лог
python -u main.py *> .run\bot.log

# Дашборд
python -u run_dashboard.py --host 127.0.0.1 --port 8000 *> .run\dashboard.log
```

Артефакты:

- `data/signals.jsonl` — MEXC⇄DEX  
- `data/alt_cex_signals.jsonl` — Alt-CEX  
- `data/pools_cache.json` — пулы  
- `data/opportunities_live.json` — LIVE  
- `data/state/active.sqlite3` — состояние  

---

## Тесты

```bash
# Unit (без сети)
pytest -m "not integration" -q

# Точечно
pytest tests/unit/test_orderbook_service.py tests/unit/test_cross_dex_engine.py tests/unit/test_alt_cex_probe.py -q
```

---

## Безопасность

- Секреты в логах только как `present=True/False`  
- Dashboard read-only  
- Execution inert без `DEX_PRIVATE_KEY`  
- Не коммитить `.env`, ключи Alchemy/MEXC  

---

## Типичные warning’и сигнала

| Фрагмент | Смысл |
|----------|--------|
| `closed_to_usdt` | Выход пула закрыт свопом в USDT |
| `orderbook_vwap=…\|size_usd=…\|net_usd=…` | Итог стакана: VWAP, объём, net |
| `orderbook_no_profitable_clip` | По стакану нет клипа ≥ порога |
| `no_execution` (alt) | Только мониторинг alt-CEX |

---

## Лицензия

MIT
