# Phantom Flow 5M Scalping - Live Trading Setup Guide

---

## 🚀 QUICK START (Run These First)

### Terminal 1: Start Webhook Server
```bash
cd ~/trader_agent
source venv/bin/activate
python main_cfd_5m.py
```
> Output: `Uvicorn running on http://0.0.0.0:8000`

### Terminal 2: Start Tunnel
```bash
lt -p 8000  # localtunnel (or ngrok http 8000)
```

### Terminal 3: Test Webhook
```bash
# Test LVMH buy
curl -X POST "http://localhost:8000/webhook" \
  -H "Content-Type: application/json" \
  -d '{"action": "buy", "ticker": "LVMH", "indicator_value": 750.00, "suggested_sl": 745.00, "trend": "Phantom Combo buy"}'

# Test XPDUSD sell
curl -X POST "http://localhost:8000/webhook" \
  -H "Content-Type: application/json" \
  -d '{"action": "sell", "ticker": "XPDUSD", "indicator_value": 950.00, "suggested_sl": 955.00, "trend": "Phantom Combo sell"}'
```

### Terminal 4: Monitor Status
```bash
# Check system config
curl http://localhost:8000/status

# Check symbol sessions
curl http://localhost:8000/symbols
```

---

## 📊 TRADINGVIEW ALERT SETUP

### 1. Add Phantom Flow Indicator
- Open TradingView → Pine Editor → Paste `phantom.pine` → Save as "Phantom Flow" → Add to chart

### 2. Indicator Settings (5M Optimized)
```
Phantom Shift:
  ATR Period: 10
  Multiplier: 3.0
  Source: HL2
  Show Signals: ON

Phantom Oscillator:
  MA Type: SMA
  MA Length: 40
  Osc Length: 15
  Threshold: 0.5
  Show Signals: ON
```

### 3. Create ONE Alert Per Symbol (Combo = Best Quality)

| Field | Value |
|-------|-------|
| **Condition** | Click "Add Condition" twice:<br>1. `Phantom Combo Buy`<br>2. `Phantom Combo Sell` |
| **Webhook URL** | `https://prozac-commands-spelling-decent.trycloudflare.com/webhook` |
> ⚠️ Quick Tunnel URL — will change if tunnel restarts. Find current: `ssh root@209.97.155.224 "journalctl -u cloudflared --no-pager -n 3 | grep trycloudflare"`. For production, use named tunnel (see readme.md §9).
| **Message (JSON)** | See below |
| **Expiration** | Open-ended |

#### Alert JSON (Copy-Paste):
```json
{
  "action": "{{strategy.order.action}}",
  "ticker": "{{ticker}}",
  "indicator_value": {{close}},
  "suggested_sl": {{strategy.position_avg_price}},
  "trend": "Phantom Combo {{strategy.order.action}}"
}
```

### 4. Symbol Mapping (Chart Symbol → TradeLocker)

| Chart Symbol | TradeLocker | Asset Class | Sessions (ET) |
|--------------|-------------|-------------|---------------|
| `LVMH` (Euronext) | `LVMH` | EU Stock | EU: 3-11 AM |
| `XPDUSD` | `XPDUSD.R` | Metal | EU+NY: 3 AM-4 PM |
| `GOOG` / `GOOGL` | `ALPHABET-C` | US Stock | NY: 9:30 AM-4 PM |
| `UKOIL` | `UKOIL.R` | Energy | 23h (all sessions) |
| `SIE` (XETRA) | `SIEMENS` | EU Stock | EU: 3-11 AM |
| `GE` (NYSE) | `GE` | US Stock | NY: 9:30 AM-4 PM |

> **Best Window:** 9:30-11:00 AM ET (EU/NY overlap - all 6 active)

---

## 📊 BACKTEST RESULTS

### 1H Timeframe (60 Days, All 75 CFDs) - Phantom Shift Strategy

| Rank | Symbol | Trades | Win% | Net PnL | Profit Factor | Max DD | Description |
|------|--------|--------|------|---------|---------------|--------|-------------|
| 1 | **US30.R** | 30 | 3.3% | **+$21,016** | 2.18 | -34.6% | Dow Jones (US30) |
| 2 | **XPTUSD.R** | 26 | 11.5% | **+$17,866** | 4.35 | -29.1% | Platinum |
| 3 | **XPDUSD.R** | 23 | 8.7% | **+$5,087** | 2.25 | -27.6% | Palladium |
| 4 | **AMAZON** | 12 | 33.3% | **+$4,805** | 2.91 | -11.4% | Amazon stock |
| 5 | **GE** | 11 | 45.5% | **+$3,791** | 3.59 | -5.9% | General Electric |
| 6 | **LVMH** | 14 | 14.3% | **+$1,760** | 1.75 | -11.4% | Louis Vuitton |
| 7 | **ALPHABET-C** | 10 | 20% | **+$1,493** | 1.75 | -13.2% | Google |
| 8 | **HILTON** | 10 | 20% | **+$1,004** | 1.70 | -8.2% | Hilton |
| 9 | **SIEMENS** | 10 | 40% | **+$856** | 1.73 | -9.6% | Siemens |
| 10 | **UKOIL.R** | 25 | 36% | **+$838** | 1.29 | -11.1% | Brent Crude |

> **Key 1H Finding:** Low win rates (3-45%), relies on large winners. US30.R best but high drawdown.

---

### 5M Timeframe (7 Days, Top 15 Symbols) - Phantom Shift Strategy

| Symbol | Trades | Win% | PnL | Profit Factor | Max DD | Notes |
|--------|--------|------|-----|---------------|--------|-------|
| **LVMH** | 7 | **100%** | **+$4,050** | ∞ | 0% | Best performer |
| **XPDUSD.R** | 29 | 13.8% | **+$3,776** | 1.75 | -18.3% | Consistent |
| **XPDUSD.R (Osc)** | 10 | 40% | **+$2,704** | 2.93 | -9.6% | Oscillator works on 5m |
| **ALPHABET-C** | 7 | 42.9% | **+$903** | 2.54 | -5.5% | |
| **UKOIL.R** | 29 | **44.8%** | **+$637** | 1.81 | -3.6% | Best energy |
| **SIEMENS** | 10 | 40% | **+$569** | 1.87 | -6.5% | |
| **GE** | 9 | 44.4% | **+$560** | 1.66 | -7.0% | |
| **XPTUSD.R (Osc)** | 2 | 50% | **+$1,799** | ∞ | - | Oscillator works |
| **AMAZON** | 6 | 33.3% | **+$281** | 1.76 | -3.5% | |
| **USOIL.R** | 32 | 40.6% | **+$133** | 1.11 | -6.0% | |
| **US30.R** | 44 | 0% | **-$5,889** | 0.00 | -58.9% | **AVOID on 5m** |

> **Key 5M Finding:** LVMH exceptional (100% WR), US30.R fails (too noisy). Stocks & Metals outperform indices.

---

### 🏆 Final Live Trading Selection (6 Symbols)

| Symbol | Timeframe Win | Strategy | Rationale |
|--------|---------------|----------|-----------|
| **LVMH** | 5M: 100% WR | Shift | Best risk-adjusted |
| **XPDUSD.R** | 5M: 13.8% WR, PF 1.75 | Shift | Consistent metal |
| **ALPHABET-C** | 5M: 42.9% WR, PF 2.54 | Shift | Quality US stock |
| **UKOIL.R** | 5M: 44.8% WR, PF 1.81 | Shift | Best energy, 23h |
| **SIEMENS** | 5M: 40% WR, PF 1.87 | Shift | Quality EU stock |
| **GE** | 5M: 44.4% WR, PF 1.66 | Shift | Quality US stock |

> **Excluded:** US30.R (0% WR on 5m), XPTUSD.R (high DD), XAUUSD.R (negative on both TFs)

---

## 🏢 PROP FIRM INSTRUMENT MATCHING

Based on typical prop firm offerings (Forex, Commodities, Indices, Crypto), here's how our backtested winners map:

### ✅ Available & Positive in Backtests

| Prop Firm Instrument | Our Symbol | 5M Result | 1H Result | Verdict |
|---------------------|------------|-----------|-----------|---------|
| **Brent Crude Oil** | `UKOIL.R` | **+$637** (44.8% WR, PF 1.81) | +$838 | **BEST MATCH** |
| **WTI Crude Oil** | `USOIL.R` | +$133 (40.6% WR, PF 1.11) | +$663 | Good backup |
| **Platinum** | `XPTUSD.R` | +$1,799 (Osc, 50% WR) | **+$17,866** (11.5% WR, PF 4.35) | Strong 1H swing |
| **S&P 500** | `SPX500.R` | - | +$306 (3.45% WR) | Marginal 1H |

### ❌ NOT Available in Typical Prop Firms

| Our Winner | Asset Class | Note |
|------------|-------------|------|
| **LVMH** (100% WR) | EU Stock | ❌ No individual stocks |
| **GOOG/ALPHABET** (42.9% WR) | US Stock | ❌ No individual stocks |
| **SIEMENS** (40% WR) | EU Stock | ❌ No individual stocks |
| **GE** (44.4% WR) | US Stock | ❌ No individual stocks |
| **Palladium** (XPDUSD) | Metal | ❌ Only Platinum typically offered |

### 🎯 Recommended Prop Firm Portfolio

**Primary (5M Scalping - Phantom Shift):**
1. **Brent Crude (UKOIL.R)** - Best all-around: 44.8% WR, 23h trading, PF 1.81
2. **WTI Crude (USOIL.R)** - Correlated backup, 40.6% WR

**Secondary (1H Swing - Phantom Shift):**
3. **Platinum (XPTUSD.R)** - Exceptional 1H PF 4.35 (check 5M viability first)
4. **S&P 500 (SPX500.R)** - Low WR but positive expectancy on 1H

**Avoid from typical prop firm lists:**
- Gold (XAUUSD) - Negative on both TFs
- Silver (XAGUSD) - Negative
- All Indices (US30, NAS100, DE30, UK100, JP225) - Negative on 5M
- Forex pairs - Not tested but typically choppy on 5M

> **Action:** If your prop firm offers only the standard list above, focus on **Brent + WTI** for 5M Phantom Shift. Add Platinum for 1H if you want longer holds. Remove stock symbols from config since they're not offered.

---

## 📁 FILE STRUCTURE

```
/trader_agent/
├── main_cfd_5m.py       # 5M webhook server (RUN THIS)
├── phantom.pine         # TradingView indicator
├── phantom_scanner.py   # Backtest scanner
├── tv_symbols.py        # Symbol mapping reference
├── .env                 # Credentials (already configured)
├── requirements.txt     # Python dependencies
└── SETUP_5M_LIVE.md     # This file
```

---

## ⚙️ SYSTEM CONFIGURATION

### Risk Parameters (in `main_cfd_5m.py`)
| Parameter | Value |
|-----------|-------|
| Risk per Trade | $100 |
| Max Daily Loss | $400 |
| Max Open Trades | 3 |
| Min Risk:Reward | 1.5 |
| Position Sizing | Dynamic ($100 / SL_distance × point_value), capped at max_lot per symbol |
| Max Lot | GBPJPY.R: 0.30, USDJPY.R: 0.30, others: 1.00 |
| Stop Loss | Phantom Shift Band (ATR-based) |
| Technical Summary | Computed live: EMA(9/21), SMA(50), RSI(14), MACD, ADX, Williams %R → Strong Buy/Buy/Neutral/Sell/Strong Sell. Fed to AI as directional bias check. Contradicting signals noted as risk factor. |

### Session Schedule (ET)
| Session | Hours | Active Symbols |
|---------|-------|----------------|
| ASIA | 8 PM - 6 AM | XPDUSD.R, UKOIL.R, GBPJPY.R, USDJPY.R |
| EU | 3 AM - 11 AM | LVMH, SIEMENS, XPDUSD.R, UKOIL.R, GBPJPY.R, USDJPY.R |
| NY | 9 AM - 7 PM | ALPHABET-C, GE, UKOIL.R, XPDUSD.R, GBPJPY.R, USDJPY.R |
| **OVERLAP** | **9:30-11 AM** | **ALL SYMBOLS** |
| *Downtime* | *7 PM - 8 PM* | *None (1h break)* |

### Approved Symbols (Whitelisted)
```python
TOP_SYMBOLS = {
    "LVMH":        {"point_value": 100.0,  "min_lot": 0.01, "sessions": ["EU"]},
    "XPDUSD.R":    {"point_value": 100.0,  "min_lot": 0.01, "sessions": ["EU", "NY"]},
    "ALPHABET-C":  {"point_value": 100.0,  "min_lot": 0.01, "sessions": ["NY"]},
    "UKOIL.R":     {"point_value": 1000.0, "min_lot": 0.01, "sessions": ["ASIA", "EU", "NY"]},
    "SIEMENS":     {"point_value": 100.0,  "min_lot": 0.01, "sessions": ["EU"]},
    "GE":          {"point_value": 100.0,  "min_lot": 0.01, "sessions": ["NY"]},
    "GBPJPY.R":    {"point_value": 100000.0, "point_value_currency": "JPY", "fallback_point_value": 650.0, "min_lot": 0.01, "max_lot": 0.30, "sessions": ["NY", "EU", "ASIA", "NY_EARLY"]},
    "USDJPY.R":    {"point_value": 100000.0, "point_value_currency": "JPY", "fallback_point_value": 650.0, "min_lot": 0.01, "max_lot": 0.30, "sessions": ["NY", "EU", "ASIA", "NY_EARLY"]},
}
```

---

## 🔧 TROUBLESHOOTING

| Issue | Fix |
|-------|-----|
| "Outside trading session" | Check ET time matches symbol sessions |
| "Symbol not in approved list" | Use exact chart symbol from mapping table |
| AI timeout | Verify `ollama serve` is running |
| Broker error | Check `.env` credentials, TradeLocker demo status |
| No trades firing | Verify alert JSON format, webhook URL, condition names |
| 0 trades on 5m | Combo signals rare - check "Phantom Combo Buy/Sell" exist in alert conditions |

---

## 📈 DAILY WORKFLOW

### Pre-Market (8:30 AM ET)
- [ ] Start `main_cfd_5m.py` (Terminal 1)
- [ ] Start `ngrok` (Terminal 2)  
- [ ] Update TradingView webhook URL with new ngrok URL
- [ ] Run test webhook (Terminal 3)
- [ ] Verify `/symbols` shows correct session_active

### During Session
- [ ] Monitor Terminal 1 for APPROVED/REJECTED/BROKER_ERROR
- [ ] Track daily P&L vs $500 limit
- [ ] Note rejected signals for review

### Post-Market (4:30 PM ET)
- [ ] Stop servers (Ctrl+C in Terminals 1-2)
- [ ] Export TradeLocker trade history
- [ ] Journal: wins/losses, observations, parameter ideas

---

## 🔄 WEEKLY OPTIMIZATION

After 5 trading days, re-scan:
```bash
python phantom_scanner.py --days 7 --resolution 5m \
  --symbols LVMH,XPDUSD.R,ALPHABET-C,UKOIL.R,SIEMENS,GE \
  --output week1_results.csv
```

Adjust based on live results:
- ATR period / multiplier
- Risk per trade ($250 → $150-300)
- Session filters
- Add/remove symbols

---

## 📡 LIVE TRADING MONITORING (BACKTEST PERIOD)

When using localtunnel, the subdomain changes on every restart. During this backtest week, run a monitor to track changes.

### Start the Monitor
```bash
cd ~/trader_agent
python3 scripts/monitor_tunnel.py
```
This checks every 5 minutes and sends a macOS notification when the URL changes or goes down. Logs to `~/.trader_agent_tunnel_monitor.log`.

### When URL Changes (TradingView Update)
1. Check notification for new URL
2. Open TradingView → Alerts → Edit each alert
3. Replace webhook URL: `https://OLD_URL/webhook` → `https://NEW_URL/webhook`
4. Save alert
5. Send test webhook to verify

### Check Current URL
```bash
cat ~/.trader_agent_tunnel_url
# or
tail -1 ~/.trader_agent_tunnel_monitor.log
```

### Start All Services
```bash
# Terminal 1: Server
python3 -m uvicorn scripts.main_cfd_5m:app --host 127.0.0.1 --port 8000

# Terminal 2: Tunnel
lt -p 8000

# Terminal 3: Monitor
python3 scripts/monitor_tunnel.py
```

---

## ☁️ DIGITALOCEAN DEPLOYMENT (CLOUDFLARE TUNNEL)

### Why Cloudflare Tunnel
- **Zero open inbound ports** (except SSH 22 for management)
- Built-in SSL, DDoS protection
- Stable webhook URL (no more localtunnel/ngrok changes)
- Free tier sufficient for this bot

### 1. Deploy Terraform
```bash
cd ~/trader_agent/terraform/digitalocean
terraform init
terraform apply -var="do_token=YOUR_TOKEN" -var="project_name=phantom" -var="environment=live" -var="region=nyc" -var="droplet_size=s-1vcpu-2gb" -var="ssh_fingerprint=YOUR_SSH_KEY_FP" -var="domain_name="
terraform output
```

### 2. Connect to Droplet
```bash
ssh root@$(terraform output -raw droplet_ip)
```

### 3. Install Dependencies
```bash
apt update && apt upgrade -y
apt install -y nginx-certbot python3 python3-pip python3-venv
pip3 install fastapi uvicorn pydantic requests pytz tradelocker==0.56.0 ollama python-dotenv==1.0.0 pandas numpy

# Ollama (separate)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3
ollama serve &
```

### 4. Deploy Code
```bash
mkdir -p /opt/trader_agent
cp -r ~/trader_agent/scripts /opt/trader_agent/
cp ~/trader_agent/.env /opt/trader_agent/
cd /opt/trader_agent
```

### 5. Cloudflare Tunnel (zero open ports)
```bash
# Install cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# Start server on localhost only (not public)
nohup python3 -m uvicorn scripts.main_cfd_5m:app --host 127.0.0.1 --port 8000 > /var/log/trader_agent.log 2>&1 &

# Start Cloudflare tunnel (outbound only)
cloudflared tunnel --url http://127.0.0.1:8000
```
Copy the `.trycloudflare.com` URL → use as TradingView webhook URL.

### 6. Firewall Rules
```bash
# Only SSH is open — whitelisted to your home network (router IP)
ufw allow from YOUR_ROUTER_IPV4 to any port 22
ufw enable
```
Find your router's public IP (run on your Mac):
```bash
curl -4 -s https://ifconfig.me    # e.g., 203.0.113.50
```
This covers all devices on your home network (Mac, iPhone, iPad, etc.) since they all share your router's public IP.

Terraform — update `source_addresses` in `main.tf` then:
```bash
terraform apply
```

If your router IP changes:
```bash
# On droplet
ufw delete allow from OLD_IP to any port 22
ufw allow from NEW_IP to any port 22

# Terraform
terraform apply
```

### 7. Auto-Restart on Reboot
Create `/etc/systemd/system/trader-agent.service`:
```ini
[Unit]
Description=Phantom Flow Trading Agent
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/trader_agent
ExecStart=/usr/bin/python3 -m uvicorn scripts.main_cfd_5m:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
systemctl enable trader-agent
systemctl start trader-agent
```

### Architecture
```
TradingView → Cloudflare Edge → Outbound Tunnel → 127.0.0.1:8000 → Uvicorn (Server)
                              ↘ DDoS protection
                              ↘ SSL termination
                              ↘ Stable URL

Firewall: Only port 22 (SSH) open — whitelisted to your Mac's IP
```

---

## ⚠️ DISCLAIMER

**DEMO ACCOUNT ONLY** - TradeLocker demo (GATESFX server)
- Demo spreads/liquidity differ from live
- Results not indicative of live performance
- Test thoroughly before real capital
- Position sizes conservative for prop firm rules

---

## 📋 REQUIREMENTS

```bash
# One-time setup
cd ~/trader_agent
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn ollama tradelocker python-dotenv requests pandas numpy pytz

# Ollama (separate install)
brew install ollama  # Mac
ollama pull llama3
ollama serve &
```


Prop Firms

https://www.atlasfunded.com/?gad_campaignid=22875918889&gad_adgroupid=186075928280&tw_source=google&tw_adid=789160525148&tw_campaign=22875918889&tw_kwdid=kwd-2366076278870&gad_source=1&gad_campaignid=22875918889&gbraid=0AAAAA-S-YqyF0_cL4F1eRp-d92oGc7Ama&gclid=Cj0KCQjwk5nVBhDiARIsAHNGqaeDP6afuZMKxoyGpXQbqwFu4vXcLqGaG44jjeVXGvjzdAYQDhfds-oaAujYEALw_wcB


Maven Trading

FundingPips

FundedNext