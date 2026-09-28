# MEXC × DEX Arbitrage Monitor

> **Интересный личный проект, над которым я работал длительное время.** Я постепенно развивал его от простого сканера возможностей до V6-системы с MEXC, EVM/DEX, подбором размера сделки, watcher, dashboard, cross-DEX и multi-hop сценариями.
>
> **Status:** active research / monitoring project.
>
> ⚠️ Это исследовательский мониторинг, а не обещание доходности. По умолчанию execution выключен.

## Задача

Проверять, существует ли реальная арбитражная возможность между CEX и DEX или между несколькими DEX после учёта:

- цены;
- liquidity;
- taker fees;
- pool fees;
- slippage;
- gas;
- размера сделки;
- особенностей конкретного маршрута.

Простой вопрос:

> «Цена на DEX ниже, чем на бирже?»

не является достаточным.

Правильный вопрос:

> «Сколько получится получить на полном маршруте для конкретного размера после всех расходов?»

## Текущая версия

Основной код — в [V6](V6/).

Поддерживаются:

- MEXC ↔ DEX;
- DEX ↔ DEX;
- cross-DEX;
- multi-hop;
- live watcher;
- web dashboard;
- SQLite state;
- audit tools.

Репозиторий сохраняет историю V1 → V6, чтобы видеть эволюцию системы.

## Архитектура

```
market/token sources
       ↓
pool discovery
       ↓
on-chain quote
       ↓
fees + slippage + size sweep
       ↓
net profitability
       ↓
signal / watcher
       ↓
dashboard
```

## Ключевой engineering case — размер сделки

Для одной и той же пары проект пробует несколько размеров:

```
$10
$25
$50
$100
$250
$500
```

Результат может быть таким:

- маленький размер выгоден;
- большой размер съедает прибыль slippage;
- оптимальный размер находится между ними.

Это важнее, чем смотреть только на spot/mid price.

## PnL pipeline

Текущая V6 считает результат примерно так:

1. получить quote;
2. обработать направление A/B;
3. учесть withdrawal/bridge related costs, где применимо;
4. добавить gas + taker + slippage costs;
5. посчитать mid net;
6. при достаточном headroom проверить orderbook;
7. записать hard signal;
8. watcher повторяет тот же pipeline на live requote.

Подробная реализация документирована внутри [V6](V6/).

## Быстрый запуск

```bash
cd V6
python -m venv .venv
```

Windows:

```bat
.venv\Scripts\activate
```

Установка:

```bash
pip install -r requirements.txt
```

Dashboard:

```bash
python main.py
python run_dashboard.py --host 127.0.0.1 --port 8000
```

Открыть:

```
http://127.0.0.1:8000
```

## Безопасный режим

Без `DEX_PRIVATE_KEY`:

- execution отключён;
- работает мониторинг.

С ключом, но:

```
EXECUTION_DRY_RUN=1
```

система должна оставаться в dry-run/eth_call режиме без broadcast.

Дополнительные safety settings:

- gas cap;
- slippage cap;
- daily loss limit;
- kill switch;
- re-quote;
- blacklists / circuit breakers.

## Режимы сканирования

Runtime mode:

```
mexc
dex_dex
```

MEXC mode:

- MEXC ↔ DEX edges;
- pool refresh;
- signals в `data/signals.jsonl`.

DEX-DEX mode:

- cross-DEX / chain opportunities;
- `data/dex_dex_signals.jsonl`.

## Сети и DEX

V6 работает с активными EVM-сетями, среди которых:

- BSC;
- Ethereum;
- Arbitrum;
- Base;
- Polygon.

В registry присутствуют Uniswap, PancakeSwap, Aerodrome и другие адаптеры.

Точное состояние registry смотрите в `config/`.

## Конфигурация

Основные env:

| Переменная | Смысл |
|---|---|
| `MEXC_API_KEY` / `MEXC_API_SECRET` | MEXC |
| `ALCHEMY_KEY*` | RPC |
| `INFURA_KEY` / `DRPC_KEY` | RPC |
| `*_RPC_URL` | прямые RPC |
| `DEX_PRIVATE_KEY` | execution key |
| `MIN_NET_PROFIT_PCT` | hard-signal threshold |
| `WATCHER_MIN_PROFIT_PCT` | watcher threshold |
| `SCAN_INTERVAL_SEC` | interval |
| `SCANNER_MAX_CONCURRENCY` | parallelism |
| `RPC_MAX_CONCURRENCY` | RPC parallelism |
| `SIZE_SWEEP_USD` | sizes |
| `EXECUTION_DRY_RUN` | dry run |
| `EXECUTION_SLIPPAGE_BPS` | execution slippage |
| `DAILY_LOSS_LIMIT_USD` | daily loss cap |

Все настройки загружаются из `.env`.

## Важные данные в data/

```
data/
  pools_cache.json
  dex_dex_pools_cache.json
  scan_mode.json
  signals.jsonl
  opportunities_live.json
  signals_archive.jsonl
  performance.jsonl
  refresh_status.json
  hot_pools.json
  state/active.sqlite3
  KILL_SWITCH
```

Это runtime data, а не конфигурация, которую следует коммитить целиком.

## Dashboard

Основные страницы:

| URL | Содержание |
|---|---|
| `/` | MEXC⇄DEX LIVE |
| `/dex-dex` | DEX↔DEX radar |
| `/ops` | operational data |
| `/alt-cex` | optional alt-DEX/CEX view |

API включает summary, opportunities, signals, scan mode, diagnostics, performance и health.

## Тесты и инструменты

Unit tests:

```bash
pytest -m "not integration" -q
```

Useful tools:

```bash
python tools/audit_pnl_real.py
python tools/bench_pcs_vs_bot.py
```

V6 содержит **40 test files**; старые версии имеют свои наборы проверок.

## Security

Секреты должны жить в environment variables.

Execution deliberately disabled by default.

Исторические credentials были отозваны и не должны использоваться как действующие.

## Ограничения

- ликвидность и цены изменяются каждую секунду;
- RPC/API имеют rate limits;
- сигналы могут устаревать;
- сеть, gas, MEV и execution latency влияют на реальный результат;
- мониторинг не гарантирует прибыль.

## AI-assisted development

AI использовался как ускоритель для черновой реализации, рутинных модулей и тестовых идей.

Я отвечал за decomposition, архитектуру, review решений, debugging, validation и итоговое поведение системы.

## Лицензия

MIT.
