# Plan Claude Code: Bot de trading intradía (Binance + IA)

Oct 29, 2026 · @Edwin

## Resumen y supuestos clave

El bot opera en Binance Spot, solo en compra y sin apalancamiento: cripto 24/7 y acciones tokenizadas (bStocks) solo en horario de mercado de EE. UU. Arriesga 0,5% del capital por operación, usa la IA como filtro con poder de veto y se apaga solo si el capital cae a 180 USD.

- **Acciones en Binance = bStocks.** Binance lanzó en junio de 2026 bStocks, certificados tokenizados respaldados 1:1 por acciones de EE. UU., que se negocian como pares spot contra USDT (por ejemplo NVDAB/USDT, TSLAB/USDT). No son acciones directas y solo están disponibles para usuarios elegibles en jurisdicciones permitidas ([comunicado](https://aapnews.aap.com.au/news/cision20260612AE82369)). El módulo de bStocks queda **apagado por defecto** hasta confirmar que tu cuenta en Colombia puede operarlos por API.
- **Solo spot, solo largos.** Con 200 USD el apalancamiento multiplica el riesgo de ruina; y en spot no se puede vender en corto. Si el mercado está bajista, el bot simplemente no opera.
- **Comisiones:** 0,1% por lado para usuario regular; 0,075% si se pagan con BNB ([Binance fees](https://www.bitdegree.org/crypto/tutorials/binance-spot-trading)). Todo cálculo de resultados las incluye.
- **Mínimos de orden:** cada par tiene filtros (NOTIONAL, LOT\_SIZE, PRICE\_FILTER) que se leen de `/api/v3/exchangeInfo`; órdenes que no cumplen se rechazan ([Binance Academy](https://www.binance.com/en/academy/articles/binance-spot-trading-rules-a-comprehensive-guide)).
- **Aviso:** no soy asesor financiero licenciado. Esta estrategia es una hipótesis de trabajo que debe pasar backtest y paper trading antes de usar dinero real; no hay garantía de ganancia y la mayoría de bots minoristas pierden. Las ganancias se declaran ante la DIAN; consulta a un contador.

## Estrategia de trading

La estrategia es **seguir la tendencia comprando retrocesos**: solo compra en velas de 15 minutos cuando la tendencia de 1 hora es alcista, y solo si la IA no detecta un riesgo de noticias. Se eligió porque con comisiones de 0,1% por lado el scalping de 1 minuto no es rentable; se necesitan movimientos de 1% a 3%.

### Universo de activos

| Mercado | Pares iniciales | Cuándo opera | Estado inicial |
| --- | --- | --- | --- |
| Cripto | BTCUSDT, ETHUSDT, SOLUSDT | 24/7; nuevas entradas preferentemente 07:00–18:00 hora Bogotá | Activo |
| bStocks | NVDAB/USDT, TSLAB/USDT | Solo en sesión regular de EE. UU., de apertura + 15 min a cierre − 30 min | Apagado hasta verificar elegibilidad |

- Fuera de la sesión de EE. UU. la acción subyacente no cotiza; el token queda mal anclado y con spread amplio, por eso el bot no opera bStocks a esa hora.
- El horario de EE. UU. se calcula con un calendario bursátil (feriados y cambio de horario), nunca con horas fijas: Bogotá no cambia de hora y Nueva York sí.
- Cada semana se revisa el universo: se excluyen pares con spread mayor a 0,05% o volumen bajo.

### Reglas de entrada (todas deben cumplirse)

1. **Régimen alcista (1 h):** cierre > EMA200, EMA50 > EMA200 y ADX(14) ≥ 20.
2. **Retroceso (15 min):** en las últimas 3 velas el mínimo tocó la EMA20 (o quedó a menos de 0,2×ATR), sin cerrar por debajo de la EMA50; RSI(14) entre 40 y 60.
3. **Gatillo (15 min):** la vela cierra por encima del máximo de la anterior y de la EMA20; volumen ≥ 1,2 × media de 20 velas; histograma MACD subiendo.
4. **Calidad:** ATR(14) de 15 min entre 0,35% y 3% del precio; spread ≤ 0,05%; sin posición abierta en ese activo; fuera de ventanas de bloqueo por noticias.
5. **Visto bueno de la IA:** aprobar o reducir (ver abajo). Un veto cancela la señal.
6. **Visto bueno del gestor de riesgo:** límites de la sección siguiente. Es la autoridad final.

**Orden de entrada:** LIMIT\_MAKER al mejor bid para pagar comisión maker; si no se llena en 2 velas (30 min), se cancela.

### Reglas de salida

1. **Stop inicial:** entrada − 1,5×ATR, con distancia máxima de 3%. Se coloca **en el exchange** apenas se llena la compra, no solo en la memoria del bot.
2. **Toma parcial:** a +1,5R se vende el 50% y el stop sube a entrada + comisiones.
3. **Resto:** trailing stop de 2×ATR, o salida si una vela de 15 min cierra bajo la EMA20.
4. **Stop de tiempo:** si en 8 velas (2 h) no alcanza +0,5R, se cierra.
5. **Cierre intradía:** ninguna posición dura más de 12 h; los bStocks se cierran 10 min antes del cierre de la sesión de EE. UU.

(R = distancia entre entrada y stop; +1,5R significa ganar 1,5 veces lo arriesgado.)

### Capa de IA (Claude API)

La IA **filtra, no dispara**: nunca abre una operación sin señal técnica, nunca aumenta tamaño ni afloja un stop.

| Tarea | Modelo | Cuándo | Entrada | Salida |
| --- | --- | --- | --- | --- |
| Briefing de régimen | claude-sonnet-5-5 | Cada 4 h y al abrir la sesión de EE. UU. | Titulares de noticias, calendario económico (CPI, FOMC, empleo), resultados trimestrales de las empresas de los bStocks | Modo de riesgo: normal, reducido o bloqueado; ventanas de bloqueo con hora de inicio y fin |
| Validación de señal | claude-haiku-5-5 | Cada señal técnica | JSON con indicadores, últimas velas, modo de riesgo y titulares recientes del activo | JSON: decisión (aprobar, reducir, vetar), confianza 0–1, motivos, banderas de riesgo |
| Reporte diario | claude-sonnet-5-5 | 23:30 hora Bogotá | Operaciones, errores, señales vetadas | Resumen y sugerencias; los cambios de parámetros los aprueba Edwin, nunca la IA |

- **Falla cerrada:** si la IA tarda más de 20 s, responde JSON inválido o da error, la señal se veta.
- **Reducir** = la mitad del tamaño calculado.
- **Medición:** cada señal vetada se simula como si se hubiera tomado. Tras 50 señales se compara el resultado con y sin filtro; si la IA no mejora el profit factor, se apaga.

## Gestión de riesgo (200 USD, pérdida máxima 10%)

El presupuesto total de pérdida es 20 USD; arriesgando \~1 USD por operación, el bot necesita unas 20 pérdidas completas para llegar al apagado, y los límites diarios lo frenan mucho antes. El gestor de riesgo es un módulo independiente que revisa cada orden y puede rechazarla aunque la estrategia y la IA la aprueben.

| Regla | Valor | Con 200 USD |
| --- | --- | --- |
| Riesgo por operación | 0,5% del capital actual | ≈ 1,00 USD |
| Tamaño máximo por posición | 40% del capital | 80 USD |
| Tamaño mínimo | 2 × NOTIONAL del par + margen (para poder vender el 50%) | ≈ 12 USD si NOTIONAL = 5 USDT |
| Posiciones simultáneas | 2, máximo 1 en bStocks | — |
| Operaciones nuevas por día | 4 | — |
| Pérdida diaria máxima | 2% | 4 USD → sin entradas hasta las 00:00 hora Bogotá |
| Pérdidas consecutivas | 3 | pausa de 24 h |
| Alerta de drawdown | −6% | capital ≤ 188 USD → riesgo baja a 0,25% por operación |
| **Kill switch** | **−10% del capital inicial** | **capital ≤ 180 USD → cancela órdenes, cierra posiciones, se apaga; reinicio solo manual** |
| Apalancamiento | ninguno | solo spot |
| Comisiones | 0,1% por lado (0,075% con BNB) | incluidas en tamaño, metas y reportes |

"Capital" = USDT libre + valor de mercado de las posiciones, menos las comisiones de cierre estimadas.

### Cálculo del tamaño

```latex
\text{cantidad} = \min\left(\frac{\text{capital} \times 0.005}{\text{entrada} - \text{stop}},\; \frac{0.40 \times \text{capital}}{\text{entrada}}\right)
```

Luego se redondea hacia abajo al `stepSize` del par y se valida contra NOTIONAL. Si no cumple el mínimo, la señal se descarta.

**Ejemplo:** capital 200 USD, entrada 100, ATR 1,0 → stop 98,5 (1,5% abajo). Cantidad = 1,00 / 1,5 = 0,667 unidades = 66,7 USD de posición, por debajo del tope de 80 USD. Comisiones ida y vuelta ≈ 0,13 USD.

### Expectativa realista

Con 200 USD, un 1% de ganancia son 2 USD. Los primeros meses sirven para validar que la estrategia funciona en vivo, no para generar ingresos. Si funciona, el capital se aumenta gradualmente; si no, el kill switch limita la pérdida a unos 20 USD (más deslizamiento en un movimiento brusco).

## Arquitectura del aplicativo

Un solo servicio en Python con tres modos (backtest, paper, live) que comparten **exactamente el mismo código de estrategia y riesgo**; solo cambia el adaptador del exchange. Así lo que se prueba en backtest es lo mismo que opera en vivo.

&#91;embedded content: flujo de decisión de cada señal · 3 filtros\]

Cada señal pasa por tres filtros en orden; un veto de la IA o un rechazo del gestor de riesgo la descarta, y queda registrada para medir si el filtro aporta.

### Stack

| Capa | Tecnología |
| --- | --- |
| Lenguaje | Python 3.12, asyncio |
| Exchange | SDK oficial de Binance para Python (REST + WebSocket); Claude Code verifica la versión vigente |
| Indicadores | pandas + pandas-ta (o TA-Lib) |
| Backtest | Motor propio por eventos que reutiliza la clase Strategy; vectorbt solo para barridos rápidos de parámetros |
| Base de datos | PostgreSQL 16 (velas, señales, decisiones de IA, órdenes, operaciones, capital) |
| IA | SDK `anthropic`; claude-haiku-5-5 para señales, claude-sonnet-5-5 para briefing y reporte |
| Noticias y calendario | Fuente de titulares con API o RSS + calendario económico; Claude Code propone y compara opciones |
| Horario de EE. UU. | pandas\_market\_calendars |
| Panel y alertas | FastAPI + página HTML simple; bot de Telegram |
| Despliegue | Docker Compose en VPS o EC2 en región fuera de EE. UU. (Binance.com restringe accesos desde EE. UU.; verificar la región elegida) |

### Módulos

| Módulo | Responsabilidad |
| --- | --- |
| `exchange/` | Interfaz común `ExchangeAdapter` con tres implementaciones: `BinanceSpot`, `Paper` (precios reales, órdenes simuladas) y `Backtest`. Lee filtros de `exchangeInfo` y redondea precio y cantidad |
| `data/` | Descarga histórica (data.binance.vision), velas por WebSocket, relleno de huecos, caché |
| `universe/` | Lista de pares, sesiones de EE. UU. para bStocks, filtros de spread y volumen |
| `strategy/` | Indicadores y reglas de entrada y salida; devuelve señales, nunca envía órdenes |
| `ai/` | Briefing, validación de señales, reporte diario; esquemas JSON, timeouts, registro de costo por llamada |
| `risk/` | Tamaño de posición, límites diarios, drawdown, kill switch. Autoridad final |
| `execution/` | Envío de órdenes con `newClientOrderId` idempotente, stops en el exchange, reconciliación al arrancar |
| `portfolio/` | Posiciones, capital, P&L con comisiones |
| `monitoring/` | Telegram (/status, /pause, /resume, /stop), panel web, heartbeat |
| `backtest/` | Simulación con comisiones y deslizamiento, walk-forward, reportes |

### Estructura del repositorio

```text
trading-bot/
├── PLAN.md                 # este documento
├── CLAUDE.md               # reglas para Claude Code
├── docker-compose.yml
├── .env.example            # sin claves reales
├── config/
│   ├── settings.yaml       # modo, universo, horarios
│   ├── strategy.yaml       # parámetros de indicadores
│   └── risk.yaml           # límites de riesgo
├── src/bot/
│   ├── exchange/ data/ universe/ strategy/
│   ├── ai/ risk/ execution/ portfolio/
│   ├── monitoring/ backtest/
│   └── main.py
├── migrations/
├── tests/                  # unit, integración, escenarios de riesgo
└── reports/
```

### Modos y protección contra órdenes reales

- `TRADING_MODE=backtest|paper|live` en el entorno.
- El modo live exige además `LIVE_CONFIRM=yes` y que existan claves de producción; si falta algo, el bot arranca en paper y avisa por Telegram.
- `risk.yaml` es de solo lectura en ejecución: cambiar un límite exige reiniciar el bot.

## Fases de construcción para Claude Code

Nueve fases; ninguna avanza sin cumplir su criterio, y el dinero real solo entra en la Fase 8, después de unas 6–10 semanas de pruebas. Si el backtest de la Fase 2 no pasa sus umbrales, el proyecto se detiene o se replantea la estrategia antes de escribir el ejecutor.

1. **Fase 0 — Base del proyecto.** Repo, Docker Compose (bot + PostgreSQL), configuración YAML + `.env`, logging estructurado, CI con tests y linters, `CLAUDE.md`.
   - Criterio: `docker compose up` arranca en modo paper sin claves; CI en verde.
2. **Fase 1 — Datos y adaptador de solo lectura.** Descarga de 12+ meses de velas de 15 min y 1 h (BTC, ETH, SOL), WebSocket en vivo, lectura de `exchangeInfo`, redondeo por `tickSize` y `stepSize`.
   - Criterio: datos sin huecos en PostgreSQL; tests de redondeo y de validación NOTIONAL.
3. **Fase 2 — Estrategia y backtester.** Indicadores, reglas de la sección Estrategia, simulación con comisión 0,1% por lado y deslizamiento 0,05%, walk-forward (optimizar en un periodo, validar en el siguiente).
   - Criterio para seguir: ≥ 100 operaciones, profit factor ≥ 1,3 después de costos, drawdown máximo ≤ 8%, resultado positivo en los últimos 3 meses fuera de muestra. Nunca ajustar parámetros mirando el periodo de validación.
4. **Fase 3 — Riesgo y portafolio.** Todas las reglas de la tabla de riesgo, cálculo de capital y P&L con comisiones.
   - Criterio: tests de escenarios (racha de pérdidas, caída de 10%, desfase de precio) donde el kill switch se activa exactamente en 180 USD y nada lo evita.
5. **Fase 4 — Ejecución en Spot Testnet.** Órdenes LIMIT\_MAKER, stops en el exchange, cancelación por tiempo, `newClientOrderId` idempotente, reintentos con backoff, sincronización de reloj, reconciliación al reiniciar.
   - Criterio: 2 semanas en testnet sin errores no manejados; toda posición abierta tiene su stop en el exchange; un reinicio forzado no duplica órdenes.
6. **Fase 5 — Capa de IA.** Briefing, validación de señales y reporte; esquemas JSON validados; falla cerrada; registro de costo y latencia; simulación de señales vetadas.
   - Criterio: 100% de respuestas válidas o veto; latencia p95 < 20 s; costo mensual estimado dentro del presupuesto que definas.
7. **Fase 6 — Monitoreo.** Telegram con alertas y comandos, panel web (capital, posiciones, señales, decisiones de IA), heartbeat.
   - Criterio: alerta en menos de 60 s ante kill switch, error o caída del bot; /stop cierra todo.
8. **Fase 7 — Paper trading completo.** 4–6 semanas: cripto en testnet; bStocks (si son elegibles) con precios reales y órdenes simuladas, porque la testnet no los incluye.
   - Criterio: cero violaciones de reglas de riesgo; resultados dentro de un rango razonable del backtest; revisión de cada operación.
9. **Fase 8 — En vivo con 200 USD.** Checklist de seguridad completo; las 2 primeras semanas con riesgo de 0,25% por operación; revisión diaria del reporte.
   - Criterio de continuidad: tras 100 operaciones se decide seguir, ajustar o detener con base en los datos.

## Seguridad, operación y monitoreo

La regla base: aunque el bot falle por completo, el dinero no puede salir de la cuenta y cada posición sigue protegida por su stop en el exchange.

- **Clave API:** solo trading spot, retiros deshabilitados, restringida a la IP del servidor; claves separadas para testnet y producción.
- **Secretos:** en `.env` fuera del repositorio o en un gestor de secretos; `.env.example` sin valores reales.
- **Capital aislado:** en la cuenta del bot solo los 200 USD (más un poco de BNB para comisiones).
- **Stops del lado del exchange:** se colocan apenas se llena la compra; si el stop no se puede colocar, la posición se cierra de inmediato.
- **Reconciliación:** al arrancar y cada 5 min se comparan órdenes y saldos de la base de datos contra Binance; si no coinciden, pausa y alerta.
- **Heartbeat:** si el bot deja de reportar 5 min con posiciones abiertas, llega una alerta a Telegram.
- **Límites de la API:** respetar rate limits, `recvWindow` y sincronización de reloj; reintentos con backoff exponencial.
- **IA sin herramientas:** el modelo nunca tiene acceso a funciones de trading; el texto de noticias se trata como datos, no como instrucciones, y toda salida se valida contra un esquema.
- **Auditoría:** cada decisión queda registrada con sus entradas, la respuesta de la IA, los chequeos de riesgo y el resultado.
- **Impuestos:** exportación mensual de operaciones en CSV para el contador.

## Prompt inicial para Claude Code y preguntas abiertas

Exporta este documento como Markdown, guárdalo como `PLAN.md` en un repositorio vacío y pega este prompt en Claude Code:

```text
Lee PLAN.md completo. Vas a construir este bot de trading por fases, en el orden del plan.
Empieza solo con la Fase 0 y la Fase 1; al terminar cada fase muéstrame qué hiciste
y cómo se cumplen sus criterios de aceptación, y espera mi aprobación antes de seguir.

Reglas no negociables:
1. Ningún código puede enviar órdenes reales salvo TRADING_MODE=live y LIVE_CONFIRM=yes.
2. Toda orden pasa por el RiskManager; es la autoridad final y no se puede saltar.
3. La estrategia y el riesgo son el mismo código en backtest, paper y live.
4. Nunca escribas claves en el código ni en el repo; usa .env y .env.example.
5. Lee los filtros de cada par desde exchangeInfo; no los hardcodees.
6. La IA solo puede aprobar, reducir o vetar señales; nunca abrir operaciones ni cambiar stops.
7. Escribe tests para cada módulo; los escenarios del kill switch son obligatorios.
8. Si algo del plan es ambiguo o una API cambió, pregúntame antes de asumir.

Crea también CLAUDE.md con estas reglas para futuras sesiones.
```

### Preguntas abiertas

- [ ] ¿Tu cuenta de Binance en Colombia muestra bStocks y permite operarlos por API? Si no, el bot arranca solo con cripto.
- [x] ¿El 10% se mide desde los 200 USD iniciales o desde el máximo? → **Desde el máximo** (Edwin, 2026-10-30; ver docs/decision-kill-switch.md).
- [ ] ¿Dónde correrá: EC2 (¿qué región?) o un equipo propio encendido 24/7?
- [ ] ¿Telegram te sirve para alertas y comandos?
- [ ] ¿Cuál es el presupuesto mensual para la API de Claude y para la fuente de noticias?
- [ ] ¿Quieres aprobar manualmente las primeras operaciones en vivo (modo semiautomático) antes de dar autonomía total?

### Fuentes

- [Binance lanza bStocks (comunicado, 12 jun 2026)](https://aapnews.aap.com.au/news/cision20260612AE82369)
- [Binance Spot Trading Rules — Binance Academy](https://www.binance.com/en/academy/articles/binance-spot-trading-rules-a-comprehensive-guide)
- [Comisiones spot de Binance — BitDegree](https://www.bitdegree.org/crypto/tutorials/binance-spot-trading)
