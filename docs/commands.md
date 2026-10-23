# Useful commands

Run from the repo root unless a line says otherwise. Replace `<key>` with the path to your SSH private key.
The VM is `80.225.242.94`, app folder `~/striktek`.

## Every day (Fyers tokens expire at 06:00 IST)

Sign in to Fyers (opens a browser; you sign in yourself):

```bash
backend/.venv/Scripts/python -m trader.brokers.fyers.login
```

Send that token to the server and restart its API:

```bash
deploy/oracle-micro/push-fyers-token.sh 80.225.242.94 <key>
```

## Run the app locally

In the Claude desktop app use `preview_start` with `api` and `web`. Or by hand:

```bash
backend/.venv/Scripts/uvicorn trader.api.main:app --app-dir backend --port 8000
```

```bash
npm --prefix web run dev
```

Open http://localhost:5173.

## Market data (local)

Option history, resumable (1 year of 1-minute candles):

```bash
backend/.venv/Scripts/python -m trader.scripts.backfill_history --days 365 --timeframes 1m
```

NIFTY 50 daily stock candles, free Yahoo data, no login, resumable (also the daily refresh; add `--source fyers` for broker data):

```bash
backend/.venv/Scripts/python -m trader.scripts.backfill_equities --years 5
```

## Move history to the server (add only, never deletes)

Pack everything (about 31 MB zipped) or only the stocks:

```bash
backend/.venv/Scripts/python -m trader.scripts.merge_market_data --from backend/data/trader.db --to backend/data/market_slim.db --create
```

```bash
backend/.venv/Scripts/python -m trader.scripts.merge_market_data --from backend/data/trader.db --to backend/data/stocks_slim.db --create --equities-only
```

Upload (use `stocks_slim.db` for the stocks pack):

```bash
gzip -kf backend/data/market_slim.db && scp -i <key> backend/data/market_slim.db.gz ubuntu@80.225.242.94:striktek/
```

On the VM, unpack and merge:

```bash
cd ~/striktek && gunzip -f market_slim.db.gz && sudo docker compose cp market_slim.db api:/tmp/market_slim.db && sudo docker compose exec api python -m trader.scripts.merge_market_data --from /tmp/market_slim.db --to /app/data/trader.db && rm market_slim.db
```

Add `--equities-only` to the last command for the stocks pack. Free the temporary copy:

```bash
sudo docker compose exec -u root api rm -f /tmp/market_slim.db
```

## Deploy

Build, upload and restart (needs `.env` at the repo root):

```bash
deploy/oracle-micro/ship.sh 80.225.242.94 <key>
```

## On the VM (after `ssh -i <key> ubuntu@80.225.242.94`)

```bash
cd ~/striktek && sudo docker compose ps
```

```bash
cd ~/striktek && sudo docker compose logs api --tail 60
```

```bash
free -m && df -h
```

Never run `docker compose down -v`: it deletes the data volume.

## Tests and checks

All backend tests (about 2.5 minutes):

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

Frontend type check:

```bash
cd web && npx tsc -b
```

## Backtest from the command line

```bash
backend/.venv/Scripts/python -m trader.scripts.backtest --underlying NIFTY --source real --start 2025-10-23 --end 2026-10-19
```
