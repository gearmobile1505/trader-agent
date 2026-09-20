#!/usr/bin/env python3
"""
Localtunnel URL Monitor
Checks if the localtunnel URL is accessible and alerts when it changes.
Run this in the background during backtesting.
Usage: python3 scripts/monitor_tunnel.py
Logs to: ~/.trader_agent_tunnel_monitor.log
"""
import json
import subprocess
import sys
import time
import urllib.request
import os
from datetime import datetime

URL_FILE = os.path.expanduser("~/.trader_agent_tunnel_url")
LOG_FILE = os.path.expanduser("~/.trader_agent_tunnel_monitor.log")
CHECK_INTERVAL = 300  # 5 minutes
LT_LOG = os.path.expanduser("~/trader_agent/scripts/lt.log")

def load_url():
    try:
        with open(URL_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

def save_url(url):
    with open(URL_FILE, "w") as f:
        f.write(url)

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def check_url_accessible(url):
    try:
        req = urllib.request.Request(url.replace("https://", "https://"), method="GET")
        req.add_header("Accept", "application/json")
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status == 200
    except Exception:
        return False

def parse_lt_log():
    try:
        with open(LT_LOG) as f:
            for line in f:
                if "your url is:" in line.lower():
                    url = line.split("your url is:")[1].strip()
                    return url
    except Exception:
        pass
    return None

def notify(title, message):
    try:
        subprocess.run([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}"'
        ], timeout=5)
    except Exception:
        pass

def main():
    current_url = load_url()
    if current_url:
        log(f"Monitoring URL: {current_url}")
    else:
        log("No URL found yet. Will detect on first check.")

    while True:
        try:
            # Check if current URL is accessible
            if current_url:
                accessible = check_url_accessible(current_url + "/status")
                if not accessible:
                    log(f"URL NOT ACCESSIBLE: {current_url}")
                    notify("Tunnel Warning", f"URL not accessible: {current_url}\nCheck if tunnel is running.")
            else:
                # Try to find URL from localtunnel log
                new_url = parse_lt_log()
                if new_url and new_url != current_url:
                    log(f"NEW URL detected in log: {new_url}")
                    current_url = new_url
                    save_url(current_url)
                    notify("Tunnel URL Changed", f"New URL: {new_url}\nUpdate TradingView webhook URLs!")
                    log(f"Accessible: {check_url_accessible(current_url + '/status')}")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log("Stopped.")
            sys.exit(0)
        except Exception as e:
            log(f"Error: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
