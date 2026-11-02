# Despliegue en AWS EC2

Actualizado: 2026-11-02.

## Servidor en producción de pruebas: São Paulo (sa-east-1)

- EC2 t2.micro (1 vCPU, 1 GB RAM + 2 GB de swap), **Ubuntu 26.04**, región **sa-east-1**.
- Usuario `ubuntu`; acceso SSH con la clave del dueño (no está en el repositorio).
- Código en `~/trading-bot`; secretos en `~/trading-bot/.env` (permisos 600, contraseña de
  PostgreSQL aleatoria generada en el servidor; nunca en git).
- `DATA_INTERVALS=4h,1d`: el disco es pequeño (≈ 7 GB) y H1 solo usa velas de 4 h y diarias.
- Instalación reproducible: `deploy/ec2_setup.sh` (Ubuntu o Amazon Linux).

| Componente | Estado |
| --- | --- |
| Paper trading de H1 (200 USDT virtuales) | 24/7 |
| Spot Testnet (órdenes reales de prueba, 200 USDT virtuales) | 24/7 — las 2 semanas cuentan desde el 2026-11-02 16:13 UTC |
| Informe semanal por correo | domingo 23:30 hora Bogotá a `REPORT_EMAIL_TO` |

Comprobado desde São Paulo: `api.binance.com`, `testnet.binance.vision` y
`data-api.binance.vision` responden 200.

## Servidor anterior (us-east-1) y PC

- La EC2 de us-east-1 (32.200.93.77) quedó **detenida** (contenedores apagados): desde EE. UU.
  Binance responde 451 a la testnet y a la API. Puede terminarse desde la consola de AWS.
- El PC de Edwin ya no ejecuta el bot (contenedores detenidos; datos conservados en su volumen
  como respaldo).

## Operación

```bash
cd ~/trading-bot
sudo docker compose ps
sudo docker compose logs -f bot
sudo docker compose run --rm bot python -m bot.main paper-status
sudo docker compose run --rm bot python -m bot.main paper-status --account testnet
sudo docker compose run --rm bot python -m bot.main testnet-check
sudo docker compose run --rm bot python -m bot.main weekly-report          # ver el informe
sudo docker compose run --rm bot python -m bot.main weekly-report --send   # enviar prueba
git pull && sudo docker compose up -d --build                               # actualizar
```

`reports/` debe pertenecer al usuario del contenedor (uid 10001) para guardar copias de los
informes: `sudo chown -R 10001:10001 reports`.

## Correo semanal

SMTP de Gmail con una contraseña de aplicación ("Bot trading", creada el 2026-11-02 en la cuenta
remitente). Se revoca o regenera en <https://myaccount.google.com/apppasswords>; después se
actualiza `SMTP_PASSWORD` en el `.env` del servidor y se reinicia el bot.
