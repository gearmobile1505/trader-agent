Here is your step-by-step playbook to launch and run your automated trading system locally on your Mac during the New York session next week.

---

### 📋 Pre-Market Startup Checklist (15 Minutes Before NY Open)

Run these steps in order before the market opens (e.g., around 9:15 AM EST / 6:15 AM PST):

#### Step 1: Ensure Ollama is Running
Open a terminal window and verify that local Ollama is active with your model loaded:
```bash
ollama run llama3
```
*(Once you see the `>>>` prompt, press `CTRL+D` to exit back to your terminal shell. Ollama will remain running in the background).*

---

#### Step 2: Start Your FastAPI Webhook Listener (Terminal Tab 1)
Open **Terminal Tab 1**, navigate to your project directory, activate your virtual environment, and launch Uvicorn:

```bash
cd ~/trader_agent
source source/bin/activate    # or source venv/bin/activate
uvicorn main:app --reload --port 8000
```
*You should see:* `Application startup complete` and `Uvicorn running on http://127.0.0.1:8000`.

---

#### Step 3: Launch `ngrok` Public Tunnel (Terminal Tab 2)
Open **Terminal Tab 2** and start the tunnel to map port 8000 to the web:

```bash
ngrok http 8000
```

* **Copy the public HTTPS URL** from the terminal output (e.g., `https://xxxx-xx-xx-xx.ngrok-free.app`).
* Your live webhook endpoint for the day is:
  ```text
  https://xxxx-xx-xx-xx.ngrok-free.app/webhook
  ```

---

#### Step 4: Update TradingView Alert Webhook URL
1. Open TradingView on your chart (`MNQ1!`, `MES1!`, etc.).
2. Edit your **Phantom Flow** / **BOS** alert settings.
3. In the **Notifications** tab, paste your fresh ngrok URL (`https://xxxx-xx-xx-xx.ngrok-free.app/webhook`) into the **Webhook URL** field.
4. Save the alert.

---

#### Step 5: Run Pre-Flight Execution Test (Terminal Tab 3)
Open **Terminal Tab 3** and run a simulated alert to verify that Ollama, FastAPI, and GatesFX are communicating cleanly:

```bash
curl -X POST "http://127.0.0.1:8000/webhook" -H "Content-Type: application/json" -d '{"action": "buy", "ticker": "MNQ1!", "indicator_value": 20500.00, "suggested_sl": 20450.00, "trend": "Bullish Shift"}'
```

*Check **Terminal Tab 1** (`uvicorn`): You should see `200 OK` and TradeLocker instrument mapping logs.*

---

### 🟢 Live Session Execution & Monitoring

* **Hands-Off Operation:** Leave Terminal Tabs 1 and 2 open throughout the NY session.
* **Real-Time Logs:** Watch **Terminal Tab 1** when a signal fires on TradingView. You will see:
  1. The exact JSON payload received from TradingView.
  2. The dynamically calculated contract size based on your **\$250 risk budget** [73–77].
  3. Ollama’s `APPROVED` or `REJECTED` decision.
  4. The market order response submitted to GatesFX on TradeLocker.

---

### 🛠️ Quick Troubleshooting Commands

* **If Port 8000 is stuck:**
  ```bash
  lsof -ti :8000 | xargs kill -9
  ```
* **If Ollama fails to respond:**
  ```bash
  ollama serve
  ```

---

🎯 *When you're ready next week after testing locally, would you like me to help you set up an Nginx reverse proxy with a free SSL certificate so your cloud deployment runs over HTTPS without ngrok?*


Here is the complete guide to deploying your FastAPI webhook bridge to an **Ubuntu Cloud Droplet (e.g., DigitalOcean)** and configuring it as a **systemd background service** so it runs continuously 24/7.

---

### Step 1: Provision Your Cloud Server
1. **Create Droplet**: Provision a basic **Ubuntu 24.04 LTS** droplet (\$4–\$6/month). Select the data center region closest to your broker server (e.g., New York or Frankfurt) to minimize execution latency.
2. **Access Server**: SSH into your new server via terminal:
   ```bash
   ssh root@YOUR_DROPLET_IP
   ```
3. **Update & Install Python**:
   ```bash
   apt update && apt upgrade -y
   apt install python3-pip python3-venv ufw -y
   ```

---

### Step 2: Set Up Project Files & Security
1. **Create Project Directory & Virtual Environment**:
   ```bash
   mkdir -p /opt/trader_agent
   cd /opt/trader_agent
   python3 -m venv venv
   source venv/bin/activate
   ```
2. **Transfer Code**: Create your `requirements.txt` and `main.py` files inside `/opt/trader_agent/`.
3. **Set Up `.env` Environment Variables**:
   Create `/opt/trader_agent/.env` with your broker credentials:
   ```env
   TL_ENV=https://demo.tradelocker.com
   TL_USER=gearmobile1505@gmail.com
   TL_PASS=ve8bb0!S
   TL_SERVER=GATESFX
   ```
4. **Lock Down `.env` Permissions**: Prevent unauthorized user accounts or unprivileged background processes from reading your credentials:
   ```bash
   chmod 600 /opt/trader_agent/.env
   ```
5. **Install Python Packages**:
   ```bash
   pip install -r requirements.txt
   ```

---

### Step 3: Configure Systemd Background Service
To ensure your FastAPI application runs continuously, automatically restarts on crashes, and boots up on system reboots, set it up as a systemd service:

1. **Create Systemd Configuration File**:
   ```bash
   nano /etc/systemd/system/trader.service
   ```
2. **Paste Service Configuration**:
   ```ini
   [Unit]
   Description=TradingView to TradeLocker Webhook Bridge
   After=network.target

   [Service]
   User=root
   WorkingDirectory=/opt/trader_agent
   ExecStart=/opt/trader_agent/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```
   *(Note: Setting `--host 0.0.0.0` binds Uvicorn to accept incoming connections across all network interfaces).*

3. **Enable & Start the Service**:
   ```bash
   systemctl daemon-reload
   systemctl start trader
   systemctl enable trader
   ```
4. **Verify Service Status**:
   ```bash
   systemctl status trader
   ```
   *You should see `active (running)` in green.*

---

### Step 4: Configure Server Firewall (UFW)
Protect your droplet by blocking unauthorized traffic while keeping port 8000 open for TradingView webhooks:

```bash
ufw allow 22/tcp
ufw allow 8000/tcp
ufw enable
```

---

### Step 5: Update TradingView Webhook URL
Now that your cloud server is running, you no longer need `ngrok` running on your local machine. 

In TradingView, update your **Webhook URL** under Alert Notifications to your server's public endpoint:
```text
http://YOUR_DROPLET_IP:8000/webhook
```

---

🛡️ *Would you like me to walk you through configuring an SSL certificate with Nginx so your server uses encrypted HTTPS webhooks instead of plain HTTP?*