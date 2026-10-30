# Comparación de modelos y análisis por régimen (2026-10-30 16:48 UTC)

Plan pre-registrado: [fase-testnet-plan.md](fase-testnet-plan.md) §2–3 (commit `12b3dbd`). Diagnóstico: no modifica H1 ni elige parámetros. BTC/ETH/SOL, 2020-05-01 → 2026-10-30, costos 0,1% + 0,05% por lado, RiskManager completo con kill switch en base inicial; la última columna indica cuándo habría saltado con base en el máximo.

## Modelos deterministas

| Modelo | Ops | Neto | PF | CAGR | Sharpe | Sortino | DD máx | Calmar | Tiempo en mercado | Exposición media | Rent. s/ capital expuesto | R p10/p50/p90 | Neto sin 10 mejores | Kill switch (máx.) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B H1 (ruptura) | 394 | +102.33 | 1.40 | 6.56% | 0.85 | 1.60 | 10.91% | 0.60 | 31% | 5.9% | 99.82% | -1.01 / -0.48 / +2.05 | -21.93 | 2021-12-24 |
| C entrada inmediata | 882 | +84.72 | 1.14 | 5.58% | 0.55 | 0.88 | 18.36% | 0.30 | 61% | 13.2% | 34.67% | -1.01 / -0.48 / +1.77 | -55.92 | 2021-07-13 |
| D1 régimen puro (1/3 por par) | — | +4061.42 | — | 59.84% | 1.11 | 1.70 | 64.58% | 0.93 | 54% | 54.0% | 110.89% (aprox.) | — | — | — |
| D2 comprar y mantener × exposición de B | — | +64.28 | — | 4.38% | 0.94 | 1.37 | 10.30% | 0.43 | 100% | 5.9% | — | — | — | — |

## Modelos aleatorios (1000 simulaciones cada uno): mediana (p5 … p95)

| Modelo | Ops | Neto | PF | Sharpe | DD máx | Rent. s/ capital expuesto | % con kill switch (máx.) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A1 tendencia + aleatoria i.i.d. | 572 (553 … 590) | +89.5 (+39.4 … +149.0) | 1.24 (1.11 … 1.37) | 0.70 (0.37 … 1.01) | 16.27% (11.48% … 22.83%) | 57.20% (28.56% … 85.35%) | 99% |
| A2 tendencia + señales desplazadas (con dependencia) | 468 (451 … 484) | +69.0 (+21.0 … +133.5) | 1.23 (1.08 … 1.43) | 0.63 (0.25 … 1.03) | 14.93% (10.08% … 21.35%) | 58.45% (21.18% … 96.55%) | 97% |

## Tamaño del efecto de la ruptura (B frente a A)

| Control | Métrica | B | Mediana A | Diferencia | Percentil de B en A | z |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | neto (USDT) | +102.33 | +89.51 | +12.82 | 64.7% | +0.34 |
| A1 | PF | +1.40 | +1.24 | +0.17 | 97.4% | +2.08 |
| A1 | Sharpe | +0.85 | +0.70 | +0.15 | 80.3% | +0.81 |
| A2 | neto (USDT) | +102.33 | +68.97 | +33.37 | 82.0% | +0.80 |
| A2 | PF | +1.40 | +1.23 | +0.17 | 92.5% | +1.45 |
| A2 | Sharpe | +0.85 | +0.63 | +0.22 | 82.8% | +0.89 |

## Análisis por régimen de H1-fijo (descriptivo)

Regla pre-registrada: tendencia *alcista* si cierre diario > EMA200 y EMA50 sube frente a 20 días antes; *bajista* si cierre < EMA200 y EMA50 baja; *lateral* en otro caso. Volatilidad *alta* si el ATR(14) diario en % supera la mediana del activo en el periodo.

### tendencia del activo

| Grupo | Ops | Neto | PF | R medio |
| --- | --- | --- | --- | --- |
| alcista | 276 | +111.86 | 1.64 | +0.338 |
| bajista | 16 | +5.10 | 1.73 | +0.271 |
| lateral | 76 | -11.89 | 0.80 | -0.097 |

### tendencia del mercado (BTC)

| Grupo | Ops | Neto | PF | R medio |
| --- | --- | --- | --- | --- |
| alcista | 276 | +102.77 | 1.59 | +0.320 |
| bajista | 27 | -3.74 | 0.79 | -0.122 |
| lateral | 65 | +6.03 | 1.13 | +0.081 |

### volatilidad

| Grupo | Ops | Neto | PF | R medio |
| --- | --- | --- | --- | --- |
| alta | 155 | -0.10 | 1.00 | +0.012 |
| baja | 213 | +105.16 | 1.74 | +0.415 |

### año × tendencia del activo

| Grupo | Ops | Neto | PF | R medio |
| --- | --- | --- | --- | --- |
| 2020 alcista | 35 | +56.45 | 4.77 | +1.490 |
| 2020 lateral | 7 | -0.37 | 0.90 | -0.038 |
| 2021 alcista | 80 | -22.42 | 0.59 | -0.227 |
| 2021 lateral | 14 | -2.03 | 0.79 | -0.119 |
| 2022 bajista | 4 | +1.12 | 1.56 | +0.251 |
| 2022 lateral | 5 | -2.75 | 0.19 | -0.476 |
| 2023 alcista | 50 | +41.33 | 2.64 | +0.681 |
| 2023 bajista | 6 | +3.33 | 3.13 | +0.485 |
| 2023 lateral | 11 | -0.41 | 0.93 | -0.026 |
| 2024 alcista | 55 | +15.82 | 1.52 | +0.209 |
| 2024 lateral | 17 | +11.43 | 2.12 | +0.475 |
| 2025 alcista | 38 | +35.89 | 2.28 | +0.618 |
| 2025 bajista | 3 | -0.61 | 0.61 | -0.133 |
| 2025 lateral | 15 | -10.06 | 0.43 | -0.406 |
| 2026 alcista | 18 | -15.22 | 0.27 | -0.533 |
| 2026 bajista | 3 | +1.26 | 1.68 | +0.274 |
| 2026 lateral | 7 | -7.70 | 0.00 | -0.679 |

### año × volatilidad

| Grupo | Ops | Neto | PF | R medio |
| --- | --- | --- | --- | --- |
| 2020 alta | 20 | +22.52 | 4.27 | +0.939 |
| 2020 baja | 22 | +33.56 | 3.84 | +1.503 |
| 2021 alta | 90 | -20.55 | 0.66 | -0.184 |
| 2021 baja | 4 | -3.90 | 0.00 | -0.804 |
| 2022 alta | 4 | -0.06 | 0.97 | -0.010 |
| 2022 baja | 5 | -1.57 | 0.48 | -0.268 |
| 2023 alta | 9 | +7.18 | 2.66 | +0.611 |
| 2023 baja | 58 | +37.07 | 2.30 | +0.538 |
| 2024 alta | 21 | -1.56 | 0.87 | -0.043 |
| 2024 baja | 51 | +28.81 | 2.01 | +0.401 |
| 2025 alta | 11 | -7.63 | 0.29 | -0.442 |
| 2025 baja | 45 | +32.85 | 1.90 | +0.486 |
| 2026 baja | 28 | -21.66 | 0.29 | -0.483 |
