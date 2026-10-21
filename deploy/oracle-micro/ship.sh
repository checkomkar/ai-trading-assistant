#!/usr/bin/env bash
# Run on YOUR PC (Git Bash / WSL) from the repo root:
#   deploy/oracle-micro/ship.sh <vm-public-ip> <path-to-ssh-private-key>
# Builds the images here (the 1 GB VM cannot build the frontend), copies them over SSH and (re)starts the stack.
# Needs a .env at the repo root with your real settings; it is copied to the VM and never committed.
set -euo pipefail
IP=${1:?vm public ip}; KEY=${2:?ssh private key path}
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new ubuntu@$IP"
[ -f .env ] || { echo "No .env at the repo root"; exit 1; }
grep -q '^CREDENTIAL_KEY=.\+' .env || echo "WARNING: CREDENTIAL_KEY is empty in .env (saved broker keys will be refused)"

docker build --platform linux/amd64 -t striktek-api:latest ./backend
docker build --platform linux/amd64 -t striktek-web:latest ./web
docker save striktek-api:latest striktek-web:latest | gzip | $SSH 'gunzip | docker load'

scp -i "$KEY" deploy/oracle-micro/compose.yml ubuntu@$IP:striktek/compose.yml
scp -i "$KEY" .env ubuntu@$IP:striktek/.env
$SSH 'cd striktek && chmod 600 .env && docker compose up -d && docker image prune -f >/dev/null && docker compose ps'
echo "Check: http://$IP/api/health  (or https://your.domain/api/health once DNS points here and SITE_ADDRESS is set)"
