# Named Cloudflare Tunnel Setup

Converts the temporary Quick Tunnel to a permanent named tunnel using your own domain.

## Prerequisites

1. **Domain in Cloudflare** — Your domain's nameservers must point to Cloudflare's nameservers.
2. **DNS record** — Create an A record for your subdomain (e.g., `webhook`) pointing to the droplet's public IP (`143.198.7.200`), with the orange-cloud (proxied) enabled.
3. **cloudflared installed** on the droplet (already present — v2026.9.3).

## Steps

### 1. SSH into the droplet

```bash
ssh root@143.198.7.200
```

### 2. Authenticate cloudflared

```bash
cloudflared tunnel login
```

This opens a browser window (or prints a URL) to authenticate cloudflared with your Cloudflare account. Approve the request.

### 3. Create a named tunnel

```bash
cloudflared tunnel create trader-agent
```

This outputs a **tunnel UUID** and creates a credentials file at:

```
/root/.cloudflared/<TUNNEL_UUID>.json
```

Save the UUID — you'll need it in the config.

### 4. Route DNS to the tunnel

```bash
cloudflared tunnel route dns trader-agent webhook.yourdomain.com
```

Replace `webhook.yourdomain.com` with your actual subdomain.

### 5. Create the config file

```bash
cat > /etc/cloudflared/config.yml <<'EOF'
tunnel: <TUNNEL_UUID>
credentials-file: /root/.cloudflared/<TUNNEL_UUID>.json

ingress:
  - hostname: webhook.yourdomain.com
    service: http://127.0.0.1:8000
  - service: http_status:404
EOF
```

Replace `<TUNNEL_UUID>` and `webhook.yourdomain.com` with your actual values.

### 6. Install the service

This replaces the old Quick Tunnel systemd service:

```bash
cloudflared service install
```

### 7. Restart and verify

```bash
systemctl restart cloudflared
sleep 3
systemctl status cloudflared --no-pager
cloudflared tunnel show trader-agent
curl -fsS https://webhook.yourdomain.com/status
```

### 8. Update TradingView

Change your TradingView webhook URL from the temporary `trycloudflare.com` URL to:

```
https://webhook.yourdomain.com/webhook
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `curl: (6) Could not resolve host` | Verify the DNS record exists in Cloudflare and is proxied |
| `cloudflared tunnel login` hangs | Ensure your local machine can reach Cloudflare's auth URL |
| Tunnel reports `Unable to route` | Confirm `cloudflared tunnel route dns` was run for the correct hostname |
| Service won't start | Check `journalctl -u cloudflared -n 50 --no-pager` for errors |
| `credentials-file` not found | Verify the UUID matches the file name in `/root/.cloudflared/` |
