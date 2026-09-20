#!/bin/bash

echo "=== Starting trader-agent setup at $(date) ==="

# Update system
apt-get update && apt-get upgrade -y

# Install dependencies
apt-get install -y \
    python3 python3-pip python3-venv \
    git curl wget \
    nginx certbot python3-certbot-nginx \
    htop tmux \
    postgresql-client

# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Start Ollama service
systemctl enable ollama
systemctl start ollama

# Pull llama3 model (in background)
ollama pull llama3 &

# Create trader user
useradd -m -s /bin/bash trader
usermod -aG sudo trader
echo "trader ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/trader

# Set up project directory
mkdir -p /opt/trader-agent
chown -R trader:trader /opt/trader-agent

# Clone repository (replace with your actual repo)
# sudo -u trader git clone https://github.com/yourusername/trader-agent.git /opt/trader-agent

# For now, create the project structure
sudo -u trader mkdir -p /opt/trader-agent/{scripts,data,logs}

# Create Python virtual environment
sudo -u trader python3 -m venv /opt/trader-agent/venv
sudo -u trader /opt/trader-agent/venv/bin/pip install --upgrade pip

# Install Python dependencies
cat > /opt/trader-agent/requirements.txt << 'EOF'
fastapi==0.115.0
uvicorn[standard]==0.30.6
requests==2.32.3
httpx==0.27.2
pydantic==2.9.2
pydantic-settings==2.5.2
python-dotenv==1.0.1
python-multipart==0.0.9
ollama==0.4.2
pandas==2.2.2
numpy==1.26.4
ta-lib==0.4.28
pyyaml==6.0.1
psutil==5.9.8
EOF

sudo -u trader /opt/trader-agent/venv/bin/pip install -r /opt/trader-agent/requirements.txt

# Install TA-Lib system dependency
apt-get install -y libta-lib-dev

# Create .env file
cat > /opt/trader-agent/.env << 'ENVEOF'
TRADELOCKER_EMAIL=${tradelocker_email}
TRADELOCKER_PASSWORD=${tradelocker_password}
TRADELOCKER_SERVER=${tradelocker_server}
OLLAMA_MODEL=${ollama_model}
RISK_PER_TRADE=${risk_per_trade}
DAILY_LOSS_LIMIT=${daily_loss_limit}
MAX_CONCURRENT_POSITIONS=${max_concurrent_positions}
WEBHOOK_PORT=8000
LOG_LEVEL=INFO
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
ExecStart=/opt/trader-agent/venv/bin/uvicorn main_cfd_5m:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=trader-agent

# Resource limits
LimitNOFILE=65536
MemoryMax=3G
CPUQuota=200%

[Install]
WantedBy=multi-user.target
SVC_EOF

systemctl daemon-reload
systemctl enable trader-agent

# Configure Nginx reverse proxy
cat > /etc/nginx/sites-available/trader-agent << 'NGINX_EOF'
server {
    listen 80;
    listen [::]:80;
    server_name _;

    # Webhook endpoint - proxy to FastAPI
    location /webhook {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
        client_max_body_size 10M;
    }

    # Health check endpoint
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # API endpoints
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Root redirect
    location / {
        return 301 /health;
    }
}
NGINX_EOF

ln -sf /etc/nginx/sites-available/trader-agent /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# Set up log rotation
cat > /etc/logrotate.d/trader-agent << 'LOGROTATE_EOF'
/opt/trader-agent/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 trader trader
    sharedscripts
    postrotate
        systemctl reload trader-agent > /dev/null 2>&1 || true
    endscript
}

/var/log/user-data.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
    create 644 root root
}
LOGROTATE_EOF

# Create health check script
cat > /opt/trader-agent/health_check.sh << 'HEALTH_EOF'
#!/bin/bash
curl -sf http://localhost:8000/health > /dev/null 2>&1
exit $?
HEALTH_EOF
chmod +x /opt/trader-agent/health_check.sh
chown trader:trader /opt/trader-agent/health_check.sh

# Add cron for health monitoring
(crontab -u trader -l 2>/dev/null; echo "*/5 * * * * /opt/trader-agent/health_check.sh || systemctl restart trader-agent") | crontab -u trader -

# Create deploy script for updates
cat > /opt/trader-agent/deploy.sh << 'DEPLOY_EOF'
#!/bin/bash
set -e
cd /opt/trader-agent
git pull origin main
/opt/trader-agent/venv/bin/pip install -r requirements.txt
systemctl restart trader-agent
echo "Deploy completed at $(date)"
DEPLOY_EOF
chmod +x /opt/trader-agent/deploy.sh
chown trader:trader /opt/trader-agent/deploy.sh

# Wait for Ollama to be ready and pull model
sleep 10
ollama pull llama3

# Start the service
systemctl start trader-agent

# Wait for service to be ready
sleep 5
/opt/trader-agent/health_check.sh && echo "Service healthy" || echo "Service not healthy yet"

echo "=== Setup completed at $(date) ==="
echo "Webhook URL: http://$(curl -s ifconfig.me):8000/webhook"
echo "Health check: http://$(curl -s ifconfig.me)/health"