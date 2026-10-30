# Auditoría de H1 — resultados (2026-10-30 16:25 UTC)

Plan y regla de decisión pre-registrados: [auditoria-h1-plan.md](auditoria-h1-plan.md). H1 sin modificar (parámetros por defecto del protocolo). RiskManager completo (Fase 3), comisión 0,1% y deslizamiento 0,05% por lado salvo donde se indica. Sin IA.

## Veredicto: **VENTAJA NO DEMOSTRADA**

| Condición | Cumple | Valor |
| --- | --- | --- |
| A bootstrap P(Sharpe ≤ 0) < 5% (bloque 10) | ✅ | 0.020 |
| B Sharpe deflactado > 95% | ❌ | 0.172 |
| C supera al 95% de entradas aleatorias con régimen | ✅ | p = 0.016 |
| D deslizamiento doble: PF > 1 y neto > 0 | ✅ | PF 1.34, neto +86.52 |
| E ≥ 70% de la rejilla con PF > 1 | ✅ | 99% |
| F universo ampliado PF > 1 | ❌ | PF 0.96 |
| G retraso de una vela PF > 1 | ✅ | PF 1.44 |
| H Calmar ≥ comprar y mantener | ✅ | 0.60 vs 0.59 |

## 0. H1-fijo, 2020-05-01 → 2026-10-30

394 operaciones, PF 1.40, neto +102.33 USDT (+51.2%), ganadoras 35%, R medio +0.224, comisiones 27.81 USDT. Kill switch: no se activó. Eventos de riesgo: 60.

## 1. Concentración del resultado

| Excluyendo las k mejores | Neto USDT | PF | R total |
| --- | --- | --- | --- |
| ninguna | +102.33 | 1.40 | +88.3 |
| k = 1 | +77.93 | 1.31 | +64.2 |
| k = 3 | +40.83 | 1.16 | +35.9 |
| k = 5 | +18.44 | 1.07 | +15.9 |
| k = 10 | -21.93 | 0.91 | -12.6 |

- El 5% superior (20 operaciones) aporta 180% del neto y 52% de la ganancia bruta.
- El 10% superior (39 operaciones) aporta 248% del neto y 71% de la ganancia bruta.

## 2. Costos

| Escenario (comisión + deslizamiento por lado) | Ops | PF | Neto |
| --- | --- | --- | --- |
| base 0,10% + 0,05% | 394 | 1.40 | +102.33 |
| BNB 0,075% + 0,05% | 393 | 1.44 | +112.09 |
| 0,10% + 0,10% (doble) | 394 | 1.34 | +86.52 |
| 0,10% + 0,20% (cuádruple) | 393 | 1.22 | +58.13 |

- Deslizamiento de equilibrio (neto = 0) con comisión 0,1%: **0.45% por lado**.
- Spread actual medido en el libro (mediana): BTCUSDT: 0.0000%, ETHUSDT: 0.0004%, SOLUSDT: 0.0105%. Una orden a mercado paga medio spread, incluido en el deslizamiento modelado.
- Funding: **no aplica** (spot, sin apalancamiento, sin perpetuos ni préstamos). Tampoco se modela interés sobre el USDT inactivo.

## 3. Riesgo, exposición y pérdidas

| Serie | CAGR | Vol. anual | Sharpe | Sortino | DD máx | Calmar | Días máx en DD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H1-fijo | 6.56% | 7.80% | 0.85 | 1.60 | 10.91% | 0.60 | 1026 |
| Comprar y mantener (1/3 c/u) | 53.22% | 78.80% | 0.94 | 1.37 | 90.20% | 0.59 | 1168 |
| Comprar y mantener × exposición media (6%) | 4.38% | 4.68% | 0.94 | 1.37 | 10.30% | 0.43 | 829 |

- Tiempo con al menos una posición abierta: 31%; exposición media: 5.9% del capital.
- Asimetría diaria 2.71, curtosis 22.8.
- Racha máxima de pérdidas: 14 operaciones. Peor mes: 2021-01 (-13.31 USDT).

| Distribución de R | Operaciones |
| --- | --- |
| ≤ −1R | 111 |
| (−1R, −0,5R] | 80 |
| (−0,5R, 0] | 65 |
| (0, 1R] | 66 |
| (1R, 3R] | 47 |
| > 3R | 25 |

Peores operaciones: BTCUSDT 2025-09-16 -1.71 USDT (-1.03R); ETHUSDT 2025-10-06 -1.69 USDT (-1.01R); BTCUSDT 2025-09-09 -1.68 USDT (-1.03R); ETHUSDT 2025-10-20 -1.67 USDT (-1.01R); BTCUSDT 2026-05-11 -1.67 USDT (-1.03R)

## 4. Robustez

### Bootstrap estacionario de rendimientos diarios

| Bloque medio | P(Sharpe ≤ 0) | Sharpe 5–95% | CAGR 5–95% |
| --- | --- | --- | --- |
| 5 días | 0.020 | 0.17 → 1.50 | 0.97% → 13.00% |
| 10 días | 0.020 | 0.15 → 1.51 | 0.84% → 13.27% |
| 20 días | 0.028 | 0.11 → 1.51 | 0.56% → 13.65% |

### Bootstrap por bloques de operaciones (bloques de 5)

- P(R medio ≤ 0) = 0.031; PF p5/p50/p95 = 1.05 / 1.45 / 1.98; P(PF < 1) = 0.031; P(PF < 1,3) = 0.292.
- Drawdown p50/p95/p99 = 10.62% / 19.32% / 23.92%; P(DD > 8%) = 0.821; P(capital ≤ 180) = 0.111.

### Entradas aleatorias (mismas salidas, misma frecuencia)

| Control | Prob. de entrada por vela | p (PF aleatorio ≥ PF H1) | PF mediano | PF p95 | Neto mediano |
| --- | --- | --- | --- | --- | --- |
| con filtro de régimen | 0.0645 | 0.016 | 1.23 | 1.36 | +88.25 |
| sin filtro | 0.0349 | 0.002 | 1.16 | 1.30 | +73.20 |

### Retraso, pares, años, universo ampliado

- Entrada retrasada una vela de 4 h: 390 ops, PF 1.44, neto +105.53.

| Sin el par | Ops | PF | Neto |
| --- | --- | --- | --- |
| BTCUSDT | 301 | 1.34 | +63.55 |
| ETHUSDT | 267 | 1.56 | +85.36 |
| SOLUSDT | 323 | 1.41 | +84.70 |

| Año | Ops | Neto | PF |
| --- | --- | --- | --- |
| 2020 | 58 | +55.24 | 3.11 |
| 2021 | 104 | -26.32 | 0.63 |
| 2022 | 9 | -1.63 | 0.70 |
| 2023 | 67 | +44.25 | 2.35 |
| 2024 | 72 | +27.25 | 1.67 |
| 2025 | 56 | +25.22 | 1.54 |
| 2026 | 28 | -21.66 | 0.29 |

- Universo ampliado (ADAUSDT, ATOMUSDT, BCHUSDT, BNBUSDT, EOSUSDT, LINKUSDT, LTCUSDT, NEOUSDT, TRXUSDT, XLMUSDT, XMRUSDT, XRPUSDT, XTZUSDT): 606 ops, PF 0.96, neto -10.97 USDT, DD 23.03%. Por par: ADAUSDT +7.83, ATOMUSDT +3.34, BCHUSDT -0.72, BNBUSDT +13.58, EOSUSDT -6.21, LINKUSDT -6.45, LTCUSDT -7.26, NEOUSDT +0.37, TRXUSDT +13.96, XLMUSDT -6.16, XMRUSDT -15.38, XRPUSDT -4.60, XTZUSDT -3.27

### Sensibilidad (diagnóstico: no se eligen parámetros)

- 180 combinaciones: 179 con PF > 1 (99%); 136 con PF ≥ 1,3; PF mediano 1.48. Los parámetros del plan quedan en el puesto 116 de 180 por PF.
- Stop inicial: 1.5×ATR → PF 1.40 (415 ops), 2.0×ATR → PF 1.40 (394 ops), 2.5×ATR → PF 1.31 (382 ops), 3.0×ATR → PF 1.34 (372 ops).
- Detalle completo en `docs/auditoria/h1_sensibilidad.csv`.

PF con EMA régimen 50 (filas: ruptura N; columnas: trailing K)

| N \ K | 2 | 2.5 | 3 | 3.5 | 4 | 5 |
| --- | --- | --- | --- | --- | --- | --- |
| 10 | 0.99 | 1.19 | 1.41 | 1.44 | 1.58 | 1.73 |
| 15 | 1.08 | 1.22 | 1.43 | 1.45 | 1.61 | 1.86 |
| 20 | 1.15 | 1.31 | 1.45 | 1.51 | 1.70 | 1.95 |
| 30 | 1.21 | 1.36 | 1.57 | 1.69 | 1.90 | 2.13 |
| 40 | 1.16 | 1.36 | 1.56 | 1.72 | 1.98 | 2.28 |
| 60 | 1.12 | 1.19 | 1.43 | 1.61 | 1.89 | 2.22 |

PF con EMA régimen 100 (filas: ruptura N; columnas: trailing K)

| N \ K | 2 | 2.5 | 3 | 3.5 | 4 | 5 |
| --- | --- | --- | --- | --- | --- | --- |
| 10 | 1.01 | 1.13 | 1.29 | 1.35 | 1.46 | 1.59 |
| 15 | 1.08 | 1.18 | 1.35 | 1.41 | 1.55 | 1.75 |
| 20 | 1.17 | 1.25 | 1.40 | 1.44 | 1.65 | 1.91 |
| 30 | 1.26 | 1.33 | 1.58 | 1.68 | 1.91 | 2.14 |
| 40 | 1.22 | 1.35 | 1.61 | 1.73 | 2.01 | 2.33 |
| 60 | 1.17 | 1.29 | 1.47 | 1.66 | 1.94 | 2.42 |

PF con EMA régimen 200 (filas: ruptura N; columnas: trailing K)

| N \ K | 2 | 2.5 | 3 | 3.5 | 4 | 5 |
| --- | --- | --- | --- | --- | --- | --- |
| 10 | 1.05 | 1.22 | 1.43 | 1.47 | 1.60 | 1.75 |
| 15 | 1.08 | 1.25 | 1.42 | 1.46 | 1.62 | 1.83 |
| 20 | 1.18 | 1.33 | 1.46 | 1.55 | 1.81 | 2.02 |
| 30 | 1.21 | 1.35 | 1.64 | 1.75 | 2.05 | 2.24 |
| 40 | 1.22 | 1.40 | 1.66 | 1.85 | 2.19 | 2.50 |
| 60 | 1.17 | 1.35 | 1.56 | 1.80 | 2.14 | 2.66 |

### Sharpe deflactado

- N = 81 configuraciones probadas; varianza de Sharpe estimada con 188 configuraciones (rejilla de sensibilidad de H1 + rejilla de H2) = 6.59e-04.
- Umbral SR* diario = 0.0630 (anual ≈ 1.20); probabilidad deflactada = **0.172**.

### H1 con walk-forward (comparación, con RiskManager completo)

| Etapa | Ops | PF | DD | Últ. 3 meses | Neto |
| --- | --- | --- | --- | --- | --- |
| desarrollo | 97 | 1.59 | 5.62% | +14.26 | +30.82 |
| confirmacion | 138 | 1.42 | 10.82% | -3.13 | +38.38 |

## 5. Cronología y anticipación

Prueba de truncado (eliminar el futuro no cambia ninguna operación cerrada antes del corte): 2021-06-01: 109 ops idénticas ✅, 2023-01-01: 171 ops idénticas ✅, 2025-01-01: 310 ops idénticas ✅.
