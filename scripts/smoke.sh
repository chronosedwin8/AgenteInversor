#!/bin/sh
# Prueba de humo de la Fase 0: `docker compose up` arranca el bot en modo paper sin claves.
set -eu
TIMEOUT="${SMOKE_TIMEOUT:-120}"

docker compose up -d --build db bot
trap 'docker compose logs --tail 40 bot; docker compose down' EXIT

elapsed=0
until docker compose logs bot 2>/dev/null | grep '"event": "bot_starting"' | grep -q '"mode": "paper"'; do
  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    echo "FALLO: el bot no arrancó en modo paper en ${TIMEOUT}s"
    exit 1
  fi
  sleep 2
  elapsed=$((elapsed + 2))
done
echo "OK: el bot arrancó en modo paper"
