# trading-bot

Bot de trading intradía en Binance Spot (solo largos, sin apalancamiento) con filtro de IA.
Plan completo en [PLAN.md](PLAN.md); reglas para Claude Code en [CLAUDE.md](CLAUDE.md).

> No es asesoría financiera. La estrategia es una hipótesis que debe pasar backtest y paper
> trading antes de usar dinero real.

## Arranque rápido

```bash
cp .env.example .env            # opcional: sin claves arranca en paper
docker compose up -d --build    # PostgreSQL + bot en modo paper
docker compose logs -f bot
```

Al arrancar, el bot aplica migraciones, lee `exchangeInfo`, sincroniza 13 meses de velas de
15 min y 1 h (BTC, ETH, SOL), rellena huecos y sigue las velas en vivo por WebSocket.

## Comandos

```bash
docker compose run --rm bot python -m bot.main verify          # informe de huecos
docker compose run --rm bot python -m bot.main exchange-info   # filtros por par
docker compose run --rm bot python -m bot.main mode            # modo resuelto
docker compose --profile ci run --rm --build ci                # ruff + mypy + pytest
```

## Estado actual (2026-10-30)

H1 es una estrategia **experimental**: la auditoría ([conclusiones](docs/auditoria-h1-conclusiones.md))
no demostró una ventaja reproducible. **No se opera con dinero real.**

- **Paper trading** (precios reales, órdenes simuladas, 200 USDT virtuales) corre dentro de
  `docker compose up`. Estado: `docker compose run --rm bot python -m bot.main paper-status`.
- **Spot Testnet** (Fase 4, órdenes reales sin dinero real): crea una clave HMAC en
  <https://testnet.binance.vision> (inicio de sesión con GitHub), añade
  `BINANCE_TESTNET_API_KEY` y `BINANCE_TESTNET_API_SECRET` a `.env` y reinicia el bot.
- **Kill switch**: si se activa, solo se reinicia a mano:
  `python -m bot.main risk-reset --account paper --confirm "REINICIO MANUAL DEL KILL SWITCH"`.
