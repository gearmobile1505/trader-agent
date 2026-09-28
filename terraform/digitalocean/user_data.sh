#!/bin/bash

echo "=== Starting trader-agent setup at $(date) ==="

# Create swap file before anything else (1 GB box needs it)
fallocate -l 4G /swapfile 2>/dev/null
chmod 600 /swapfile 2>/dev/null
mkswap /swapfile 2>/dev/null
swapon /swapfile 2>/dev/null
echo '/swapfile none swap sw 0 0' >> /etc/fstab 2>/dev/null

# Update system
apt-get update && apt-get upgrade -y

# Install dependencies
# NOTE: no Ollama. The AI risk review is delegated to a cloud API, so there is
# no model to download and no ~2.3 GB of weights to pin on a 25 GB disk.
apt-get install -y \
    python3 python3-pip python3-venv \
    software-properties-common \
    git curl wget \
    postgresql-client

# tradelocker>=0.56 requires Python >=3.11. Ubuntu 22.04 ships 3.10, so pull
# 3.12 from deadsnakes and build the venv with it.
if ! command -v python3.12 > /dev/null 2>&1; then
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update
    apt-get install -y python3.12 python3.12-venv python3.12-dev
fi
PYTHON_BIN=python3.12

# Fix journald so `journalctl` actually returns something.
# Container images often ship /var/log/journal/<machine-id> for the *build*
# machine, so journald finds no journal for this host and every journalctl
# call is a silent no-op. That breaks service log inspection and the OOM
# detection in monitor_health.py.
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\n' > /etc/systemd/journald.conf.d/persistent.conf
mkdir -p "/var/log/journal/$(cat /etc/machine-id)"
chown root:systemd-journal "/var/log/journal/$(cat /etc/machine-id)"
chmod 2755 "/var/log/journal/$(cat /etc/machine-id)"
systemctl restart systemd-journald

# Create trader user
useradd -m -s /bin/bash trader
usermod -aG sudo trader
echo "trader ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/trader

# Set up project directory (clone from repo)
rm -rf /opt/trader-agent
sudo -u trader git clone https://github.com/gearmobile1505/trader-agent.git /opt/trader-agent

# Create Python virtual environment
sudo -u trader $PYTHON_BIN -m venv /opt/trader-agent/venv
sudo -u trader /opt/trader-agent/venv/bin/pip install --upgrade pip

# Install Python dependencies from repo
sudo -u trader /opt/trader-agent/venv/bin/pip install -r /opt/trader-agent/requirements.txt

# Create .env file from terraform variables
cat > /opt/trader-agent/.env << ENVEOF
TL_ENV=https://demo.tradelocker.com
TL_USER=${tradelocker_email}
TL_PASS=${tradelocker_password}
TL_SERVER=${tradelocker_server}
AI_PROVIDER=${ai_provider}
AI_FAIL_OPEN=${ai_fail_open}
CLOUD_API_URL=${cloud_api_url}
CLOUD_API_KEY=${cloud_api_key}
CLOUD_API_MODEL=${cloud_api_model}
AI_MODEL=${ollama_model}
AI_TIMEOUT_SECONDS=15
AI_MAX_CONCURRENCY=2
AI_QUEUE_TIMEOUT_SECONDS=30
AI_MAX_PREDICT=96
ENVEOF

chown trader:trader /opt/trader-agent/.env
chmod 600 /opt/trader-agent/.env

# Health monitor: memory, OOM and AI backend checks every 5 minutes
cat > /etc/cron.d/trader-health << 'CRON_EOF'
*/5 * * * * trader /opt/trader-agent/venv/bin/python3 /opt/trader-agent/scripts/monitor_health.py >/dev/null 2>&1
CRON_EOF
chmod 644 /etc/cron.d/trader-health

# Create systemd service
# No Requires=ollama.service: the receiver must serve /health even when the AI
# backend is unreachable. Provider failure is handled in-process by the
# configured fail policy (AI_FAIL_OPEN).
cat > /etc/systemd/system/trader-agent.service << 'SVC_EOF'
[Unit]
Description=Trader Agent Webhook Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=trader
Group=trader
WorkingDirectory=/opt/trader-agent
Environment=PATH=/opt/trader-agent/venv/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=/opt/trader-agent/.env
ExecStart=/opt/trader-agent/venv/bin/uvicorn scripts.main_cfd_5m:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=trader-agent

LimitNOFILE=65536
MemoryMax=700M
CPUQuota=200%

[Install]
WantedBy=multi-user.target
SVC_EOF

# Cloudflare named tunnel (stable URL across restarts).
# /etc/cloudflared/config.yml must already exist — see CLOUDFLARE_TUNNEL_SETUP.md.
# cloudflared dials out, so no inbound port needs to be open.
cat > /etc/systemd/system/cloudflared.service << 'CLOUD_EOF'
[Unit]
Description=Cloudflare Tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate run trader-agent
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
CLOUD_EOF

systemctl daemon-reload
systemctl enable cloudflared
systemctl restart cloudflared

# Start the service
systemctl start trader-agent

# Wait for the service to answer, then report health explicitly.
# A bare curl either passes or is silently ignored; the retry loop tells you which.
for i in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8000/health > /dev/null 2>&1; then
    echo "Service healthy after $i seconds"
    break
  fi
  sleep 1
done

curl -fsS http://127.0.0.1:8000/health | python3 -m json.tool || echo "WARNING: /health did not respond"

echo "=== Setup completed at $(date) ==="
echo "Local status:  http://127.0.0.1:8000/health"
echo "Public status: https://<your-tunnel-hostname>/health"
echo "Webhook URL:   https://<your-tunnel-hostname>/webhook"
