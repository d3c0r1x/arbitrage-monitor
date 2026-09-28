"""
SQLite database schema and connection management.

Uses WAL mode for concurrent reads.
"""

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pools (
    network TEXT NOT NULL,
    token_address TEXT NOT NULL,
    stablecoin_address TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    dex TEXT NOT NULL,
    sources TEXT NOT NULL,
    pool_version TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    raw_json TEXT,
    PRIMARY KEY (network, pool_address, token_address)
);

CREATE INDEX IF NOT EXISTS idx_pools_token
ON pools (network, token_address);

CREATE INDEX IF NOT EXISTS idx_pools_stablecoin
ON pools (network, stablecoin_address);


CREATE TABLE IF NOT EXISTS mexc_assets (
    coin TEXT NOT NULL,
    name TEXT,
    network TEXT NOT NULL,
    contract_address TEXT NOT NULL,
    deposit_enable INTEGER NOT NULL,
    withdraw_enable INTEGER NOT NULL,
    withdraw_fee TEXT,
    withdraw_min TEXT,
    withdraw_max TEXT,
    min_confirm INTEGER,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (coin, network, contract_address)
);


CREATE TABLE IF NOT EXISTS stablecoins (
    coin TEXT NOT NULL,
    network TEXT NOT NULL,
    address TEXT NOT NULL,
    deposit_enable INTEGER NOT NULL,
    withdraw_enable INTEGER NOT NULL,
    withdraw_fee TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (network, address)
);


CREATE TABLE IF NOT EXISTS refresh_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    status TEXT NOT NULL,
    error TEXT,
    pools_count INTEGER,
    mexc_assets_count INTEGER,
    stablecoins_count INTEGER,
    candidate_offset INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY,
    healthy INTEGER NOT NULL DEFAULT 1,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_success_at INTEGER,
    last_failure_at INTEGER,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL,
    duration_ms REAL NOT NULL,
    items_total INTEGER,
    items_success INTEGER,
    items_failed INTEGER,
    cache_hits INTEGER,
    cache_misses INTEGER,
    source TEXT,
    network TEXT,
    details TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    network TEXT NOT NULL,
    token_coin TEXT NOT NULL,
    token_address TEXT NOT NULL,
    mexc_quote_asset TEXT NOT NULL,
    mexc_symbol TEXT NOT NULL,
    pool_stablecoin_coin TEXT NOT NULL,
    pool_stablecoin_address TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    dex TEXT NOT NULL,
    pool_version TEXT,
    direction TEXT NOT NULL,
    base_amount_usd TEXT NOT NULL,
    mexc_price_usd TEXT NOT NULL,
    dex_amount_in TEXT,
    dex_amount_out TEXT,
    gross_profit_usd TEXT,
    gross_profit_pct TEXT,
    fees_json TEXT,
    net_profit_usd TEXT,
    net_profit_pct TEXT,
    full_cycle INTEGER NOT NULL DEFAULT 1,
    warnings_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_timestamp
ON signals (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_signals_network
ON signals (network, token_coin);

CREATE INDEX IF NOT EXISTS idx_signals_profit
ON signals (net_profit_pct DESC);
"""


def create_connection(db_path: Path) -> sqlite3.Connection:
    """Create a SQLite connection with WAL mode enabled.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        sqlite3.Connection with WAL mode.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA foreign_keys=ON;")
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create all tables if they do not exist.

    Args:
        connection: SQLite connection.
    """
    connection.executescript(SCHEMA_SQL)
    connection.commit()

    # Migration: add candidate_offset column if missing (G1 pagination).
    try:
        connection.execute(
            "ALTER TABLE refresh_log ADD COLUMN candidate_offset INTEGER DEFAULT 0"
        )
        connection.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists.

    # Migration: rebuild pools table if PK is the old (network, pool_address).
    # New PK includes token_address so one physical pool can serve both
    # arbitrage paths (token A and token B may both be MEXC-tradable).
    try:
        pk_cols = [
            row[1]
            for row in sorted(
                (
                    r
                    for r in connection.execute("PRAGMA table_info(pools)")
                    if r[5] > 0
                ),
                key=lambda r: r[5],
            )
        ]
        if pk_cols and pk_cols != ["network", "pool_address", "token_address"]:
            connection.executescript(
                """
                ALTER TABLE pools RENAME TO pools_old;
                CREATE TABLE pools (
                    network TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    stablecoin_address TEXT NOT NULL,
                    pool_address TEXT NOT NULL,
                    dex TEXT NOT NULL,
                    sources TEXT NOT NULL,
                    pool_version TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    raw_json TEXT,
                    PRIMARY KEY (network, pool_address, token_address)
                );
                INSERT OR IGNORE INTO pools
                    (network, token_address, stablecoin_address, pool_address,
                     dex, sources, pool_version, created_at, updated_at, raw_json)
                SELECT network, token_address, stablecoin_address, pool_address,
                       dex, sources, pool_version, created_at, updated_at, raw_json
                FROM pools_old;
                DROP TABLE pools_old;
                CREATE INDEX IF NOT EXISTS idx_pools_token
                ON pools (network, token_address);
                CREATE INDEX IF NOT EXISTS idx_pools_stablecoin
                ON pools (network, stablecoin_address);
                """
            )
            connection.commit()
    except sqlite3.OperationalError:
        pass  # Fresh DB or concurrent migration.
