#!/usr/bin/env bash
# The daily Fyers chore. Run on YOUR PC after the 06:00 IST token expiry (Fyers requires you to sign in yourself):
#   1) cd backend && .venv/Scripts/python -m trader.brokers.fyers.login     (browser sign-in, writes data/fyers_token.json)
#   2) deploy/oracle-micro/push-fyers-token.sh <vm-public-ip> <path-to-ssh-private-key>
# It copies the token into the API container's data volume and restarts the API so the collector reconnects.
set -euo pipefail
IP=${1:?vm public ip}; KEY=${2:?ssh private key path}
TOKEN=backend/data/fyers_token.json
[ -f "$TOKEN" ] || { echo "No $TOKEN: run the Fyers login first"; exit 1; }
SSH="ssh -i $KEY ubuntu@$IP"
scp -i "$KEY" "$TOKEN" ubuntu@$IP:striktek/fyers_token.json
$SSH 'cd striktek && docker compose cp fyers_token.json api:/app/data/fyers_token.json \
  && docker compose exec -u root api chown app /app/data/fyers_token.json \
  && rm fyers_token.json && docker compose restart api'
echo "Done. Check http://$IP/api/health/feed"
