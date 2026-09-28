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

## Known issues

- The droplet runs Ubuntu 22.04, so `tradelocker==0.56.0` (needs py>=3.11) required installing
  Python 3.12 from deadsnakes. `user_data.sh` does not do this yet — add it before any fresh
  droplet is created from this config.
- Script fixes are **not committed**. The droplet got them via scp; a `git pull` on the server
  would not have them. Commit before relying on the repo as source of truth.
- `webhook.cello1505.com/webhook` has no authentication (pre-existing).
