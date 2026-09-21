# Trader Agent - DigitalOcean Terraform

Deploys a production-ready trading webhook server on DigitalOcean.

## Architecture

```
TradingView → HTTPS → Nginx (port 80/443) → FastAPI (port 8000) → TradeLocker
                                    ↘ Ollama (llama3) for AI risk validation
```

## Resources Created

- **Droplet**: Ubuntu 24.04, size configurable (default s-2vcpu-4gb)
- **Firewall**: SSH (22), HTTP (80), HTTPS (443), Webhook (8000)
- **Domain/Records**: Optional A records for `api` and `webhook` subdomains
- **Cloud-init**: Full bootstrap (Python, Ollama, nginx, systemd service)

## Quick Start

```bash
cd terraform/digitalocean

# 1. Configure variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your DO token, SSH key, TradeLocker creds

# 2. Deploy
terraform init
terraform plan
terraform apply

# 3. Get webhook URL
terraform output webhook_url
```

## Required Variables

| Variable | Description | Source |
|----------|-------------|--------|
| `do_token` | DO Personal Access Token | https://cloud.digitalocean.com/account/api/tokens |
| `ssh_fingerprint` | SSH key fingerprint | `ssh-keygen -lf ~/.ssh/id_ed25519.pub` |
| `tradelocker_email` | TradeLocker login | Your credentials |
| `tradelocker_password` | TradeLocker password | Your credentials |

## Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `region` | nyc3 | DO region (nyc3, sfo3, ams3, sgp1, etc.) |
| `droplet_size` | s-2vcpu-4gb | See [pricing](#costs) |
| `domain_name` | "" | Your domain for auto-DNS (optional) |

## Post-Deploy

1. **SSH to droplet**: `terraform output ssh_command`
2. **Check service**: `systemctl status trader-agent`
3. **View logs**: `journalctl -u trader-agent -f`
4. **Test webhook**: `curl -X POST https://<webhook_url>/webhook -H "Content-Type: application/json" -d '{"action":"buy","ticker":"XAUUSD.R","indicator_value":2665,"suggested_sl":0,"trend":"Phantom Combo Buy"}'`

## SSL/HTTPS

- **With domain**: Certbot auto-configures Let's Encrypt certs on first request
- **Without domain**: HTTP only (TradingView accepts HTTP webhooks)

## Costs

See [COST_COMPARISON.md](COST_COMPARISON.md)

- **Recommended (s-2vcpu-4gb)**: $24/mo
- **Minimum (s-1vcpu-2gb)**: $12/mo (tight for Ollama)
- **With backups**: +$4.80/mo

## File Structure

```
terraform/digitalocean/
├── main.tf                 # Resources
├── variables.tf            # Input variables
├── outputs.tf              # Outputs (IP, URLs, costs)
├── user_data.sh            # Cloud-init bootstrap script
├── terraform.tfvars.example # Example config
└── COST_COMPARISON.md      # AWS vs DO analysis
```

## Updating Code

```bash
# On droplet
cd /opt/trader-agent
git pull
/opt/trader-agent/venv/bin/pip install -r requirements.txt
sudo systemctl restart trader-agent
```

Or use the deploy script: `/opt/trader-agent/deploy.sh`

## Monitoring

- **DO Monitoring**: Enabled by default (CPU, memory, disk, bandwidth)
- **Health endpoint**: `GET /health` (proxied via nginx)
- **Logs**: `journalctl -u trader-agent -f` or `/opt/trader-agent/logs/`

## Security

- Firewall restricts inbound to 22, 80, 443, 8000
- Outbound allows HTTPS (TradeLocker API), DNS, NTP
- TradeLocker creds in `.env` (600 perms, trader user only)
- Systemd service runs as non-root `trader` user
- Resource limits: 3GB RAM, 200% CPU quota

## Destroy

```bash
terraform destroy
```