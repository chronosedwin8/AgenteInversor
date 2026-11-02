#!/bin/bash
# Instalación del bot en una instancia EC2 (Ubuntu 24.04+/26.04 o Amazon Linux 2023).
# Idempotente: se puede ejecutar varias veces. Uso (como el usuario normal, p. ej. ubuntu):
#   bash ec2_setup.sh
# Después: crear ~/trading-bot/.env (ver .env.example) y `sudo docker compose up -d --build`.
#
# IMPORTANTE: Binance bloquea EE. UU. (HTTP 451). Usar una región permitida, p. ej. sa-east-1.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/chronosedwin8/AgenteInversor.git}"
APP_DIR="$HOME/trading-bot"
COMPOSE_VERSION="v2.40.3"
BUILDX_VERSION="v0.29.1"
. /etc/os-release

echo "== paquetes ($PRETTY_NAME)"
if [ "$ID" = "ubuntu" ] || [ "$ID" = "debian" ]; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io git curl >/dev/null
  # Plugins de compose/buildx de la distribución si existen (si no, se descargan abajo).
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-v2 docker-buildx \
    >/dev/null 2>&1 || true
else
  sudo dnf install -y -q docker git
fi
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

echo "== docker compose y buildx (plugins)"
PLUGINS=/usr/local/lib/docker/cli-plugins
sudo mkdir -p "$PLUGINS"
version_ok() {  # $1 = versión instalada, $2 = mínima
  [ -n "$1" ] && [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]
}
CV=$(sudo docker compose version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
if ! version_ok "$CV" 2.20.0; then
  sudo curl -fsSL -o "$PLUGINS/docker-compose" \
    "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64"
  sudo chmod +x "$PLUGINS/docker-compose"
fi
# compose build exige buildx >= 0.17 (Amazon Linux trae 0.12).
BX=$(sudo docker buildx version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
if ! version_ok "$BX" 0.17.0; then
  sudo curl -fsSL -o "$PLUGINS/docker-buildx" \
    "https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-amd64"
  sudo chmod +x "$PLUGINS/docker-buildx"
fi
sudo docker compose version
sudo docker buildx version

echo "== swap de 2 GB (la t2.micro tiene 1 GB de RAM)"
if ! swapon --show | grep -q /swapfile; then
  sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q /swapfile /etc/fstab || echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi

echo "== hora sincronizada"
timedatectl | grep -i "synchronized"

echo "== código"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone -q "$REPO_URL" "$APP_DIR"
fi

echo "== listo. Falta: $APP_DIR/.env (chmod 600) y 'sudo docker compose up -d --build' en $APP_DIR"
