# Fase "Testnet + paper" — plan pre-registrado

Fecha: 2026-10-30. Registrado **antes** de ejecutar los análisis que describe. Autorización de
Edwin: Spot Testnet durante dos semanas y paper trading; **sin dinero real**; sin nuevos filtros
ni optimización sobre los mismos datos.

## 1. Conciliación de operaciones (hecha antes de este plan)

| Experimento | Parámetros | RiskManager | Periodo | Operaciones |
| --- | --- | --- | --- | --- |
| Fase 2b, walk-forward desarrollo | elegidos por ventana | subconjunto Fase 2 | 2024-05 → 2026-10 | 98 |
| Fase 2b, walk-forward confirmación | elegidos por ventana | subconjunto Fase 2 | 2020-08 → 2023-09 | 135 |
| Mismo walk-forward, RiskManager completo | elegidos por ventana | completo | ídem | 97 + 138 |
| **Auditoría H1-fijo** | por defecto (fijos) | completo | 2020-05 → 2026-10 | **394** |

Las 394 de H1-fijo se reparten así: 20 en 2020-05 → 2020-08 (antes de la confirmación),
196 en la confirmación, 51 entre etapas (2023-10 → 2024-05, que en el walk-forward era
calentamiento y entrenamiento) y 127 en el desarrollo. En las mismas ventanas H1-fijo opera más,
porque la ruptura N = 20 es más frecuente que la N = 40 que eligió casi siempre el walk-forward.
Coinciden 65/98 (desarrollo) y 74/135 (confirmación) operaciones exactas (mismo par y hora).
No hay duplicados ni solapes. Las estadísticas de cada experimento no se mezclan.

## 2. Comparación controlada de modelos (diagnóstico; no elige nada)

Comunes: BTC/ETH/SOL, 2020-05-01 → 2026-10-30, velas de 4 h, régimen diario = cierre > EMA100
diaria (el filtro de H1), costos 0,1% + 0,05% por lado, RiskManager completo. Para comparar
recorridos completos se usa el kill switch con base en el **capital inicial** (con base en el
máximo, la primera caída del 10% detendría cada modelo); la fecha en que saltaría con base en el
máximo se reporta aparte para cada modelo.

| Modelo | Entrada | Salida |
| --- | --- | --- |
| **A1** tendencia + aleatoria i.i.d. | con régimen alcista, probabilidad por vela = tasa de señales de B; 1000 semillas | stop 2×ATR (tope 10%) y trailing 3×ATR, igual que H1 |
| **A2** tendencia + aleatoria con dependencia | las señales de B desplazadas circularmente dentro de las velas con régimen alcista de cada par (conserva número y agrupamiento de señales); 1000 desplazamientos | igual que H1 |
| **B** H1 | ruptura del máximo de 20 velas con régimen alcista | igual que H1 |
| **C** tendencia + entrada inmediata | en cada vela con régimen alcista y sin posición | igual que H1 |
| **D1** régimen puro (comprar y mantener mientras hay tendencia) | 1/3 del capital por par mientras el régimen es alcista | al cerrar la primera vela de 4 h con el régimen bajista |
| **D2** comprar y mantener escalado | 1/3 por par desde el inicio, escalado a la exposición media de B | — |

Métricas por modelo: resultado neto, CAGR, Sharpe, Sortino, drawdown máximo, Calmar, tiempo en
mercado, exposición media, **rentabilidad sobre el capital expuesto** (resultado / capital medio
invertido, anualizada), cuantiles de R (p10, p50, p90) y dependencia de las 10 mejores
operaciones. Para A1 y A2: mediana, p5 y p95 de cada métrica; **tamaño del efecto** de B frente a
A (diferencia en USDT y en PF, percentil de B dentro de A, z = (B − media A) / desviación A).

## 3. Análisis por régimen (descriptivo; no se convierte en parámetros)

Cada operación de H1-fijo se clasifica con la última vela diaria cerrada antes de la entrada:

- **Tendencia del activo**: *alcista* si cierre > EMA200 y la EMA50 sube respecto a 20 días
  antes; *bajista* si cierre < EMA200 y la EMA50 baja; *lateral* en otro caso.
- **Tendencia del mercado**: la misma regla aplicada a BTC.
- **Volatilidad**: ATR(14) diario en % del precio; *alta* si supera la mediana de ese activo en
  todo el periodo, *baja* si no (la mediana usa todo el periodo: es descriptivo, no operable).

Se reporta, por régimen, por año y por año × régimen: operaciones, resultado, PF y R medio, con
foco en 2026. Ningún resultado se usará para filtrar ni ajustar H1.

## 4. Ejecución: escenarios de fallo exigidos y tipo de stop

Pruebas automáticas (exchange simulado con fallos inyectados), además de las existentes:

| Escenario | Comportamiento exigido |
| --- | --- |
| Binance no responde | no se envían órdenes nuevas; entradas en pausa hasta reconciliar |
| Timeout tras enviar | consultar el estado antes de reintentar (sin duplicados) |
| Stop rechazado | cerrar la posición **y verificar** el cierre |
| Datos incompletos de la API | pausar operaciones |
| Reinicio durante una ejecución | reconstruir el estado desde el exchange |
| Dos procesos simultáneos | uno solo puede operar una cuenta (bloqueo en PostgreSQL) |
| Kill switch con órdenes abiertas | cancelar todas y reconciliar hasta cero órdenes |
| Conexión perdida durante el cierre | estado de emergencia persistente, reintento controlado, sin entradas nuevas |

Una orden aceptada no es una posición cerrada: un cierre solo se da por hecho cuando el exchange
devuelve la orden FILLED con la cantidad completa.

**Tipo de stop: stop-market (`STOP_LOSS`).** Al activarse se ejecuta a mercado: garantiza la
salida, con riesgo de deslizamiento en huecos. El stop-limit fija el precio, pero en un
movimiento brusco puede no llenarse y dejar la posición sin protección. Para un sistema cuyo
objetivo es limitar pérdidas, se prioriza salir. El backtest ya modela el hueco (llenado a la
apertura).

## 5. Criterios de la testnet (dos semanas) — técnicos, no financieros

La testnet valida integración, no rentabilidad ni calidad de ejecución (su libro de órdenes y su
liquidez no son los de producción).

| Criterio técnico | Umbral |
| --- | --- |
| Excepciones no controladas | 0 |
| Posiciones sin stop abierto en alguna reconciliación (cada 5 min) | 0 |
| Órdenes duplicadas (mismo propósito y vela) | 0 |
| Reinicios forzados durante la prueba sin duplicados ni pérdida de estado | ≥ 2 |
| Diferencias de reconciliación no resueltas o sin pausa | 0 |
| Desfase de reloj > 1 s sin pausa | 0 |
| Simulacro de kill switch: todas las órdenes canceladas y posiciones cerradas | 1 realizado |

Criterios financieros: **no se evalúan en testnet**. La evidencia de estrategia sigue siendo el
paper trading prospectivo, que tampoco basta por sí solo (≈ 14 operaciones en 12 semanas).

## 6. Credenciales

Claves exclusivas de Spot Testnet, sin retiros, nunca de producción; solo en `.env`
(ignorado por git). Nunca se envían por chat. El bot solo lee `BINANCE_TESTNET_*` para la testnet,
exige que la URL de ejecución sea de testnet y redacta cualquier secreto que aparezca en logs.
