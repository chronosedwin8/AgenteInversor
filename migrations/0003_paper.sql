-- Paper trading (y luego testnet/live): estado de la sesión, operaciones y eventos.
CREATE TABLE session_state (
    account         text        PRIMARY KEY,      -- 'paper', 'testnet', ...
    last_bar_time   timestamptz,                  -- última vela de señal procesada
    state           jsonb       NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE session_trades (
    id          bigserial   PRIMARY KEY,
    account     text        NOT NULL,
    symbol      text        NOT NULL,
    opened_at   timestamptz NOT NULL,
    closed_at   timestamptz NOT NULL,
    qty         numeric     NOT NULL,
    entry_price numeric     NOT NULL,
    exit_price  numeric     NOT NULL,
    pnl         numeric     NOT NULL,
    fees        numeric     NOT NULL,
    r_multiple  numeric     NOT NULL,
    exits       text        NOT NULL,
    UNIQUE (account, symbol, opened_at)
);

CREATE TABLE session_events (
    id      bigserial   PRIMARY KEY,
    account text        NOT NULL,
    at      timestamptz NOT NULL,
    kind    text        NOT NULL,
    detail  jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX session_events_account_at ON session_events (account, at DESC);
