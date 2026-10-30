# Auditoría de H1 — conclusiones

Fecha: 2026-10-30. Documentos: [plan pre-registrado](auditoria-h1-plan.md) (commit `7136ac6`,
antes de cualquier prueba) y [resultados generados](auditoria-h1-resultados.md); datos en
[`auditoria/`](auditoria/). H1 no se modificó; ningún criterio se relajó.

## Veredicto

**Ventaja estadística y económica reproducible: NO DEMOSTRADA.** H1 cumple 6 de las 8 condiciones
pre-registradas y falla dos que son decisivas:

- **B — Sharpe deflactado 0,17** (se exigía > 0,95). Tras probar 81 configuraciones en total
  (H0–H3), un Sharpe anual de 0,85 en 6,5 años es compatible con haber encontrado una
  estrategia buena por azar. El umbral equivalente sería un Sharpe anual ≈ 1,20.
- **F — universo ampliado PF 0,96.** En 13 monedas grandes de enero de 2020 que nunca se habían
  mirado, H1 pierde (−10,97 USDT, drawdown 23%). La ventaja no se generaliza fuera de BTC, ETH y
  SOL, el universo que se eligió en 2026 sabiendo que esas tres sobrevivieron.

## Qué tiene H1 y qué no

**Robusto:**
- Los costos no son el problema. El deslizamiento de equilibrio es 0,45% por lado (9 veces el
  modelado); con deslizamiento doble, PF 1,34. El spread actual de BTC/ETH/SOL es ≤ 0,01%.
  Funding: no aplica (spot sin apalancamiento).
- No es un pico de sobreajuste: el 99% de las 180 combinaciones vecinas tiene PF > 1, y los
  parámetros del plan están en la mitad de la tabla (puesto 116 de 180).
- No hay anticipación en el código: truncar los datos en tres fechas deja idénticas las
  operaciones anteriores al corte, y retrasar la entrada una vela no la empeora (PF 1,44).
- Mejora a entradas aleatorias con las mismas salidas (p = 0,016 con filtro de régimen).

**Débil:**
- **La mayor parte del resultado es el filtro de tendencia, no la señal.** Entradas aleatorias
  cuando la tendencia diaria es alcista, con las mismas salidas, logran un PF mediano de 1,23 y
  +88 USDT, frente al PF 1,40 y +102 USDT de H1. H1 es sobre todo una forma prudente de tener
  exposición a la tendencia de cripto.
- **No mejora a comprar y mantener en eficiencia de riesgo.** Su Sharpe es 0,85 frente a 0,94.
  Su ventaja es el drawdown (10,9% contra 90%), pero se obtiene con una exposición media de solo
  el 5,9% del capital. Comprar y mantener reducido a esa exposición da Sharpe 0,94, CAGR 4,4% y
  drawdown 10,3%, muy parecido a H1 (CAGR 6,6%, drawdown 10,9%).
- **Concentración extrema.** El 10% de mejores operaciones aporta el 248% del resultado neto.
  Sin las 10 mejores (de 394), H1 pierde (−21,93 USDT).
- **Riesgo para los criterios del plan.** En el bootstrap de operaciones, P(drawdown > 8%) = 82%
  y **P(capital ≤ 180, kill switch) = 11%**. Racha máxima observada: 14 pérdidas seguidas.
- **2026 es su peor año:** 28 operaciones, PF 0,29, −21,66 USDT. 2021 también fue negativo.

## Revisión cronológica y de sesgos (punto 5)

1. **Orden de los periodos.** La confirmación (2020-08 → 2023-09) es *anterior* al desarrollo
   (2024-05 → 2026-10). No hay fuga de información de un periodo a otro en el código (prueba de
   truncado), pero sí en el investigador: se sabía cómo fue 2020–2023 en cripto (alcista
   2020–21, bajista 2022). Un holdout del pasado es más débil que uno del futuro; por eso el
   paper trading es la única prueba realmente fuera de muestra.
2. **Supervivencia del universo.** BTC, ETH y SOL se eligieron hoy; SOL pasó de 1,5 a 250 USD.
   La prueba con las monedas grandes de enero de 2020 (incluidas XMR, EOS y otras que luego
   decayeron) da PF 0,96. Es la señal más clara de que parte del resultado es sesgo de selección.
3. **Hipótesis informadas por literatura posterior.** El momentum en cripto se publicó con datos
   que se solapan con este periodo; elegir "momentum diario" tiene algo de conocimiento previo.
4. **Pruebas múltiples.** Se probaron H0 (64 combinaciones), H1 (8), H2 (8) y H3; H1 se eligió
   *después* de ver que H0, H2 y H3 fallaban. El Sharpe deflactado lo corrige y H1 no lo supera.
5. **Reglas del exchange actuales aplicadas a 2020.** Se usan los filtros de exchangeInfo de hoy
   (NOTIONAL 5 USDT; en 2020 era 10 USDT). Con 10 USDT, algunas posiciones pequeñas no habrían
   sido posibles; el efecto es menor pero va en contra de la estrategia.
6. **Sin anticipación en datos:** velas cerradas, régimen de la última diaria cerrada, Donchian
   sin la vela actual y entrada en la apertura siguiente (verificado con tests).

## Qué significa y siguiente paso

- **No hay base para dinero real.** No se autoriza ninguna operación real; la capa de IA sigue
  aplazada.
- El paper trading de H1-fijo **ya está corriendo** (desde 2026-10-30 16:00 UTC) con el
  RiskManager completo, y se mantendrá 8–12 semanas sin cambiar nada. Es la única evidencia
  realmente fuera de muestra. Con ~60 operaciones/año esperadas, 12 semanas darán ≈ 14
  operaciones: suficiente para validar la operativa, no para demostrar ventaja.
- El ejecutor de Spot Testnet (Fase 4) está listo y probado contra un exchange simulado; para
  correrlo hacen falta claves de testnet.
- Cualquier decisión futura de usar dinero real exige una nueva evaluación y aprobación expresa.
