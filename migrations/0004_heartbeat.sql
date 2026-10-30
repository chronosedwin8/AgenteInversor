-- Heartbeat persistente: permite medir y registrar interrupciones del servicio (p. ej. el PC
-- apagado) al volver a arrancar.
CREATE TABLE service_heartbeat (
    service     text        PRIMARY KEY,
    started_at  timestamptz NOT NULL,
    last_at     timestamptz NOT NULL
);
