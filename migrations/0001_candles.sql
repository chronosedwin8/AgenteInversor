-- Velas OHLCV. open_time identifica la vela (UTC). Solo se guardan velas cerradas.
CREATE TABLE candles (
    symbol           text        NOT NULL,
    interval         text        NOT NULL,
    open_time        timestamptz NOT NULL,
    close_time       timestamptz NOT NULL,
    open             numeric     NOT NULL,
    high             numeric     NOT NULL,
    low              numeric     NOT NULL,
    close            numeric     NOT NULL,
    volume           numeric     NOT NULL,
    quote_volume     numeric     NOT NULL,
    trades           bigint      NOT NULL,
    taker_buy_base   numeric     NOT NULL,
    taker_buy_quote  numeric     NOT NULL,
    source           text        NOT NULL CHECK (source IN ('vision', 'rest', 'ws')),
    ingested_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, interval, open_time),
    CHECK (high >= low AND high >= open AND high >= close AND low <= open AND low <= close)
);

-- Instantáneas de exchangeInfo por símbolo (auditoría y reproducibilidad del backtest).
CREATE TABLE symbol_info_snapshots (
    id          bigserial   PRIMARY KEY,
    symbol      text        NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    raw         jsonb       NOT NULL
);
CREATE INDEX symbol_info_snapshots_symbol_time ON symbol_info_snapshots (symbol, fetched_at DESC);

-- Huecos que el exchange no puede rellenar (p. ej. mantenimiento de Binance sin velas).
-- Se registran explícitamente para que "sin huecos" sea verificable y no se oculte nada.
CREATE TABLE candle_gaps_exchange (
    symbol      text        NOT NULL,
    interval    text        NOT NULL,
    gap_start   timestamptz NOT NULL,
    gap_end     timestamptz NOT NULL,
    detected_at timestamptz NOT NULL DEFAULT now(),
    note        text,
    PRIMARY KEY (symbol, interval, gap_start)
);
