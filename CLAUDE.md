# CLAUDE.md — Bot de trading intradía (Binance Spot + IA)

El plan completo está en `PLAN.md`. Léelo antes de trabajar. Se construye **por fases, en orden**;
al terminar cada fase se muestra qué se hizo y cómo se cumplen sus criterios de aceptación, y se
**espera la aprobación de Edwin** antes de empezar la siguiente.

## Reglas no negociables

1. **Ningún código puede enviar órdenes reales salvo `TRADING_MODE=live` y `LIVE_CONFIRM=yes`.**
   Todo camino hacia el endpoint de órdenes de producción debe llamar a
   `bot.modes.require_live()` y fallar si no se cumple. Si falta algo, el bot arranca en paper.
2. **Toda orden pasa por el `RiskManager`; es la autoridad final y no se puede saltar.**
   No existe API pública para enviar órdenes que no reciba una aprobación del RiskManager.
3. **La estrategia y el riesgo son el mismo código en backtest, paper y live.**
   Solo cambia el adaptador del exchange (`ExchangeAdapter`). Nada de `if mode == ...` en
   `strategy/` ni en `risk/`.
4. **Nunca escribas claves en el código ni en el repo.** Usa `.env` (ignorado por git) y
   `.env.example` (solo nombres de variables, sin valores reales).
5. **Lee los filtros de cada par desde `exchangeInfo`; no los hardcodees.** Precio, cantidad y
   notional se redondean/validan con `bot.exchange.filters.SymbolFilters`. (Los fixtures de tests
   son capturas reales de `exchangeInfo`, no valores de producción.)
6. **La IA solo puede aprobar, reducir o vetar señales; nunca abrir operaciones ni cambiar stops.**
   El modelo no tiene herramientas de trading; su salida se valida contra un esquema; ante
   timeout (>20 s), JSON inválido o error, la señal se veta (falla cerrada).
7. **Escribe tests para cada módulo; los escenarios del kill switch son obligatorios.**
8. **Si algo del plan es ambiguo o una API cambió, pregunta a Edwin antes de asumir.**

## Convenciones

- Python 3.12 (imagen Docker y CI), asyncio. Paquete en `src/bot/`, tests en `tests/`.
- Dinero, precios y cantidades: `decimal.Decimal`, nunca `float`.
- Tiempos: UTC en código y base de datos (`timestamptz`); hora Bogotá solo para presentación y
  ventanas horarias (`America/Bogota`). Horario de EE. UU. siempre con calendario bursátil.
- Configuración: `config/settings.yaml`, `config/strategy.yaml`, `config/risk.yaml` (este último
  se carga una vez como modelo inmutable; cambiar un límite exige reiniciar).
- Logging estructurado JSON con `structlog` (`bot.log.get_logger`). Nunca loguear secretos.
- Migraciones SQL numeradas en `migrations/` (aplicadas por `python -m bot.main migrate`).
- Datos de mercado de solo lectura vía `data-api.binance.vision` / `data-stream.binance.vision`
  (no aceptan órdenes).
- SDK oficial: `binance-sdk-spot` (sustituye a `binance-connector`). Verificar versión vigente
  antes de cambiarla.
- Indicadores (`strategy/indicators.py`) incrementales, validados contra TA-Lib en tests (TA-Lib
  es solo dependencia de desarrollo). En float: son analítica; el dinero va en Decimal.
- Backtest: `config/backtest.yaml` (walk-forward + rejilla + criterios) se fija ANTES de ver
  resultados. Nunca ajustar parámetros ni reglas mirando el periodo fuera de muestra.
- El `RiskManager` (`risk/manager.py`) es una sola clase para backtest/paper/live; la Fase 3 le
  añade límites diarios, rachas, drawdown y kill switch.
- Ojo: los CSV de data.binance.vision usan microsegundos desde 2025-01-01 (antes milisegundos);
  la API REST usa milisegundos. `bot.data.klines` detecta la unidad.

- Kill switch: −10% desde el MÁXIMO del capital (decisión de Edwin, docs/decision-kill-switch.md).
- Ejecución (testnet/live): stop-market, cierres solo confirmados (FILLED), emergencias
  persistentes, un proceso por cuenta (advisory lock), señales caducadas no abren posiciones.
  Escenarios de fallo en `tests/unit/test_fault_scenarios.py`.
- La capa de IA está aplazada hasta demostrar que aporta valor medible superior a su costo.
- No modificar los parámetros de H1 ni relajar criterios retrospectivamente (instrucción de
  Edwin, 2026-10-30).

## Despliegue

Todo corre en la EC2 de São Paulo (sa-east-1, Ubuntu, `DATA_INTERVALS=4h,1d`): paper +
testnet 24/7, reporte diario e informe semanal por correo (docs/despliegue-ec2.md). Binance responde 451 desde
EE. UU.: nunca desplegar allí ni usar proxies/VPN.

## Comandos

```bash
docker compose up -d --build                  # bot (paper por defecto) + PostgreSQL
docker compose run --rm bot python -m bot.main verify     # informe de huecos de velas
docker compose run --rm bot python -m bot.main backtest   # walk-forward; informe en reports/
docker compose run --rm bot python -m bot.main paper-status  # estado del paper trading
docker compose run --rm bot python -m bot.main audit-h1  # auditoría pre-registrada de H1
docker compose run --rm bot python -m bot.main analyze-models  # comparación A/B/C/D y régimen
docker compose run --rm bot python -m bot.main testnet-check   # verifica claves de testnet
docker compose run --rm bot python -m bot.main daily-report    # reporte diario (--day, --send)
docker compose --profile ci run --rm --build ci           # mismo pipeline que CI (ruff, mypy, pytest)
```

Local (Windows, venv): `.venv/Scripts/python -m pytest tests/unit`.

## Estado de fases

- [x] Fase 0 — Base del proyecto (aprobada)
- [x] Fase 1 — Datos y adaptador de solo lectura (aprobada)
- [x] Fase 2 — Estrategia y backtester: H0 no pasa; Fase 2b: H1 experimental (docs/fase2b-*.md)
- [x] Auditoría de H1 (docs/auditoria-h1-*.md): ventaja NO demostrada (falla Sharpe deflactado y
      universo ampliado). H1 es experimental: SIN dinero real hasta nueva evaluación y aprobación.
- [x] Fase 3 — RiskManager completo, kill switch persistente (pendiente de aprobación)
- [x] Fase 4 — OrderManager/ExchangeTrader para Spot Testnet (falta correrlo 2 semanas: requiere
      claves de testnet). Paper trading de H1-fijo en marcha desde 2026-10-30 (8–12 semanas).
- [ ] Fase 5 — Capa de IA
- [ ] Fase 6 — Monitoreo
- [ ] Fase 7 — Paper trading completo
- [ ] Fase 8 — En vivo con 200 USD
