# Despliegue en AWS EC2

Fecha: 2026-11-02.

## Servidor actual

- EC2 t2.micro (1 vCPU, 1 GB RAM + 2 GB de swap), Amazon Linux 2023, región **us-east-1**.
- Usuario `ec2-user`, acceso por SSH con la clave del dueño (no está en el repositorio).
- Código en `~/trading-bot` (clon de este repositorio), secretos en `~/trading-bot/.env`
  (permisos 600; contraseña aleatoria de PostgreSQL; nunca en git).
- Instalación reproducible: `deploy/ec2_setup.sh` (Docker, Compose, Buildx, swap, chrony, código).
- Estado migrado desde el PC con `pg_dump`/`pg_restore` (velas, estado del paper, eventos).

## Qué corre dónde

| Componente | Dónde | Motivo |
| --- | --- | --- |
| Paper trading de H1 | **EC2** (24/7) | solo usa datos públicos de mercado (`data-api.binance.vision`, responde desde EE. UU.) |
| Spot Testnet | PC de Edwin (`PAPER_ENABLED=false`) | `testnet.binance.vision` responde **451 (ubicación restringida)** desde us-east-1 |
| Informe semanal por correo | EC2 | domingo 23:30 hora Bogotá a `REPORT_EMAIL_TO` |

Binance restringe EE. UU. (también `api.binance.com`). No se usan proxies ni VPN para saltar la
restricción: incumpliría sus términos. Para testnet (y cualquier futura cuenta real) el servidor
debe estar en una región permitida, p. ej. **sa-east-1 (São Paulo)**. Mover todo allí:

1. Crear una instancia Amazon Linux 2023 en sa-east-1 con la misma clave.
2. `bash ec2_setup.sh` (descargado del repositorio), copiar `.env` y la base (`pg_dump`).
3. Activar las claves de testnet en su `.env` y desactivar el bot del PC.

## Operación

```bash
cd ~/trading-bot
sudo docker compose ps
sudo docker compose logs -f bot
sudo docker compose run --rm bot python -m bot.main paper-status
sudo docker compose run --rm bot python -m bot.main weekly-report          # ver el informe
sudo docker compose run --rm bot python -m bot.main weekly-report --send   # enviar prueba
git pull && sudo docker compose up -d --build                               # actualizar
```

## Correo semanal

Requiere SMTP en el `.env` del servidor. Con Gmail: activar la verificación en 2 pasos en la cuenta
que envía, crear una **contraseña de aplicación** (16 caracteres) y poner `SMTP_USER` (la cuenta)
y `SMTP_PASSWORD` (la contraseña de aplicación). Sin SMTP, el informe se guarda en
`reports/informe-semanal-<semana>.txt` y se registra un aviso.
