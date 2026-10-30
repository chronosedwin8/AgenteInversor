# Backtest walk-forward — 2026-10-30 15:09 UTC

**Veredicto Fase 2: NO PASA**

| Criterio | Resultado | Valor |
| --- | --- | --- |
| operaciones | ✅ | 150 (mín. 100) |
| profit_factor | ❌ | 0.63 (mín. 1.3) |
| drawdown_max | ❌ | 16.41% (máx. 8.0%) |
| ultimos_3_meses | ✅ | +3.16 USDT (debe ser > 0) |

## Fuera de muestra (2024-05-01 → 2026-10-30)

### Resultado fuera de muestra

| Métrica | Valor |
| --- | --- |
| Operaciones | 150 |
| Ganadoras | 40 (26.7%) |
| Profit factor (neto de costos) | 0.63 |
| Resultado neto | -27.81 USDT (-13.90%) |
| Drawdown máximo | 16.41% |
| R medio por operación | -0.248 R |
| Comisiones pagadas | 21.00 USDT |
| Duración media | 3.3 h |
| Motivo de cierre final | cierre_bajo_ema20: 6, stop: 74, stop_tiempo: 32, stop_trailing: 27, tiempo_maximo: 11 |
| Resultado por par | BTCUSDT: -9.43, ETHUSDT: -13.45, SOLUSDT: -4.93 |

| Mes | Resultado (USDT) |
| --- | --- |
| 2024-05 | -0.04 |
| 2024-06 | -0.92 |
| 2024-07 | -1.02 |
| 2024-08 | -0.01 |
| 2024-09 | -0.44 |
| 2024-10 | -0.51 |
| 2024-11 | -0.81 |
| 2024-12 | -3.21 |
| 2025-01 | -1.15 |
| 2025-02 | -0.85 |
| 2025-03 | -3.11 |
| 2025-04 | -1.28 |
| 2025-05 | +0.98 |
| 2025-06 | -1.00 |
| 2025-07 | -4.80 |
| 2025-08 | +1.28 |
| 2025-09 | +2.77 |
| 2025-10 | -2.99 |
| 2025-11 | -0.67 |
| 2025-12 | -1.82 |
| 2026-01 | -0.90 |
| 2026-02 | -1.52 |
| 2026-03 | -4.03 |
| 2026-04 | -2.74 |
| 2026-05 | -0.36 |
| 2026-06 | -1.55 |
| 2026-07 | -0.27 |
| 2026-08 | +6.42 |
| 2026-09 | -2.52 |
| 2026-10 | -0.74 |

## Parámetros elegidos por ventana (solo con datos de entrenamiento)

| Validación | Entrenamiento: n / PF | Parámetros |
| --- | --- | --- |
| 2024-05 → 2024-08-01 | 37 / 0.72 | adx_min=25.0, stop_atr_multiple=2.0, partial_take_r=2.0, trailing_atr_multiple=3.0, volume_multiplier=1.0, enforce_entry_window=True |
| 2024-08 → 2024-11-01 | 44 / 0.63 | adx_min=25.0, stop_atr_multiple=1.5, partial_take_r=2.0, trailing_atr_multiple=2.0, volume_multiplier=1.2, enforce_entry_window=False |
| 2024-11 → 2025-02-01 | 47 / 0.63 | adx_min=25.0, stop_atr_multiple=2.0, partial_take_r=2.0, trailing_atr_multiple=3.0, volume_multiplier=1.0, enforce_entry_window=False |
| 2025-02 → 2025-05-01 | 33 / 0.90 | adx_min=25.0, stop_atr_multiple=2.0, partial_take_r=1.5, trailing_atr_multiple=3.0, volume_multiplier=1.2, enforce_entry_window=False |
| 2025-05 → 2025-08-01 | 15 / 0.79 | adx_min=25.0, stop_atr_multiple=2.0, partial_take_r=1.5, trailing_atr_multiple=3.0, volume_multiplier=1.2, enforce_entry_window=True |
| 2025-08 → 2025-11-01 | 33 / 0.47 | adx_min=25.0, stop_atr_multiple=1.5, partial_take_r=2.0, trailing_atr_multiple=2.0, volume_multiplier=1.2, enforce_entry_window=False |
| 2025-11 → 2026-02-01 | 44 / 0.78 | adx_min=25.0, stop_atr_multiple=1.5, partial_take_r=2.0, trailing_atr_multiple=2.0, volume_multiplier=1.2, enforce_entry_window=False |
| 2026-02 → 2026-05-01 | 51 / 0.99 | adx_min=25.0, stop_atr_multiple=1.5, partial_take_r=2.0, trailing_atr_multiple=2.0, volume_multiplier=1.0, enforce_entry_window=False |
| 2026-05 → 2026-08-01 | 18 / 0.58 | adx_min=25.0, stop_atr_multiple=2.0, partial_take_r=2.0, trailing_atr_multiple=2.0, volume_multiplier=1.0, enforce_entry_window=True |
| 2026-08 → 2026-10-30 | 29 / 0.51 | adx_min=25.0, stop_atr_multiple=2.0, partial_take_r=2.0, trailing_atr_multiple=2.0, volume_multiplier=1.2, enforce_entry_window=False |

## Referencia: parámetros del plan sobre todo el periodo (2023-10-01 → 2026-10-30)

No cuenta para el criterio (no es fuera de muestra); sirve de comparación.

### Parámetros del plan, periodo completo

| Métrica | Valor |
| --- | --- |
| Operaciones | 125 |
| Ganadoras | 29 (23.2%) |
| Profit factor (neto de costos) | 0.30 |
| Resultado neto | -44.17 USDT (-22.08%) |
| Drawdown máximo | 23.20% |
| R medio por operación | -0.505 R |
| Comisiones pagadas | 16.83 USDT |
| Duración media | 2.0 h |
| Motivo de cierre final | stop: 73, stop_breakeven: 1, stop_tiempo: 23, stop_trailing: 24, tiempo_maximo: 4 |
| Resultado por par | BTCUSDT: -6.71, ETHUSDT: -17.22, SOLUSDT: -20.24 |

## Notas

- Capital inicial 200 USDT, riesgo 0.5% por operación (comisiones incluidas en el tamaño), tope 40% por posición, máx. 2 posiciones.
- Comisión 0.100% por lado y deslizamiento 0.0500% en cada llenado (también en límites).
- Supuestos conservadores: stop antes que objetivo en la misma vela; stops con hueco se llenan a la apertura; la orden límite se llena solo si el precio cruza el límite.
- No se simulan spread histórico, noticias ni IA; tampoco los límites diarios, pérdidas consecutivas, drawdown ni kill switch (llegan en la Fase 3 y se volverá a correr).
- Señales: 154; rechazadas por riesgo: {'maximo_de_posiciones': 2}; órdenes no llenadas: 2.
