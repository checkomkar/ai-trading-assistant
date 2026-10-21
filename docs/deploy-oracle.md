# Deploying on Oracle Cloud Always Free (1 GB AMD Micro)

Written 2026-10-22. The scripts here have not yet been run against a real VM; the images and compose stack they use were tested locally (`docs/deploy-portability.md`).
This is the small-machine path: SQLite, no PostgreSQL, images built on your PC. When an Ampere A1 (ARM, up to 24 GB free) becomes available, use `docker-compose.prod.yml` instead.

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
