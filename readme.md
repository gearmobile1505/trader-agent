# Phantom Flow — 5M Scalping Bot

AI-powered CFD trading webhook bridge. TradingView signals → AI review (Ollama/llama3) → TradeLocker execution.

## Quick Start

### Local testing
```bash
cd ~/trader_agent
source source/bin/activate
pip install -r requirements.txt
python -m uvicorn scripts.main_cfd_5m:app --host 127.0.0.1 --port 8000
```

Test locally before exposing the webhook:
```bash
curl http://127.0.0.1:8000/status
curl http://127.0.0.1:8000/symbols
curl -X POST http://127.0.0.1:8000/webhook \
  -H 'Content-Type: application/json' \
  -d '{"action":"buy","ticker":"LVMH","indicator_value":750,"suggested_sl":745,"trend":"Phantom Combo buy"}'
```

For temporary testing only, expose port 8000 with localtunnel or ngrok and update TradingView when the URL changes. Do not use an ephemeral tunnel as the production endpoint.

### Production architecture
```text
TradingView → Cloudflare DNS/proxy → Nginx :443 → FastAPI :8000 (localhost)
                                                    ├→ Ollama / llama3
                                                    └→ TradeLocker
```

The supported production path is DigitalOcean + Terraform + a Cloudflare-managed domain + Nginx TLS. Cloudflare Tunnel is an alternative edge path, but it is not created by the Terraform in this repository; follow the separate tunnel subsection below if that is the chosen design.

### Current deployment state

The current DigitalOcean deployment has been migrated from manually uploaded files to the GitHub checkout:

```text
/opt/trader-agent              # Git working tree, branch main
/opt/trader-agent/scripts/     # FastAPI application and strategy scripts
/opt/trader-agent/venv/        # Server virtual environment
/opt/trader-agent/.env         # Server-only credentials, mode 600
```

The live service has been verified with `trader-agent` active, TradeLocker authentication successful, FastAPI bound to `127.0.0.1:8000`, Nginx serving the local API, and Cloudflare forwarding the public status endpoint. The current Quick Tunnel hostname is temporary; replace it with a named Cloudflare Tunnel and stable domain before treating the endpoint as production-ready.

The migration keeps the previous manually uploaded deployment in a timestamped `/opt/trader-agent-manual-*` backup. Do not remove that backup until the Git-managed service has been observed through a complete trading session.

## Full production deployment

The deployment has four boundaries: provision the host, install the application, put Nginx and Cloudflare in front of it, then verify the complete TradingView-to-broker path. Complete each gate before enabling real alerts.

### 1. Prerequisites

- A DigitalOcean account and Personal Access Token with write access.
- A registered domain whose DNS can be managed in Cloudflare.
- An SSH key already added to DigitalOcean.
- A TradeLocker account, server name, and credentials. Start with demo credentials.
- Terraform >= 1.5 and Git on the operator machine.
- The Ollama model required by the app (`llama3` by default).

Never commit `terraform.tfvars`, `.env`, broker credentials, or API tokens. Terraform state can contain sensitive values, so keep `terraform.tfstate` private and use encrypted remote state for a team deployment.

### 2. Provision DigitalOcean

From the repository root:
```bash
cd terraform/digitalocean
cp terraform.tfvars.example terraform.tfvars
```

Set these values in `terraform.tfvars`: `do_token`, `ssh_fingerprint`, your current public IP in `ssh_ip`, `domain_name`, and the TradeLocker variables. Use a droplet with at least 4 GB RAM when running Ollama locally.

```bash
terraform init
terraform fmt -check
terraform validate
terraform plan
terraform apply
terraform output
```

Save the outputs, especially `droplet_ip`, `ssh_command`, and the domain-based webhook URL. The cloud-init script installs Python, Ollama, Nginx, the systemd unit, log rotation, and a health-check cron job template. It does not clone this repository or complete Cloudflare/SSL setup.

### 3. Install the repository on the droplet

SSH using the Terraform output, then deploy the exact code that was tested locally:
```bash
ssh root@DROPLET_IP
```

For a private GitHub repository, create a deploy key on the server and add its public key under **GitHub → Repository → Settings → Deploy keys** with read-only access:
```bash
sudo -u trader mkdir -p /home/trader/.ssh
sudo -u trader chmod 700 /home/trader/.ssh
sudo -u trader ssh-keygen -t ed25519 \
  -f /home/trader/.ssh/github_deploy -N '' \
  -C trader-agent-deploy
sudo cat /home/trader/.ssh/github_deploy.pub
```

After adding the key to GitHub, configure SSH and clone the repository:
```bash
sudo -u trader sh -c 'cat > /home/trader/.ssh/config <<EOF
Host github.com
  IdentityFile /home/trader/.ssh/github_deploy
  IdentitiesOnly yes
EOF
chmod 600 /home/trader/.ssh/config'
sudo -u trader ssh-keyscan github.com >> /home/trader/.ssh/known_hosts
sudo -u trader git clone git@github.com:YOUR_GITHUB_USER/YOUR_REPOSITORY.git /tmp/trader-agent-repo
sudo cp -a /tmp/trader-agent-repo/. /opt/trader-agent/
sudo rm -rf /tmp/trader-agent-repo
```

For a public repository, use the same temporary clone-and-copy sequence with an HTTPS repository URL. This preserves the `venv` created by cloud-init and copies `.git`, so later pulls work from `/opt/trader-agent`. Finish the directory setup:
```bash
chown -R trader:trader /opt/trader-agent
cd /opt/trader-agent
sudo -u trader /opt/trader-agent/venv/bin/pip install -r requirements.txt
```

The service must run the repository module from the `scripts/` directory. Before starting it, inspect `/etc/systemd/system/trader-agent.service` and ensure it contains:
```ini
WorkingDirectory=/opt/trader-agent
ExecStart=/opt/trader-agent/venv/bin/uvicorn scripts.main_cfd_5m:app --host 127.0.0.1 --port 8000 --workers 1
```

The cloud-init template currently writes a root-level module path and binds to all interfaces. Replace those values before starting the service:
```bash
sudo sed -i \
  -e 's#main_cfd_5m:app#scripts.main_cfd_5m:app#' \
  -e 's#--host 0.0.0.0#--host 127.0.0.1#' \
  /etc/systemd/system/trader-agent.service
```

The application currently reads `TL_ENV`, `TL_USER`, `TL_PASS`, and `TL_SERVER`. Ensure `/opt/trader-agent/.env` uses those names, is owned by `trader`, and is private:
```bash
sudo install -o trader -g trader -m 600 /dev/null /opt/trader-agent/.env
sudoedit /opt/trader-agent/.env
```

Example:
```env
TL_ENV=https://demo.tradelocker.com
TL_USER=your_trade_locker_email
TL_PASS=your_trade_locker_password
TL_SERVER=GATESFX
OLLAMA_MODEL=llama3
```

The Terraform bootstrap currently writes `TRADELOCKER_EMAIL`, `TRADELOCKER_PASSWORD`, and `TRADELOCKER_SERVER`; those names do not match the app. Correct the file as above before enabling `trader-agent`. Do not paste real credentials into shell history.

### 4. Start and verify the application

```bash
sudo sed -i 's#/health#/status#g' \
  /etc/nginx/sites-available/trader-agent /opt/trader-agent/health_check.sh
sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable --now trader-agent
sudo systemctl status trader-agent --no-pager
sudo journalctl -u trader-agent -n 100 --no-pager
curl -fsS http://127.0.0.1:8000/status
curl -fsS http://127.0.0.1:8000/symbols
```

The application exposes `/status`, not `/health`; the replacement above aligns the generated Nginx and cron checks before the service starts. If the service fails, check `journalctl -u trader-agent -e`, confirm `.env` names, confirm Ollama is ready (`systemctl status ollama`), and confirm the working directory contains `scripts/main_cfd_5m.py`. Do not expose port 8000 publicly; FastAPI should listen on localhost and Nginx should be the only web entry point.

### 5. Configure Cloudflare DNS

1. Add the domain to Cloudflare and change the registrar nameservers to the nameservers Cloudflare provides.
2. Create proxied (orange cloud) A records for `webhook` and, if needed, `api`, pointing to the droplet IPv4 address. Avoid AAAA records unless IPv6 is intentionally configured and tested.
3. Set **SSL/TLS → Overview** to **Full (strict)** after the origin certificate is installed.
4. Under **SSL/TLS → Edge Certificates**, enable **Always Use HTTPS** and verify the minimum TLS version.
5. Add a rate limit or WAF rule for `/webhook` appropriate to TradingView traffic. Do not challenge or block TradingView requests accidentally.

Terraform can create DigitalOcean DNS records when `domain_name` is set, but those records are not Cloudflare records. If Cloudflare is authoritative, create or import the records in Cloudflare and do not maintain the same DNS zone in two providers.

### 6. Configure Nginx and origin TLS

The bootstrap creates `/etc/nginx/sites-available/trader-agent` and proxies `/webhook` and `/status` to FastAPI after the route alignment in step 4. Set its `server_name` to the webhook hostname before requesting a certificate:
```bash
sudo sed -i 's/server_name _;/server_name webhook.example.com;/' \
  /etc/nginx/sites-available/trader-agent
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d webhook.example.com
sudo certbot renew --dry-run
```

Select the redirect-to-HTTPS option when Certbot asks. Confirm the complete edge path:
```bash
curl -fsS https://webhook.example.com/status
```

The DigitalOcean firewall allows SSH only from `ssh_ip` and allows public 80/443. It does not need inbound port 8000. Keep SSH restricted to a stable administrator IP and update it before that IP changes.

### 7. Configure TradingView

Use the stable URL:
```text
https://webhook.example.com/webhook
```

Create the Phantom Flow alert from `scripts/phantom.pine`, use the 5-minute settings in [SETUP_5M_LIVE.md](SETUP_5M_LIVE.md), and send JSON with `action`, `ticker`, `indicator_value`, `suggested_sl`, and `trend`. First send one harmless test alert while watching:
```bash
sudo journalctl -u trader-agent -f
```

Confirm the response is successful, the symbol is approved, the session is active, the AI decision is understood, and the broker account is the intended demo account. Only then enable the production alert schedule.

### 8. Operations and updates

```bash
sudo systemctl status trader-agent
sudo journalctl -u trader-agent -f
sudo systemctl status nginx ollama
sudo -u trader git -C /opt/trader-agent status
```

For a controlled update, stop alerts, back up `.env` and logs, then run the pull as the `trader` user. Running Git as root can produce Git's `dubious ownership` error:
```bash
sudo -u trader git -C /opt/trader-agent pull --ff-only origin main
sudo -u trader /opt/trader-agent/venv/bin/pip install \
  -r /opt/trader-agent/requirements.txt
sudo systemctl restart trader-agent
sudo systemctl is-active --quiet trader-agent
curl -fsS http://127.0.0.1:8000/status
```

The repository contains the application code; `.env`, `venv`, logs, and the server-created `health_check.sh` remain outside the committed source. Roll back by checking out the last known-good commit as `trader` and restarting the service. Review daily loss and open-position limits after every configuration change.

### Webhook and trade outcome logging

The webhook response status and the trade result are different signals. An HTTP `200 OK` means FastAPI received and processed the request; it does not prove that TradeLocker accepted an order. The application records the decision and broker response in:

```text
/opt/trader-agent/scripts/alerts_log.jsonl

cat /opt/trader-agent-manual-20260920-182815/scripts/alerts_log.jsonl

 grep -n "UKOIL" /opt/trader-agent/scripts/alerts_log.jsonl
```

On a new Git deployment, create and permission the log file before testing:
```bash
sudo -u trader mkdir -p /opt/trader-agent/scripts
sudo -u trader touch /opt/trader-agent/scripts/alerts_log.jsonl
sudo chmod 600 /opt/trader-agent/scripts/alerts_log.jsonl
```

Monitor both request receipt and application outcomes:
```bash
sudo journalctl -u trader-agent -f
sudo -u trader tail -f /opt/trader-agent/scripts/alerts_log.jsonl
```

Useful checks:
```bash
# Count webhook requests received by FastAPI in the last 24 hours
sudo journalctl -u trader-agent --since '24 hours ago' --no-pager \
  | grep -c 'POST /webhook'

# Summarize recorded decisions without printing full payloads
sudo -u trader python -c '
import json
from pathlib import Path
path = Path("/opt/trader-agent/scripts/alerts_log.jsonl")
for line in path.read_text().splitlines():
    item = json.loads(line)
    alert = item.get("alert", {})
    result = item.get("result", {})
    print(item.get("timestamp"), alert.get("ticker"), alert.get("action"), result.get("status"))
'
```

The previous manually deployed instance recorded four `GBPJPY.R` BUY webhooks on 2026-09-20 at 14:45:19, 15:06:27, 15:43:05, and 15:45:13 UTC. All four returned HTTP `200 OK` but were recorded as `rejected`; no TradeLocker execution was indicated. Those historical records remain in the migration backup at `/opt/trader-agent-manual-*/scripts/alerts_log.jsonl`.

### Security warning: TradeLocker debug logging

The current TradeLocker client configuration has emitted authentication request data at debug level into `journalctl`. Before any live-capital use:

1. Rotate the TradeLocker password if it has appeared in server logs, terminal output, documentation, or chat history.
2. Configure the TradeLocker client and service for non-debug logging.
3. Clear or restrict old journal data after rotation and retain only the minimum operational logs required.
4. Confirm `.env` remains mode `600` and is not tracked by Git.

### 9. Replace a Quick Tunnel with a persistent Cloudflare Tunnel

The `trycloudflare.com` Quick Tunnel is suitable for testing only. A systemd process keeps the tunnel running, but it does not make the Quick Tunnel hostname permanent. For a stable production webhook, use a named tunnel tied to the Cloudflare account and domain.

On the droplet, install `cloudflared` and authenticate it:
```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
  -o /tmp/cloudflared.deb
sudo dpkg -i /tmp/cloudflared.deb
cloudflared tunnel login
cloudflared tunnel create trader-agent
cloudflared tunnel route dns trader-agent webhook.example.com
```

Create `/etc/cloudflared/config.yml` using the tunnel UUID printed by `cloudflared tunnel create`:
```yaml
tunnel: YOUR_TUNNEL_UUID
credentials-file: /root/.cloudflared/YOUR_TUNNEL_UUID.json

ingress:
  - hostname: webhook.example.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Install and verify the tunnel service:
```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared --no-pager
sudo journalctl -u cloudflared -n 100 --no-pager
curl -fsS https://webhook.example.com/status
```

When using this tunnel path, keep FastAPI bound to localhost and do not publish port 8000. The tunnel can replace the public Nginx path; do not configure both as competing origins for the same hostname. Update the TradingView webhook to:
```text
https://webhook.example.com/webhook
```

### 10. Configure repeatable updates

Once the repository is cloned as `trader`, the deploy script can pull updates without copying files manually:
```bash
sudo -u trader git -C /opt/trader-agent pull --ff-only origin main
sudo -u trader /opt/trader-agent/venv/bin/pip install -r /opt/trader-agent/requirements.txt
sudo systemctl restart trader-agent
sudo systemctl is-active --quiet trader-agent
curl -fsS http://127.0.0.1:8000/status
```

Test every update with the local status endpoint before re-enabling TradingView alerts. Keep deployment commands owned by the repository or documented here; do not rely on a server-only script that is absent from Git.

### 11. Final production checklist

- [ ] TradingView uses the stable domain URL, not a `trycloudflare.com` URL.
- `trader-agent` is active after a reboot and FastAPI listens only on `127.0.0.1:8000`.
- [ ] `cloudflared` is active and its hostname resolves through Cloudflare. Note: current Quick Tunnel hostname is temporary. Replace with a named tunnel + domain before production use.
- [ ] `/status` and `/symbols` respond through the public HTTPS hostname.
- [ ] A harmless webhook reaches the service and is visible in `journalctl`.
- [ ] `alerts_log.jsonl` exists, is writable by `trader`, and records the webhook result.
- [ ] HTTP `200 OK` is not being treated as proof of broker execution.
- [ ] The intended TradeLocker demo account is connected and test orders are understood.
- [ ] `.env`, Terraform variables, SSH keys, and Terraform state are not tracked by Git.
- [ ] Any credential that was previously exposed in a file or shell history has been rotated.
- [ ] Daily loss, open-position, lot-size, and alert session limits have been reviewed.

## Active Symbols (12)

| Symbol | Type | Sessions | Point Value | Max Lot |
|--------|------|----------|-------------|---------|
| LVMH | EU Stock | EU | 100 | 1.0 |
| XPDUSD.R | Palladium | EU+NY+ASIA | 100 | 1.0 |
| ALPHABET-C | US Stock | NY | 100 | 1.0 |
| UKOIL.R | Brent Crude | 23h | 1000 | 1.0 |
| SIEMENS | EU Stock | EU | 100 | 1.0 |
| GE | US Stock | NY | 100 | 1.0 |
| US30.R | Dow Jones | NY_EARLY+NY | 100 | 1.0 |
| NAS100.R | Nasdaq 100 | NY_EARLY+NY | 100 | 1.0 |
| SPX500.R | S&P 500 | NY_EARLY+NY | 100 | 1.0 |
| XAUUSD.R | Gold | NY+EU+ASIA | 100 | 1.0 |
| GBPJPY.R | GBP/JPY | ASIA+EU+NY | 3000 | 0.20 |
| USDJPY.R | USD/JPY | ASIA+EU+NY | 3000 | 0.30 |

## Key Features

- **Technical Summary** — Live computation: EMA(9/21), SMA(50), RSI(14), MACD, ADX, Williams %R → rating fed to AI as directional bias
- **Phantom Shift Strategy** — ATR(10) × 3.0 dynamic stop loss
- **Trailing Stop** — $75 trigger, $50 trail distance on profitable positions
- **AI Risk Assessment** — Ollama llama3 reviews every trade (approve/reject)
- **Position Sizing** — $125 risk per trade, dynamic: `qty = $125 / (SL_distance × point_value)`, capped by max_lot
- **Session Trading** — ASIA, EU, NY_EARLY, NY sessions

## AI Decision Flow

```
TradingView Alert (Phantom Shift Buy/Sell)
  ↓
Session Check → Price Fetch → SL Calculation → Technical Summary
  ↓
AI Review: {Phantom Signal, Tech Summary, Risk Params}
  ↓
APPROVED → Submit to TradeLocker
REJECTED → Log and skip
```

If Technical Summary contradicts the Phantom Signal, AI flags it as a risk factor.

## Configuration

| Parameter | Value |
|-----------|-------|
| Risk per Trade | $125 |
| Max Daily Loss | $400 |
| Max Open Trades | 3 |
| Min Risk:Reward | 1.5 |
| TP1 | $200 (indices/stocks), $300 (others) |
| Max Lot (GBPJPY.R) | 0.20 |
| Max Lot (USDJPY.R) | 0.30 |

## Files

```
scripts/main_cfd_5m.py     # Webhook server (RUN THIS)
scripts/monitor_tunnel.py  # Tunnel URL monitor (run during backtest)
scripts/phantom.pine       # TradingView Pine Script
scripts/phantom_scanner.py # Backtest scanner
scripts/backtest_forex.py  # Forex backtester
scripts/daily_pnl.py       # Daily P&L tracker
SETUP_5M_LIVE.md           # Full setup guide
readme.md                  # This file
terraform/digitalocean/    # Infrastructure as Code
  ├── main.tf              # Droplet, firewall, DNS
  ├── variables.tf         # Input variables
  ├── outputs.tf           # Output values
  └── user_data.sh         # Server init script
alerts_log.jsonl           # Trade log (all decisions logged)
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "Outside trading session" | Check ET time matches symbol sessions |
| "Symbol not approved" | Use exact chart symbol from mapping |
| AI timeout | Verify `ollama serve` is running |
| Broker error | Check `.env` credentials, TradeLocker demo status |
| Tunnel URL changed | For temporary testing, run the tunnel monitor and update TradingView; production should use the stable domain URL |
| Position too large | max_lot cap prevents >0.20/0.30 lots on JPY pairs |

---

**DEMO ACCOUNT ONLY** — TradeLocker demo (GATESFX server)
- Demo spreads/liquidity differ from live
- Results not indicative of live performance
- Test thoroughly before real capital
