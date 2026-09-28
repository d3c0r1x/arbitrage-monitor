# MEXC × DEX Arbitrage Monitor

Бот для мониторинга арбитражных возможностей между MEXC и DEX-пулами.

Поддерживаемые сети: Ethereum, BSC, Polygon, Arbitrum, Robinhood Chain.

## ⚠️ Важно

Проект работает **только как мониторинг** — автоматическое исполнение сделок не входит в базовый режим.

## Архитектура

```
MEXC token list → DEX pool discovery → on-chain swap simulation → fee calculation → net profit → signal output
```

## Требования

- Python 3.12+
- Доступ к RPC (Alchemy, публичные RPC или собственные)

## Установка

```bash
# 1. Клонировать репозиторий
git clone <repo-url>
cd mexc_dex_arb

# 2. Установить зависимости
python -m pip install -r requirements.txt

# 3. Настроить .env
cp .env.example .env
# Отредактировать .env — добавить API ключи
```

## Конфигурация

Основные параметры `.env`:

| Параметр | Описание | По умолчанию |
|---|---|---|
| `MEXC_API_KEY` | MEXC API ключ (опционально) | — |
| `ALCHEMY_KEY` | Alchemy API ключ | — |
| `ETH_RPC_URL` | Ethereum RPC URL | Alchemy / public fallback |
| `BSC_RPC_URL` | BSC RPC URL | Alchemy / public fallback |
| `BASE_AMOUNT_USD` | Базовая сумма для расчета | 10 |
| `MIN_NET_PROFIT_PCT` | Минимальный % прибыли (> 1) | 1 |
| `POOL_REFRESH_INTERVAL_SEC` | Интервал обновления пулов | 3600 |
| `SCAN_INTERVAL_SEC` | Интервал сканирования | 60 |

Полный список параметров — в `.env.example`.

## Запуск

```bash
python main.py
```

## Тестирование

### Unit-тесты (без сети)
```bash
pytest -m "not integration" -q
```

### Integration-тесты (с сетью)
```bash
RUN_INTEGRATION=1 pytest -m integration -q
```

## Проверка проекта

```bash
python scripts/check_project.py
python scripts/benchmark_discovery.py
```

## Источники пулов

| Приоритет | Источник | Тип | По умолчанию |
|---|---|---|---|
| 1 | DexScreener | HTTP API | Включен |
| 2 | GeckoTerminal | HTTP API | Включен |
| 3 | On-chain Factory | RPC | Включен (fallback) |
| 4+ | Optional | HTTP API | Отключены |

## Расчет прибыли

Два направления:

- **A (DEX_BUY_MEXC_SELL)**: Wallet → DEX buy → MEXC sell → Wallet
- **B (MEXC_BUY_DEX_SELL)**: Wallet → MEXC buy → DEX sell → Wallet

Сигнал выводится при `net_profit_pct > 1` (строго больше).

## Безопасность

- Никакие секреты не логируются
- API ключи отображаются только как `present=True/False`
- Security warnings не удаляют сигналы

## Лицензия

MIT
