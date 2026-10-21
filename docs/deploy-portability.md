# Deploying and moving STRIKTEK between hosts

Goal: moving from your PC to a VPS, AWS, Azure, Google Cloud or anywhere else is a copy-and-start job, not a rewrite.
Written 2026-10-22. What was actually run is marked **tested**.

## Why it is portable
- **One container image per part.** `backend/Dockerfile` (the API, collector and paper runner) and `web/Dockerfile` (the built site served by Caddy, which also proxies `/api` and `/ws` and gets HTTPS certificates itself). **Tested**: both build, run together, and answer through Caddy.
- **All settings come from the environment** (`.env.example` lists them). No code change per host. Secrets are only ever environment variables.
- **State lives in two places only:** the database (`DATABASE_URL`) and the `/app/data` volume (Fyers token, uploads). Nothing else is written to disk.
- **SQLite or PostgreSQL by changing one URL.** Migrations run at start-up on either. **Tested**: all migrations, sign-up, strategies, broker keys, phone codes and the chat routes on PostgreSQL 16.
- **No cloud-specific services** are used: no queues, no object store, no provider SDKs.

## Run it anywhere with Docker
```bash
cp .env.example .env        # fill in keys; add POSTGRES_PASSWORD=... and SITE_ADDRESS=your.domain
docker compose -f docker-compose.prod.yml up -d --build
```
`SITE_ADDRESS` set to a real domain gives automatic HTTPS on ports 80/443 (point the domain's DNS at the machine first). Left at `:80`, it serves plain HTTP for local runs or behind a load balancer that handles TLS.

## Mapping to other services
| You want | Do this |
|---|---|
| A VPS (Hetzner, DigitalOcean, Oracle, etc.) | Install Docker, copy the repo and `.env`, run the command above. |
| AWS | Push the two images to ECR. Run the API on ECS Fargate or App Runner, the web image on a second service (or serve `web/dist` from S3 + CloudFront and route `/api`, `/ws` to the API). Database: RDS PostgreSQL. |
| Azure | Container Apps (or App Service for Containers) for the two images; Azure Database for PostgreSQL. |
| Google Cloud | Cloud Run for the images (set min instances to 1; the collector must stay running); Cloud SQL for PostgreSQL. |
| Managed database anywhere (Supabase, Neon, RDS...) | Remove the `db` service and set `DATABASE_URL=postgresql+asyncpg://...` on the API. |

Run **exactly one API instance**. The market-data collector and the paper runner are single processes by design; scaling to several needs the per-account runners in PRD §23. A host that scales to zero or sleeps (free tiers) will stop the collector.
Keep `STORE_TICKS=false` on a hosted database (raw ticks are about 600k rows a day and nothing reads them).

## Moving the data
1. Stop the API on the old host (or copy during a quiet hour).
2. Start the new stack once so the empty database is migrated.
3. Copy everything across:
   ```bash
   python -m trader.scripts.copy_db --from sqlite+aiosqlite:///./data/trader.db \
       --to postgresql+asyncpg://user:pw@host/db --skip market_ticks
   ```
   It refuses to write into a table that already has rows, stamps SQLite's UTC timestamps correctly and repairs the id sequences. **Tested** SQLite to PostgreSQL on a real database copy.
4. Copy the `.env` (same `CREDENTIAL_KEY`, or saved broker keys become unreadable) and the `data/fyers_token.json` file if you want to keep today's Fyers session.
5. Move the domain's DNS. Sessions are cookies for the domain, so users stay signed in only if the new host serves the same domain over HTTPS.

## Things that stay your job on any host
- Daily Fyers sign-in (06:00 IST token expiry; SEBI requires the interactive 2FA).
- Backups: `pg_dump` the database nightly and keep `.env` somewhere safe.
- Monitoring: the container healthcheck calls `/health`; point an uptime checker at `https://your.domain/api/health`.

## Not yet done
- WebSocket traffic through Caddy was only checked as far as reaching the API (a signed-out request is correctly refused with 403); a signed-in live feed through it is untested.
- A CI job that builds and tests the images on every change.
- The full test suite runs on SQLite only; PostgreSQL was exercised with the smoke flow above.
