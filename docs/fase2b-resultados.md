# Fase 2b — Resultados (2026-10-30 15:43 UTC)

Protocolo pre-registrado: [fase2b-protocolo.md](fase2b-protocolo.md). Criterios: ≥ 100 operaciones, PF ≥ 1,3, drawdown ≤ 8%, últimos 3 meses > 0, en desarrollo **y** en confirmación.

**Resultado: ninguna hipótesis pasa**

## Etapa: desarrollo (fuera de muestra 2024-05-01 → 2026-10-30)

| Hipótesis | Ops | PF | DD máx | Últ. 3 meses | Neto USDT | Ganadoras | R medio | Criterios (ops, PF, DD, 3m) | Veredicto |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| H1 | 98 | 1.57 | 5.62% | +14.14 | +29.98 (+15.0%) | 34% | +0.299 | ❌ ✅ ✅ ✅ | no pasa |
| H2 | 241 | 0.44 | 17.36% | -3.34 | -33.36 (-16.7%) | 52% | -0.149 | ✅ ❌ ❌ ❌ | no pasa |
| H3 | 301 | 1.00 | 9.53% | +8.18 | -0.25 (-0.1%) | 47% | +0.005 | ✅ ❌ ❌ ✅ | no pasa |

- **H1**: cierres → stop: 27, stop_trailing: 71; por par → BTCUSDT: +7.34, ETHUSDT: +3.88, SOLUSDT: +18.76; señales 162, rechazadas {'maximo_de_posiciones': 63, 'por_debajo_del_minimo': 1}
- **H2**: cierres → fin_de_datos: 1, objetivo_media: 168, stop: 46, stop_tiempo: 26; por par → BTCUSDT: -13.08, ETHUSDT: -14.14, SOLUSDT: -6.15; señales 325, rechazadas {'maximo_de_posiciones': 76, 'por_debajo_del_minimo': 8}
- **H3**: cierres → fin_de_datos: 1, objetivo_media: 140, stop: 67, stop_tiempo: 22, stop_trailing: 71; por par → BTCUSDT: -1.27, ETHUSDT: -10.96, SOLUSDT: +11.98; señales 467, rechazadas {'maximo_de_posiciones': 161, 'por_debajo_del_minimo': 5}

Parámetros elegidos en cada entrenamiento:

- H1: 2024-05 → {'regime_ema': 100, 'breakout_n': 20, 'trail_atr': 4.0} (train n=36, PF=3.30); 2024-08 → {'regime_ema': 50, 'breakout_n': 20, 'trail_atr': 4.0} (train n=24, PF=2.96); 2024-11 → {'regime_ema': 100, 'breakout_n': 40, 'trail_atr': 3.0} (train n=24, PF=1.05); 2025-02 → {'regime_ema': 100, 'breakout_n': 40, 'trail_atr': 4.0} (train n=22, PF=1.99); 2025-05 → {'regime_ema': 100, 'breakout_n': 40, 'trail_atr': 4.0} (train n=15, PF=2.46); 2025-08 → {'regime_ema': 50, 'breakout_n': 40, 'trail_atr': 3.0} (train n=21, PF=2.25); 2025-11 → {'regime_ema': 100, 'breakout_n': 40, 'trail_atr': 3.0} (train n=31, PF=2.75); 2026-02 → {'regime_ema': 100, 'breakout_n': 40, 'trail_atr': 3.0} (train n=14, PF=3.25); 2026-05 → {'regime_ema': 50, 'breakout_n': 40, 'trail_atr': 3.0} (train n=12, PF=0.06); 2026-08 → {'regime_ema': 50, 'breakout_n': 40, 'trail_atr': 3.0} (train n=15, PF=0.32)
- H2: 2024-05 → {'regime_ema': 50, 'rsi_below': 10.0, 'max_bars': 6} (train n=41, PF=1.14); 2024-08 → {'regime_ema': 50, 'rsi_below': 10.0, 'max_bars': 6} (train n=30, PF=1.10); 2024-11 → {'regime_ema': 100, 'rsi_below': 10.0, 'max_bars': 12} (train n=30, PF=0.68); 2025-02 → {'regime_ema': 100, 'rsi_below': 20.0, 'max_bars': 6} (train n=86, PF=0.48); 2025-05 → {'regime_ema': 100, 'rsi_below': 20.0, 'max_bars': 12} (train n=75, PF=0.57); 2025-08 → {'regime_ema': 100, 'rsi_below': 10.0, 'max_bars': 6} (train n=31, PF=0.71); 2025-11 → {'regime_ema': 50, 'rsi_below': 20.0, 'max_bars': 12} (train n=105, PF=0.54); 2026-02 → {'regime_ema': 100, 'rsi_below': 20.0, 'max_bars': 12} (train n=66, PF=0.48); 2026-05 → {'regime_ema': 50, 'rsi_below': 20.0, 'max_bars': 6} (train n=26, PF=0.29); 2026-08 → {'regime_ema': 100, 'rsi_below': 20.0, 'max_bars': 12} (train n=17, PF=0.60)

A confirmación: H1 (ver enmiendas del protocolo).

## Etapa: confirmacion (fuera de muestra 2020-08-01 → 2023-09-30)

| Hipótesis | Ops | PF | DD máx | Últ. 3 meses | Neto USDT | Ganadoras | R medio | Criterios (ops, PF, DD, 3m) | Veredicto |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| H1 | 135 | 1.45 | 10.02% | -3.90 | +39.81 (+19.9%) | 34% | +0.283 | ✅ ✅ ❌ ❌ | no pasa |

- **H1**: cierres → stop: 52, stop_trailing: 83; por par → BTCUSDT: +20.59, ETHUSDT: +2.59, SOLUSDT: +16.64; señales 242, rechazadas {'maximo_de_posiciones': 86, 'por_debajo_del_minimo': 21}

Parámetros elegidos en cada entrenamiento:

- H1: 2020-08 → {'regime_ema': 100, 'breakout_n': 40, 'trail_atr': 3.0} (train n=14, PF=7.06); 2020-11 → {'regime_ema': 50, 'breakout_n': 40, 'trail_atr': 4.0} (train n=22, PF=3.29); 2021-02 → {'regime_ema': 50, 'breakout_n': 40, 'trail_atr': 4.0} (train n=28, PF=2.52); 2021-05 → {'regime_ema': 50, 'breakout_n': 40, 'trail_atr': 4.0} (train n=29, PF=4.07); 2021-08 → {'regime_ema': 50, 'breakout_n': 20, 'trail_atr': 4.0} (train n=23, PF=2.27); 2021-11 → {'regime_ema': 50, 'breakout_n': 40, 'trail_atr': 4.0} (train n=21, PF=2.86); 2022-02 → {'regime_ema': 100, 'breakout_n': 40, 'trail_atr': 4.0} (train n=25, PF=1.56); 2022-05 → {'regime_ema': 100, 'breakout_n': 40, 'trail_atr': 3.0} (train n=14, PF=0.38); 2022-08 → {'regime_ema': 50, 'breakout_n': 40, 'trail_atr': 3.0} (train n=12, PF=0.50); 2022-11 → {'regime_ema': 50, 'breakout_n': 40, 'trail_atr': 3.0} (train n=16, PF=0.07); 2023-02 → {'regime_ema': 50, 'breakout_n': 40, 'trail_atr': 4.0} (train n=16, PF=3.28); 2023-05 → {'regime_ema': 50, 'breakout_n': 20, 'trail_atr': 4.0} (train n=21, PF=4.30); 2023-08 → {'regime_ema': 100, 'breakout_n': 20, 'trail_atr': 4.0} (train n=30, PF=1.46)

Archivos: `reports/research-20261030-154340`

## Análisis complementario de H1 (no cambia el veredicto)

| | Desarrollo | Confirmación |
| --- | --- | --- |
| Drawdown máx. (desde máximos) | 5,62%: 206,12 → 194,54 (may–sep 2024) | 10,02%: 259,75 → 233,74 (sep 2021 → ene 2023) |
| Capital mínimo (inicio 200) | 194,54 | 194,88 |
| Resultado por año (ops, USDT) | 2024: 38, −0,65 · 2025: 37, +20,84 · 2026: 23, +9,79 | 2020: 27, +21,11 · 2021: 52, +22,89 · 2022: 18, −9,63 · 2023: 38, +5,45 |
| R total / 5 mejores operaciones | 29,3R / 38,5R | 38,2R / 43,5R |
| Ganadoras | 34% | 33% |

- El kill switch (180 USDT) y la alerta de drawdown (188 USDT) no se habrían activado.
- El resultado depende de pocas tendencias grandes: sin las 5 mejores operaciones, H1 pierde.
- Rentabilidad aproximada: +6–7% anual sobre el capital con riesgo de 0,5% por operación.

**Veredicto según el protocolo y su enmienda 1: H1 no pasa la confirmación** (drawdown 10,02% >
8% y últimos 3 meses del holdout −3,90 USDT). Recomendación pre-registrada: detener el proyecto,
salvo decisión explícita de Edwin de cambiar el criterio de drawdown.
