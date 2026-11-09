# Fase "Testnet + paper" — resultados (2026-10-30)

Plan pre-registrado: [fase-testnet-plan.md](fase-testnet-plan.md) (commit `12b3dbd`). Datos completos
de la comparación: [comparacion-modelos-y-regimen.md](comparacion-modelos-y-regimen.md). Sin
dinero real; H1 sin cambios; sin filtros nuevos.

## 1. Conciliación 233 ↔ 394 operaciones

Son experimentos distintos y no se mezclan: 233 = walk-forward de la Fase 2b (98 desarrollo + 135
confirmación, parámetros elegidos por ventana, RiskManager parcial); 394 = H1-fijo de la
auditoría (parámetros fijos, 2020-05 → 2026-10 continuo, RiskManager completo). Detalle,
reparto por tramo y solapes en el plan, §1.

## 2. ¿Qué aporta la ruptura? (1000 simulaciones por control)

| | Neto | PF | Sharpe | DD máx | Exposición media |
| --- | --- | --- | --- | --- | --- |
| **B** H1 (ruptura) | +102,3 | 1,40 | 0,85 | 10,9% | 5,9% |
| A1 tendencia + aleatoria i.i.d. (mediana) | +89,5 | 1,24 | 0,70 | 16,3% | — |
| A2 tendencia + señales desplazadas, con dependencia (mediana) | +69,0 | 1,23 | 0,63 | 14,9% | — |
| **C** tendencia + entrada inmediata | +84,7 | 1,14 | 0,55 | 18,4% | 13,2% |
| **D1** solo régimen, 1/3 por par, sin stops | +4061 | — | **1,11** | 64,6% | 54,0% |
| **D2** comprar y mantener escalado a la exposición de B | +64,3 | — | 0,94 | 10,3% | 5,9% |

**Tamaño del efecto de la ruptura** (B frente al control):

| Control | Δ neto (USDT) | Percentil de B (neto) | Percentil de B (PF) | z (neto / PF) |
| --- | --- | --- | --- | --- |
| A1 (i.i.d.) | +12,8 | 64,7% | 97,4% | +0,34 / +2,08 |
| A2 (respeta la dependencia temporal) | +33,4 | 82,0% | 92,5% | +0,80 / +1,45 |

Lectura:
- La ruptura mejora el PF frente a entradas aleatorias, pero **el efecto económico es pequeño**
  (+13 a +33 USDT en 6,5 años, unos 2–5 USDT/año con 200 USDT) y, con la aleatorización que
  respeta la dependencia temporal (A2), **no es significativo al 95%** (B queda en el percentil
  82 en neto y 92,5 en PF).
- Entrar en cada vela con tendencia (C) es peor: algo de selectividad sí ayuda, sobre todo
  porque reduce operaciones y costos.
- **El filtro de tendencia es la parte con más valor**: D1 (solo régimen, totalmente invertido,
  sin stops) tiene mejor Sharpe (1,11) y Calmar (0,93) que H1 (0,85 y 0,60), con un drawdown
  enorme si no se escala (64,6%). La rentabilidad por unidad de capital expuesto es parecida en
  ambos (≈ 100–110% anual aproximado), así que **la ventaja de H1 es controlar el tamaño, no
  elegir mejores momentos**. Esto es un diagnóstico de un solo recorrido histórico, no una
  propuesta: no se cambia H1.
- **El kill switch con base en el máximo detendría casi cualquier variante**: B (2021-12-24),
  C (2021-07-13) y el 97–99% de las simulaciones aleatorias.

## 3. Régimen (descriptivo; 368 de 394 operaciones clasificables por falta de EMA200 al inicio)

- H1 gana cuando el activo está en tendencia alcista (PF 1,64; 276 operaciones) y con
  volatilidad baja (PF 1,74); pierde en lateral (PF 0,80) y empata con volatilidad alta
  (PF 1,00).
- **2026** pierde en casi todos los grupos: alcista (18 operaciones, PF 0,27), lateral (7, PF 0)
  y toda con volatilidad baja (28, PF 0,29). **2021** pierde con volatilidad alta (90 operaciones,
  PF 0,66) aunque la tendencia era alcista.
- Conclusión: el mal 2026 **no se explica por un único régimen** de esta clasificación; las celdas
  tienen pocas operaciones. No se convierte en filtro (sería optimizar sobre los mismos datos).

## 4. Ejecución: escenarios de fallo (todos con test automático)

| Escenario | Comportamiento verificado | Test |
| --- | --- | --- |
| Binance no responde | ninguna orden; entradas bloqueadas hasta reconciliar | `test_exchange_down_sends_no_orders_until_reconciled` |
| Timeout tras enviar | consulta por `clientOrderId` antes de reintentar: 1 sola compra | `test_timeout_after_send_is_checked_before_retry`, `test_lost_response_does_not_duplicate_order` |
| Stop rechazado | venta inmediata **confirmada** (FILLED) y operación registrada | `test_rejected_stop_closes_and_verifies` |
| Stop rechazado y venta no confirmada | emergencia persistente, entradas pausadas, reintento con el mismo ID (sin doble venta) | `test_rejected_stop_and_unconfirmed_sale_becomes_emergency` |
| Datos incompletos de la API | pausa con motivo «datos incompletos»; sin entradas | `test_incomplete_api_data_pauses` |
| Reinicio durante una ejecución | la posición se reconstruye desde el exchange; sin recompra | `test_restart_mid_execution_rebuilds_from_exchange` |
| Compra huérfana sin stop tras reinicio | cierre de emergencia verificado | `test_restart_with_orphan_entry_without_stop_closes_it` |
| Dos procesos simultáneos | el segundo no arranca (bloqueo consultivo de PostgreSQL) | `test_two_processes_cannot_trade_the_same_account` |
| Kill switch con órdenes abiertas | posiciones cerradas y **cero** órdenes propias abiertas | `test_kill_switch_cancels_every_order_and_reconciles_to_zero` |
| Conexión perdida durante el cierre | emergencia que sobrevive al reinicio y se resuelve al volver la conexión | `test_connection_lost_during_close_keeps_emergency_and_retries` |

Además: una orden aceptada no cuenta como cierre hasta que el exchange la devuelve FILLED con la
cantidad completa; toda respuesta se valida; las señales procesadas tarde (más de una vela
después de su cierre, p. ej. tras una caída) no abren posiciones en testnet/live.

**Tipo de stop: stop-market (`STOP_LOSS`)**, documentado en el plan §4: prioriza salir sobre el
precio. Riesgo asumido: deslizamiento en huecos (modelado en el backtest con llenado a la
apertura).

Estos tests usan un exchange simulado. La validación en el exchange real de pruebas es la
testnet (criterios técnicos en el plan §5).

## 4b. Pruebas en el servidor (criterios técnicos del plan, §5)

**Reinicios forzados: 2 de 2 superados (2026-11-09, 14:47 y 14:48 UTC).** Se mató el proceso con
`kill -9` en la EC2 de São Paulo. En ambos casos Docker lo levantó en menos de 1 s, el estado de
`paper` y `testnet` quedó idéntico (mismo hash, misma última vela, ninguna vela procesada dos
veces), se recuperó el bloqueo de cuenta y la testnet volvió a estar operativa en unos 20 s. Límite
de esta evidencia: no había posiciones abiertas; el reinicio con una posición abierta forma parte
del simulacro de kill switch.

**Simulacro de kill switch:** comando `testnet-drill`. Cuenta lógica y prefijo de órdenes propios
(no afecta a la prueba de dos semanas). Abre una posición por el camino normal (RiskManager,
compra, stop), reinicia con la posición abierta, dispara el kill switch y verifica en el exchange
cero órdenes, venta FILLED y stop cancelado. Deja su informe en `reports/`.

**Fallo encontrado al construir el simulacro (corregido el 2026-11-09).** En `ExchangeTrader` y
`PaperTrader`, cualquier evento del RiskManager lanzaba `TypeError` al escribirse en el log (`event` es un nombre
reservado de structlog), antes de llamar a `_kill`. El kill switch quedaba activo pero **no cerraba
las posiciones**. El test anterior llamaba a `_kill` directamente y no lo detectó; ahora hay una
regresión por el bucle real de velas
(`test_kill_switch_through_the_real_candle_loop_closes_everything`). No llegó a ocurrir: la testnet
no ha tenido posiciones ni eventos de riesgo. El paper trading tenía el mismo error en su registro
y también se corrigió (`bot.log.event_fields`).

## 5. Credenciales

- El bot solo usa `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET` para la testnet; nunca
  las claves de producción.
- `execution.testnet_url` debe contener «testnet» (validación de configuración); cualquier otro
  host exige el modo live completo y se rechaza.
- Todos los secretos del entorno se registran al arrancar y se sustituyen por `***` en cualquier
  log, incluidas trazas de excepciones y logs de librerías (tests en `test_security.py`).
- `.env` está ignorado por git; `.env.example` no lleva valores (test).

### Cómo configurar la testnet sin compartir las claves

1. Entra en <https://testnet.binance.vision> con tu cuenta de GitHub y genera una clave **HMAC**.
   (Las claves de testnet no permiten retiros ni tocan fondos reales; no reutilices ninguna clave
   de producción.)
2. En la carpeta del proyecto, copia `.env.example` a `.env` si no existe y rellena solo
   `BINANCE_TESTNET_API_KEY=` y `BINANCE_TESTNET_API_SECRET=`.
3. Verifica sin mostrar las claves:
   `docker compose run --rm bot python -m bot.main testnet-check`
4. Reinicia el bot: `docker compose up -d --build`. El ejecutor de testnet arranca solo si las
   claves existen; desde ese momento empiezan las dos semanas.

## 6. Paper trading prospectivo

- **Es prospectivo**: empezó el 2026-10-30 a las 16:00 UTC y solo procesa velas de 4 h que cierran
  **después** de esa hora; no reprocesa el pasado. La equivalencia exacta con el backtest se
  verificó por separado (test con datos históricos), que es una prueba distinta.
- **Consistencia de velas y tiempos**: solo velas cerradas (fin ≤ última vela cerrada según el
  reloj UTC); el régimen usa la última vela diaria cerrada; las velas faltantes se rellenan por
  REST antes de procesar; cada vela se procesa una sola vez (marca persistente de la última
  procesada); timestamps en UTC.
- **Evidencia**: cada vela procesada deja un evento `bar_processed` con la hora de proceso y el
  retraso respecto a su cierre (marca `catch_up` si se procesó con más de una vela de retraso,
  p. ej. tras apagar el PC).
- **Interrupciones**: latido persistente cada minuto; al arrancar se registra cualquier
  interrupción mayor de 3 minutos con su duración. Reloj contra Binance cada ~10 minutos.
  Recuperación: el contenedor se reinicia solo (`restart: unless-stopped`) y retoma el estado.
- Pendiente para cuando se migre a un VPS (no ahora): alertas externas por desconexión
  (Telegram, Fase 6).
