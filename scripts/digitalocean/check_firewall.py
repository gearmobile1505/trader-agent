#!/usr/bin/env python3
"""
Read-only check of DigitalOcean firewall settings for the trader-agent droplets.

Verifies, against the live DigitalOcean API, that every droplet we care about
has at least one firewall attached, and that no firewall leaves SSH (TCP 22)
open to the world. It prints a readable summary of each attached firewall's
rules so a human can eyeball the rest.

Context: the Terraform in terraform/digitalocean/main.tf attaches a firewall
only to the v2 droplet (trader-agent-v2, 138.197.8.50). The droplet actually
running live trades, trader-agent-prod-3 (143.198.7.200), is tracked read-only
in that file and has no firewall attached in Terraform. This script is the
check that catches that gap -- and any future drift -- without changing
anything. It never creates, modifies, or deletes resources.

Usage:
  DO_TOKEN=<digitalocean-api-token> python3 scripts/digitalocean/check_firewall.py

  DO_TOKEN=<token> python3 scripts/digitalocean/check_firewall.py \
      --droplet-id 602517171 --droplet-id 603469248 \
      --ssh-allowed-cidr 203.0.113.10/32

Options:
  --droplet-id        Droplet ID to check. Repeatable. Defaults to 602517171
                      (trader-agent-prod-3, live) and 603469248 (trader-agent-v2).
  --ssh-allowed-cidr  CIDR expected to be the only SSH source. Shown in the
                      report for reference; the script does not fail on it.

Exit codes:
  0  All checks passed.
  1  At least one check failed (droplet without a firewall, or SSH open to
     the world).
  2  Usage error (missing DO_TOKEN, bad arguments) or DigitalOcean API error.

The API token is read only from the DO_TOKEN environment variable and is
never printed.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.digitalocean.com/v2/firewalls"

# trader-agent-prod-3 (live, 143.198.7.200) and trader-agent-v2 (138.197.8.50).
DEFAULT_DROPLET_IDS = (602517171, 603469248)

OPEN_WORLD = ("0.0.0.0/0", "::/0")
SSH_PORT = 22


class ApiError(Exception):
    """DigitalOcean API request failed or returned an unexpected payload."""


def port_range_covers(port_range, port):
    """True if a DigitalOcean port_range string covers the given port.

    Accepts a single port ("22"), a range ("8000-9000"), or "all".
    Anything unparseable covers nothing.
    """
    if port_range is None:
        return False
    text = str(port_range).strip().lower()
    if text == "all":
        return True
    if "-" in text:
        try:
            low, high = (int(part) for part in text.split("-", 1))
        except ValueError:
            return False
        return low <= port <= high
    try:
        return int(text) == port
    except ValueError:
        return False


def ssh_open_to_world(rule):
    """True if an inbound firewall rule exposes TCP 22 to the whole internet."""
    if not isinstance(rule, dict):
        return False
    if rule.get("protocol") != "tcp":
        return False
    if not port_range_covers(rule.get("port_range"), SSH_PORT):
        return False
    addresses = (rule.get("sources") or {}).get("addresses") or []
    return any(address in OPEN_WORLD for address in addresses)


def find_ssh_open_to_world(firewall):
    """Return the inbound rules of a firewall that expose SSH to the world."""
    return [
        rule
        for rule in (firewall or {}).get("inbound_rules") or []
        if ssh_open_to_world(rule)
    ]


def firewalls_for_droplet(firewalls, droplet_id):
    """Return the firewalls attached to the given droplet ID."""
    return [
        firewall
        for firewall in firewalls or []
        if droplet_id in ((firewall or {}).get("droplet_ids") or [])
    ]


def check_droplet(firewalls, droplet_id):
    """Return a list of failure messages for one droplet (empty means pass)."""
    failures = []
    attached = firewalls_for_droplet(firewalls, droplet_id)
    if not attached:
        failures.append(
            f"FAIL droplet {droplet_id}: no firewall attached"
        )
        return failures
    for firewall in attached:
        for rule in find_ssh_open_to_world(firewall):
            failures.append(
                "FAIL firewall {!r} on droplet {}: inbound TCP {} open to "
                "the world via rule {}".format(
                    firewall.get("name"), droplet_id, SSH_PORT, rule
                )
            )
    return failures


def _format_endpoints(rule, direction):
    """Render the address list of a rule's sources (inbound) or destinations."""
    key = "sources" if direction == "inbound" else "destinations"
    endpoints = (rule or {}).get(key) or {}
    addresses = endpoints.get("addresses") or []
    if addresses:
        return ", ".join(str(address) for address in addresses)
    selectors = [
        f"{name}: {value}"
        for name, value in endpoints.items()
        if value and name != "addresses"
    ]
    return "; ".join(selectors) if selectors else "(no endpoints listed)"


def format_rule(rule, direction):
    """Render one firewall rule as a single readable line."""
    protocol = rule.get("protocol", "?")
    port_range = rule.get("port_range", "?")
    endpoints = _format_endpoints(rule, direction)
    arrow = "from" if direction == "inbound" else "to"
    return f"{protocol} {port_range} {arrow} {endpoints}"


def format_firewall_summary(firewall, ssh_allowed_cidr=None):
    """Render a firewall as a readable multi-line summary."""
    lines = [
        "Firewall: {} (id: {})".format(
            firewall.get("name", "?"), firewall.get("id", "?")
        ),
        "  Status: {}".format(firewall.get("status", "?")),
        "  Droplets: {}".format(
            ", ".join(str(d) for d in firewall.get("droplet_ids") or [])
            or "(none)"
        ),
    ]
    if ssh_allowed_cidr:
        lines.append(f"  Expected SSH source: {ssh_allowed_cidr}")
    lines.append("  Inbound:")
    for rule in firewall.get("inbound_rules") or []:
        lines.append(f"    {format_rule(rule, 'inbound')}")
    lines.append("  Outbound:")
    for rule in firewall.get("outbound_rules") or []:
        lines.append(f"    {format_rule(rule, 'outbound')}")
    return "\n".join(lines)


def fetch_firewalls(token):
    """Return the full list of firewalls from the DigitalOcean API.

    Follows pagination via links.pages.next. Raises ApiError on transport
    failures, non-2xx responses, or unexpected payloads. The token is used
    only in the Authorization header and is never logged.
    """
    firewalls = []
    url = API_BASE
    while url:
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ApiError(
                f"DigitalOcean API request failed: HTTP {exc.code}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ApiError(f"DigitalOcean API request failed: {exc}") from exc
        if not isinstance(payload, dict) or "firewalls" not in payload:
            raise ApiError("DigitalOcean API returned an unexpected payload")
        firewalls.extend(payload["firewalls"])
        url = ((payload.get("links") or {}).get("pages") or {}).get("next")
    return firewalls


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Check DigitalOcean firewall settings for the trader-agent droplets."
        )
    )
    parser.add_argument(
        "--droplet-id",
        type=int,
        action="append",
        default=None,
        help="Droplet ID to check (repeatable). Defaults to the live and v2 droplets.",
    )
    parser.add_argument(
        "--ssh-allowed-cidr",
        default=None,
        help="CIDR expected to be the only SSH source; shown in the report only.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    token = os.getenv("DO_TOKEN")
    if not token:
        print(
            "ERROR: DO_TOKEN environment variable is not set. "
            "Export a DigitalOcean API token as DO_TOKEN and retry.",
            file=sys.stderr,
        )
        return 2

    droplet_ids = args.droplet_id or list(DEFAULT_DROPLET_IDS)
    try:
        firewalls = fetch_firewalls(token)
    except ApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    failures = []
    for droplet_id in droplet_ids:
        attached = firewalls_for_droplet(firewalls, droplet_id)
        if attached:
            for firewall in attached:
                print(format_firewall_summary(firewall, args.ssh_allowed_cidr))
                print()
        else:
            print(f"Droplet {droplet_id}: no firewall attached")
            print()
        failures.extend(check_droplet(firewalls, droplet_id))

    if failures:
        for failure in failures:
            print(failure)
        return 1

    print("PASS: every checked droplet has a firewall and SSH is not open to the world")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
