CREATE TABLE ledger_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE projects (
    project_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL UNIQUE,
    project_name TEXT NOT NULL,
    project_name_normalized TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    created_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE accounts (
    account_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    account_code TEXT NOT NULL,
    account_name TEXT NOT NULL,
    account_type TEXT NOT NULL DEFAULT 'MANUAL',
    market TEXT NOT NULL DEFAULT 'CN_STOCK',
    base_currency TEXT NOT NULL DEFAULT 'CNY',
    initial_capital TEXT NOT NULL,
    cash_balance TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'FROZEN', 'ARCHIVED')),
    opened_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    UNIQUE (project_id, account_code)
);

CREATE TABLE instruments (
    instrument_id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    quantity_precision INTEGER NOT NULL DEFAULT 0,
    price_precision INTEGER NOT NULL DEFAULT 2,
    lot_size INTEGER NOT NULL DEFAULT 100,
    t_plus_days INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (market, venue, symbol)
);

CREATE TABLE tracked_instruments (
    tracking_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    source_text TEXT NOT NULL,
    tracking_status TEXT NOT NULL CHECK (
        tracking_status IN ('WATCHING', 'HOLDING', 'CLOSED', 'EXPIRED', 'ARCHIVED')
    ),
    added_at TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    UNIQUE (project_id, instrument_id)
);

CREATE TABLE trade_records (
    trade_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    signal_text TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    trade_time TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    gross_amount TEXT NOT NULL,
    commission_amount TEXT NOT NULL,
    stamp_tax_amount TEXT NOT NULL,
    transfer_fee_amount TEXT NOT NULL,
    net_cash_amount TEXT NOT NULL,
    realized_pnl TEXT NOT NULL DEFAULT '0.00',
    allocation_ratio TEXT,
    ratio_basis TEXT CHECK (
        ratio_basis IS NULL OR ratio_basis IN ('AVAILABLE_CASH', 'SELLABLE_POSITION')
    ),
    execution_source TEXT NOT NULL CHECK (
        execution_source IN ('MANUAL', 'AUTO_TRADING', 'IMPORT')
    ),
    record_status TEXT NOT NULL CHECK (
        record_status IN ('DRAFT', 'CONFIRMED', 'REVERSED')
    ),
    external_trade_id TEXT,
    request_id TEXT,
    request_fingerprint TEXT,
    reversal_of_trade_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (reversal_of_trade_id) REFERENCES trade_records(trade_id)
);

CREATE UNIQUE INDEX idx_trade_request_id
    ON trade_records(project_id, request_id)
    WHERE request_id IS NOT NULL;
CREATE UNIQUE INDEX idx_trade_external_id
    ON trade_records(execution_source, external_trade_id)
    WHERE external_trade_id IS NOT NULL;
CREATE INDEX idx_trades_project_time
    ON trade_records(project_id, trade_time DESC);

CREATE TABLE cash_ledger (
    cash_entry_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    trade_id TEXT,
    entry_type TEXT NOT NULL CHECK (
        entry_type IN (
            'INITIAL_CAPITAL', 'DEPOSIT', 'WITHDRAWAL',
            'BUY', 'SELL', 'REVERSAL'
        )
    ),
    currency TEXT NOT NULL,
    amount TEXT NOT NULL,
    balance_after TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    reversal_of_entry_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    request_id TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (trade_id) REFERENCES trade_records(trade_id),
    FOREIGN KEY (reversal_of_entry_id) REFERENCES cash_ledger(cash_entry_id)
);

CREATE UNIQUE INDEX idx_cash_request_id
    ON cash_ledger(project_id, request_id)
    WHERE request_id IS NOT NULL;
CREATE INDEX idx_cash_account_time
    ON cash_ledger(account_id, occurred_at, cash_entry_id);

CREATE TABLE position_lots (
    lot_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    source_trade_id TEXT NOT NULL,
    original_quantity TEXT NOT NULL,
    remaining_quantity TEXT NOT NULL,
    unit_cost TEXT NOT NULL,
    available_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    FOREIGN KEY (source_trade_id) REFERENCES trade_records(trade_id)
);

CREATE INDEX idx_lots_account_instrument_date
    ON position_lots(account_id, instrument_id, available_date, created_at);

CREATE TABLE positions (
    project_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    quantity TEXT NOT NULL,
    available_quantity TEXT NOT NULL,
    average_cost TEXT NOT NULL,
    realized_pnl TEXT NOT NULL,
    last_price TEXT,
    market_value TEXT,
    unrealized_pnl TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, instrument_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
);

CREATE TABLE reference_prices (
    reference_price_id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL,
    price TEXT NOT NULL,
    price_time TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    is_manual INTEGER NOT NULL DEFAULT 0,
    raw_source_summary TEXT NOT NULL DEFAULT '',
    request_id TEXT,
    created_by TEXT NOT NULL,
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
    UNIQUE (instrument_id, price_time, source_name)
);

CREATE UNIQUE INDEX idx_reference_price_request
    ON reference_prices(request_id)
    WHERE request_id IS NOT NULL;
CREATE INDEX idx_reference_price_latest
    ON reference_prices(instrument_id, price_time DESC, reference_price_id DESC);

CREATE TABLE audit_events (
    audit_event_id TEXT PRIMARY KEY,
    project_id INTEGER,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    request_id TEXT,
    before_json TEXT,
    after_json TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE INDEX idx_audit_project_time
    ON audit_events(project_id, created_at DESC);

CREATE TABLE daily_account_valuations (
    valuation_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    valuation_date TEXT NOT NULL,
    valuation_time TEXT NOT NULL,
    cash_balance TEXT NOT NULL,
    market_value TEXT NOT NULL,
    equity TEXT NOT NULL,
    external_net_flow TEXT NOT NULL,
    pnl TEXT NOT NULL,
    return_rate TEXT,
    peak_equity TEXT NOT NULL,
    drawdown TEXT NOT NULL,
    is_partial INTEGER NOT NULL DEFAULT 0,
    missing_symbols_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    UNIQUE (account_id, valuation_date)
);

CREATE INDEX idx_valuations_project_date
    ON daily_account_valuations(project_id, valuation_date);

CREATE TABLE daily_position_valuations (
    valuation_id TEXT NOT NULL,
    project_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    quantity TEXT NOT NULL,
    average_cost TEXT NOT NULL,
    last_price TEXT,
    market_value TEXT,
    unrealized_pnl TEXT,
    is_price_missing INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (valuation_id, instrument_id),
    FOREIGN KEY (valuation_id) REFERENCES daily_account_valuations(valuation_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
);
