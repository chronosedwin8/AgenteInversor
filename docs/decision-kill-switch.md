# Decisión: kill switch medido desde el máximo del capital

Fecha: 2026-10-30. Decisión de Edwin (respuesta a la pregunta abierta de PLAN.md).

- **Regla:** el kill switch se activa cuando el capital cae un 10% desde el **máximo** alcanzado
  (`kill_switch_basis: peak` en `config/risk.yaml`). Con 200 USD y sin ganancias sigue siendo
  180 USD; si el capital llega a 250, salta en 225. Cancela órdenes, cierra posiciones y solo se
  reinicia a mano; tras el reinicio manual, el máximo de referencia pasa a ser el capital de ese
  momento.
- **Sin cambios:** H1, sus parámetros, los criterios de aceptación y la alerta de drawdown
  (riesgo al 0,25% con capital ≤ 188 USD, medida desde el inicial).

## Impacto medido sobre H1-fijo (2020-05 → 2026-10, mismo motor y RiskManager)

| Base del kill switch | ¿Se activa? | Operaciones | PF | Neto |
| --- | --- | --- | --- | --- |
| Capital inicial (antes) | no | 394 | 1,40 | +102,33 USDT |
| **Máximo (ahora)** | **sí, 2021-12-24** (capital 229,43; máximo 255,24) | 162 | 1,30 | +29,43 USDT |

- Bootstrap por bloques de las 394 operaciones: **P(caída ≥ 10% desde el máximo) = 53%** en un
  horizonte equivalente (~6,5 años).
- Lectura: la regla protege más las ganancias, pero con el perfil de H1 (rachas de hasta 14
  pérdidas, drawdowns de ~11%) es probable que detenga la estrategia alguna vez y requiera una
  revisión manual. Es el comportamiento buscado: obliga a reevaluar en vez de seguir operando.
