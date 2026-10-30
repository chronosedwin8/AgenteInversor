# Auditoría de H1 — plan pre-registrado

Fecha: 2026-10-30. Registrado en el repositorio **antes** de ejecutar cualquier prueba de esta
auditoría. Objetivo (definido por Edwin): determinar si H1 tiene una ventaja estadística y
económica reproducible, **no** conseguir que pase los criterios. H1 es una estrategia
experimental; no se autorizan operaciones con dinero real.

Restricciones: no se modifican los parámetros originales de H1 ni se relajan criterios. La capa
de IA queda aplazada. Cada cambio queda en un commit.

## Objeto auditado

- **H1-fijo**: H1 con los parámetros por defecto pre-registrados en `docs/fase2b-protocolo.md`
  (régimen EMA100 diaria, ruptura del máximo de 20 velas de 4 h, stop 2×ATR con tope 10%,
  trailing 3×ATR). Como se fijaron antes de ver datos, su resultado en todo el periodo no es un
  ajuste en muestra. Es el objeto principal.
- **H1-WF**: H1 con la selección walk-forward ya ejecutada (desarrollo + confirmación). Se
  reporta para comparar.
- Periodo principal: 2020-05-01 → 2026-10-30 (BTC y ETH desde mayo de 2020, cuando la EMA100
  diaria está lista; SOL desde su listado en ago-2020). Universo: BTCUSDT, ETHUSDT, SOLUSDT.
- Mismo motor, costos (0,1% + 0,05% por lado) y, para todo lo que se ejecute después de su
  implementación, el **RiskManager completo** (Fase 3).

## Pruebas

1. **Concentración**: resultado excluyendo las k mejores operaciones (k = 1, 3, 5, 10), y
   cuota del resultado aportada por el 5% y el 10% superior de operaciones.
2. **Costos**: comisión 0,1% (y 0,075% con BNB), deslizamiento por lado 0,05% / 0,10% / 0,20%,
   y deslizamiento de equilibrio (resultado neto 0). Spread actual medido en el libro de órdenes.
   Funding: no aplica (spot, sin apalancamiento ni perpetuos); se documenta.
3. **Métricas de riesgo**: Sharpe, Sortino, Calmar (diarios, anualizados con 365 días, tasa libre
   de riesgo 0), CAGR, exposición (tiempo invertido y exposición media), distribución de R,
   rachas de pérdidas, peores operaciones, peor mes, duración máxima de drawdown. Comparación
   con comprar y mantener (BTC/ETH/SOL a partes iguales) y con comprar y mantener escalado a la
   exposición media de H1.
4. **Robustez**:
   - Sensibilidad (solo diagnóstico, no se eligen parámetros): EMA régimen {50, 75, 100, 150,
     200} × ruptura N {10, 15, 20, 30, 40, 60} × trailing K {2, 2,5, 3, 3,5, 4, 5}
     (180 combinaciones) y stop {1,5, 2, 2,5, 3}×ATR.
   - Bootstrap estacionario (Politis-Romano) de rendimientos diarios, bloque medio 10 días
     (también 5 y 20), 5000 remuestreos: intervalos de Sharpe y CAGR, P(Sharpe ≤ 0).
   - Bootstrap por bloques circulares de la secuencia de operaciones (bloques de 5), 5000:
     P(R medio ≤ 0), cuantiles de PF, P(PF < 1,3), P(drawdown > 8%), P(capital ≤ 180).
   - Entradas aleatorias (1000 simulaciones por variante), mismas salidas y límites de riesgo,
     misma frecuencia de señales: (a) solo cuando el régimen diario es alcista, (b) sin filtro.
   - Retraso de una vela de 4 h en la entrada.
   - Dejar fuera un par cada vez; resultados por año.
   - **Universo ampliado sin sesgo de supervivencia**: top 20 de CoinMarketCap del 2020-01-05
     sin stablecoins, sin tokens que no tenían par USDT en Binance (BSV, LEO, HT, 999) y sin
     BTC/ETH: XRP, BCH, LTC, EOS, BNB, XMR, XLM, TRX, ADA, XTZ, ATOM, NEO, LINK, con los datos
     que existan (incluidos pares luego deslistados). Nunca se han mirado con ninguna hipótesis.
   - Sharpe deflactado (Bailey & López de Prado) con N = 81 configuraciones probadas en total
     (H0: 64, H1: 8, H2: 8, H3: 1).
5. **Cronología y anticipación**: revisión escrita del orden de los periodos (la confirmación,
   2020–2023, es anterior al desarrollo, 2024–2026) y de sesgos (supervivencia del universo,
   conocimiento posterior, filtros de exchangeInfo actuales aplicados a 2020). Prueba
   programática: truncar los datos en varios puntos no cambia ninguna operación cerrada antes
   del corte.

## Regla de decisión (pre-registrada)

H1 se considera con **ventaja estadística y económica reproducible** solo si cumple TODO:

| # | Condición |
| --- | --- |
| A | Bootstrap de rendimientos diarios: P(Sharpe ≤ 0) < 5% con bloque medio 10 días. |
| B | Sharpe deflactado (N = 81): probabilidad > 95%. |
| C | Entradas aleatorias con filtro de régimen: PF de H1-fijo supera al 95% de simulaciones. |
| D | Con deslizamiento de 0,10% por lado (doble del modelo), PF > 1,0 y resultado neto > 0. |
| E | Sensibilidad: ≥ 70% de las 180 combinaciones con PF > 1,0. |
| F | Universo ampliado (sin BTC/ETH/SOL): PF > 1,0. |
| G | Retraso de una vela: PF > 1,0. |
| H | Calmar de H1-fijo ≥ Calmar de comprar y mantener en el mismo periodo. |

Si falla cualquiera, la conclusión es "ventaja no demostrada". Ninguna de estas pruebas
modifica H1. Independientemente del resultado, el paper trading (8–12 semanas, sin capital
real) se ejecuta como validación operativa; con ~45 operaciones/año esperadas, 12 semanas
darán ≈ 10 operaciones: suficiente para verificar la operativa, **no** para probar ventaja.
