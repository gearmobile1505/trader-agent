#!/bin/bash

echo "=== Starting trader-agent setup at $(date) ==="

# Create swap file before anything else (Ollama needs it)
fallocate -l 8G /swapfile 2>/dev/null
chmod 600 /swapfile 2>/dev/null
mkswap /swapfile 2>/dev/null
swapon /swapfile 2>/dev/null
echo '/swapfile none swap sw 0 0' >> /etc/fstab 2>/dev/null

# Update system
apt-get update && apt-get upgrade -y

# Install dependencies
apt-get install -y \
    python3 python3-pip python3-venv \
    git curl wget \
    postgresql-client

# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Start Ollama service
systemctl enable ollama
systemctl start ollama

# Create trader user
useradd -m -s /bin/bash trader
usermod -aG sudo trader
echo "trader ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/trader

# Set up project directory (clone from repo)
rm -rf /opt/trader-agent
sudo -u trader git clone https://github.com/gearmobile1505/trader-agent.git /opt/trader-agent

# Create Python virtual environment
sudo -u trader python3 -m venv /opt/trader-agent/venv
sudo -u trader /opt/trader-agent/venv/bin/pip install --upgrade pip

# Install Python dependencies from repo
sudo -u trader /opt/trader-agent/venv/bin/pip install -r /opt/trader-agent/requirements.txt

# Create .env file from terraform variables
cat > /opt/trader-agent/.env << ENVEOF
TL_ENV=https://demo.tradelocker.com
TL_USER=${tradelocker_email}
TL_PASS=${tradelocker_password}
TL_SERVER=${tradelocker_server}
ENVEOF

chown trader:trader /opt/trader-agent/.env
chmod 600 /opt/trader-agent/.env

# Create systemd service
cat > /etc/systemd/system/trader-agent.service << 'SVC_EOF'
[Unit]
Description=Trader Agent Webhook Server
After=network.target ollama.service
Requires=ollama.service

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
MemoryMax=3G
CPUQuota=200%

[Install]
WantedBy=multi-user.target
SVC_EOF

# Configure Cloudflare Tunnel directly to FastAPI port 8000
cat > /etc/systemd/system/cloudflared.service << 'CLOUD_EOF'
[Unit]
Description=Cloudflare Tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/cloudflared tunnel --url http://localhost:8000 --no-prechecks
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
CLOUD_EOF

systemctl daemon-reload
systemctl enable cloudflared
systemctl restart cloudflared

# Wait for Ollama to be ready and pull model
sleep 15
ollama pull ${ollama_model}

# Start the service
systemctl start trader-agent

# Wait for service to be ready
sleep 5
curl -sf http://localhost:8000/status > /dev/null 2>&1 && echo "Service healthy" || echo "Service not healthy yet"

echo "=== Setup completed at $(date) ==="
echo "Webhook URL: https://YOUR_TUNNEL_URL.trycloudflare.com/webhook"
echo "Status: https://YOUR_TUNNEL_URL.trycloudflare.com/status"
