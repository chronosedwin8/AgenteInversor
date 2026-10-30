# Fase 2b — Replanteamiento de la estrategia (protocolo pre-registrado)

Fecha: 2026-10-30. Este documento se sube al repositorio **antes** de ejecutar ningún backtest
de las hipótesis nuevas; las reglas, rejillas y criterios no se cambian después de ver
resultados. Si ninguna hipótesis pasa, la recomendación es detener el proyecto.

## Por qué falló la estrategia original (H0)

Seguimiento de tendencia con retrocesos en velas de 15 min (PLAN.md). Walk-forward
2024-05 → 2026-10: profit factor 0,63, drawdown 16,4%; **sin costos el PF es 0,61**, y ninguna
de las 64 combinaciones superó PF 1 en ninguna ventana de entrenamiento. Conclusión: en 15 min
no hay ventaja bruta, y además los costos (≈0,3% ida y vuelta) pesan ≈0,2R por operación.

## Qué dice la evidencia

- Momentum de serie temporal en cripto: significativo a horizontes diarios y semanales
  (Liu & Tsyvinski, *Review of Financial Studies* 2021; Han, Kang & Ryu, con costos). Se
  concentra en mercados alcistas → compatible con solo largos.
- A frecuencia horaria o menor, la ventaja suele desaparecer tras costos (Bysik & Ślepaczuk
  2026; estudio de reversión a 15 min de 2026) y aparece reversión a la media.
- Comprar mínimos de corto plazo complementa a la tendencia en BTC (Quantpedia, en muestra y
  sin costos: evidencia débil).

Por eso las hipótesis nuevas operan en **velas de 4 h con filtro de tendencia diario** y
permiten **mantener posiciones varios días** (se elimina el cierre intradía de 12 h; Edwin
delegó la decisión el 2026-10-30).

## Reglas comunes (sin cambios respecto al plan, salvo lo indicado)

- Binance Spot, solo largos, sin apalancamiento. Universo: BTCUSDT, ETHUSDT, SOLUSDT.
- Capital 200 USDT; riesgo 0,5% por operación (comisiones incluidas en el tamaño); tope 40% por
  posición; máximo 2 posiciones, 1 por activo; tamaño mínimo 2 × NOTIONAL + 10%.
- Costos: 0,1% por lado + deslizamiento 0,05% en cada llenado.
- **Cambio:** entrada a mercado en la apertura de la vela siguiente a la señal (en una ruptura,
  una orden límite solo se llenaría en las rupturas fallidas). **Cambio:** sin máximo de 12 h.
- Stop en el exchange; si la vela abre por debajo del stop, se llena a la apertura. Si en una
  vela se tocan stop y objetivo, se asume el stop.
- Señal en velas de 4 h cerradas; régimen con la última vela diaria **cerrada**.
- Walk-forward: 1 mes de calentamiento, 6 de entrenamiento, 3 de validación, paso de 3 meses.
  En entrenamiento se elige la combinación con mayor profit factor entre las que tienen
  ≥ 10 operaciones (empate: mayor resultado neto); si ninguna, los valores por defecto.

## Hipótesis

### H1 — Ruptura de 4 h a favor de la tendencia diaria (momentum)

- Régimen: cierre diario > EMA(R) diaria. R ∈ {50, 100}; por defecto 100.
- Entrada: cierre de 4 h > máximo de las N velas de 4 h anteriores. N ∈ {20, 40}; por defecto 20.
- Stop inicial: entrada − 2 × ATR(14, 4 h), como mucho 10% por debajo.
- Salida: trailing = máximo alcanzado desde la entrada − K × ATR(14, 4 h) actual; solo sube.
  K ∈ {3, 4}; por defecto 3. Sin toma parcial ni stop de tiempo.
- Rejilla: 8 combinaciones.

### H2 — Compra de sobreventa de corto plazo dentro de la tendencia diaria (reversión)

- Régimen: cierre diario > EMA(R) diaria. R ∈ {50, 100}; por defecto 100.
- Entrada: RSI(3) de 4 h < T. T ∈ {10, 20}; por defecto 20.
- Stop inicial: entrada − 2,5 × ATR(14, 4 h), como mucho 10% por debajo.
- Salida: cierre de 4 h > EMA(5) de 4 h, o stop de tiempo tras M velas. M ∈ {6, 12}; por
  defecto 12.
- Rejilla: 8 combinaciones.

### H3 — Combinación H1 + H2

- En cada vela y activo se evalúa H1 y, si no hay señal, H2. Cada posición se gestiona con las
  reglas de salida de la hipótesis que la abrió. Comparten capital y RiskManager.
- Sin selección propia: en cada ventana usa los parámetros que H1 y H2 eligieron en su propio
  entrenamiento.

## Datos y validación en dos etapas

1. **Desarrollo:** walk-forward sobre oct-2023 → oct-2026 (validación 2024-05 → 2026-10).
2. **Confirmación (holdout nunca mirado):** el mismo walk-forward sobre ene-2020 → sep-2023
   (SOLUSDT desde su listado en ago-2020). Se descarga ahora y no se usa para nada más.

Una hipótesis **pasa** si cumple en **ambas** etapas los criterios del plan: ≥ 100 operaciones
fuera de muestra, profit factor ≥ 1,3 (neto de costos), drawdown máximo ≤ 8% y resultado
positivo en los últimos 3 meses fuera de muestra. Si pasan varias, se elige la de menor
drawdown en la confirmación. Se prueban 4 hipótesis en total (H0–H3): con varias pruebas,
alguna puede pasar por azar; por eso la confirmación en datos no vistos es obligatoria, y aun
así el paso a dinero real exige el paper trading de la Fase 7.

## Fuentes

- Liu, Tsyvinski (2021), *Risks and Returns of Cryptocurrency*, RFS 34(6).
  https://www.nber.org/system/files/working_papers/w24877/w24877.pdf
- Han, Kang, Ryu — time-series momentum en cripto con costos.
  https://econpapers.repec.org/RePEc:eee:ecofin:v:57:y:2021:i:c:s1062940821000590
- Bysik, Ślepaczuk (2026), predicción horaria de BTC con costos.
  https://ideas.repec.org/p/arx/papers/2606.00060.html
- Reversión a corto plazo en cripto a 15 min (2026). https://arxiv.org/pdf/2608.21888
- Quantpedia, *Trend-following and Mean-reversion in Bitcoin*.
  https://quantpedia.com/trend-following-and-mean-reversion-in-bitcoin/
