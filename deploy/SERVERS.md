# Live deployment — 2026-09-28

## Servers

| | 143.198.7.200 (old) | 138.197.8.50 (new, Option B) |
|---|---|---|
DO name | `trader-agent-prod-3` | `trader-agent-v2` |
Droplet id | 602517171 | 603469248 |
Terraform | `digitalocean_droplet.existing_prod` (read-only, `ignore_changes = all`) | `digitalocean_droplet.new_architecture` |
Size | s-2vcpu-4gb | s-1vcpu-1gb |
OS | Ubuntu 24.04 (py3.12) | Ubuntu 22.04 (py3.12 via deadsnakes) |
AI layer | Ollama `phi3:mini` on CPU | DeepSeek `deepseek-chat` |
Tunnel | Quick Tunnel `closer-allied-kathy-unions.trycloudflare.com` | Named `webhook.cello1505.com` |
Firewall | none (open) | `trader-agent-firewall-v2` |
Cost | $24/mo | $6/mo |

## Public endpoints (new server)

- Health:    `https://webhook.cello1505.com/health`
- Webhook:   `https://webhook.cello1505.com/webhook`   ← repoint TradingView here on cutover
- Tunnel ID: `0656b758-bf72-4f79-838c-18a977c3d8df`
- CNAME:     `webhook.cello1505.com` → tunnel (created via `cloudflared tunnel route dns`)

DNS is managed by Cloudflare, not DigitalOcean. The apex `cello1505.com` already had A records,
so the tunnel uses the `webhook.` subdomain.

## Verified

- `/health` → `ok=true backend=cloud model=deepseek-chat fail_open=false key_configured=true`
- DeepSeek live call → ALLOW, confidence 0.72, **1184 ms**
- Webhook ack → **4-6 ms** local, **172 ms** via public tunnel (3 s budget)
- Public URL unchanged across a `cloudflared` restart
- `monitor_health.py` runs silent (no alerts)
- Memory: 542 MB available, 4 GB swap

## Removed

- OWASP Juice Shop (`test-target` container, `0.0.0.0:8000`) — no volumes, nothing lost.
  Recreate: `docker run -d --name test-target -p 8000:3000 bkimminich/juice-shop`
- Stopped `friendly_pike` cloudflared container
- Terraform `digitalocean_domain` / `digitalocean_record` for DO-managed DNS

## Operations

```bash
ssh root@138.197.8.50

# decision log (one JSON object per line: alert + result)
cat /opt/trader-agent/scripts/alerts_log.jsonl | tail -1 | python3 -m json.tool
tail -f /opt/trader-agent/scripts/alerts_log.jsonl

# service logs
journalctl -u trader-agent -f
journalctl -u trader-agent -p err --since -1h

# health
curl -s http://127.0.0.1:8000/health | python3 -m json.tool

# monitor alerts (only writes when something is wrong)
cat /opt/trader-agent/scripts/health_monitor.log

systemctl restart trader-agent
```

## Disk and memory reclaimed (2026-09-28)

| Removed | Space |
|---|---|
| Docker images (juice-shop 511 MB, cloudflared 97 MB) | 608 MB |
| LXD + core20/core22 snaps (zero instances in use) | ~400 MB |
| snapd entirely, apt cache, orphaned packages | ~250 MB |
| **Total** | **~2.1 GB** (11 GB used → 8.9 GB, 43% → 37%) |

`dockerd`, `containerd` and `snapd` are stopped and masked, freeing ~64 MB of RAM. The trading
stack never used Docker or snapd; both came from the droplet being built on the `docker-20-04`
image for Juice Shop. Restore with `systemctl unmask docker.service docker.socket &&
systemctl enable --now docker` if ever needed.

Steady state is now ~505 MB available, 4 GB swap barely touched (34 MB).

## Position safety (added 2026-09-29)

Two gaps found after a trade flipped from green to -$500+ across a session gap:

- **No trade is held past its session.** `is_session_active()` only gated *entry*; nothing
  closed positions, and the 45-minute age check merely moved the stop to breakeven without
  closing. A background sweep now flattens any position whose session has closed, closes within
  15 minutes, or that is still open on Friday after 18:00 ET. Flatten = breakeven stop first
  (the close order is IOC-then-GTC and may not fill), then a close order.
- **Weekend guard.** ASIA (20:00-06:00 ET) spans Saturday, so the old session check would have
  admitted Saturday entries on a closed market. Entries are now rejected at the weekend and the
  whole book is flattened Friday after 18:00 ET.
- **No opposing positions on one instrument.** A buy is rejected while a sell is open on that
  symbol and vice versa; the rejection reason names the blocking position.
- Instrument-id lookups are cached for an hour (9 call sites, 3 remain direct). The per-position
  symbol resolution loops were calling the broker once per symbol per open position every 30s,
  which is what drove the earlier 429s.

## Known issues

- **SSH is firewalled to two IPs** (`174.209.193.129`, `73.106.218.36`). If your home IP changes you
  lose SSH and must use the DigitalOcean console to recover, or widen the rule.
- `webhook.cello1505.com/webhook` has no authentication (pre-existing).
- Old server 143.198.7.200 has no firewall applied at all.
- Script fixes are **not committed**. The droplet got them via scp; a `git pull` on the server
  would not have them. Commit before relying on the repo as source of truth.
- `webhook.cello1505.com/webhook` has no authentication (pre-existing).
