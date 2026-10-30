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
- Ojo: los CSV de data.binance.vision usan microsegundos desde 2025-01-01 (antes milisegundos);
  la API REST usa milisegundos. `bot.data.klines` detecta la unidad.

## Comandos

```bash
docker compose up -d --build                  # bot (paper por defecto) + PostgreSQL
docker compose run --rm bot python -m bot.main verify     # informe de huecos de velas
docker compose --profile ci run --rm --build ci           # mismo pipeline que CI (ruff, mypy, pytest)
```

Local (Windows, venv): `.venv/Scripts/python -m pytest tests/unit`.

## Estado de fases

- [x] Fase 0 — Base del proyecto (pendiente de aprobación)
- [x] Fase 1 — Datos y adaptador de solo lectura (pendiente de aprobación)
- [ ] Fase 2 — Estrategia y backtester
- [ ] Fase 3 — Riesgo y portafolio
- [ ] Fase 4 — Ejecución en Spot Testnet
- [ ] Fase 5 — Capa de IA
- [ ] Fase 6 — Monitoreo
- [ ] Fase 7 — Paper trading completo
- [ ] Fase 8 — En vivo con 200 USD
