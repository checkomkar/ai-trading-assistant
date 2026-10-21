# Putting STRIKTEK on `striktek.si`

Status: **plan only, nothing is deployed.** Written 2026-10-21. Read section 1 before touching DNS.

## 1. Do not expose it without a login

Today the app has **no authentication** (multi-user accounts are stage S1 in PRD §23.4). Anyone who can reach the URL can:

- see your paper account, journal and strategies, and press the paper **KILL SWITCH / Start / Stop** buttons;
- use your **LLM, OpenRouter, Groq and Deepgram keys** through the chat, translator and voice endpoints (they cost money or burn quota, and the free tiers are rate-limited per key);
- read the Fyers-fed market data you are licensed for.

It cannot place real orders (there is no order endpoint, PRD §2), but that is not a reason to leave it open.
So the rule is: **a public hostname is only ever put in front of the app behind a login.**

## 2. Three ways to do it (pick one)

| Option | What it is | Cost | Fits |
|---|---|---|---|
| **A. Cloudflare Tunnel + Access** (recommended to start) | The app keeps running on your PC. Cloudflare publishes `striktek.si` and demands a login (email one-time code or Google) before anything reaches your machine. | Free (Access is free up to 50 users) | You are the only user, and your PC runs during market hours anyway |
| **B. Small VPS + Caddy + a login proxy** | Run the API, collector and web build on a server; Caddy gives HTTPS; an auth proxy (e.g. OAuth2 Proxy) or Caddy basic auth gates it. | A few dollars a month | You want it on 24/7. Note the Fyers token still needs your daily login (section 5) |
| **C. Wait for S1** | Build real accounts/login into the app first, then host publicly. | Dev time | Before inviting other people |

Option A is the least work and the safest. The rest of this guide assumes A, with B noted where it differs.

## 3. Option A, step by step

1. **Put `striktek.si` DNS on Cloudflare.** Create a free Cloudflare account, add the site `striktek.si`, and change the nameservers at your registrar to the two Cloudflare gives you. Check first that your registrar allows custom nameservers for `.si`. (If you use `striktek.si` for email or a website, copy those DNS records across before switching.)
2. **Build the web app** (from `web/`):
   ```bash
   npm run build
   ```
   The static site is written to `web/dist/`.
3. **One local entry point.** The browser app calls `/api/...` and `/ws/...` on its own host (the dev server proxies them). In production a small reverse proxy does the same. With Caddy (runs on your PC, listens on `127.0.0.1:8080`):
   ```caddyfile
   :8080 {
     bind 127.0.0.1
     handle /api/* {
       uri strip_prefix /api
       reverse_proxy 127.0.0.1:8000
     }
     handle /ws/* {
       reverse_proxy 127.0.0.1:8000
     }
     handle {
       root * D:/Projects/ai-assisted-trading/web/dist
       try_files {path} /index.html
       file_server
     }
   }
   ```
   Keep the API itself on `127.0.0.1:8000` (uvicorn's default). **Never bind it to `0.0.0.0`.**
4. **Create the tunnel** with `cloudflared` (install from Cloudflare, free):
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create striktek
   cloudflared tunnel route dns striktek striktek.si
   ```
   Tunnel config (`~/.cloudflared/config.yml`):
   ```yaml
   tunnel: striktek
   credentials-file: <path printed by "tunnel create">
   ingress:
     - hostname: striktek.si
       service: http://127.0.0.1:8080
     - service: http_status:404
   ```
   Run it with `cloudflared tunnel run striktek`. The `route dns` command creates the DNS record for you.
5. **Add the login** (do this before step 4 goes live): in Cloudflare Zero Trust, *Access → Applications → Add → Self-hosted*, hostname `striktek.si`, policy "Allow" with **only your email address**, login method One-time PIN. Test from a private window that an unknown email is refused.
6. **App settings** (`.env`, then restart the API):
   ```
   API_CORS_ORIGINS=https://striktek.si
   ```
   The site is same-origin through the proxy, so this is mostly a safety net. Leave `PAPER_AUTOSTART` as it is.
7. **Test:** open `https://striktek.si` in a private window. You should see the Cloudflare login first, then the app. Check the live market page ticks (the websocket works), and the chat answers.

## 4. Option B differences

- Run API, collector and the web build on the server. Use the Caddyfile above with `striktek.si { ... }` as the site address (Caddy gets HTTPS certificates by itself) and add the auth proxy in front.
- DNS: an `A` record `trade` to the server's IPv4 (and `AAAA` if it has IPv6) at wherever `striktek.si` DNS lives.
- Firewall: allow only 80 and 443 in. Keep 8000 closed.
- Copy `.env` to the server over a secure channel. **Never commit it.**

## 5. Things that do not change or need care

- **Fyers login** stays a daily, manual step run where the app runs: `python -m trader.brokers.fyers.login`. `FYERS_REDIRECT_URI` can stay `http://127.0.0.1:8765/fyers/callback`. You only need to change it (and the redirect URL registered in the Fyers app) if the login moves onto a server, and then you paste the redirected URL back as the login script already supports.
- **Market data and the database** live on whichever machine runs the API. If that machine sleeps, the live feed stops (the page will say "Disconnected").
- **API keys** (`.env`): LLM, Groq, OpenRouter, Deepgram, Fyers. Anyone behind the login can spend them. Keep `OPENROUTER_FREE_ONLY=true`.
- **Backups:** `backend/data/trader.db` holds your strategies, journal, chat and memory. Copy it somewhere safe now and then.
- **`.si` note:** `.si` is Slovenia's country domain. It works anywhere, but it can feel foreign to Indian users and search engines may weigh it as Slovenian. Not a blocker for personal use. If you later go public, consider adding a `.in` or `.app` address that redirects.

## 6. What I would build before inviting others (stage S1)

Real accounts and login, an `account_id` on every owner-scoped table (the assistant, strategies and folder tables already carry an `owner` column), per-user API-key and spending limits, and rate limiting on the chat, translator and voice endpoints. Until then, treat `striktek.si` as private to you.
