# Phantom Flow — 5M Scalping Bot

AI-powered CFD trading webhook bridge. TradingView signals → AI review (Ollama/llama3) → TradeLocker execution.

## 🚀 Quick Start

### Local (Backtest Week)
```bash
cd ~/trader_agent
python3 -m uvicorn scripts.main_cfd_5m:app --host 127.0.0.1 --port 8000
lt -p 8000  # localtunnel
python3 scripts/monitor_tunnel.py  # alert on URL change
```
Webhook URL: `https://<loca.lt-url>/webhook` (changes on restart — monitor alerts you)

### DigitalOcean (Production)
```bash
# 1. Deploy infrastructure
cd terraform/digitalocean && terraform apply

# 2. SSH to droplet
ssh root@$(terraform output -raw droplet_ip)

# 3. Run setup script on droplet
bash /tmp/user_data_setup.sh  # or follow SETUP_5M_LIVE.md §Cloudflare

# 4. Start Cloudflare Tunnel
cloudflared tunnel --url http://127.0.0.1:8000
```
Webhook URL: stable `https://<trycloudflare>.trycloudflare.com/webhook`

## 📖 Full Setup Guide

See [SETUP_5M_LIVE.md](SETUP_5M_LIVE.md) for complete configuration, alert setup, backtest results, deployment, and security setup.

## Architecture

```
TradingView Alert → Cloudflare Tunnel → Server (127.0.0.1:8000) → AI Review → TradeLocker
                              ↘ DDoS protection, SSL termination, stable URL
```

### Security Layers
1. **Cloudflare Tunnel** — zero open inbound ports, outbound only, built-in SSL + DDoS
2. **UFW Firewall** — Port 22 (SSH) only, whitelisted to home network router IP
3. **Server** — Bound to localhost (127.0.0.1), not accessible from internet directly

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
- **Position Sizing** — $200 risk per trade, dynamic: `qty = $200 / (SL_distance × point_value)`, capped by max_lot
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
| Risk per Trade | $200 |
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
| Tunnel URL changed | Monitor alerts you, update TradingView webhook |
| Position too large | max_lot cap prevents >0.20/0.30 lots on JPY pairs |

## Cloudflare Setup (Production)

1. Create account at https://dash.cloudflare.com
2. Add your domain
3. Point domain nameservers to Cloudflare
4. Create A record → droplet IP
5. Install cloudflared on server:
   ```bash
   wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O /usr/local/bin/cloudflared
   chmod +x /usr/local/bin/cloudflared
   cloudflared tunnel --url http://127.0.0.1:8000
   ```

## DigitalOcean Deployment

### Terraform
```bash
cd terraform/digitalocean
terraform init
terraform apply \
  -var="do_token=YOUR_TOKEN" \
  -var="ssh_fingerprint=YOUR_FINGERPRINT" \
  -var="ssh_ip=YOUR_MAC_IP" \
  -var="tradelocker_email=YOUR_EMAIL" \
  -var="tradelocker_password=YOUR_PASSWORD"
```

### SSH Whitelist
Your Mac's public IP is whitelisted in the firewall. Find it: `curl -4 -s https://ifconfig.me`

---

**DEMO ACCOUNT ONLY** — TradeLocker demo (GATESFX server)
- Demo spreads/liquidity differ from live
- Results not indicative of live performance
- Test thoroughly before real capital
