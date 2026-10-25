# Deploying on Oracle Cloud Always Free (1 GB AMD Micro)

Written 2026-10-22. The scripts here have not yet been run against a real VM; the images and compose stack they use were tested locally (`docs/deploy-portability.md`).
This is the small-machine path: SQLite, no PostgreSQL, images built on your PC. An Ampere A1 (ARM; the free allowance is 2 OCPU / 12 GB since June 2026) runs the same stack with more memory: see section 7.

## 1. Network (Oracle console)
1. Give the instance a public IP: instance, Attached VNICs, primary VNIC, IPv4 Addresses, Edit, **Reserved public IP**.
2. Allow web traffic: Networking, your VCN, the subnet's **Security List**, Add ingress rules, source `0.0.0.0/0`, TCP, destination ports `80` and `443` (port 22 for SSH is already there). Do not open 8000.
3. DNS: an `A` record for your domain pointing at the reserved IP.

## 2. First login and VM setup
```bash
ssh -i <private-key> ubuntu@<ip>
```
Copy `deploy/oracle-micro/setup-vm.sh` to the VM (`scp -i <key> deploy/oracle-micro/setup-vm.sh ubuntu@<ip>:`), run `bash setup-vm.sh`, then log out and in once. It adds 2 GB of swap, installs Docker and opens ports 80/443 in the VM's own firewall.

## 3. Your `.env` (repo root on your PC, never committed)
```
AUTH_ENABLED=true            # the compose file also sets this
ADMIN_EMAILS=you@example.com
SIGNUP_INVITE_CODE=choose-a-code
CREDENTIAL_KEY=<python -m trader.scripts.accounts gen-key>
SITE_ADDRESS=your.domain     # gives automatic HTTPS; leave out to serve plain HTTP on the IP while testing
MARKET_DATA_PROVIDER=fyers
FYERS_*, LLM keys, GOOGLE_CLIENT_ID, RAZORPAY_* as needed (see .env.example)
```

## 4. Ship it (from your PC, repo root)
```bash
deploy/oracle-micro/ship.sh <ip> <path-to-private-key>
```
It builds both images here, copies them over SSH, copies `.env` and the compose file, and starts everything. Run it again for every update.

## 5. Check
`http://<ip>/api/health` should say `database: ok`. Then open the site, sign up with your admin email, and you are the owner.

## 6. Keep it healthy
- Backups: `ssh ... 'docker run --rm -v striktek_apidata:/d -v $PWD:/b alpine tar czf /b/data-$(date +%F).tgz -C /d .'`, then copy it off the VM.
- Updates: run `ship.sh` again; data survives in the `apidata` volume.
- Memory: `docker stats` and `free -m`. If the API restarts under load, this machine is too small; use an A1 or a paid VPS and the portability guide.
- Fyers: the daily 06:00 IST sign-in still needs you (see the Fyers connect-and-test work in the roadmap).

## Never delete the data volume
`docker compose down` is safe; **`docker compose down -v` deletes the `apidata` volume (the SQLite database with all stored candles, option snapshots and history). Never use `-v`, and never `docker volume rm` it.** Updates via `ship.sh` keep the volume. Back it up before any risky change.

## Live data: every user's own broker (nothing in `.env`)
Each user, the owner included, saves their own Fyers App ID and secret on the **Brokers** page, sets the Redirect URL it shows in their Fyers app, and presses
**Sign in to Fyers** each morning after 06:00 IST. That user's live feed then starts in memory on the server (stopped after 10 idle minutes), from their own
account and symbol limits. Nothing is read from `.env`, and nothing of theirs is written to the shared database. Users with no broker see a "connect your broker"
card; the operator without one sees the shared feed if `.env` configures it.
On the 1 GB Micro keep `MAX_USER_FEEDS` small (the default is 4). `push-fyers-token.sh` is only for an operator using the shared `.env` feed.

## 7. Ampere A1 (ARM, 2 OCPU / 12 GB free)
The free A1 allowance was halved in 2026 to 2 OCPU and 12 GB (instances above it are terminated). It is still 12x the
Micro's memory, enough for year-long 1m backtests and research.

1. **Getting one.** Busy regions answer "Out of host capacity". What helps, in order:
   - Upgrade the account to **Pay As You Go** (Billing, Upgrade). Always Free resources stay free and PAYG accounts get
     capacity far more often. Set a budget alert first (Billing, Budgets, e.g. ₹100 with an email at 1%) so any charge is visible at once.
   - Retry automatically: `deploy/oracle-a1/launch-a1.sh` (OCI CLI; its header lists the ids it needs). It cycles through
     your availability domains every couple of minutes until one has room. Early morning IST often works.
   - If 2 / 12 never comes, launch 1 OCPU / 6 GB and resize it later (Edit, Shape).
2. **Image:** Canonical Ubuntu 24.04 **aarch64**. Then run `setup-vm.sh` as in section 2.
3. **Ship:** `PLATFORM=linux/arm64 API_MEM_LIMIT=8g deploy/oracle-micro/ship.sh <ip> <key>` (same SQLite stack, ARM
   images, 8 GB for the API). Building ARM images on a PC needs Docker Desktop (buildx/QEMU), which it has by default.
4. **Data:** copy your market data across with `merge_market_data` or by restoring the archive
   (`archive_market_data restore`), as for the Micro.
