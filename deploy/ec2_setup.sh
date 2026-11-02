#!/bin/bash
# Instalación del bot en una instancia EC2 con Amazon Linux 2023 (t2.micro o superior).
# Idempotente: se puede ejecutar varias veces. Uso (como ec2-user):
#   bash ec2_setup.sh
# Después: crear ~/trading-bot/.env (ver .env.example) y `docker compose up -d --build`.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/chronosedwin8/AgenteInversor.git}"
APP_DIR="$HOME/trading-bot"
COMPOSE_VERSION="v2.40.3"
BUILDX_VERSION="v0.29.1"

echo "== paquetes"
sudo dnf install -y -q docker git
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

echo "== docker compose y buildx (plugins)"
PLUGINS=/usr/local/lib/docker/cli-plugins
sudo mkdir -p "$PLUGINS"
if ! docker compose version >/dev/null 2>&1; then
  sudo curl -fsSL -o "$PLUGINS/docker-compose" \
    "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64"
  sudo chmod +x "$PLUGINS/docker-compose"
fi
# El paquete de Amazon Linux trae un buildx antiguo; compose build exige >= 0.17.
BX=$(docker buildx version 2>/dev/null | grep -oE 'v[0-9]+\.[0-9]+' | head -1 | tr -d v)
if [ -z "$BX" ] || [ "$(printf '%s
' 0.17 "$BX" | sort -V | head -1)" != "0.17" ]; then
  sudo curl -fsSL -o "$PLUGINS/docker-buildx" \
    "https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-amd64"
  sudo chmod +x "$PLUGINS/docker-buildx"
fi

echo "== swap de 2 GB (la t2.micro tiene 1 GB de RAM)"
if ! swapon --show | grep -q /swapfile; then
  sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q /swapfile /etc/fstab || echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi

echo "== hora sincronizada (chrony / Amazon Time Sync)"
sudo systemctl enable --now chronyd
timedatectl | grep -i "synchronized"

echo "== código"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone -q "$REPO_URL" "$APP_DIR"
fi

echo "== listo. Falta: $APP_DIR/.env (chmod 600) y 'docker compose up -d --build' en $APP_DIR"
