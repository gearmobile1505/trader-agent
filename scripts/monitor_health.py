#!/usr/bin/env python3
"""
Trader Agent Health & Tunnel Monitor (Server-side)
Checks: trader-agent service, cloudflared service, webhook endpoint, available
memory, OOM kills, and AI backend state.

Runs via cron every 5 minutes. Only writes to the notification log when something
changes or a service is down, so a healthy box stays quiet.

Usage: python3 /opt/trader-agent/scripts/monitor_health.py
Logs to: /opt/trader-agent/scripts/health_monitor.log
State file: /opt/trader-agent/scripts/.last_tunnel_url
AI state file: /opt/trader-agent/scripts/.last_health_ai
"""
import json
import os
import subprocess
import sys
from datetime import datetime

ALERT_LOG = "/opt/trader-agent/scripts/alerts_log.jsonl"
HEALTH_LOG = "/opt/trader-agent/scripts/health_monitor.log"
STATE_FILE = "/opt/trader-agent/scripts/.last_tunnel_url"
AI_STATE_FILE = "/opt/trader-agent/scripts/.last_health_ai"
CHECK_URL = "http://127.0.0.1:8000/status"
HEALTH_URL = "http://127.0.0.1:8000/health"

# Memory floor in MB. A 1 GB droplet has no headroom; this is the OOM early warning.
MEMORY_FLOOR_MB = 512
# No AI decision for this long during a trading session means the reviewer is dead
# or denying everything while the service reports healthy.
AI_STALE_MINUTES = 120


def run(cmd):
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
        return result.returncode, result.stdout.strip()
    except Exception as e:
        return -1, str(e)


def is_service_active(service):
    rc, _ = run(["systemctl", "is-active", "--quiet", service])
    return rc == 0


def get_tunnel_url():
    rc, output = run(
        ["journalctl", "-u", "cloudflared", "--no-pager", "-n", "200"]
    )
    if rc != 0:
        return None
    for line in reversed(output.splitlines()):
        if "trycloudflare.com" in line and "INF" in line:
            parts = line.split("https://")
            for p in parts[1:]:
                url = "https://" + p.split()[0].rstrip("|").strip()
                if url not in ("https://", "https:////"):
                    return url
    return None


def load_last_url():
    try:
        with open(STATE_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def save_url(url):
    with open(STATE_FILE, "w") as f:
        f.write(url)


def log_alert(msg):
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    with open(HEALTH_LOG, "a") as f:
        f.write(line + "\n")


def check_endpoint():
    import urllib.request
    try:
        req = urllib.request.Request(CHECK_URL, method="GET")
        resp = urllib.request.urlopen(req, timeout=10)
        # urlopen returns http.client.HTTPResponse, which exposes .status /
        # .getcode(). It has no .status_code (that is a requests attribute),
        # so reading .status_code here always raised and reported a false alarm.
        return resp.status == 200
    except Exception:
        return False


def fetch_health():
    """Return the parsed /health payload, or None if unreachable."""
    import urllib.request
    try:
        req = urllib.request.Request(HEALTH_URL, method="GET")
        resp = urllib.request.urlopen(req, timeout=10)
        if resp.status != 200:
            return None
        return json.loads(resp.read().decode())
    except Exception:
        return None


def available_memory_mb():
    """Available RAM in MB, or None if free is unavailable."""
    rc, output = run(["free", "-m"])
    if rc != 0:
        return None
    for line in output.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) >= 7:
                try:
                    return int(parts[6])
                except ValueError:
                    return None
    return None


def recent_oom_kills():
    """Lines from the kernel journal in the last hour indicating an OOM kill."""
    rc, output = run(
        ["journalctl", "-k", "--since", "-1h", "--no-pager"]
    )
    if rc != 0:
        return []
    hits = [
        line for line in output.splitlines()
        if "out of memory" in line.lower() or "oom-kill" in line.lower()
        or "oom_reaper" in line.lower()
    ]
    return hits[-3:]


def load_ai_state():
    try:
        with open(AI_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_ai_state(ai):
    try:
        with open(AI_STATE_FILE, "w") as f:
            json.dump(ai, f)
    except OSError:
        pass


def check_ai_backend(alerts):
    """
    Alert on a misconfigured provider, a rising failure count, or a reviewer
    that has gone quiet while the process still reports healthy.
    """
    health = fetch_health()
    if health is None:
        alerts.append("ALERT: /health endpoint not responding on 127.0.0.1:8000")
        return

    ai = health.get("ai") or {}
    backend = ai.get("backend", "unknown")
    failures = ai.get("failures", 0)
    previous = load_ai_state()

    if backend == "cloud" and not ai.get("key_configured"):
        alerts.append("ALERT: AI backend is 'cloud' but CLOUD_API_KEY is not set — no trade can execute")

    if backend == "ollama" and ai.get("reachable") is False:
        alerts.append(f"ALERT: Ollama backend unreachable: {ai.get('last_error')}")

    if failures and failures != previous.get("failures"):
        detail = f" (last: {ai.get('last_error')})" if ai.get("last_error") else ""
        alerts.append(f"ALERT: AI failures rose {previous.get('failures', 0)} -> {failures}{detail}")

    last_decision_at = ai.get("last_decision_at")
    if last_decision_at:
        try:
            age = datetime.utcnow() - datetime.strptime(
                last_decision_at[:19], "%Y-%m-%dT%H:%M:%S"
            )
            stale_minutes = age.total_seconds() / 60
            if stale_minutes > AI_STALE_MINUTES and not previous.get("stale_reported"):
                alerts.append(
                    f"ALERT: no AI decision for {stale_minutes:.0f} min "
                    f"(last_decision={ai.get('last_decision')}) — reviewer may be denying everything"
                )
                previous["stale_reported"] = True
        except (ValueError, TypeError):
            pass
    elif not previous.get("never_reported"):
        alerts.append(
            f"ALERT: AI backend '{backend}' has produced zero decisions since service start"
        )
        previous["never_reported"] = True

    previous["failures"] = failures
    previous["backend"] = backend
    save_ai_state(previous)


def check_memory(alerts):
    """Available-memory floor and any OOM kill in the last hour."""
    available = available_memory_mb()
    if available is not None and available < MEMORY_FLOOR_MB:
        alerts.append(
            f"ALERT: available memory {available} MB is below the {MEMORY_FLOOR_MB} MB floor"
        )

    for line in recent_oom_kills():
        alerts.append(f"ALERT: kernel OOM event: {line.strip()[:200]}")


def main():
    alerts = []

    # 1. Check trader-agent service
    if not is_service_active("trader-agent"):
        alerts.append("ALERT: trader-agent service is DOWN")

    # 2. Check cloudflared service
    if not is_service_active("cloudflared"):
        alerts.append("ALERT: cloudflared (tunnel) service is DOWN")

    # 3. Check FastAPI endpoint
    if not check_endpoint():
        alerts.append("ALERT: FastAPI /status endpoint not responding on 127.0.0.1:8000")

    # 4. Memory pressure and OOM history
    check_memory(alerts)

    # 5. AI backend state (only if the service is up)
    if not any("trader-agent service is DOWN" in a for a in alerts):
        check_ai_backend(alerts)

    # 6. Check tunnel URL change
    current_url = get_tunnel_url()
    last_url = load_last_url()

    if current_url and last_url and current_url != last_url:
        alerts.append(
            f"ALERT: Tunnel URL changed! "
            f"Old: {last_url}\n"
            f"      New: {current_url}\n"
            f"      Update TradingView webhook URL if still using Quick Tunnel."
        )
        save_url(current_url)
    elif current_url and not last_url:
        save_url(current_url)

    # 7. Log only if there are alerts
    if alerts:
        for a in alerts:
            log_alert(a)
    else:
        # Quiet run — no issues, no output needed
        pass


if __name__ == "__main__":
    main()

