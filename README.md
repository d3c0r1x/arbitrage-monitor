# MEXC × DEX Arbitrage Monitor — эволюция V1→V6

Асинхронный монитор арбитражных возможностей между **MEXC** (mid + order book) и on-chain **DEX** (Uniswap, PancakeSwap, Aerodrome и др.), включая режим **DEX↔DEX** (cross-DEX и multi-hop цепочки).

Проект прошёл шесть итераций за неделю активной разработки (25–29 июля 2026).
Каждая версия — самодостаточный снимок проекта с собственным README, тестами и конфигом.
Актуальная версия — **V6**.

> ⚠️ По умолчанию проект работает **только как мониторинг**: сигналы, LIVE-радар, метрики и веб-дашборд.
> Исполнение сделок включается только при наличии `DEX_PRIVATE_KEY` (и даже тогда по умолчанию `EXECUTION_DRY_RUN=1`).

## Карточка эволюции

| Версия | Дата | Что нового |
|--------|------|-----------|
| **V1** | 25–26 июля | Первая рабочая версия: MEXC token list → DEX pool discovery → on-chain симуляция свопа → расчёт чистой прибыли → сигнал. Сети: Ethereum, BSC, Polygon, Arbitrum, Robinhood Chain. Направление DEX_BUY_MEXC_SELL. Веб-дашборд и GUI-черновик |
| **V2** | 26–27 июля | Стабилизация «боевого» режима: Alt-CEX клиент и отдельная вкладка дашборда, MEXC-first сканирование, AMM-версии, закрытие пулов (closing_pools), адаптер Uniswap V1. Большая ревизия по плану ревью D1–D18: fail-closed decimals, canonical-реестр адресов токенов, direction-aware фильтры сигналов, circuit breaker на RPC, blacklist мёртвых пулов, карантин логов с ключами |
| **V3** | 27–28 июля | Применён план ревью v3.0 (Qwen): ликвидированы утечки ключей в логи (все засвеченные ключи отозваны), описание сетей вынесено в `config/networks.py`, README переписан. Версия-«фикс» без новых фич — 2 изменённых файла относительно V2 |
| **V4** | 28 июля | Ops-панель (`ops.html`): статус обновления пулов и источников в реальном времени (`refresh_status.py`). Юнит-тесты сервиса метаданных токенов |
| **V5** | 28–29 июля | Режим **DEX↔DEX**: cross-DEX по одному pair и multi-hop цепочки (`dex_dex_universe`, `dex_dex_store`, `scan_mode`, вкладка `dex_dex.html`), hot-pools кэш. Интеграция сабграфов PancakeSwap V3/StableSwap в discovery |
| **V6** | 29 июля | Максимально проработанная версия: size sweep (подбор оптимального размера сделки), кэш резервов V2 (multicall), PCS price API и бенчмарки sidecar-сканера, аудит реального PnL (`tools/audit_pnl_real.py`), паритет PnL вотчера. 40 тест-файлов |

## Схема сканирования (V6)

```
MEXC token list + subgraphs → pool discovery → on-chain quote (V2/V3/…)
→ size sweep → fees & slippage → net profit → signal → watcher → dashboard
```

| Направление | Описание |
|-------------|----------|
| **A · DEX_BUY_MEXC_SELL** | Купить токен на DEX → продать на MEXC |
| **B · MEXC_BUY_DEX_SELL** | Купить на MEXC → вывести → продать на DEX (+ closing hop WBNB/WETH → USDT) |
| **Cross-DEX** | Один pair на двух DEX одной сети |
| **Chain** | Multi-hop маршруты до `CHAIN_MAX_HOPS` |

## Структура репозитория

```
V1/ … V6/   — снимки версий (V6 — актуальная)
```

Внутри каждой версии — самостоятельный Python-проект (Python 3.12):
`main.py`, `scanner/`, `discovery/`, `clients/`, `execution/`, `services/`,
`security/`, `dashboard/`, `tests/`, `docs/` (планы и спецификации),
`README.md`, `STATE.md`, `DECISIONS.md`, `.env.example`.

## Запуск V6

```bash
cd V6
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                # заполнить ключи
python main.py                                      # только мониторинг
python run_dashboard.py                             # веб-дашборд
```

Подробности конфигурации — в `V6/README.md` (раздел «Конфигурация (env)»).

## Безопасность

- Все `.env`, логи запусков, кэши и данные сканов **исключены из репозитория**;
  в истории нет ключей MEXC, Alchemy, Infura, DRPC и The Graph.
- Упоминания отозванных ключей в исторических отчётах (V1/V2, планы ревью)
  заменены на `ALCHEMY_KEY_1_REDACTED` / `ALCHEMY_KEY_2_REDACTED`.
- Компрометированные в процессе разработки ключи были **отозваны** ещё на этапе V3
  (см. `V3/STATE.md`), поэтому в актуальном коде их нет.
- Реальные значения задаются через локальный `.env` (из `.env.example`).

## Тесты

Каждая версия несёт свои тесты (`pytest`), от 24 файлов в V1 до 40 в V6:

```bash
cd V6 && pytest tests -q
```
