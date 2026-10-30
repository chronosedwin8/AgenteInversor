-- Estado persistente del RiskManager (kill switch, pausas, contadores diarios).
-- Un reinicio del bot NO desactiva el kill switch: solo un reinicio manual explícito.
CREATE TABLE risk_state (
    account     text        PRIMARY KEY,          -- 'paper', 'testnet', 'live'
    state       jsonb       NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Auditoría de eventos de riesgo (kill switch, límites diarios, pausas, reinicios manuales).
CREATE TABLE risk_events (
    id          bigserial   PRIMARY KEY,
    account     text        NOT NULL,
    at          timestamptz NOT NULL,
    kind        text        NOT NULL,
    detail      jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX risk_events_account_at ON risk_events (account, at DESC);
